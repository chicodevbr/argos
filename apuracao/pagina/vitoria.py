"""Página "Como Lula venceu em 2026": o 2º turno de presidente por município, comparado ao 2º turno de
2022 (Lula x Bolsonaro) e ao 1º turno de 2026, com a abstenção.

uv run python -m apuracao.pagina --vitoria

Dados:
- 2026: arquivos de resultado do TSE por município (snapshots coletados na noite e no 1º turno; o último
  de cada município, todos com 100% das seções), lidos de data/parquet (snapshot_*).
- 2022 e a série de abstenção 2006-2022: Portal de Dados Abertos do TSE (hist_votacao, hist_comparecimento).
Os totais nacionais conferem com os oficiais. Os totais incluem o exterior; mapa e tabela só têm municípios.
O número 22 é Flávio Bolsonaro em 2026 e Jair Bolsonaro em 2022 (mesmo partido); "margem" = votos de Lula
menos votos do 22. As frases com afirmações sobre os dados são calculadas aqui.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from apuracao.carga.ibge import ler_malha
from apuracao.config import carregar
from apuracao.modelo import consultas
from apuracao.pagina.abstencao import NOMES_UF
from apuracao.pagina.perdas import faixas
from apuracao.pagina.recortes import nome_proprio
from apuracao.pagina.regioes import _br

MODELO = Path(__file__).with_name("modelo_vitoria.html")
LULA, N22 = 13, 22
BASE = 2022  # 2º turno de comparação
COLUNAS = ["nome", "uf", "ibge", "regiao", "eleitores", "l26", "f26", "l22", "b22", "l1", "v1", "abst26", "abst22", "abst1"]


def _ultimos_mu(eleicao: int) -> str:
    return f"""
    SELECT * FROM snapshot_totais WHERE eleicao = {int(eleicao)} AND cargo = 1 AND tpabr = 'mu'
    QUALIFY row_number() OVER (PARTITION BY abrangencia ORDER BY ts_tse DESC, ts_coleta DESC) = 1"""


def municipios_2026(con, eleicao: int) -> pd.DataFrame:
    """Último snapshot de cada município (e localidade do exterior): votos de Lula e do 22, válidos e participação."""
    df = con.execute(f"""
        WITH t AS ({_ultimos_mu(eleicao)})
        SELECT t.abrangencia AS chave, any_value(t.pct_secoes) AS pct_secoes,
               any_value(t.eleitorado) AS aptos, any_value(t.abstencao) AS abstencao,
               any_value(t.votos_validos) AS validos,
               coalesce(sum(c.votos) FILTER (c.numero = {LULA}), 0) AS lula,
               coalesce(sum(c.votos) FILTER (c.numero = {N22}), 0) AS n22
        FROM t JOIN snapshot_candidatos c USING (arquivo_raw) GROUP BY t.abrangencia
    """).df()
    incompletos = df[df["pct_secoes"] < 100]
    if len(incompletos):
        raise SystemExit(f"{len(incompletos)} municípios sem 100% das seções na eleição {eleicao} "
                         f"(ex.: {incompletos['chave'].iloc[0]}): rode o modelo construir com os snapshots finais")
    return df


def municipios_2022(con) -> pd.DataFrame:
    """2º turno de 2022 por município: votos de Lula e Bolsonaro (com trânsito, como os totais oficiais) e abstenção."""
    votos = con.execute(f"""
        SELECT uf || cod_mun_tse AS chave,
               sum(votos) FILTER (numero = {LULA}) AS l22, sum(votos) FILTER (numero = {N22}) AS b22
        FROM hist_votacao WHERE ano = {BASE} AND turno = 2 AND cargo = 1 GROUP BY 1
    """).df()
    part = con.execute(f"""
        SELECT uf || cod_mun_tse AS chave, sum(aptos) AS aptos22, sum(abstencao) AS abst_n22
        FROM hist_comparecimento WHERE ano = {BASE} AND turno = 2 AND cargo = 1 GROUP BY 1
    """).df()
    return votos.merge(part, on="chave", how="outer")


def serie_abstencao(con, t1: int, t2: int) -> list[dict]:
    """Abstenção nacional de presidente, 1º e 2º turnos: histórico + 2026 (arquivo BR do TSE)."""
    hist = con.execute("""
        SELECT ano, turno, 100 * sum(abstencao) / sum(aptos) AS pct FROM hist_comparecimento
        WHERE cargo = 1 GROUP BY ALL ORDER BY ALL
    """).df()
    saida = [{"ano": int(r.ano), "turno": int(r.turno), "pct": round(float(r.pct), 2)} for r in hist.itertuples()]
    for turno, e in ((1, t1), (2, t2)):
        a, ab = con.execute(f"""
            SELECT eleitorado, abstencao FROM snapshot_totais WHERE eleicao = {int(e)} AND cargo = 1 AND abrangencia = 'br'
            ORDER BY ts_tse DESC, ts_coleta DESC LIMIT 1""").fetchone()
        saida.append({"ano": 2026, "turno": turno, "pct": round(100 * ab / a, 2)})
    return sorted(saida, key=lambda r: (r["ano"], r["turno"]))


def _soma(df: pd.DataFrame) -> dict:
    s = {k: int(df[k].sum()) for k in ("l26", "f26", "l22", "b22", "l1", "v1", "aptos", "abst_n", "aptos22", "abst_n22",
                                        "aptos1", "abst_n1")}
    s["pct26"] = round(100 * s["l26"] / (s["l26"] + s["f26"]), 5)
    s["pct22"] = round(100 * s["l22"] / (s["l22"] + s["b22"]), 5) if s["l22"] + s["b22"] else None
    s["pct1"] = round(100 * s["l1"] / s["v1"], 5) if s["v1"] else None
    s["abst26"] = round(100 * s["abst_n"] / s["aptos"], 5)
    s["abst22"] = round(100 * s["abst_n22"] / s["aptos22"], 5) if s["aptos22"] else None
    s["abst1"] = round(100 * s["abst_n1"] / s["aptos1"], 5) if s["aptos1"] else None
    s["ganho_margem"] = (s["l26"] - s["f26"]) - (s["l22"] - s["b22"])
    return s


def montar_dados(dir_parquet: Path, cfg) -> dict:
    con = consultas.conectar(dir_parquet)
    t1, t2 = cfg.eleicao_por_id("2026-t1-federal").codigo, cfg.eleicao_por_id("2026-t2-federal").codigo
    m26 = municipios_2026(con, t2).rename(columns={"lula": "l26", "n22": "f26", "abstencao": "abst_n"})
    m1 = municipios_2026(con, t1)[["chave", "lula", "validos", "aptos", "abstencao"]].rename(
        columns={"lula": "l1", "validos": "v1", "aptos": "aptos1", "abstencao": "abst_n1"})
    mun = con.execute("SELECT uf || cod_tse AS chave, cod_ibge, nome, uf, regiao FROM municipios").df()
    d = (m26.merge(m1, on="chave", how="left").merge(municipios_2022(con), on="chave", how="left")
         .merge(mun, on="chave", how="left"))
    d["uf"] = d["uf"].fillna(d["chave"].str[:2])
    d["regiao"] = d["regiao"].where(d["uf"] != "zz", "Exterior")
    d = d.fillna({k: 0 for k in ("l22", "b22", "aptos22", "abst_n22", "l1", "v1", "aptos1", "abst_n1")})

    bra = d[d["uf"] != "zz"]
    linhas = []
    for x in bra.itertuples():
        tem22 = x.l22 + x.b22 > 0
        linhas.append([
            nome_proprio(x.nome), x.uf.upper(), x.cod_ibge, x.regiao, int(x.aptos), int(x.l26), int(x.f26),
            int(x.l22) if tem22 else None, int(x.b22) if tem22 else None, int(x.l1), int(x.v1),
            round(100 * x.abst_n / x.aptos, 2) if x.aptos else None,
            round(100 * x.abst_n22 / x.aptos22, 2) if x.aptos22 else None,
            round(100 * x.abst_n1 / x.aptos1, 2) if x.aptos1 else None,
        ])
    linhas.sort(key=lambda l: (l[1], l[0]))

    brasil = _soma(d)
    ufs = {u.upper(): _soma(g) for u, g in d[d["uf"] != "zz"].groupby("uf")}
    regioes = {r: _soma(g) for r, g in d.groupby("regiao")}
    col = {c: i for i, c in enumerate(COLUNAS)}
    var22 = [l[col["l26"]] / (l[col["l26"]] + l[col["f26"]]) * 100 - l[col["l22"]] / (l[col["l22"]] + l[col["b22"]]) * 100
             for l in linhas if l[col["l22"]] is not None and l[col["l26"]] + l[col["f26"]] > 0]
    var1 = [l[col["l26"]] / (l[col["l26"]] + l[col["f26"]]) * 100 - 100 * l[col["l1"]] / l[col["v1"]]
            for l in linhas if l[col["v1"]] and l[col["l26"]] + l[col["f26"]] > 0]
    dados = {
        "colunas": COLUNAS, "municipios": linhas, "brasil": brasil, "ufs": ufs, "regioes": regioes,
        "serie": serie_abstencao(con, t1, t2),
        "faixas": {"2022": faixas(var22), "t1": faixas(var1)},
        "metodo": METODO,
    }
    dados["textos"] = textos(dados)
    return dados


METODO = [
    "Votos de Lula (número 13) e do número 22 no 2º turno para presidente: Flávio Bolsonaro em 2026, Jair Bolsonaro "
    "em 2022. Percentuais sobre os votos válidos, que no 2º turno são a soma dos dois. Margem = votos de Lula menos "
    "votos do 22.",
    "2026: arquivos de resultado por município publicados pelo TSE na noite da apuração (o último de cada município, "
    "com 100% das seções). 2022 e a série de abstenção de 2006 a 2022: Portal de Dados Abertos do TSE. Os totais "
    "nacionais conferem com os resultados oficiais.",
    "Os números do Brasil, das regiões e o placar incluem os votos do exterior; mapa, estados e tabela trazem só os "
    "municípios. Municípios que não existiam em 2022 ficam fora das comparações com 2022.",
    "Na comparação com o 1º turno de 2026, o percentual de Lula no 1º turno é sobre todos os votos válidos (com os "
    "outros candidatos); a diferença mostra quanto ele ganhou entre os turnos.",
    "Abstenção: eleitores aptos que não votaram, sobre o total de aptos. Mapa: malha municipal do IBGE, projeção "
    "cônica equivalente de Albers.",
]


def _n(v) -> str:
    return f"{int(v):,}".replace(",", ".")


# Preposição de cada estado ("no Rio Grande do Sul", "em São Paulo", "na Bahia").
PREP_UF = {"AL": "em", "GO": "em", "MG": "em", "MS": "em", "MT": "em", "PE": "em", "RO": "em", "RR": "em",
           "SC": "em", "SE": "em", "SP": "em", "BA": "na", "PB": "na"}


def no_uf(uf: str) -> str:
    return f"{PREP_UF.get(uf, 'no')} {NOMES_UF[uf]}"


def _mi(v: float) -> str:
    """'1,6 milhão de votos', '4,4 milhões de votos', '208.492 votos' (singular abaixo de 2 milhões)."""
    v = abs(v)
    if v < 1_000_000:
        return f"{_n(v)} votos"
    x = _br(v / 1e6, 1)
    return f"{x} {'milhão' if v < 2_000_000 else 'milhões'} de votos"


def _pontos(v: float, sinal: bool = False) -> str:
    """'1,1 ponto', '2,7 pontos', com sinal opcional (+/−); singular abaixo de 2."""
    s = ("+" if v > 0 else "−" if v < 0 else "") if sinal else ""
    return f"{s}{_br(abs(v), 1)} {'ponto' if abs(v) < 2 else 'pontos'}"


def _pct_dois(l, f) -> float:
    return 100 * l / (l + f)


def _mun(m: list, col: dict) -> str:
    return f"{m[col['nome']]} ({m[col['uf']]})"


def textos(D: dict) -> dict:
    b, col = D["brasil"], {c: i for i, c in enumerate(D["colunas"])}
    marg26, marg22 = b["l26"] - b["f26"], b["l22"] - b["b22"]
    dl, df_ = b["l26"] - b["l22"], b["f26"] - b["b22"]
    lede = (f"Lula venceu com {_br(b['pct26'])}% dos votos válidos, {_pontos(b['pct26'] - b['pct22'])} acima de 2022, "
            f"e abriu {_mi(marg26)} de vantagem, contra {_mi(marg22)} em 2022. ")
    if df_ > 0 and dl > df_:
        lede += (f"Flávio Bolsonaro teve {_mi(df_)} a mais que Jair Bolsonaro em 2022, mas Lula cresceu mais: "
                 f"{_mi(dl)} a mais.")
    else:
        lede += (f"Lula teve {_mi(dl)} {'a mais' if dl > 0 else 'a menos'} que em 2022, e o 22, "
                 f"{_mi(df_)} {'a mais' if df_ > 0 else 'a menos'}.")

    # Onde a vitória foi decidida: ganho de margem por região e estado
    g = b["ganho_margem"]
    regs = sorted(((r, v["ganho_margem"]) for r, v in D["regioes"].items()), key=lambda t: -t[1])
    ufs = sorted(((u, v["ganho_margem"]) for u, v in D["ufs"].items()), key=lambda t: -t[1])
    top3 = ufs[:3]
    reg_pos = [t for t in regs if t[1] > 0][:2]
    reg_neg = [t for t in regs if t[1] < 0]
    encolheu = [t for t in ufs if t[1] < 0]
    decisivo = (f"A vantagem de Lula cresceu {_mi(g)} em relação a 2022. Três estados explicam "
                f"{_br(100 * sum(v for _, v in top3) / g, 0)}% desse aumento: "
                + ", ".join(f"{NOMES_UF[u]} (+{_mi(v)})" for u, v in top3[:-1])
                + f" e {NOMES_UF[top3[-1][0]]} (+{_mi(top3[-1][1])}). Por região, "
                + " e ".join(f"o {r} (+{_mi(v)})" for r, v in reg_pos) + " somaram mais que o aumento total")
    if reg_neg:
        decisivo += (", compensando as perdas " + " e ".join(f"no {r} ({_mi(v).replace('votos', 'votos a menos')})"
                                                             for r, v in reg_neg[::-1]))
    decisivo += "."
    if encolheu:
        pior = encolheu[-1]
        decisivo += (f" A vantagem de Lula diminuiu em {len(encolheu)} estados; a maior perda foi "
                     f"{no_uf(pior[0])} (−{_mi(pior[1])}).")

    # Onde Lula mais cresceu (2022 e 1º turno)
    def cresceu(chave: str) -> str:
        ms = []
        for m in D["municipios"]:
            if m[col["l26"]] + m[col["f26"]] == 0:
                continue
            p26 = _pct_dois(m[col["l26"]], m[col["f26"]])
            if chave == "2022":
                if m[col["l22"]] is None:
                    continue
                ms.append((m, p26 - _pct_dois(m[col["l22"]], m[col["b22"]])))
            elif m[col["v1"]]:
                ms.append((m, p26 - 100 * m[col["l1"]] / m[col["v1"]]))
        subiu = [t for t in ms if t[1] > 0]
        el = sum(t[0][col["eleitores"]] for t in ms)
        el_subiu = sum(t[0][col["eleitores"]] for t in subiu)
        grandes = [t for t in ms if t[0][col["eleitores"]] >= 100_000]
        mg = max(grandes, key=lambda t: t[1])
        ref = "que em 2022" if chave == "2022" else "que no 1º turno"
        uf_v = {u: v["pct26"] - (v["pct22"] if chave == "2022" else v["pct1"]) for u, v in D["ufs"].items()}
        um, un = max(uf_v, key=uf_v.get), min(uf_v, key=uf_v.get)
        txt = (f"Lula teve percentual maior {ref} em {_n(len(subiu))} dos {_n(len(ms))} municípios")
        if len(subiu) < len(ms) / 2:
            txt += (f", poucos mas grandes: eles reúnem {_br(100 * el_subiu / el, 0)}% do eleitorado. "
                    f"O crescimento veio das cidades maiores.")
        else:
            txt += "."
        pior = "o pior" if uf_v[un] < 0 else "o menor crescimento"
        txt += (f" Entre as {len(grandes)} cidades com 100 mil eleitores ou mais, o maior crescimento: "
                f"{_mun(mg[0], col)}, {_pontos(mg[1], True)}. Por estado, o melhor resultado foi {no_uf(um)} "
                f"({_pontos(uf_v[um], True)}) e {pior} {no_uf(un)} ({_pontos(uf_v[un], True)}).")
        return txt

    # Onde o 22 teve menos votos que em 2022
    menos = [(m, m[col["f26"]] - m[col["b22"]]) for m in D["municipios"] if m[col["b22"]] is not None]
    caiu = [t for t in menos if t[1] < 0]
    soma_caiu = -sum(v for _, v in caiu)
    pior22 = min(caiu, key=lambda t: t[1]) if caiu else None
    ufs22_caiu = sorted(((u, v["f26"] - v["b22"]) for u, v in D["ufs"].items() if v["f26"] < v["b22"]), key=lambda t: t[1])
    flavio = (f"No país, Flávio teve {_mi(df_)} {'a mais' if df_ > 0 else 'a menos'} que Jair Bolsonaro em 2022. "
              f"{'Mas em' if df_ > 0 else 'Em'} {_n(len(caiu))} dos {_n(len(menos))} municípios o 22 teve menos votos "
              f"que em 2022, {_mi(soma_caiu)} a menos no total")
    flavio += (f". A maior queda: {_mun(pior22[0], col)}, com {_mi(pior22[1])} a menos." if pior22 else ".")
    if ufs22_caiu:
        flavio += (f" Em {len(ufs22_caiu)} estados o 22 perdeu votos no total: "
                   + ", ".join(f"{NOMES_UF[u]} (−{_mi(v)})" for u, v in ufs22_caiu[:-1])
                   + (" e " if len(ufs22_caiu) > 1 else "")
                   + f"{NOMES_UF[ufs22_caiu[-1][0]]} (−{_mi(ufs22_caiu[-1][1])}).")

    # Abstenção
    s2 = [r for r in D["serie"] if r["turno"] == 2]
    atual = next(r for r in s2 if r["ano"] == 2026)
    menores = [r for r in s2 if r["ano"] < 2026 and r["pct"] < atual["pct"]]
    if menores:
        ult = max(menores, key=lambda r: r["ano"])
        desde = f"a menor num 2º turno desde {ult['ano']} ({_br(ult['pct'])}%)"
    else:
        desde = f"a menor num 2º turno da série, iniciada em {min(r['ano'] for r in s2)}"
    caiu_uf = sum(1 for v in D["ufs"].values() if v["abst22"] is not None and v["abst26"] < v["abst22"])
    abst = (f"A abstenção no 2º turno foi de {_br(atual['pct'])}%, {desde}, e {_pontos(b['abst22'] - b['abst26'])} "
            f"abaixo de 2022 ({_br(b['abst22'])}%). Caiu em {caiu_uf} dos {len(D['ufs'])} estados.")
    # Relação com o crescimento de Lula: quintis da variação da abstenção, ponderados pelo eleitorado
    rel = pd.DataFrame([{"da": m[col["abst26"]] - m[col["abst22"]], "e": m[col["eleitores"]],
                         "dl": _pct_dois(m[col["l26"]], m[col["f26"]]) - _pct_dois(m[col["l22"]], m[col["b22"]])}
                        for m in D["municipios"] if m[col["l22"]] is not None and m[col["abst22"]] is not None
                        and m[col["l26"]] + m[col["f26"]] > 0])
    rel["q"] = pd.qcut(rel["da"], 5, labels=False)
    q = rel.groupby("q").apply(lambda g: (g["dl"] * g["e"]).sum() / g["e"].sum(), include_groups=False)
    da = rel.groupby("q")["da"].median()
    def mov(v):  # noqa: E306
        return f"ganhou {_pontos(v)}" if v > 0 else f"perdeu {_pontos(v)}" if v < 0 else "ficou igual"
    relacao = (f"Onde a abstenção mais caiu (o quinto dos municípios com maior queda, mediana de "
               f"{_pontos(da.iloc[0], True)}), Lula {mov(q.iloc[0])} em relação a 2022. Onde ela menos caiu ou subiu "
               f"(mediana de {_pontos(da.iloc[-1], True)}), Lula {mov(q.iloc[-1])}. A queda da abstenção foi maior "
               f"onde Lula perdeu terreno. Médias ponderadas pelo eleitorado; a relação não mostra quem passou a votar "
               f"em quem." if q.iloc[0] < q.iloc[-1] else
               f"Onde a abstenção mais caiu (mediana de {_pontos(da.iloc[0], True)}), Lula {mov(q.iloc[0])} em relação "
               f"a 2022; onde ela menos caiu ou subiu, {mov(q.iloc[-1])}. Médias ponderadas pelo eleitorado; a relação "
               f"não mostra quem passou a votar em quem.")
    return {"lede": lede, "decisivo": decisivo, "cresceu": {"2022": cresceu("2022"), "t1": cresceu("t1")},
            "flavio": flavio, "abstencao": abst, "relacao": relacao, "turnos": abst_turnos(D),
            "quintis": [{"da": round(float(da.iloc[i]), 2), "dl": round(float(q.iloc[i]), 2)} for i in range(len(q))]}


def abst_turnos(D: dict) -> str:
    """Abstenção do 1º para o 2º turno de 2026: Brasil, municípios, cidades grandes e estados onde subiu."""
    b, col = D["brasil"], {c: i for i, c in enumerate(D["colunas"])}
    ms = [(m, m[col["abst26"]] - m[col["abst1"]]) for m in D["municipios"]
          if m[col["abst26"]] is not None and m[col["abst1"]] is not None]
    caiu = sum(1 for _, v in ms if v < 0)
    grandes = sorted((t for t in ms if t[0][col["eleitores"]] >= 100_000), key=lambda t: t[1])
    subiu_uf = sorted(((u, v["abst26"] - v["abst1"]) for u, v in D["ufs"].items() if v["abst26"] > v["abst1"]),
                      key=lambda t: -t[1])
    txt = (f"Do 1º para o 2º turno, a abstenção caiu de {_br(b['abst1'])}% para {_br(b['abst26'])}% no país e diminuiu "
           f"em {_n(caiu)} dos {_n(len(ms))} municípios. Entre as {len(grandes)} cidades com 100 mil eleitores ou mais, "
           f"as maiores quedas foram em " + _lista([f"{m[col['nome']]} ({m[col['uf']]}, {_pontos(v, True)})"
                                                    for m, v in grandes[:3]]) + ".")
    if subiu_uf:
        def uf_txt(u, v):  # noqa: E306
            x = D["ufs"][u]
            return (f"{NOMES_UF[u]} ({_br(x['abst1'], 1)}% → {_br(x['abst26'], 1)}%)" if v >= 0.05
                    else f"{NOMES_UF[u]} (+{_br(v, 2)} ponto)")
        txt += (f" Em {len(subiu_uf)} {'estado' if len(subiu_uf) == 1 else 'estados'} ela subiu: "
                + _lista([uf_txt(u, v) for u, v in subiu_uf]) + ".")
    return txt


def _lista(itens: list[str]) -> str:
    """'a', 'a e b', 'a, b e c'."""
    return itens[0] if len(itens) == 1 else ", ".join(itens[:-1]) + " e " + itens[-1]


def gerar(dir_parquet: Path, malha: Path, saida: Path, config: Path = Path("config/eleicoes.toml")) -> Path:
    dados = montar_dados(dir_parquet, carregar(config))
    titulo = "Como Lula venceu em 2026"
    html = MODELO.read_text().replace("__TITULO__", titulo)
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(ler_malha(malha), separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
