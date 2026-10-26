"""Página "do 1º ao 2º turno" por região: votos de Lula (13) e do nº 22, brancos, nulos e abstenção em
cada unidade (RA do DF, RA da cidade do Rio...), no 1º e no 2º turno do mesmo ano.

uv run python -m apuracao.pagina --recorte brasilia --turnos --site site   (--ano 2022 para conferir com 2022)

Dados: detalhe por seção (aptos, comparecimento, abstenção) e votação por seção (votos de cada número),
os dois do Portal de Dados Abertos e com os dois turnos no mesmo arquivo depois que o TSE publica o 2º.
A unidade de cada seção vem do 1º turno (regioes.secoes_por_unidade: local de votação dentro da malha).
No 2º turno, a seção herda a unidade da mesma seção (município, zona, número) no 1º; se não existir, a do
mesmo local de votação. Votos válidos: 1º turno = candidatos válidos do resultado oficial
(votos.candidatos_validos); 2º turno = 13 e 22.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from apuracao.carga import secao
from apuracao.config import Config
from apuracao.pagina import regioes, votos
from apuracao.pagina.regioes import Recorte, _br, em

MODELO = Path(__file__).with_name("modelo_turnos.html")
A, B = 13, 22
BRANCO, NULO = 95, 96
DATAS = {2026: ("4 de outubro", "25 de outubro"), 2022: ("2 de outubro", "30 de outubro")}
CHAVE = ["cod_mun", "zona", "secao"]


def unidade_no_2o(s2: pd.DataFrame, s1: pd.DataFrame) -> pd.DataFrame:
    """Seções do 2º turno com a `unidade` da mesma seção no 1º; sem ela, a do mesmo local de votação."""
    por_secao = s1[CHAVE + ["unidade"]]
    por_local = s1.dropna(subset=["unidade"]).drop_duplicates(["cod_mun", "zona", "local"])[
        ["cod_mun", "zona", "local", "unidade"]]
    m = s2.merge(por_secao, on=CHAVE, how="left")
    falta = m["unidade"].isna()
    if falta.any():
        m2 = m.loc[falta, s2.columns].merge(por_local, on=["cod_mun", "zona", "local"], how="left")
        m.loc[falta, "unidade"] = m2["unidade"].to_numpy()
    return m


def agregar(secoes: pd.DataFrame, votos_secao: pd.DataFrame, validos: set[int]) -> pd.DataFrame:
    """Por unidade: aptos, comparecimento, abstencoes, validos, a (13), b (22), outros, brancos, nulos."""
    v = votos_secao.merge(secoes[CHAVE + ["unidade"]], on=CHAVE, how="left")
    if v["unidade"].isna().any() and not secoes["unidade"].isna().any():
        raise ValueError(f"{int(v['unidade'].isna().sum())} linhas de voto sem seção no detalhe")
    v = v.assign(unidade=v["unidade"].fillna("__sem__"))
    p = v.pivot_table(index="unidade", columns="numero", values="votos", aggfunc="sum", fill_value=0)
    col = lambda n: p[n] if n in p.columns else 0  # noqa: E731
    val = p[[c for c in p.columns if c in validos]].sum(axis=1)
    t = pd.DataFrame({"validos": val, "a": col(A), "b": col(B), "brancos": col(BRANCO), "nulos": col(NULO)})
    t["outros"] = t["validos"] - t["a"] - t["b"]
    s = secoes.assign(unidade=secoes["unidade"].fillna("__sem__")).groupby("unidade")[
        ["aptos", "comparecimento", "abstencoes"]].sum()
    return s.join(t, how="left").fillna(0).astype(int)


def _bloco(x: pd.Series) -> dict:
    d = {k: int(x[k]) for k in ("aptos", "comparecimento", "abstencoes", "validos", "a", "b", "outros", "brancos", "nulos")}
    d["abst_pct"] = round(100 * d["abstencoes"] / d["aptos"], 3) if d["aptos"] else None
    d["a_pct"] = round(100 * d["a"] / d["validos"], 3) if d["validos"] else None
    d["b_pct"] = round(100 * d["b"] / d["validos"], 3) if d["validos"] else None
    d["margem"] = None if d["a_pct"] is None else round(d["a_pct"] - d["b_pct"], 3)
    return d


def _cresc(antes: int, depois: int) -> float | None:
    return round(100 * (depois - antes) / antes, 3) if antes else None


def montar_dados(r: Recorte, ano: int, t1: pd.DataFrame, t2: pd.DataFrame, nomes: dict[int, str]) -> dict:
    linhas = []
    for k in (k for k in t1.index if k != "__sem__"):
        if k not in t2.index:
            continue
        nome = r.nomes.get(k, str(k))
        u1, u2 = _bloco(t1.loc[k]), _bloco(t2.loc[k])
        linhas.append({"ra": nome, "chave": k, "extra": r.extras.get(nome, ""), "t1": u1, "t2": u2,
                       "cresc_a": _cresc(u1["a"], u2["a"]), "cresc_b": _cresc(u1["b"], u2["b"]),
                       "dif_a": u2["a"] - u1["a"], "dif_b": u2["b"] - u1["b"],
                       "var_margem": round(u2["margem"] - u1["margem"], 3),
                       "var_abst": round(u2["abst_pct"] - u1["abst_pct"], 3)})
    linhas.sort(key=lambda u: u["t2"]["margem"])   # da maior vantagem do 22 à maior do 13
    tot1, tot2 = _bloco(t1.sum()), _bloco(t2.sum())
    # faixas do mapa de mudança da margem: ±c e ±2c, com c "redondo" perto de 1/4 da maior mudança
    maior = max((abs(u["var_margem"]) for u in linhas), default=1)
    c = min((x for x in (0.5, 1, 1.5, 2, 3, 5, 10) if x >= maior / 4), default=10)
    sem = {t: int(x.loc["__sem__", "aptos"]) if "__sem__" in x.index else 0 for t, x in (("t1", t1), ("t2", t2))}
    total = {"t1": tot1, "t2": tot2, "cresc_a": _cresc(tot1["a"], tot2["a"]), "cresc_b": _cresc(tot1["b"], tot2["b"]),
             "dif_a": tot2["a"] - tot1["a"], "dif_b": tot2["b"] - tot1["b"]}
    d = {"ano": ano, "ras": linhas, "total": total, "A": A, "B": B,
         "nomes": {str(A): nomes[A], str(B): nomes[B]},
         "meta": {"titulo": titulo(r), "rotulo": r.rotulo, "sigla": r.sigla, "unidade": r.unidade,
                  "unidades": r.unidades, "abrev": r.abrev, "nome_total": r.nome_total, "datas": DATAS[ano],
                  "cortes_var": [-2 * c, -c, c, 2 * c],
                  "metodo": metodo(r), "sem_unidade": sem, "sem_secoes": [],
                  "pequenas": [u["ra"] for u in sorted(linhas, key=lambda u: u["t2"]["aptos"]) if u["t2"]["aptos"] < 10_000]}}
    d["textos"] = _textos(r, d)
    return d


TITULOS = {"brasilia": "Do 1º ao 2º turno em Brasília", "rio": "Do 1º ao 2º turno no Rio",
           "estado-rj": "Do 1º ao 2º turno no Estado do Rio"}


def titulo(r: Recorte) -> str:
    return TITULOS.get(r.id, f"Do 1º ao 2º turno: {r.nome_total}")


def metodo(r: Recorte) -> list[str]:
    return [
        "Votos de cada candidato e abstenção seção a seção, no 1º e no 2º turno para presidente (Portal de Dados "
        "Abertos do TSE, \"votação por seção\" e \"detalhe da votação por seção\"), incluindo os eleitores em "
        "trânsito. Abstenção = eleitores que não votaram ÷ eleitores aptos, a mesma conta do TSE. Votos válidos "
        "excluem brancos e nulos; no 1º turno, \"outros\" são os demais candidatos válidos.",
        "Crescimento = votos no 2º turno ÷ votos no 1º turno − 1, na mesma região. Mudança da margem = (% de Lula "
        "− % do 22 nos válidos do 2º turno) − (a mesma conta no 1º turno): mostra quem ficou com mais da parte "
        "dos votos que mudou entre os turnos.",
        *r.metodo[1:],
        "A região de cada seção é a do 1º turno; no 2º turno, a seção herda a região da mesma seção no 1º "
        "(ou, se ela não existia, a do mesmo local de votação).",
    ]


def _mil(v: int) -> str:
    """'640 mil', '1,2 milhão', '850' (votos)."""
    v = abs(v)
    if v >= 1_000_000:
        x = _br(v / 1e6, 1).replace(",0", "")
        return f"{x} {'milhão' if v < 2_000_000 else 'milhões'}"
    if v >= 10_000:
        return f"{_br(v / 1e3, 0)} mil"
    return _br(v, 0)


def _pontos(v: float) -> str:
    return "ponto" if round(abs(v), 1) < 2 else "pontos"


def _textos(r: Recorte, d: dict) -> dict:
    us, t = d["ras"], d["total"]
    na, nb = d["nomes"][str(A)], d["nomes"][str(B)]
    t1, t2 = t["t1"], t["t2"]
    art, de = ("as", "das") if r.todas == "todas" else ("os", "dos")
    n = len(us)

    def ganho(dif, cresc):
        return f"{'ganhou' if dif >= 0 else 'perdeu'} {_mil(dif)} votos ({'+' if cresc > 0 else ''}{_br(cresc, 1)}%)"
    dif_abst = t2["abstencoes"] - t1["abstencoes"]
    lede = (f"{r.no_lugar}, {nb if t2['b'] > t2['a'] else na} venceu o 2º turno com "
            f"{_br(max(t2['a_pct'], t2['b_pct']))}% dos votos válidos. Do 1º para o 2º turno, {na} "
            f"{ganho(t['dif_a'], t['cresc_a'])}, e {nb} {ganho(t['dif_b'], t['cresc_b'])}. No 1º turno, os outros "
            f"candidatos tinham somado {_mil(t1['outros'])} votos. A abstenção foi de {_br(t2['abst_pct'])}%, contra "
            f"{_br(t1['abst_pct'])}% no 1º turno: {_mil(dif_abst)} eleitores {'a mais' if dif_abst >= 0 else 'a menos'} "
            f"deixaram de votar.")

    def vit(nome, lista):
        if not lista:
            return f"{nome} não venceu em nenhuma"
        if len(lista) == 1:
            return f"{nome} venceu só {em(r, lista[0])}"
        return f"{nome} venceu em {len(lista)} {de} {n} {r.abrev}"
    ganhou_a = [u["ra"] for u in us if u["t2"]["margem"] > 0]
    ganhou_b = [u["ra"] for u in us if u["t2"]["margem"] < 0]
    vitorias = f"{vit(nb, ganhou_b)}; {vit(na, ganhou_a)}"

    grandes = [u for u in us if u["t2"]["aptos"] >= 10_000] or us
    cresceu_a = sum(u["dif_a"] > 0 for u in us)
    cresceu_b = sum(u["dif_b"] > 0 for u in us)

    def contagem(nome, k):
        if k == n:
            return f"{nome} ganhou votos em {r.todas} {art} {n} {r.abrev}"
        if k == 0:
            return f"{nome} perdeu votos em {r.todas} {art} {n} {r.abrev}"
        return f"{nome} ganhou votos em {k} {de} {n} {r.abrev}"
    max_a = max(grandes, key=lambda u: u["cresc_a"] or -1e9)
    max_b = max(grandes, key=lambda u: u["cresc_b"] or -1e9)
    cresc = (f"{contagem(na, cresceu_a)}; {contagem(nb, cresceu_b)}. Em proporção, {na} cresceu mais "
             f"{em(r, max_a['ra'])} ({'+' if max_a['cresc_a'] > 0 else ''}{_br(max_a['cresc_a'], 1)}%) e {nb} "
             f"{em(r, max_b['ra'])} ({'+' if max_b['cresc_b'] > 0 else ''}{_br(max_b['cresc_b'], 1)}%).")
    ord_var = sorted(us, key=lambda u: u["var_margem"])
    mais_b, mais_a = ord_var[0], ord_var[-1]
    if mais_a["var_margem"] > 0 and mais_b["var_margem"] < 0:
        titulo_var = (f"{na} ganhou mais terreno {em(r, mais_a['ra'])}; {nb}, {em(r, mais_b['ra'])}")
    elif mais_a["var_margem"] <= 0:
        titulo_var = f"{nb} ganhou terreno em {r.todas} {art} {r.abrev}; mais {em(r, mais_b['ra'])}"
    else:
        titulo_var = f"{na} ganhou terreno em {r.todas} {art} {r.abrev}; mais {em(r, mais_a['ra'])}"
    subiu = sum(u["var_abst"] > 0 for u in us)
    if subiu == n:
        abst = f"A abstenção subiu do 1º para o 2º turno em {r.todas} {art} {n} {r.abrev}."
    elif subiu == 0:
        abst = f"A abstenção caiu do 1º para o 2º turno em {r.todas} {art} {n} {r.abrev}."
    else:
        abst = f"A abstenção subiu do 1º para o 2º turno em {subiu} {de} {n} {r.abrev} e caiu nas outras {n - subiu}." \
            if r.todas == "todas" else \
            f"A abstenção subiu do 1º para o 2º turno em {subiu} {de} {n} {r.abrev} e caiu nos outros {n - subiu}."
    a_max = max(us, key=lambda u: u["var_abst"])
    abst += (f" A maior alta foi {em(r, a_max['ra'])} ({'+' if a_max['var_abst'] > 0 else ''}"
             f"{_br(a_max['var_abst'], 1)} {_pontos(a_max['var_abst'])}).")
    return {"lede": lede, "vitorias": vitorias[0].upper() + vitorias[1:] + ".", "crescimento": cresc,
            "titulo_var": titulo_var, "abstencao": abst}


def gerar(r: Recorte, cfg: Config, dir_parquet: Path, saida: Path, ano: int = 2026) -> Path:
    dir_h = Path(cfg.historico.dir)
    mun = None
    if r.agrupar == "municipio":
        from apuracao.pagina.recortes import nome_proprio
        mun = regioes.municipios(dir_parquet, r.uf)
        r.nomes = {**{i: nome_proprio(nm) for i, nm in zip(mun["cod_ibge"], mun["nome"])}, **r.nomes}
    outro = 2022 if ano != 2022 else 2026   # só para completar coordenadas de locais (ver regioes)
    s1 = regioes.secoes_por_unidade(r, dir_h, mun, anos=(ano, outro))[ano]
    s2 = secao.secoes(dir_h / str(ano) / f"detalhe_votacao_secao_{ano}.zip", r.uf, turno=2)
    v1 = secao.votos(dir_h / str(ano) / f"votacao_secao_{ano}_BR.zip", r.uf, turno=1)
    v2 = secao.votos(dir_h / str(ano) / f"votacao_secao_{ano}_BR.zip", r.uf, turno=2)
    if r.cod_mun:
        s2, v1, v2 = (x[x["cod_mun"] == r.cod_mun] for x in (s2, v1, v2))
    if s2.empty or v2.empty:
        raise SystemExit(f"sem o 2º turno de {ano} nos arquivos por seção em {dir_h / str(ano)}: "
                         f"baixe de novo com `python -m apuracao.carga --secao {ano}` depois que o TSE publicar")
    s2 = unidade_no_2o(s2, s1)
    validos1 = set(votos.candidatos_validos(dir_parquet, cfg)[ano])
    nomes = votos.candidatos_validos(dir_parquet, cfg)[ano]
    t1, t2 = agregar(s1, v1, validos1), agregar(s2, v2, {A, B})
    dados = montar_dados(r, ano, t1, t2, nomes)
    malha = regioes.malha_pagina(r, cfg.ibge.arquivo if cfg.ibge else None)
    com = {u["chave"] for u in dados["ras"]}
    dados["meta"]["sem_secoes"] = sorted(r.nomes.get(f["properties"]["chave"], f["properties"]["chave"])
                                         for f in malha["features"] if f["properties"]["chave"] not in com)
    html = MODELO.read_text().replace("__TITULO__", dados["meta"]["titulo"])
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(malha, separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
