"""Página "Onde Lula perdeu votos": o número 13 (PT) no 1º turno de presidente, por município,
2026 comparado a 2022 (Lula) e a 2014 (Dilma).

uv run python -m apuracao.pagina --perdas

Dados: analise.municipios (2026: arquivos de resultado do TSE por município; 2022 e 2014: Portal de
Dados Abertos), já conferidos com os totais oficiais na página nacional. Só municípios do Brasil (o
exterior fica fora). Duas medidas, porque contam histórias diferentes:
- pontos percentuais dos votos válidos: onde o eleitorado mudou, independente do tamanho da cidade;
- votos absolutos: de onde saíram os votos; dominada pelas cidades grandes e afetada pela mudança do
  eleitorado entre as eleições.
As frases com afirmações sobre os dados são calculadas aqui.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from apuracao.app import analise
from apuracao.carga.ibge import ler_malha
from apuracao.modelo import consultas
from apuracao.pagina.recortes import nome_proprio
from apuracao.pagina.regioes import _br

MODELO = Path(__file__).with_name("modelo_perdas.html")
BASES = (2022, 2014)


def _r(x, n=3):
    return None if pd.isna(x) else round(float(x), n)


def _int(v) -> int:
    return 0 if pd.isna(v) else int(v)


def _milhoes(v: float) -> str:
    return _br(v / 1e6, 1) + " milhões"


def montar_dados(con) -> dict:
    disponiveis = set(analise.anos_base(con))
    bases_ok = [b for b in BASES if b in disponiveis]
    if not bases_ok:
        raise SystemExit("sem histórico de 2022 nem de 2014 carregado (python -m apuracao.carga --ano 2022 --ano 2014)")
    nomes = {b: analise.nomes(con, b) for b in bases_ok}
    mun = {b: analise.municipios(con, b) for b in bases_ok}
    mun = {b: m[m["uf"] != "zz"] for b, m in mun.items()}
    ref = mun[bases_ok[0]]
    linhas = []
    outros = {b: m.set_index(["uf", "cod_mun"]) for b, m in mun.items()}
    for _, x in ref[ref["vv_atual"].notna()].iterrows():  # só municípios com resultado em 2026
        k = (x["uf"], x["cod_mun"])
        linha = [nome_proprio(x["nome"]), x["uf"].upper(), x["cod_ibge"], x["regiao"],
                 _int(x["eleitores_atual"]), _r(x["pct_pt_atual"]), _int(x["vpt_atual"])]
        for b in BASES:
            o = outros[b].loc[k] if b in outros and k in outros[b].index else None
            linha += [None, None] if o is None or pd.isna(o["vv_base"]) else [_r(o["pct_pt_base"]), _int(o["vpt_base"])]
        linhas.append(linha)
    colunas = ["nome", "uf", "ibge", "regiao", "eleitores", "pct2026", "votos2026"] + \
              [c for b in BASES for c in (f"pct{b}", f"votos{b}")]

    bases, ufs = {}, {}
    for b in bases_ok:
        br = analise.agregar(mun[b]).iloc[0]
        bases[str(b)] = {"pct_base": _r(br["pct_pt_base"]), "pct_atual": _r(br["pct_pt_atual"]),
                         "votos_base": int(br["vpt_base"]), "votos_atual": int(br["vpt_atual"]),
                         "nome_base": nomes[b]["pt_base"].rsplit(" (", 1)[0].title(),
                         "nome_atual": nomes[b]["pt_atual"].rsplit(" (", 1)[0].title()}
        for _, u in analise.agregar(mun[b], ["uf"]).iterrows():
            d = ufs.setdefault(u["uf"].upper(), {"pct2026": _r(u["pct_pt_atual"]), "votos2026": int(u["vpt_atual"])})
            d[f"pct{b}"], d[f"votos{b}"] = _r(u["pct_pt_base"]), int(u["vpt_base"])
    d = {"colunas": colunas, "municipios": linhas, "bases": bases, "ufs": ufs}
    d["textos"] = {str(b): _textos(d, b) for b in bases_ok}
    return d


def _textos(d: dict, b: int) -> dict:
    col = {c: i for i, c in enumerate(d["colunas"])}
    ms = [m for m in d["municipios"] if m[col[f"pct{b}"]] is not None]
    var = [(m, m[col["pct2026"]] - m[col[f"pct{b}"]], m[col["votos2026"]] - m[col[f"votos{b}"]]) for m in ms]
    caiu = sum(1 for _, v, _ in var if v < 0)
    perdeu_votos = sum(1 for _, _, dv in var if dv < 0)
    x = d["bases"][str(b)]
    dif = x["pct_atual"] - x["pct_base"]
    dv = x["votos_atual"] - x["votos_base"]
    grandes = [t for t in var if t[0][col["eleitores"]] >= 100_000]
    mais_votos = min(var, key=lambda t: t[2])
    ufs = d["ufs"]
    uf_var = sorted(((u, v["pct2026"] - v[f"pct{b}"]) for u, v in ufs.items()), key=lambda t: t[1])
    caiu_uf = sum(1 for _, v in uf_var if v < 0)
    em_b = f"{x['nome_base']} em {b}" if x["nome_base"] != x["nome_atual"] else f"em {b}"
    n = lambda v: f"{v:,}".replace(",", ".")  # noqa: E731 (inteiros com ponto de milhar)
    return {
        "lede": (f"{x['nome_atual']} teve {_br(x['pct_atual'])}% dos votos válidos no 1º turno de 2026, "
                 f"{_br(abs(dif))} pontos {'a menos' if dif < 0 else 'a mais'} que {em_b} ({_br(x['pct_base'])}%), "
                 f"e {_milhoes(abs(dv))} de votos {'a menos' if dv < 0 else 'a mais'} "
                 f"({_milhoes(x['votos_atual'])} contra {_milhoes(x['votos_base'])}). O percentual caiu em "
                 f"{n(caiu)} dos {n(len(var))} municípios."),
        "votos": (f"Em número de votos, {x['nome_atual']} teve menos votos que {em_b} em {n(perdeu_votos)} "
                  f"municípios; a maior perda foi em {mais_votos[0][col['nome']]} ({mais_votos[0][col['uf']]}), "
                  f"com {n(abs(mais_votos[2]))} votos a menos."),
        "grandes": _grandes(grandes, col),
        "ufs": (f"O percentual caiu em {caiu_uf} das 27 UFs; maior queda em {uf_var[0][0]} "
                f"({_br(uf_var[0][1], 1)} pontos)"
                + (f", maior alta em {uf_var[-1][0]} (+{_br(uf_var[-1][1], 1)})." if uf_var[-1][1] > 0 else
                   f", menor queda em {uf_var[-1][0]} ({_br(uf_var[-1][1], 1)}).")),
    }


def _grandes(grandes: list, col: dict) -> str:
    if len(grandes) < 2:
        return ""
    pior = min(grandes, key=lambda t: t[1])
    melhor = max(grandes, key=lambda t: t[1])
    return (f"Entre as {len(grandes)} cidades com 100 mil eleitores ou mais, a maior queda foi em "
            f"{pior[0][col['nome']]} ({pior[0][col['uf']]}, {_br(pior[1], 1)} pontos) e o melhor resultado em "
            f"{melhor[0][col['nome']]} ({melhor[0][col['uf']]}, {'+' if melhor[1] > 0 else ''}{_br(melhor[1], 1)}).")


def gerar(dir_parquet: Path, malha: Path, saida: Path) -> Path:
    con = consultas.conectar(dir_parquet)
    dados = montar_dados(con)
    html = MODELO.read_text()
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(ler_malha(malha), separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
