"""Constrói as tabelas analíticas (Parquet) a partir de data/raw/.

data/parquet/
  snapshot_totais/part-*.parquet
  snapshot_candidatos/part-*.parquet
  municipios.parquet

Incremental: cada passada processa só os snapshots brutos ainda ausentes da
coluna `arquivo_raw` de snapshot_totais, e grava um part novo (escrita em .tmp +
os.replace, então leitores nunca veem arquivo pela metade). Apagar data/parquet/
e rodar de novo reconstrói tudo a partir do bruto.

Timestamps são gravados como TIMESTAMP sem fuso, em UTC.

Um único processo de construção por vez (o painel só lê).
"""

from __future__ import annotations

import gzip
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from apuracao.coletor.log import LogJson
from apuracao.coletor.snapshot import SUFIXO_DADO, SUFIXO_META
from apuracao.modelo import ea12, ea20

TOTAIS = [
    ("eleicao", "INTEGER"), ("turno", "INTEGER"), ("cargo", "INTEGER"),
    ("abrangencia", "VARCHAR"), ("tpabr", "VARCHAR"), ("uf", "VARCHAR"), ("cod_mun", "VARCHAR"),
    ("ts_coleta", "TIMESTAMP"), ("ts_tse", "TIMESTAMP"), ("ts_totalizacao", "TIMESTAMP"),
    ("andamento", "VARCHAR"), ("totalizacao_final", "BOOLEAN"), ("divulga", "BOOLEAN"),
    ("secoes_total", "BIGINT"), ("secoes_totalizadas", "BIGINT"), ("pct_secoes", "DOUBLE"),
    ("eleitorado", "BIGINT"), ("eleitorado_instaladas", "BIGINT"),
    ("comparecimento", "BIGINT"), ("abstencao", "BIGINT"),
    ("votos_total", "BIGINT"), ("votos_validos", "BIGINT"), ("brancos", "BIGINT"), ("nulos", "BIGINT"),
    ("arquivo_raw", "VARCHAR"),
]
CANDIDATOS = [
    ("eleicao", "INTEGER"), ("turno", "INTEGER"), ("cargo", "INTEGER"), ("abrangencia", "VARCHAR"),
    ("ts_coleta", "TIMESTAMP"), ("ts_tse", "TIMESTAMP"),
    ("sq_cand", "VARCHAR"), ("numero", "INTEGER"), ("nome", "VARCHAR"), ("partido", "VARCHAR"),
    ("votos", "BIGINT"), ("pct_validos", "DOUBLE"), ("pct_tse", "DOUBLE"),
    ("destinacao", "VARCHAR"), ("eleito", "BOOLEAN"), ("situacao", "VARCHAR"),
    ("arquivo_raw", "VARCHAR"),
]
MUNICIPIOS = [
    ("cod_tse", "VARCHAR"), ("cod_ibge", "VARCHAR"), ("nome", "VARCHAR"), ("uf", "VARCHAR"),
    ("regiao", "VARCHAR"), ("capital", "BOOLEAN"),
]

COMPACTAR_ACIMA_DE = 200  # parts por tabela


def _utc_naive(dt: datetime | None) -> datetime | None:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt else None


def _ddl(nome: str, colunas: list[tuple[str, str]]) -> str:
    return f"CREATE TABLE {nome} ({', '.join(f'{c} {t}' for c, t in colunas)})"


def _gravar_parquet(destino: Path, nome: str, colunas: list[tuple[str, str]], linhas: list[tuple]) -> None:
    """Grava `linhas` em `destino` (arquivo .parquet) de forma atômica.

    Passa por um NDJSON temporário lido em lote pelo DuckDB: executemany leva
    ~10s para 6 mil linhas, o que tornaria a reconstrução completa lenta demais.
    """
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    ndjson = destino.with_name(destino.name + ".ndjson.tmp")
    nomes = [c for c, _ in colunas]
    with open(ndjson, "w") as f:
        for linha in linhas:
            f.write(json.dumps(dict(zip(nomes, linha)), default=_json_valor, ensure_ascii=False) + "\n")
    tipos = "{" + ", ".join(f"'{c}': '{t}'" for c, t in colunas) + "}"
    con = duckdb.connect()
    try:
        con.execute(_ddl(nome, colunas))
        if linhas:
            con.execute(
                f"INSERT INTO {nome} SELECT * FROM read_json('{ndjson}', "
                f"format='newline_delimited', columns={tipos})"
            )
        con.execute(f"COPY {nome} TO '{tmp}' (FORMAT parquet)")
    finally:
        con.close()
        ndjson.unlink(missing_ok=True)
    os.replace(tmp, destino)


def _json_valor(v):
    if isinstance(v, datetime):
        return v.isoformat()
    raise TypeError(type(v))


def _glob(dir_parquet: Path, tabela: str) -> str:
    return str(Path(dir_parquet) / tabela / "*.parquet")


def ja_processados(dir_parquet: Path) -> set[str]:
    if not list((Path(dir_parquet) / "snapshot_totais").glob("*.parquet")):
        return set()
    con = duckdb.connect()
    try:
        linhas = con.execute(
            f"SELECT DISTINCT arquivo_raw FROM read_parquet('{_glob(dir_parquet, 'snapshot_totais')}')"
        ).fetchall()
    finally:
        con.close()
    return {l[0] for l in linhas}


