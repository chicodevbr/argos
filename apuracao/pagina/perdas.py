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


# Como cada página fala do lugar e das unidades. `lugar`: frase curta para uma linha da tabela
# (nacional: "em São Paulo (SP)"; DF: "no Plano Piloto"); os textos ficam iguais aos da versão nacional.
META_BRASIL = {
    "titulo": "Onde Lula perdeu votos", "rotulo": "Brasil, por município", "no_lugar": "",
    "unidades": "municípios", "unidade": "Município", "de": "dos", "grandes": "cidades",
    "titulo_tabela": "Todos os municípios", "titulo_mapa": "Variação por município, em pontos dos votos válidos",
    "titulo_grupos": "Por UF", "grupos": "UFs", "de_grupos": "das", "mostrar_uf": True,
    "projecao": "albers", "chave_malha": "codarea", "busca": "Buscar município (ex.: Campinas)",
    "metodo": [
        "Percentual do número 13 (PT) sobre os votos válidos no 1º turno para presidente: Lula em 2026 e 2022, Dilma "
        "em 2014. 2026: arquivos de resultado por município publicados pelo TSE; 2022 e 2014: Portal de Dados Abertos do "
        "TSE. Os totais nacionais conferem com os resultados oficiais. Só municípios do Brasil; os votos do exterior ficam "
        "fora, por isso os percentuais nacionais podem diferir dos oficiais na segunda casa.",
        "Pontos percentuais medem a mudança no eleitorado de cada cidade, independente do tamanho; votos absolutos "
        "mostram de onde saíram os votos, mas também mudam com o crescimento do eleitorado entre as eleições.",
        "Mapa: malha municipal do IBGE (qualidade mínima), projeção cônica equivalente de Albers. Faixas fixas para cada "
        "ano de comparação. Municípios que não existiam no ano de comparação aparecem em cinza."],
}


# Faixas do mapa da página nacional (aprovadas em 07/10/2026): cortes e cor de cada faixa, da maior queda à maior alta.
FAIXAS_BRASIL = {
    "2022": {"cortes": [-10, -6, -3, 0, 3], "cores": ["q4", "q3", "q2", "q1", "a1", "a4"],
             "rotulos": ["caiu 10 pontos ou mais", "caiu de 6 a 10", "caiu de 3 a 6", "caiu até 3", "subiu até 3", "subiu 3 ou mais"]},
    "2014": {"cortes": [-15, -8, -3, 0, 8], "cores": ["q4", "q3", "q2", "q1", "a1", "a4"],
             "rotulos": ["caiu 15 pontos ou mais", "caiu de 8 a 15", "caiu de 3 a 8", "caiu até 3", "subiu até 8", "subiu 8 ou mais"]},
}


def _passo(amplitude: float) -> float:
    return 0.5 if amplitude < 5 else 1 if amplitude < 15 else 5


