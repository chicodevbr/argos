"""Páginas "abstenção por região": um lugar (o DF, a cidade do Rio, o estado do Rio) dividido em
unidades (regiões administrativas ou municípios), no 1º turno de 2026 comparado a 2022.

uv run python -m apuracao.pagina --recorte brasilia | rio | estado-rj

Dados: detalhe por seção (presidente, com os eleitores em trânsito; a soma confere com os totais
oficiais) e, para regiões dentro de um município, os locais de votação com coordenadas e a malha
oficial das regiões (ver carga/secao.py e carga/regioes.py). Duas formas de agrupar:
- "coordenadas": cada seção vai para a região onde fica o seu local de votação. Um local sem
  coordenadas num ano herda a posição do mesmo local (município, zona, número e nome) no outro ano.
- "municipio": cada seção vai para o seu município (código TSE -> IBGE pela tabela municipios),
  desenhado com a malha municipal do IBGE.

As frases com afirmações sobre os dados são calculadas aqui, como na página nacional.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import duckdb
import pandas as pd

from apuracao.carga import regioes, secao
from apuracao.carga.ibge import _arredondar
from apuracao.config import Config
from apuracao.modelo import consultas

MODELO = Path(__file__).with_name("modelo_regioes.html")
ANOS = (2026, 2022)


@dataclass
class Recorte:
    id: str
    titulo: str                 # <title> e h1
    rotulo: str                 # fim do sobretítulo ("Distrito Federal")
    uf: str
    cod_mun: str | None         # código TSE (5 dígitos); None = a UF inteira
    sigla: str                  # indicadores: "DF 2026"
    no_lugar: str               # "No Distrito Federal"
    do_lugar: str               # "do DF"
    sujeito: str                # "o DF" (frase de comparação com o Brasil)
    nome_total: str             # linha de total da tabela
    unidade: str                # "região administrativa"
    unidades: str               # "regiões administrativas"
    abrev: str                  # "RAs"
    todas: str                  # "todas" / "todos"
    agrupar: str                # "coordenadas" | "municipio"
    malha: Path | None = None   # coordenadas: polígonos das regiões
    campo: str = ""             # coordenadas: propriedade com o nome da região
    nomes: dict[str, str] = field(default_factory=dict)       # chave -> nome de leitura
    preposicao: dict[str, str] = field(default_factory=dict)  # nome -> "no"/"na" (padrão "em")
    extras: dict[str, str] = field(default_factory=dict)      # nome -> texto extra (ex.: bairros)
    metodo: list[str] = field(default_factory=list)           # parágrafos do método
    cortes: list[float] | None = None  # faixas do mapa (5 cortes, 6 cores); None = pelos dados
    nome_curto: str = ""        # "Brasília" (títulos curtos)
    titulo_votos: str = ""      # página de votos por candidato (pagina/votos.py)
    metodo_votos: list[str] = field(default_factory=list)
    subpasta: str = ""          # no site estático: "" = raiz
    saida: str = ""             # arquivo padrão em data/pagina/


def _br(v: float, casas: int = 2) -> str:
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def em(r: Recorte, nome: str) -> str:
    return f"{r.preposicao.get(nome, 'em')} {nome}"


def _completar_coordenadas(loc: pd.DataFrame, outro: pd.DataFrame) -> pd.DataFrame:
    """Locais sem coordenada herdam as do mesmo local (município, zona, número e nome) no outro ano."""
    chave = [c for c in ("cod_mun", "zona", "local", "nome") if c in loc.columns and c in outro.columns]
    ref = outro.dropna(subset=["lat", "lon"])[chave + ["lat", "lon"]]
    m = loc.merge(ref, on=chave, how="left", suffixes=("", "_ref"))
    falta = m["lat"].isna()
    m.loc[falta, "lat"] = m.loc[falta, "lat_ref"]
    m.loc[falta, "lon"] = m.loc[falta, "lon_ref"]
    m["herdou"] = falta & m["lat_ref"].notna()
    return m.drop(columns=["lat_ref", "lon_ref"])


def secoes_por_unidade(r: Recorte, dir_historico: Path, municipios: pd.DataFrame | None = None,
                       anos: tuple[int, ...] = ANOS) -> dict[int, pd.DataFrame]:
    """Por ano: uma linha por seção, com aptos, abstenções e `unidade` (a chave do agrupamento)."""
    saida = {}
    if r.agrupar == "coordenadas":
        regs = regioes.ler(r.malha, r.campo)
        locs = {a: secao.locais(dir_historico / str(a) / f"eleitorado_local_votacao_{a}.zip", r.uf) for a in anos}
        if r.cod_mun:
            locs = {a: l[l["cod_mun"] == r.cod_mun] for a, l in locs.items()}
    for a in anos:
        s = secao.secoes(dir_historico / str(a) / f"detalhe_votacao_secao_{a}.zip", r.uf)
        if r.cod_mun:
            s = s[s["cod_mun"] == r.cod_mun]
        if r.agrupar == "coordenadas":
            # sem coordenada num ano: herda a do mesmo local em outro ano (o mais próximo primeiro)
            loc = locs[a]
            for b in sorted((b for b in anos if b != a), key=lambda b: abs(b - a)):
                loc = _completar_coordenadas(loc.drop(columns=["herdou"], errors="ignore"), locs[b]).assign(
                    herdou=lambda x, prev=loc: x["herdou"] | prev.get("herdou", False))
            loc["unidade"] = regioes.regiao_de(loc["lon"], loc["lat"], regs)
            m = s.merge(loc[["cod_mun", "zona", "local", "unidade", "herdou"]], on=["cod_mun", "zona", "local"], how="left")
            m.attrs["locais_herdados"] = int(loc["herdou"].sum())
        else:
            ibge = dict(zip(municipios["cod_tse"], municipios["cod_ibge"]))
            m = s.assign(unidade=s["cod_mun"].map(ibge))
            m.attrs["locais_herdados"] = 0
        saida[a] = m
    return saida


def cortes_por_quantil(valores: list[float], n: int = 6, passo: float = 0.5) -> list[float]:
    """Cortes para n faixas com quantidades parecidas de unidades, arredondados a `passo` (faixas
    legíveis: "23,5% a 24,5%"). Cortes repetidos depois do arredondamento são descartados."""
    s = pd.Series(valores)
    cortes = []
    for q in [i / n for i in range(1, n)]:
        c = round(float(s.quantile(q)) / passo) * passo
        if not cortes or c > cortes[-1]:
            cortes.append(c)
    return cortes


def _rotulos(cortes: list[float]) -> list[str]:
    f = lambda v: _br(v, 1).replace(",0", "")  # noqa: E731
    return ([f"abaixo de {f(cortes[0])}%"] + [f"{f(a)}% a {f(b)}%" for a, b in zip(cortes, cortes[1:])]
            + [f"{f(cortes[-1])}% ou mais"])


def montar_dados(r: Recorte, por_ano: dict[int, pd.DataFrame], abst_brasil_2026: float | None) -> dict:
    tabelas = {}
    for a, m in por_ano.items():
        g = m.groupby("unidade", dropna=False).agg(aptos=("aptos", "sum"), abst=("abstencoes", "sum"),
                                                   secoes=("secao", "size"), locais=("local", "nunique"))
        g["pct"] = 100 * g["abst"] / g["aptos"]
        tabelas[a] = g
    t26, t22 = tabelas[2026], tabelas[2022]
    sem_unidade = {a: int(t.loc[[i for i in t.index if pd.isna(i)], "aptos"].sum()) for a, t in tabelas.items()}
    linhas = []
    for k in (k for k in t26.index if not pd.isna(k)):
        a26, a22 = t26.loc[k], (t22.loc[k] if k in t22.index else None)
        nome = r.nomes.get(k, str(k))
        linhas.append({
            "ra": nome, "chave": k, "extra": r.extras.get(nome, ""),
            "aptos": int(a26["aptos"]), "abst": int(a26["abst"]), "pct": round(float(a26["pct"]), 3),
            "secoes": int(a26["secoes"]), "locais": int(a26["locais"]),
            "pct_2022": None if a22 is None else round(float(a22["pct"]), 3),
            "aptos_2022": None if a22 is None else int(a22["aptos"]),
        })
    linhas.sort(key=lambda x: -x["pct"])
    total = {a: dict(aptos=int(m["aptos"].sum()), abst=int(m["abstencoes"].sum()),
                     pct=round(100 * m["abstencoes"].sum() / m["aptos"].sum(), 3),
                     secoes=len(m), locais_herdados=m.attrs.get("locais_herdados", 0), sem_unidade=sem_unidade[a])
             for a, m in por_ano.items()}
    pequenas = [x["ra"] for x in sorted(linhas, key=lambda x: x["aptos"]) if x["aptos"] < 10_000]
    cortes = r.cortes or cortes_por_quantil([x["pct"] for x in linhas])
    d = {"ras": linhas, "df": {str(a): v for a, v in total.items()},
         "brasil_2026": None if abst_brasil_2026 is None else round(abst_brasil_2026, 3),
         "meta": {"titulo": r.titulo, "rotulo": r.rotulo, "sigla": r.sigla, "unidade": r.unidade,
                  "unidades": r.unidades, "abrev": r.abrev, "nome_total": r.nome_total,
                  "metodo": r.metodo, "pequenas": pequenas, "coordenadas": r.agrupar == "coordenadas",
                  "cortes": cortes, "rotulos": _rotulos(cortes)}}
    d["textos"] = _textos(r, d)
    return d


def _textos(r: Recorte, d: dict) -> dict:
    us = d["ras"]
    t26, t22 = d["df"]["2026"], d["df"]["2022"]
    maior, menor = us[0], us[-1]
    var = t26["pct"] - t22["pct"]
    com_base = [u for u in us if u["pct_2022"] is not None]
    subiram = sum(1 for u in com_base if u["pct"] > u["pct_2022"])
    acima = sum(1 for u in us if u["pct"] > t26["pct"])
    grandes = [u for u in us if u["aptos"] >= 100_000]
    art, de = ("as", "das") if r.todas == "todas" else ("os", "dos")
    lede = (f"{r.no_lugar}, {_br(t26['pct'])}% dos eleitores aptos não votaram no 1º turno de 2026, "
            f"{'acima' if var > 0 else 'abaixo'} dos {_br(t22['pct'])}% de 2022. Entre {art} {len(us)} "
            f"{r.unidades}, a abstenção foi de {_br(menor['pct'], 1)}% {em(r, menor['ra'])} "
            f"a {_br(maior['pct'], 1)}% {em(r, maior['ra'])}.")
    if subiram == len(com_base):
        mudou = f"A abstenção subiu em relação a 2022 em {r.todas} {art} {len(com_base)}."
    elif subiram == 0:
        mudou = f"A abstenção caiu em relação a 2022 em {r.todas} {art} {len(com_base)}."
    else:
        mudou = f"A abstenção subiu em relação a 2022 em {subiram} {de} {len(com_base)}."
    textos = {
        "lede": lede,
        "titulo_ranking": f"{maior['ra']} teve a maior abstenção {r.do_lugar}; {menor['ra']}, a menor",
        "nota_ranking": (f"{acima} {de} {len(us)} {r.abrev} ficaram acima "
                         f"da média {r.do_lugar} ({_br(t26['pct'])}%). " + mudou),
        "titulo_grandes": "",
    }
    if len(grandes) >= 2:
        g_max = max(grandes, key=lambda u: u["pct"])
        g_min = min(grandes, key=lambda u: u["pct"])
        textos["titulo_grandes"] = (f"Entre {art} {r.abrev} com mais de 100 mil "
                                    f"eleitores, a maior abstenção foi {em(r, g_max['ra'])} ({_br(g_max['pct'], 1)}%) "
                                    f"e a menor {em(r, g_min['ra'])} ({_br(g_min['pct'], 1)}%).")
    if d["brasil_2026"] is not None:
        dif = abs(t26["pct"] - d["brasil_2026"])
        textos["contexto_brasil"] = (f"No Brasil, a abstenção foi de {_br(d['brasil_2026'])}%: {r.sujeito} "
                                     f"ficou {_br(dif)} {'ponto' if round(dif, 2) == 1 else 'pontos'} "
                                     f"{'abaixo' if t26['pct'] < d['brasil_2026'] else 'acima'}.")
    return textos


def malha_pagina(r: Recorte, malha_ibge: Path | None = None, casas: int = 3) -> dict:
    """GeoJSON leve para o mapa (3 casas, ~110 m), anéis no sentido do d3, com `chave` = unidade."""
    if r.agrupar == "coordenadas":
        fonte = [(f["properties"][r.campo], f["geometry"]) for f in json.loads(Path(r.malha).read_text())["features"]]
    else:
        prefixo = _codigo_uf_ibge(r.uf)
        fonte = [(str(f["properties"]["codarea"]), f["geometry"])
                 for f in json.loads(Path(malha_ibge).read_text())["features"]
                 if str(f["properties"]["codarea"]).startswith(prefixo)]
    feats = [{"type": "Feature", "properties": {"chave": k},
              "geometry": {"type": g["type"], "coordinates": _arredondar(g["coordinates"], casas)}}
             for k, g in fonte]
    return {"type": "FeatureCollection", "features": feats}


def _codigo_uf_ibge(uf: str) -> str:
    """Primeiros 2 dígitos do código IBGE do município (33 = RJ, 53 = DF...)."""
    return {"ro": "11", "ac": "12", "am": "13", "rr": "14", "pa": "15", "ap": "16", "to": "17", "ma": "21",
            "pi": "22", "ce": "23", "rn": "24", "pb": "25", "pe": "26", "al": "27", "se": "28", "ba": "29",
            "mg": "31", "es": "32", "rj": "33", "sp": "35", "pr": "41", "sc": "42", "rs": "43", "ms": "50",
            "mt": "51", "go": "52", "df": "53"}[uf.lower()]


def municipios(dir_parquet: Path, uf: str) -> pd.DataFrame:
    return duckdb.connect().execute(
        f"SELECT cod_tse, cod_ibge, nome FROM read_parquet('{Path(dir_parquet) / 'municipios.parquet'}') WHERE uf = ?",
        [uf.lower()]).df()


def abstencao_brasil_2026(dir_parquet: Path, cfg: Config) -> float | None:
    con = consultas.conectar(dir_parquet)
    if not consultas.tem(con, "snapshot_totais"):
        return None
    t1 = next((e.codigo for e in cfg.eleicao if e.turno == 1 and "presidente" in e.cargos
               and e.ciclo.endswith("2026")), None)
    linha = con.execute(
        "SELECT abstencao, eleitorado_instaladas FROM snapshot_totais WHERE eleicao = ? AND abrangencia = 'br' "
        "ORDER BY ts_tse DESC LIMIT 1", [t1]).fetchone()
    return None if not linha or not linha[1] else 100 * linha[0] / linha[1]


def gerar(r: Recorte, cfg: Config, dir_parquet: Path, saida: Path) -> Path:
    mun = None
    if r.agrupar == "municipio":
        mun = municipios(dir_parquet, r.uf)
        from apuracao.pagina.recortes import nome_proprio
        r.nomes = {**{i: nome_proprio(n) for i, n in zip(mun["cod_ibge"], mun["nome"])}, **r.nomes}
    por_ano = secoes_por_unidade(r, Path(cfg.historico.dir), mun)
    dados = montar_dados(r, por_ano, abstencao_brasil_2026(dir_parquet, cfg))
    malha = malha_pagina(r, cfg.ibge.arquivo if cfg.ibge else None)
    # unidades do mapa sem nenhuma seção (ex.: RA sem local de votação): ficam cinza e o método diz quais
    com_dados = {u["chave"] for u in dados["ras"]}
    dados["meta"]["sem_secoes"] = sorted(r.nomes.get(f["properties"]["chave"], f["properties"]["chave"])
                                         for f in malha["features"] if f["properties"]["chave"] not in com_dados)
    html = MODELO.read_text().replace("__TITULO__", r.titulo)
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(malha, separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
