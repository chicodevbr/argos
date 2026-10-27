"""Página "quem venceu em cada urna": o 2º turno seção a seção (cada seção é uma urna), agrupado por local
de votação e desenhado no mapa pelas coordenadas do local. Brasília e cidade do Rio.

uv run python -m apuracao.pagina --recorte brasilia|rio --urnas --site site

Dados: votação por seção (votos de cada número), detalhe por seção (aptos, comparecimento, local) e locais de
votação com coordenadas, todos do 2º turno (Portal de Dados Abertos do TSE; ver carga/secao.py). A região de
cada local vem da malha do recorte (carga/regioes.py). Um mesmo prédio pode ter seções de mais de uma zona,
com números de local diferentes: os locais com o mesmo nome e as mesmas coordenadas viram um ponto só.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from apuracao.config import Config
from apuracao.pagina import regioes, votos
from apuracao.pagina.recortes import nome_proprio
from apuracao.pagina.regioes import Recorte, _br, em

MODELO = Path(__file__).with_name("modelo_urnas.html")
A, B = 13, 22
BRANCO, NULO = 95, 96
CHAVE = ["cod_mun", "zona", "secao"]
TITULOS = {"brasilia": "Quem venceu em cada urna de Brasília", "rio": "Quem venceu em cada urna do Rio"}
LIMITES = {"brasilia": "das 35 RAs (SISDIA/GDF, 2022)", "rio": "das 33 RAs da prefeitura (Instituto Pereira Passos)"}
GRANDE = 1_000   # locais com pelo menos 1.000 votos válidos entram nos destaques das frases


def por_secao(secoes: pd.DataFrame, votos_secao: pd.DataFrame) -> pd.DataFrame:
    """Uma linha por seção: aptos, comparecimento, a (13), b (22), brancos, nulos, local."""
    p = votos_secao.pivot_table(index=CHAVE, columns="numero", values="votos", aggfunc="sum", fill_value=0)
    col = lambda n: p[n] if n in p.columns else 0  # noqa: E731
    v = pd.DataFrame({"a": col(A), "b": col(B), "brancos": col(BRANCO), "nulos": col(NULO)}).reset_index()
    m = secoes.merge(v, on=CHAVE, how="left")
    if m["a"].isna().any():
        raise ValueError(f"{int(m['a'].isna().sum())} seções sem votos na votação por seção")
    m[["a", "b", "brancos", "nulos"]] = m[["a", "b", "brancos", "nulos"]].astype(int)
    return m


def por_local(s: pd.DataFrame, locais: pd.DataFrame) -> pd.DataFrame:
    """Soma as seções por local de votação (mesmo nome e coordenadas = um ponto) e conta quem venceu cada seção."""
    s = s.assign(sa=(s["a"] > s["b"]).astype(int), sb=(s["b"] > s["a"]).astype(int), se=1)
    m = s.merge(locais, on=["cod_mun", "zona", "local"], how="left")
    if m["lat"].isna().any():
        raise ValueError(f"{int(m['lat'].isna().sum())} seções sem coordenada do local de votação")
    g = m.groupby(["nome", "lat", "lon"], as_index=False).agg(
        bairro=("bairro", "first"), unidade=("unidade", "first"), zonas=("zona", lambda z: sorted(set(z))),
        aptos=("aptos", "sum"), comparecimento=("comparecimento", "sum"), a=("a", "sum"), b=("b", "sum"),
        brancos=("brancos", "sum"), nulos=("nulos", "sum"), sa=("sa", "sum"), sb=("sb", "sum"), se=("se", "sum"))
    g["validos"] = g["a"] + g["b"]
    g["margem"] = 100 * (g["a"] - g["b"]) / g["validos"].where(g["validos"] > 0)
    return g


# siglas que ficam em maiúsculas (nomes de escolas e setores do DF e do Rio); "UNB" vira "UnB"
SIGLAS = {"CEF", "EC", "E.C.", "CAIC", "CEM", "CED", "CIL", "CILB", "CEMI", "SQS", "SQN", "SGAS", "SEPS", "IFB", "CE",
          "SMU", "CCM", "CCI", "CESAS", "CEJA", "CEPAG", "COC", "CEUB", "CEMAB", "CEAN", "SHI", "RCG", "UDF", "GG",
          "JK", "IESB", "DJ", "UPIS", "SESI", "UNIP", "INCRA", "CEI", "CEPI", "SESC", "SENAI", "QNM", "QNN", "QNL",
          "II", "III", "IV", "VI", "VII", "VIII", "IX", "XI", "XII",
          "CIEP", "EDI", "GET", "E.M.", "FAETEC", "INSS", "CRAS", "UERJ", "UFRJ", "PUC", "CEFET", "IFRJ", "SENAC", "RJ", "ACM", "G.R.E.S."}


def _nome_local(n: str) -> str:
    def sigla(m):
        w = m.group(0)
        return "UnB" if w.upper() == "UNB" else w.upper() if w.upper() in SIGLAS else w
    n = re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", n.strip()))   # "( ANTIGA X )" -> "(ANTIGA X)"
    return re.sub(r"[\wÀ-ÿ.]+", sigla, nome_proprio(n))


def montar_dados(r: Recorte, s: pd.DataFrame, loc: pd.DataFrame, nomes: dict[int, str]) -> dict:
    na, nb = nomes[A], nomes[B]
    pontos = []
    for x in loc.sort_values("aptos", ascending=False).itertuples():
        pontos.append({"n": _nome_local(x.nome), "br": _nome_local(x.bairro or ""),
                       "ra": r.nomes.get(x.unidade, x.unidade) if isinstance(x.unidade, str) else None,
                       "lat": round(x.lat, 5), "lon": round(x.lon, 5), "z": [int(z) for z in x.zonas],
                       "se": int(x.se), "ap": int(x.aptos), "c": int(x.comparecimento), "a": int(x.a), "b": int(x.b),
                       "bn": int(x.brancos + x.nulos), "sa": int(x.sa), "sb": int(x.sb),
                       "m": None if pd.isna(x.margem) else round(float(x.margem), 2)})
    tot = {"secoes": len(s), "sa": int((s["a"] > s["b"]).sum()), "sb": int((s["b"] > s["a"]).sum()),
           "empates": int((s["a"] == s["b"]).sum()), "locais": len(pontos),
           "la": sum(p["m"] is not None and p["m"] > 0 for p in pontos),
           "lb": sum(p["m"] is not None and p["m"] < 0 for p in pontos),
           "aptos": int(s["aptos"].sum()), "a": int(s["a"].sum()), "b": int(s["b"].sum()),
           "bn": int((s["brancos"] + s["nulos"]).sum())}
    tot["lempates"] = tot["locais"] - tot["la"] - tot["lb"]
    tot["a_pct"] = round(100 * tot["a"] / (tot["a"] + tot["b"]), 3)
    d = {"pontos": pontos, "total": tot, "nomes": {"a": na, "b": nb},
         "meta": {"titulo": TITULOS.get(r.id, f"Quem venceu em cada urna: {r.nome_total}"), "rotulo": r.rotulo,
                  "nome_total": r.nome_total, "unidade": r.unidade, "metodo": metodo(r)}}
    d["textos"] = _textos(r, d)
    return d


def metodo(r: Recorte) -> list[str]:
    return [
        "Votos de cada seção eleitoral no 2º turno para presidente (Portal de Dados Abertos do TSE, \"votação por "
        "seção\"); cada seção corresponde a uma urna. Eleitores e comparecimento vêm do \"detalhe da votação por "
        "seção\", incluindo os eleitores em trânsito. A soma das seções confere com o resultado oficial.",
        "Cada ponto é um local de votação, na posição do cadastro de locais do TSE; o tamanho é proporcional aos "
        "eleitores aptos. Locais com o mesmo nome e as mesmas coordenadas (um prédio com seções de mais de uma "
        "zona) viram um ponto só. Margem = % de Lula − % do 22 nos votos válidos do local.",
        "A região administrativa de cada local é a que contém as suas coordenadas, nos limites oficiais "
        f"{LIMITES.get(r.id, 'da malha do recorte')}; local sem coordenada no 2º turno herda a do 1º turno, de 2022 ou do "
        "local onde as mesmas seções votaram no 1º turno. O eleitor vota onde está inscrito, que em geral é perto de "
        "onde mora, mas não sempre.",
    ]


def _textos(r: Recorte, d: dict) -> dict:
    t, na, nb = d["total"], d["nomes"]["a"], d["nomes"]["b"]
    venc, perd = (nb, na) if t["b"] > t["a"] else (na, nb)
    sv, sp = (t["sb"], t["sa"]) if t["b"] > t["a"] else (t["sa"], t["sb"])
    lv, lp = (t["lb"], t["la"]) if t["b"] > t["a"] else (t["la"], t["lb"])
    lede = (f"{r.no_lugar}, {venc} venceu o 2º turno com {_br(max(t['a_pct'], 100 - t['a_pct']))}% dos votos "
            f"válidos e ganhou em {_br(sv, 0)} das {_br(t['secoes'], 0)} urnas; {perd} ganhou em {_br(sp, 0)}"
            + (f", e {t['empates']} {'terminaram empatadas' if t['empates'] > 1 else 'terminou empatada'}" if t["empates"] else "")
            + f". Nos {_br(t['locais'], 0)} locais de votação, {venc} venceu em {_br(lv, 0)} e {perd} em {_br(lp, 0)}"
            + (f" ({t['lempates']} empatados)" if t["lempates"] else "") + ".")
    grandes = [p for p in d["pontos"] if p["a"] + p["b"] >= GRANDE and p["m"] is not None]
    ma, mb = max(grandes, key=lambda p: p["m"]), min(grandes, key=lambda p: p["m"])
    destaque = (f"Entre os locais com mais de {_br(GRANDE, 0)} votos válidos, a maior vantagem de {na} foi em "
                f"{ma['n']}{', ' + em(r, ma['ra']) if ma['ra'] else ''} ({_br(ma['m'], 1)} pontos), e a de {nb} em "
                f"{mb['n']}{', ' + em(r, mb['ra']) if mb['ra'] else ''} ({_br(-mb['m'], 1)} pontos).")
    return {"lede": lede, "destaque": destaque}


def gerar(r: Recorte, cfg: Config, dir_parquet: Path, saida: Path, ano: int = 2026) -> Path:
    from apuracao.pagina import zonas   # zonas importa este módulo; a carga (com as coordenadas completadas) é dela
    sec, loc = zonas.carregar(r, cfg, ano)
    sec = sec.drop(columns=["unidade"])
    dados = montar_dados(r, sec, por_local(sec, loc), votos.candidatos_validos(dir_parquet, cfg)[ano])
    malha = regioes.malha_pagina(r)
    for f in malha["features"]:
        f["properties"]["nome"] = r.nomes.get(f["properties"]["chave"], f["properties"]["chave"])
    html = MODELO.read_text().replace("__TITULO__", dados["meta"]["titulo"])
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(malha, ensure_ascii=False, separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