def faixas(valores: list[float]) -> dict:
    """Faixas pelos dados: quedas e altas separadas (zero separa os lados quando há os dois), até 4 de cada lado,
    cortes em quantis arredondados a números redondos. Cores: mais escuro = mudança maior, nos dois sentidos."""
    def cortes_lado(mags: list[float]) -> list[float]:
        if len(mags) < 2:
            return []
        k = min(4, max(1, len(mags) // 6))
        serie = pd.Series(sorted(mags))
        p = _passo(serie.max() - serie.min())
        cs = []
        for i in range(1, k):
            c = round(float(serie.quantile(i / k)) / p) * p
            if c > 0 and (not cs or c > cs[-1]):
                cs.append(c)
        return cs
    f = lambda v: _br(v, 1).replace(",0", "")  # noqa: E731
    pts = lambda v: f"{f(v)} ponto" if v == 1 else f"{f(v)} pontos"  # noqa: E731
    neg = [abs(v) for v in valores if v < 0]
    pos = [v for v in valores if v > 0]
    cn, cp = cortes_lado(neg), cortes_lado(pos)
    tons = {1: [4], 2: [4, 2], 3: [4, 3, 1], 4: [4, 3, 2, 1]}
    cortes, rotulos, cores = [], [], []
    if neg:  # da maior queda para zero
        lims = cn[::-1]
        if lims:
            rotulos.append(f"caiu {pts(lims[0])} ou mais")
            rotulos += [f"caiu de {f(b)} a {f(a)}" for a, b in zip(lims, lims[1:])]
            rotulos.append(f"caiu até {f(lims[-1])}")
        else:
            rotulos.append("caiu")
        cortes += [-c for c in lims]
        cores += [f"q{t}" for t in tons[len(lims) + 1]]
    if neg and pos:
        cortes.append(0)
    if pos:  # de zero para a maior alta
        if cp:
            rotulos.append(f"subiu até {f(cp[0])}")
            rotulos += [f"subiu de {f(a)} a {f(b)}" for a, b in zip(cp, cp[1:])]
            rotulos.append(f"subiu {pts(cp[-1])} ou mais")
        else:
            rotulos.append("subiu")
        cortes += cp
        cores += [f"a{t}" for t in reversed(tons[len(cp) + 1])]
    return {"cortes": cortes, "cores": cores, "rotulos": rotulos}


def _lugar_brasil(m: list, col: dict, valor: str | None = None) -> str:
    return f"em {m[col['nome']]} ({m[col['uf']]}{', ' + valor if valor else ''})"


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

    linhas.sort(key=lambda l: (l[1], l[0]))  # UF e nome: arquivo estável entre execuções
    bases, grupos = {}, {}
    for b in bases_ok:
        br = analise.agregar(mun[b]).iloc[0]
        bases[str(b)] = {"pct_base": _r(br["pct_pt_base"]), "pct_atual": _r(br["pct_pt_atual"]),
                         "votos_base": int(br["vpt_base"]), "votos_atual": int(br["vpt_atual"]),
                         "nome_base": nomes[b]["pt_base"].rsplit(" (", 1)[0].title(),
                         "nome_atual": nomes[b]["pt_atual"].rsplit(" (", 1)[0].title()}
        for _, u in analise.agregar(mun[b], ["uf"]).iterrows():
            d = grupos.setdefault(u["uf"].upper(), {"pct2026": _r(u["pct_pt_atual"]), "votos2026": int(u["vpt_atual"])})
            d[f"pct{b}"], d[f"votos{b}"] = _r(u["pct_pt_base"]), int(u["vpt_base"])
    d = {"colunas": COLUNAS, "municipios": linhas, "bases": bases, "grupos": grupos, "meta": META_BRASIL,
         "faixas": {str(b): FAIXAS_BRASIL[str(b)] for b in bases_ok}}
    d["textos"] = {str(b): _textos(d, b, _lugar_brasil, lambda g: f"em {g}") for b in bases_ok}
    return d


COLUNAS = ["nome", "uf", "ibge", "regiao", "eleitores", "pct2026", "votos2026"] + \
          [c for b in BASES for c in (f"pct{b}", f"votos{b}")]


def montar_dados_regioes(r, cfg, dir_parquet: Path) -> dict:
    """Mesma página para as regiões de um município (ex.: RAs do DF), a partir do voto por seção: a RA de cada
    seção vem do local de votação (regioes.secoes_por_unidade), e o percentual é sobre os votos válidos oficiais
    de cada ano (votos.candidatos_validos)."""
    from apuracao.carga import secao
    from apuracao.pagina import regioes, votos
    from apuracao.pagina.regioes import em
    dir_h = Path(cfg.historico.dir)
    anos = (2026, *BASES)
    sec = regioes.secoes_por_unidade(r, dir_h, anos=anos)
    cand = votos.candidatos_validos(dir_parquet, cfg)
    tabela, totais, sem_unidade = {}, {}, {}
    for a in anos:
        v = secao.votos(dir_h / str(a) / f"votacao_secao_{a}_BR.zip", r.uf)
        if r.cod_mun:
            v = v[v["cod_mun"] == r.cod_mun]
        v = v[v["numero"].isin(cand[a].keys())]
        m = v.merge(sec[a][["cod_mun", "zona", "secao", "unidade"]], on=["cod_mun", "zona", "secao"], how="left")
        pt = m[m["numero"] == 13]
        totais[a] = (int(pt["votos"].sum()), int(m["votos"].sum()))
        sem_unidade[a] = int(sec[a].loc[sec[a]["unidade"].isna(), "aptos"].sum())
        g = m.dropna(subset=["unidade"]).groupby("unidade")
        tabela[a] = pd.DataFrame({"pt": g.apply(lambda x: x.loc[x["numero"] == 13, "votos"].sum()), "val": g["votos"].sum()})
    aptos = sec[2026].groupby("unidade")["aptos"].sum()
    linhas, grupos = [], {}
    for k in tabela[2026].index:
        nome = r.nomes.get(k, str(k))
        linha = [nome, r.uf.upper(), k, "", int(aptos.get(k, 0))]
        g = {}
        for a in anos:
            t = tabela[a]
            if k in t.index and t.loc[k, "val"] > 0:
                pct, vt = round(100 * t.loc[k, "pt"] / t.loc[k, "val"], 3), int(t.loc[k, "pt"])
            else:
                pct, vt = None, None
            linha += [pct, vt]
            g[f"pct{a}"], g[f"votos{a}"] = pct, vt
        linhas.append(linha)
        grupos[nome] = g
    bases = {}
    for b in BASES:
        (pb, vb), (pa, va) = totais[b], totais[2026]
        bases[str(b)] = {"pct_base": round(100 * pb / vb, 3), "pct_atual": round(100 * pa / va, 3),
                         "votos_base": pb, "votos_atual": pa, "nome_base": cand[b][13], "nome_atual": cand[2026][13]}
    nao_atribuidos = [f"Em {a}, " + f"{sem_unidade[a]:,}".replace(",", ".") + f" eleitores aptos ficaram sem {r.unidade} "
                      f"(local de votação sem coordenadas no cadastro) e entram só no total {r.do_lugar}."
                      for a in anos if sem_unidade[a]]
    meta = {**META_BRASIL,
            "titulo": f"Onde Lula perdeu votos em {r.nome_curto}", "rotulo": f"{r.rotulo}, por {r.unidade}",
            "no_lugar": r.no_lugar, "unidades": r.abrev, "unidade": r.unidade[0].upper() + r.unidade[1:],
            "de": "das" if r.todas == "todas" else "dos", "grandes": r.abrev,
            "titulo_tabela": f"{'Todas as' if r.todas == 'todas' else 'Todos os'} {r.unidades}",
            "titulo_mapa": f"Variação por {r.unidade}, em pontos dos votos válidos",
            "titulo_grupos": f"Por {r.unidade}", "grupos": r.abrev, "de_grupos": "das" if r.todas == "todas" else "dos",
            "mostrar_uf": False, "projecao": "mercator", "chave_malha": "chave",
            "busca": f"Buscar {r.unidade}",
            "metodo": [r.metodo_votos[0].replace("Percentual de cada candidato a presidente",
                                                  "Percentual do número 13 (PT: Lula em 2026 e 2022, Dilma em 2014)")
                       .replace("no 1º turno de 2026 e de 2022", "no 1º turno de 2026, 2022 e 2014"),
                       *r.metodo_votos[1:], *nao_atribuidos]}
    i = {c: j for j, c in enumerate(COLUNAS)}
    fx = {str(b): faixas([l[i["pct2026"]] - l[i[f"pct{b}"]] for l in linhas if l[i[f"pct{b}"]] is not None])
          for b in BASES}
    d = {"colunas": COLUNAS, "municipios": linhas, "bases": bases, "grupos": grupos, "meta": meta, "faixas": fx}
    lugar = lambda m, col, valor=None: f"{em(r, m[col['nome']])}{' (' + valor + ')' if valor else ''}"  # noqa: E731
    d["textos"] = {str(b): _textos(d, b, lugar, lambda g: em(r, g)) for b in BASES}
    return d


def _textos(d: dict, b: int, lugar, lugar_grupo) -> dict:
    meta = d["meta"]
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
    gs = sorted(((g, v["pct2026"] - v[f"pct{b}"]) for g, v in d["grupos"].items() if v.get(f"pct{b}") is not None),
                key=lambda t: t[1])
    caiu_g = sum(1 for _, v in gs if v < 0)
    em_b = f"{x['nome_base']} em {b}" if x["nome_base"] != x["nome_atual"] else f"em {b}"
    n = lambda v: f"{v:,}".replace(",", ".")  # noqa: E731 (inteiros com ponto de milhar)
    abertura = f"{meta['no_lugar']}, {x['nome_atual']}" if meta["no_lugar"] else x["nome_atual"]
    todas = "todas as" if meta["de"] == "das" else "todos os"
    if caiu == 0:
        onde = f"O percentual subiu em {todas} {n(len(var))} {meta['unidades']}."
    elif caiu == len(var):
        onde = f"O percentual caiu em {todas} {n(len(var))} {meta['unidades']}."
    else:
        onde = f"O percentual caiu em {n(caiu)} {meta['de']} {n(len(var))} {meta['unidades']}."
    if perdeu_votos == 0:
        votos_txt = f"Em número de votos, {x['nome_atual']} teve mais votos que {em_b} em {todas} {meta['unidades']}."
    else:
        votos_txt = (f"Em número de votos, {x['nome_atual']} teve menos votos que {em_b} em {n(perdeu_votos)} "
                     f"{meta['unidades']}; a maior perda foi {lugar(mais_votos[0], col)}, com "
                     f"{n(abs(mais_votos[2]))} votos a menos.")
    todas_g = "todas as" if meta["de_grupos"] == "das" else "todos os"
    if gs and gs[-1][1] < 0:
        grupos_txt = (f"O percentual caiu em {todas_g} {len(gs)} {meta['grupos']}; maior queda {lugar_grupo(gs[0][0])} "
                      f"({_br(gs[0][1], 1)} pontos), menor queda {lugar_grupo(gs[-1][0])} ({_br(gs[-1][1], 1)}).")
    elif gs and gs[0][1] > 0:
        grupos_txt = (f"O percentual subiu em {todas_g} {len(gs)} {meta['grupos']}; maior alta {lugar_grupo(gs[-1][0])} "
                      f"(+{_br(gs[-1][1], 1)} pontos), menor alta {lugar_grupo(gs[0][0])} (+{_br(gs[0][1], 1)}).")
    else:
        grupos_txt = (f"O percentual caiu em {caiu_g} {meta['de_grupos']} {len(gs)} {meta['grupos']}; maior queda "
                      f"{lugar_grupo(gs[0][0])} ({_br(gs[0][1], 1)} pontos), maior alta {lugar_grupo(gs[-1][0])} "
                      f"(+{_br(gs[-1][1], 1)}).")
    return {
        "lede": (f"{abertura} teve {_br(x['pct_atual'])}% dos votos válidos no 1º turno de 2026, "
                 f"{_br(abs(dif))} pontos {'a menos' if dif < 0 else 'a mais'} que {em_b} ({_br(x['pct_base'])}%), "
                 f"e {_votos(abs(dv))} {'a menos' if dv < 0 else 'a mais'} "
                 f"({_curto(x['votos_atual'])} contra {_curto(x['votos_base'])}). " + onde),
        "votos": votos_txt,
        "grandes": _grandes(grandes, col, meta, lugar),
        "grupos": grupos_txt,
    }


def _curto(v: int) -> str:
    """'53,7 milhões' ou '675.627'."""
    return _milhoes(v) if v >= 1_000_000 else f"{v:,}".replace(",", ".")


def _votos(v: int) -> str:
    """'3,4 milhões de votos' ou '26.093 votos'."""
    return f"{_milhoes(v)} de votos" if v >= 1_000_000 else f"{v:,} votos".replace(",", ".")


def _grandes(grandes: list, col: dict, meta: dict, lugar) -> str:
    if len(grandes) < 2:
        return ""
    pior = min(grandes, key=lambda t: t[1])
    melhor = max(grandes, key=lambda t: t[1])
    if pior[1] > 0:  # todas subiram: "menor alta", não "maior queda"
        return (f"Entre as {len(grandes)} {meta['grandes']} com 100 mil eleitores ou mais, a maior alta foi "
                f"{lugar(melhor[0], col, '+' + _br(melhor[1], 1))} e a menor "
                f"{lugar(pior[0], col, '+' + _br(pior[1], 1))}.")
    return (f"Entre as {len(grandes)} {meta['grandes']} com 100 mil eleitores ou mais, a maior queda foi "
            f"{lugar(pior[0], col, _br(pior[1], 1) + (' pontos' if meta['mostrar_uf'] else ''))} e o melhor resultado "
            f"{lugar(melhor[0], col, ('+' if melhor[1] > 0 else '') + _br(melhor[1], 1))}.")


def gerar(dir_parquet: Path, malha: Path, saida: Path) -> Path:
    con = consultas.conectar(dir_parquet)
    dados = montar_dados(con)
    html = MODELO.read_text().replace("__TITULO__", dados["meta"]["titulo"])
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(ler_malha(malha), separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida


def gerar_regioes(r, cfg, dir_parquet: Path, saida: Path) -> Path:
    """Versão por região (ex.: RAs do DF); malha da página de abstenção do mesmo recorte."""
    from apuracao.pagina import regioes
    dados = montar_dados_regioes(r, cfg, dir_parquet)
    html = MODELO.read_text().replace("__TITULO__", dados["meta"]["titulo"])
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(regioes.malha_pagina(r), separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
