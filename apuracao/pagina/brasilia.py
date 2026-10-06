"""Gera a página compartilhável "Abstenção em Brasília": abstenção no 1º turno de 2026 por
região administrativa do DF, comparada a 2022.

uv run python -m apuracao.pagina --brasilia

Fontes (ver apuracao/carga/secao.py e regioes_df.py): detalhe por seção (presidente, com os
eleitores em trânsito; a soma confere com o total oficial do DF nos dois anos), locais de
votação (coordenadas) e a malha oficial das 35 RAs. A RA de cada seção é a do seu local de
votação. Um local sem coordenadas num ano herda a posição do mesmo local (zona, número e
nome iguais) no outro ano; em 2022 isso cobre 6 locais (presídios, unidades de internação e
2 escolas) e nenhuma seção fica sem RA.

As frases com afirmações sobre os dados são calculadas aqui, como na página nacional.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from apuracao.carga import regioes_df, secao
from apuracao.carga.ibge import _arredondar
from apuracao.config import Config
from apuracao.modelo import consultas

MODELO = Path(__file__).with_name("modelo_brasilia.html")
ANOS = (2026, 2022)

# Nomes como publicados na malha (caixa alta, "AGUA QUENTE" sem acento) -> forma de leitura.
NOMES = {
    "PLANO PILOTO": "Plano Piloto", "GAMA": "Gama", "TAGUATINGA": "Taguatinga", "BRAZLÂNDIA": "Brazlândia",
    "SOBRADINHO": "Sobradinho", "PLANALTINA": "Planaltina", "PARANOÁ": "Paranoá",
    "NÚCLEO BANDEIRANTE": "Núcleo Bandeirante", "CEILÂNDIA": "Ceilândia", "GUARÁ": "Guará",
    "CRUZEIRO": "Cruzeiro", "SAMAMBAIA": "Samambaia", "SANTA MARIA": "Santa Maria",
    "SÃO SEBASTIÃO": "São Sebastião", "RECANTO DAS EMAS": "Recanto das Emas", "LAGO SUL": "Lago Sul",
    "RIACHO FUNDO": "Riacho Fundo", "LAGO NORTE": "Lago Norte", "CANDANGOLÂNDIA": "Candangolândia",
    "ÁGUAS CLARAS": "Águas Claras", "RIACHO FUNDO II": "Riacho Fundo II",
    "SUDOESTE/OCTOGONAL": "Sudoeste/Octogonal", "VARJÃO": "Varjão", "PARK WAY": "Park Way",
    "SCIA": "SCIA (Estrutural)", "SOBRADINHO II": "Sobradinho II", "JARDIM BOTÂNICO": "Jardim Botânico",
    "ITAPOÃ": "Itapoã", "SIA": "SIA", "VICENTE PIRES": "Vicente Pires", "FERCAL": "Fercal",
    "SOL NASCENTE E POR DO SOL": "Sol Nascente/Pôr do Sol", "ARNIQUEIRA": "Arniqueira",
    "ARAPOANGA": "Arapoanga", "AGUA QUENTE": "Água Quente",
}


# RAs que pedem artigo ("no Itapoã", "no Gama"); as demais levam "em" ("em Ceilândia").
COM_ARTIGO = {"Plano Piloto", "Gama", "Paranoá", "Núcleo Bandeirante", "Guará", "Cruzeiro", "Recanto das Emas",
              "Lago Sul", "Riacho Fundo", "Lago Norte", "Riacho Fundo II", "Sudoeste/Octogonal", "Varjão",
              "Park Way", "SCIA (Estrutural)", "Jardim Botânico", "Itapoã", "SIA", "Sol Nascente/Pôr do Sol",
              "Arapoanga"}


def em(ra: str) -> str:
    return f"no {ra}" if ra in COM_ARTIGO else f"em {ra}"


def _br(v: float, casas: int = 2) -> str:
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def nome(ra: str) -> str:
    return NOMES.get(ra, ra.title())


def _completar_coordenadas(loc: pd.DataFrame, outro: pd.DataFrame) -> pd.DataFrame:
    """Locais sem coordenada herdam as do mesmo local (zona, número e nome) no outro ano."""
    ref = outro.dropna(subset=["lat", "lon"])[["zona", "local", "nome", "lat", "lon"]]
    m = loc.merge(ref, on=["zona", "local", "nome"], how="left", suffixes=("", "_ref"))
    falta = m["lat"].isna()
    m.loc[falta, "lat"] = m.loc[falta, "lat_ref"]
    m.loc[falta, "lon"] = m.loc[falta, "lon_ref"]
    m["herdou"] = falta & m["lat_ref"].notna()
    return m.drop(columns=["lat_ref", "lon_ref"])


def secoes_com_ra(dir_historico: Path, arquivo_regioes: Path) -> dict[int, pd.DataFrame]:
    """Por ano: uma linha por seção, com aptos, abstenções e a RA do local de votação."""
    regs = regioes_df.ler(arquivo_regioes)
    locs = {a: secao.locais(dir_historico / str(a) / f"eleitorado_local_votacao_{a}.zip", "df") for a in ANOS}
    saida = {}
    for a in ANOS:
        outro = locs[ANOS[1] if a == ANOS[0] else ANOS[0]]
        loc = _completar_coordenadas(locs[a], outro)
        loc["ra"] = regioes_df.regiao_de(loc["lon"], loc["lat"], regs)
        s = secao.secoes(dir_historico / str(a) / f"detalhe_votacao_secao_{a}.zip", "df")
        m = s.merge(loc[["zona", "local", "ra", "herdou"]], on=["zona", "local"], how="left")
        m.attrs["locais_herdados"] = int(loc["herdou"].sum())
        saida[a] = m
    return saida


def montar_dados(por_ano: dict[int, pd.DataFrame], abst_brasil_2026: float | None) -> dict:
    tabelas = {}
    for a, m in por_ano.items():
        g = m.groupby("ra", dropna=False).agg(aptos=("aptos", "sum"), abst=("abstencoes", "sum"),
                                              secoes=("secao", "size"), locais=("local", "nunique"))
        g["pct"] = 100 * g["abst"] / g["aptos"]
        tabelas[a] = g
    t26, t22 = tabelas[2026], tabelas[2022]
    sem_ra = {a: int(t.loc[[i for i in t.index if pd.isna(i)], "aptos"].sum()) for a, t in tabelas.items()}
    ras = [r for r in t26.index if not pd.isna(r)]
    linhas = []
    for r in ras:
        a26, a22 = t26.loc[r], (t22.loc[r] if r in t22.index else None)
        linhas.append({
            "ra": nome(r), "chave": r,
            "aptos": int(a26["aptos"]), "abst": int(a26["abst"]), "pct": round(float(a26["pct"]), 3),
            "secoes": int(a26["secoes"]), "locais": int(a26["locais"]),
            "pct_2022": None if a22 is None else round(float(a22["pct"]), 3),
            "aptos_2022": None if a22 is None else int(a22["aptos"]),
        })
    linhas.sort(key=lambda x: -x["pct"])
    df = {a: dict(aptos=int(m["aptos"].sum()), abst=int(m["abstencoes"].sum()),
                  pct=round(100 * m["abstencoes"].sum() / m["aptos"].sum(), 3),
                  secoes=len(m), locais_herdados=m.attrs.get("locais_herdados", 0), sem_ra=sem_ra[a])
          for a, m in por_ano.items()}
    d = {"ras": linhas, "df": {str(a): v for a, v in df.items()},
         "brasil_2026": None if abst_brasil_2026 is None else round(abst_brasil_2026, 3)}
    d["textos"] = _textos(d)
    return d


def _textos(d: dict) -> dict:
    ras = d["ras"]
    df26, df22 = d["df"]["2026"], d["df"]["2022"]
    maior, menor = ras[0], ras[-1]
    var_df = df26["pct"] - df22["pct"]
    com_base = [r for r in ras if r["pct_2022"] is not None]
    subiram = sum(1 for r in com_base if r["pct"] > r["pct_2022"])
    acima = sum(1 for r in ras if r["pct"] > df26["pct"])
    grandes = [r for r in ras if r["aptos"] >= 100_000]
    lede = (f"No Distrito Federal, {_br(df26['pct'])}% dos eleitores aptos não votaram no 1º turno de 2026, "
            f"{'acima' if var_df > 0 else 'abaixo'} dos {_br(df22['pct'])}% de 2022. Entre as "
            f"{len(ras)} regiões administrativas, a abstenção foi de {_br(menor['pct'], 1)}% "
            f"{em(menor['ra'])} a {_br(maior['pct'], 1)}% {em(maior['ra'])}.")
    textos = {
        "lede": lede,
        "titulo_ranking": f"{maior['ra']} teve a maior abstenção do DF; {menor['ra']}, a menor",
        "nota_ranking": (f"{acima} das {len(ras)} RAs ficaram acima da média do DF ({_br(df26['pct'])}%). "
                         + (f"A abstenção subiu em relação a 2022 em todas as {len(com_base)}."
                            if subiram == len(com_base) else
                            f"A abstenção subiu em relação a 2022 em {subiram} das {len(com_base)}.")),
        "titulo_grandes": "",
    }
    if grandes:
        g_max = max(grandes, key=lambda r: r["pct"])
        g_min = min(grandes, key=lambda r: r["pct"])
        textos["titulo_grandes"] = (f"Entre as RAs com mais de 100 mil eleitores, a maior abstenção foi "
                                    f"{em(g_max['ra'])} ({_br(g_max['pct'], 1)}%) e a menor {em(g_min['ra'])} "
                                    f"({_br(g_min['pct'], 1)}%).")
    if d["brasil_2026"] is not None:
        dif = abs(df26["pct"] - d["brasil_2026"])
        textos["contexto_brasil"] = (f"No Brasil, a abstenção foi de {_br(d['brasil_2026'])}%: o DF ficou "
                                     f"{_br(dif)} {'ponto' if round(dif, 2) == 1 else 'pontos'} "
                                     f"{'abaixo' if df26['pct'] < d['brasil_2026'] else 'acima'}.")
    return textos


def malha_pagina(arquivo_regioes: Path, casas: int = 3) -> dict:
    """GeoJSON leve para o mapa: 3 casas (~110 m; o DF inteiro tem ~700 px de largura), anéis no
    sentido do d3."""
    feats = []
    for f in json.loads(Path(arquivo_regioes).read_text())["features"]:
        g = f["geometry"]
        feats.append({"type": "Feature", "properties": {"chave": f["properties"]["ra_nome"]},
                      "geometry": {"type": g["type"], "coordinates": _arredondar(g["coordinates"], casas)}})
    return {"type": "FeatureCollection", "features": feats}


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


def gerar(cfg: Config, dir_parquet: Path, saida: Path) -> Path:
    por_ano = secoes_com_ra(Path(cfg.historico.dir), Path(cfg.df.arquivo))
    dados = montar_dados(por_ano, abstencao_brasil_2026(dir_parquet, cfg))
    html = MODELO.read_text()
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(malha_pagina(Path(cfg.df.arquivo)), separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