def _ts_coleta(caminho: Path) -> datetime:
    meta = caminho.with_name(caminho.name.removesuffix(SUFIXO_DADO) + SUFIXO_META)
    try:
        return datetime.fromisoformat(json.loads(meta.read_text())["ts_coleta"])
    except (OSError, ValueError, KeyError):
        # sem meta: o nome do arquivo é o próprio ts de coleta (20261025T201530.123456Z)
        base = caminho.name.removesuffix(SUFIXO_DADO).split("-")[0]
        return datetime.strptime(base, "%Y%m%dT%H%M%S.%fZ").replace(tzinfo=timezone.utc)


def linhas_do_snapshot(caminho: Path, dir_raw: Path) -> tuple[tuple, list[tuple]]:
    """Converte um snapshot bruto em (linha de totais, linhas de candidatos)."""
    rel = caminho.relative_to(dir_raw).as_posix()
    _, uf, _arquivo, _ = rel.split("/")
    r = ea20.parse(gzip.decompress(caminho.read_bytes()))
    if r.tpabr == "mu":
        abrangencia, cod_mun = f"{uf}{r.cdabr}", r.cdabr
    else:
        abrangencia, cod_mun = uf, None
    ts_coleta = _utc_naive(_ts_coleta(caminho))
    ts_tse = _utc_naive(r.ts_tse)

    total = (
        r.eleicao, r.turno, r.cargo, abrangencia, r.tpabr, uf, cod_mun,
        ts_coleta, ts_tse, _utc_naive(r.ts_totalizacao),
        r.andamento, r.totalizacao_final, r.divulga,
        r.secoes_total, r.secoes_totalizadas, r.pct_secoes,
        r.eleitorado, r.eleitorado_instaladas, r.comparecimento, r.abstencao,
        r.votos_total, r.votos_validos, r.brancos, r.nulos,
        rel,
    )
    cands = [
        (r.eleicao, r.turno, r.cargo, abrangencia, ts_coleta, ts_tse,
         c.sq_cand, c.numero, c.nome, c.partido, c.votos, c.pct_validos, c.pct_tse,
         c.destinacao, c.eleito, c.situacao, rel)
        for c in r.candidatos
    ]
    return total, cands


def construir(dir_raw: Path, dir_parquet: Path, log: LogJson, ignorar: set[str] | None = None) -> int:
    """Processa os snapshots novos. Devolve quantos entraram nas tabelas.

    `ignorar`: caminhos que já falharam nesta execução (evita repetir o erro a cada passada).
    """
    dir_raw, dir_parquet = Path(dir_raw), Path(dir_parquet)
    feitos = ja_processados(dir_parquet) | (ignorar or set())
    novos = sorted(
        p for p in dir_raw.rglob(f"*{SUFIXO_DADO}")
        if p.relative_to(dir_raw).as_posix() not in feitos
        and p.parent.name.endswith("-u")  # só EA20
    )
    totais, cands = [], []
    for p in novos:
        try:
            t, c = linhas_do_snapshot(p, dir_raw)
        except Exception as e:
            rel = p.relative_to(dir_raw).as_posix()
            log.contar("modelo_erros")
            log.evento("modelo_erro", arquivo=rel, erro=f"{type(e).__name__}: {e}")
            if ignorar is not None:
                ignorar.add(rel)
            continue
        totais.append(t)
        cands.extend(c)
    if not totais:
        return 0

    nome = f"part-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.parquet"
    # candidatos antes de totais: totais é quem marca o snapshot como processado
    _gravar_parquet(dir_parquet / "snapshot_candidatos" / nome, "snapshot_candidatos", CANDIDATOS, cands)
    _gravar_parquet(dir_parquet / "snapshot_totais" / nome, "snapshot_totais", TOTAIS, totais)
    log.contar("modelo_snapshots", len(totais))
    log.evento("modelo_passada", snapshots=len(totais), candidatos=len(cands))
    compactar(dir_parquet)
    return len(totais)


def compactar(dir_parquet: Path, acima_de: int = COMPACTAR_ACIMA_DE) -> None:
    """Junta os parts de cada tabela num só quando passam de `acima_de`."""
    for tabela in ("snapshot_totais", "snapshot_candidatos"):
        pasta = Path(dir_parquet) / tabela
        parts = sorted(pasta.glob("*.parquet"))
        if len(parts) <= acima_de:
            continue
        destino = pasta / f"part-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-compactado-{uuid.uuid4().hex[:8]}.parquet"
        tmp = destino.with_name(destino.name + ".tmp")
        lista = ", ".join(f"'{p}'" for p in parts)
        con = duckdb.connect()
        try:
            con.execute(f"COPY (SELECT * FROM read_parquet([{lista}])) TO '{tmp}' (FORMAT parquet)")
        finally:
            con.close()
        os.replace(tmp, destino)
        for p in parts:
            p.unlink(missing_ok=True)


def construir_municipios(cm_json: Path, dir_parquet: Path) -> int:
    """Gera municipios.parquet a partir de um -cm.json (EA12), puro ou .json.gz (snapshot bruto)."""
    conteudo = Path(cm_json).read_bytes()
    if str(cm_json).endswith(".gz"):
        conteudo = gzip.decompress(conteudo)
    municipios = ea12.parse(conteudo)
    linhas = [(m.cod_tse, m.cod_ibge, m.nome, m.uf, m.regiao, m.capital) for m in municipios]
    _gravar_parquet(Path(dir_parquet) / "municipios.parquet", "municipios", MUNICIPIOS, linhas)
    return len(linhas)
