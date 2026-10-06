"""Boletim de urna (Portal de Dados Abertos) -> votos de presidente por seção e o horário
em que o boletim chegou ao TSE.

Serve para reconstruir a curva da apuração de uma noite passada: o JSON de divulgação só
guarda o estado atual e o TSE não publica o histórico. Fonte: pacote CKAN
`resultados-{ano}-boletim-de-urna`, um zip `bweb_{turno}t_{UF}_{ddmmaaaahhmm}.zip` por UF
(e ZZ, exterior), com o leiame-boletimurnaweb.pdf. Sai alguns dias depois do turno.

Leiaute (leiame): um registro por seção e votável; latin-1, ';', tudo entre aspas.
Colunas usadas: SG_UF, CD_MUNICIPIO, NR_ZONA, NR_SECAO, CD_CARGO_PERGUNTA, DT_BU_RECEBIDO
("data do boletim de urna recebido"), QT_APTOS, DS_TIPO_VOTAVEL, NR_VOTAVEL, NM_VOTAVEL, QT_VOTOS.
O leiame não diz o fuso de DT_BU_RECEBIDO; a comparação com a curva publicada pelo g1 em
2022 indica horário de Brasília (ver apuracao/projecao/noite.py).

Guardamos só presidente (CD_CARGO_PERGUNTA = 1) em data/parquet/hist_boletim/, um arquivo
por ano e turno, reconstruível a partir dos zips em data/raw/historico/{ano}/boletim/.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
import pandas as pd

from apuracao.coletor.log import LogJson
from apuracao.config import Config

PRESIDENTE = "1"
PADRAO_ZIP = re.compile(r"bweb_(\d)t_([A-Z]{2})_(\d{12})\.zip$")


def urls(cfg: Config, ano: int, turno: int, client: httpx.Client) -> list[str]:
    """Zips do boletim de urna do turno, pelo catálogo do portal (os nomes têm a data de geração)."""
    r = client.get(cfg.historico.boletim_pacote.format(ano=ano))
    if r.status_code == 404:
        return []
    r.raise_for_status()
    corpo = r.json()
    if not corpo.get("success"):
        return []
    return sorted(x["url"] for x in corpo["result"]["resources"]
                  if (m := PADRAO_ZIP.search(x["url"])) and int(m.group(1)) == turno)


def _mais_recentes(nomes: list[str], turno: int) -> list[str]:
    """Um por UF: o de geração mais recente (o TSE pode republicar). Aceita nomes ou URLs."""
    por_uf: dict[str, tuple[datetime, str]] = {}
    for n in nomes:
        m = PADRAO_ZIP.search(n)
        if not m or int(m.group(1)) != turno:
            continue
        geracao = datetime.strptime(m.group(3), "%d%m%Y%H%M")
        if m.group(2) not in por_uf or geracao > por_uf[m.group(2)][0]:
            por_uf[m.group(2)] = (geracao, n)
    return [n for _, n in sorted(por_uf.values(), key=lambda x: x[1])]


def zips_mais_recentes(dir_boletim: Path, turno: int) -> list[Path]:
    return [Path(n) for n in _mais_recentes([str(p) for p in Path(dir_boletim).glob("bweb_*.zip")], turno)]


def _baixar(c: httpx.Client, url: str, destino: Path, log: LogJson) -> Path:
    if destino.exists() and destino.stat().st_size > 0:
        return destino
    tmp = destino.with_name(destino.name + ".tmp")
    with c.stream("GET", url) as resp, open(tmp, "wb") as f:
        resp.raise_for_status()
        for bloco in resp.iter_bytes(1 << 20):
            f.write(bloco)
    os.replace(tmp, destino)
    log.evento("boletim_baixado", arquivo=str(destino), bytes=destino.stat().st_size)
    return destino


def _inteiro(v: str) -> int | None:
    n = int(v)
    return None if n in (-1, -3) else n


def ler_zip(caminho: Path) -> pd.DataFrame:
    """Linhas de presidente do zip: uma por seção e votável."""
    linhas = []
    with zipfile.ZipFile(caminho) as z:
        membro = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(membro) as bruto:
            leitor = csv.DictReader(io.TextIOWrapper(bruto, encoding="latin-1", newline=""), delimiter=";")
            for r in leitor:
                if r["CD_CARGO_PERGUNTA"] != PRESIDENTE:
                    continue
                linhas.append((
                    r["SG_UF"].lower(), r["CD_MUNICIPIO"].zfill(5), r["NR_ZONA"].zfill(4),
                    r["NR_SECAO"].zfill(4), r["DT_BU_RECEBIDO"], _inteiro(r["QT_APTOS"]),
                    r["DS_TIPO_VOTAVEL"], int(r["NR_VOTAVEL"]), r["NM_VOTAVEL"], int(r["QT_VOTOS"]),
                ))
    df = pd.DataFrame(linhas, columns=["uf", "cod_mun", "zona", "secao", "recebido", "aptos",
                                       "tipo", "numero", "nome", "votos"])
    df["recebido"] = pd.to_datetime(df["recebido"], format="%d/%m/%Y %H:%M:%S", errors="coerce")
    return df


def carregar(cfg: Config, ano: int, turno: int, dir_parquet: Path, log: LogJson, baixar: bool = True,
             guardar_zip: bool = True, transport: httpx.BaseTransport | None = None) -> Path | None:
    """Baixa (se `baixar`) e lê os zips, um por vez, e grava
    data/parquet/hist_boletim/{ano}_t{turno}.parquet. Com guardar_zip=False cada zip é apagado
    depois de lido: o 1º turno tem ~1,4 GB de zips (todos os cargos) e o Parquet só uns MB."""
    dir_zip = Path(cfg.historico.dir) / str(ano) / "boletim"
    dir_zip.mkdir(parents=True, exist_ok=True)
    partes = []
    with httpx.Client(transport=transport, timeout=httpx.Timeout(60, read=300), follow_redirects=True) as c:
        if baixar:
            fontes = _mais_recentes(urls(cfg, ano, turno, c), turno)
            if not fontes:
                log.evento("boletim_indisponivel", ano=ano, turno=turno)
                return None
        else:
            fontes = [str(p) for p in zips_mais_recentes(dir_zip, turno)]
            if not fontes:
                log.evento("boletim_sem_zips", ano=ano, turno=turno)
                return None
        for fonte in fontes:
            z = _baixar(c, fonte, dir_zip / fonte.rsplit("/", 1)[-1], log) if baixar else Path(fonte)
            df = ler_zip(z)
            log.evento("boletim_lido", arquivo=z.name, linhas=len(df),
                       secoes=int(df[["uf", "cod_mun", "zona", "secao"]].drop_duplicates().shape[0]))
            partes.append(df)
            if not guardar_zip:
                z.unlink()
    df = pd.concat(partes, ignore_index=True).assign(ano=ano, turno=turno)
    destino = Path(dir_parquet) / "hist_boletim" / f"{ano}_t{turno}.parquet"
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".tmp")
    df.to_parquet(tmp, index=False)
    os.replace(tmp, destino)
    log.evento("boletim_gravado", arquivo=str(destino), linhas=len(df))
    return destino
