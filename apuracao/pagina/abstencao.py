"""Gera a página compartilhável "Abstenção no 1º turno de 2026" a partir das tabelas.

uv run python -m apuracao.pagina --saida data/pagina/abstencao-1o-turno-2026.html

Lê data/parquet (snapshots do 1º turno de 2026 por município + histórico 2014/2018/2022)
e a malha do IBGE; preenche o modelo `modelo_abstencao.html` (dados + malha embutidos).
As frases com afirmações sobre os dados (títulos dos gráficos, abertura, notas) são
CALCULADAS aqui: se os dados mudarem, a frase muda junto, em vez de ficar falsa.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from apuracao.app import analise
from apuracao.carga.ibge import ler_malha
from apuracao.modelo import consultas

MODELO = Path(__file__).with_name("modelo_abstencao.html")
NOMES_UF = {
    "AC": "Acre", "AL": "Alagoas", "AM": "Amazonas", "AP": "Amapá", "BA": "Bahia", "CE": "Ceará",
    "DF": "Distrito Federal", "ES": "Espírito Santo", "GO": "Goiás", "MA": "Maranhão", "MG": "Minas Gerais",
    "MS": "Mato Grosso do Sul", "MT": "Mato Grosso", "PA": "Pará", "PB": "Paraíba", "PE": "Pernambuco",
    "PI": "Piauí", "PR": "Paraná", "RJ": "Rio de Janeiro", "RN": "Rio Grande do Norte", "RO": "Rondônia",
    "RR": "Roraima", "RS": "Rio Grande do Sul", "SC": "Santa Catarina", "SE": "Sergipe", "SP": "São Paulo",
    "TO": "Tocantins",
}
EXTENSO = {2: "duas", 3: "três", 4: "quatro", 5: "cinco", 6: "seis"}


def _r(x, n=2):
    """Arredonda; qualquer ausente (None, NaN, pd.NA) vira None (null no JSON)."""
    return None if pd.isna(x) else round(float(x), n)


def _br(v: float, casas: int = 2) -> str:
    return f"{v:.{casas}f}".replace(".", ",")


def _fracao(parte: int, total: int) -> str:
    """'dois terços', 'metade'... só quando a fração é próxima; senão, percentual."""
    f = parte / total
    for valor, nome in ((2 / 3, "dois terços"), (1 / 2, "metade"), (3 / 4, "três quartos"),
                        (1 / 3, "um terço"), (1 / 4, "um quarto")):
        if abs(f - valor) < 0.02:
            return nome
    return f"{round(100 * f)}%"


def montar_dados(con) -> dict:
    anos = analise.anos_base(con)  # do mais recente ao mais antigo
    mun = {a: analise.municipios(con, a) for a in anos}

    brasil, exterior = {}, {}
    for a in anos:
        b = analise.agregar(mun[a]).iloc[0]
        e = analise.agregar(mun[a][mun[a]["uf"] == "zz"]).iloc[0]
        brasil[a] = dict(abst=_r(b["abst_pct_base"]), abstencoes=int(b["abst_base"]),
                         eleitores=int(b["eleitores_base"]), brancos=_r(b["brancos_pct_base"]),
                         nulos=_r(b["nulos_pct_base"]))
        exterior[a] = _r(e["abst_pct_base"])
    ref = mun[anos[0]]
    b = analise.agregar(ref).iloc[0]
    e = analise.agregar(ref[ref["uf"] == "zz"]).iloc[0]
    brasil[2026] = dict(abst=_r(b["abst_pct_atual"]), abstencoes=int(b["abst_atual"]),
                        eleitores=int(b["eleitores_atual"]), brancos=_r(b["brancos_pct_atual"]),
                        nulos=_r(b["nulos_pct_atual"]))
    exterior[2026] = _r(e["abst_pct_atual"])

    ufs: dict[str, dict] = {}
    for a in anos:
        for _, row in analise.agregar(mun[a], ["uf"]).iterrows():
            if row["uf"] == "zz":
                continue
            u = ufs.setdefault(row["uf"].upper(), {})
            u[str(a)] = _r(row["abst_pct_base"])
            u["2026"] = _r(row["abst_pct_atual"])

    m = ref[(ref["uf"] != "zz") & ref["comp_atual"].notna()]
    outros = {a: mun[a].set_index(["uf", "cod_mun"])["abst_pct_base"] for a in anos}
    colunas = ["nome", "uf", "eleitores", "abstencoes", "abst2026"] + [f"abst{a}" for a in anos] + ["ibge"]
    municipios = []
    for _, x in m.iterrows():
        k = (x["uf"], x["cod_mun"])
        municipios.append([x["nome"], x["uf"].upper(), int(x["eleitores_atual"]), int(x["abst_atual"]),
                           _r(x["abst_pct_atual"], 1)]
                          + [_r(outros[a].get(k, float("nan")), 1) for a in anos] + [x["cod_ibge"]])

    entre = _entre_turnos(con, brasil[2026]["abst"])
    dados = {"brasil": brasil, "exterior": exterior, "ufs": ufs, "municipios": municipios,
             "colunas_municipio": colunas, "entre_turnos": entre}
    dados["textos"] = _textos(dados, m)
    return dados


def _entre_turnos(con, abst_2026_t1: float) -> dict:
    mun = con.execute("""
        SELECT ano, turno, uf, sum(comparecimento) AS comp, sum(abstencao) AS abst
        FROM hist_comparecimento WHERE cargo = 1 GROUP BY ALL""").df()
    br = mun.groupby(["ano", "turno"])[["comp", "abst"]].sum()
    pct = 100 * br["abst"] / (br["comp"] + br["abst"])
    anos = sorted(a for a in pct.index.get_level_values(0).unique() if (a, 1) in pct and (a, 2) in pct)
    brasil = {str(a): {"t1": _r(pct[(a, 1)]), "t2": _r(pct[(a, 2)]), "d": _r(pct[(a, 2)] - pct[(a, 1)])} for a in anos}

    uf = mun[mun["uf"] != "zz"].groupby(["ano", "uf", "turno"])[["comp", "abst"]].sum()
    p = (100 * uf["abst"] / (uf["comp"] + uf["abst"])).unstack("turno")
    delta = (p[2] - p[1]).unstack("ano")[anos]
    ufs = {u.upper(): {**{str(a): _r(delta.loc[u, a]) for a in anos}, "media": _r(delta.loc[u].mean())}
           for u in delta.index}
    corr = delta.corr().to_numpy()[np.triu_indices(len(anos), 1)] if len(anos) > 1 else np.array([])

    gov2 = {(a, u) for a, u in con.execute(
        "SELECT DISTINCT ano, uf FROM hist_votacao WHERE cargo = 3 AND turno = 2").fetchall()}
    sinais = []
    for a in anos:
        com = [delta.loc[u, a] for u in delta.index if (a, u) in gov2]
        sem = [delta.loc[u, a] for u in delta.index if (a, u) not in gov2]
        if com and sem:
            sinais.append(np.sign(np.median(com) - np.median(sem)))
    variacoes = [v["d"] for v in brasil.values()]
    return {
        "brasil": brasil, "ufs": ufs,
        "faixa_2026": [_r(abst_2026_t1 + min(variacoes), 1), _r(abst_2026_t1 + max(variacoes), 1)] if variacoes else None,
        "correlacao": [_r(corr.min()), _r(corr.max())] if corr.size else None,
        "governador_consistente": bool(sinais) and len(set(sinais)) == 1 and sinais[0] != 0,
        "governador_sinal": int(sinais[0]) if sinais else 0,
    }


def _textos(d: dict, mun_br: pd.DataFrame) -> dict:
    """Frases da página derivadas dos dados (nada afirmado sem conferir)."""
    b = d["brasil"]
    anos = sorted(b)
    serie = [b[a]["abst"] for a in anos if b[a]["abst"] is not None]
    sobe_sempre = len(serie) > 1 and all(y > x for x, y in zip(serie, serie[1:]))
    maior = len(serie) > 1 and serie[-1] == max(serie)

    grandes = mun_br[(mun_br["eleitores_atual"] >= 100_000) & mun_br["abst_pct_base"].notna()]
    subiu = int((grandes["abst_pct_atual"] > grandes["abst_pct_base"]).sum())
    top = mun_br.nlargest(10, "abst_pct_atual")
    uf_top, n_top = top["uf"].str.upper().value_counts().idxmax(), int(top["uf"].str.upper().value_counts().max())

    partes = [f"{_br(b[2026]['abstencoes'] / 1e6, 1)} milhões de eleitores não votaram no 1º turno: "
              f"{_br(b[2026]['abst'])}% do eleitorado"]
    if maior:
        partes[0] += f", a maior das {EXTENSO.get(len(anos), len(anos))} últimas eleições presidenciais"
    if len(grandes):  # sem cidades grandes nos dados, a frase não entra
        verbo = "Subiu" if subiu * 2 > len(grandes) else "Caiu"
        n_verbo = subiu if verbo == "Subiu" else len(grandes) - subiu
        partes.append(f"{verbo} em {_fracao(n_verbo, len(grandes))} das cidades com 100 mil eleitores ou mais "
                      f"({n_verbo} de {len(grandes)})")
    if n_top >= 5:
        partes.append(f"{n_top} dos 10 maiores percentuais estão em municípios de {NOMES_UF.get(uf_top, uf_top)}")

    ext = d["exterior"][2026]
    et = d["entre_turnos"]
    nota_uf = ""
    if et["correlacao"]:
        c0, c1 = et["correlacao"]
        if c0 >= 0.5:
            nota_uf = (f"As mesmas UFs sobem mais entre turnos eleição após eleição (correlação de "
                       f"{_br(c0)} a {_br(c1)} entre os anos). ")
        else:
            nota_uf = f"A variação por UF muda bastante de uma eleição para outra (correlação de {_br(c0)} a {_br(c1)}). "
    if et["governador_consistente"]:
        nota_uf += ("Onde houve 2º turno para governador, a abstenção subiu sistematicamente "
                    + ("mais." if et["governador_sinal"] > 0 else "menos."))
    else:
        nota_uf += "Ter 2º turno para governador não mudou a abstenção de forma consistente."

    return {
        "lede": "; ".join(partes[:1] + [p[0].lower() + p[1:] for p in partes[1:]]) + ".",
        "titulo_serie": "A abstenção sobe a cada eleição" if sobe_sempre else "Abstenção no 1º turno, 2014 a 2026",
        "titulo_exterior": (f"No exterior, mais de {int(ext // 10 * 10)}% não votam" if ext is not None and ext >= 50
                            else "Abstenção no exterior"),
        "nota_turnos_uf": nota_uf,
    }


def gerar(dir_parquet: Path, malha: Path, saida: Path) -> Path:
    con = consultas.conectar(dir_parquet)
    if not analise.disponivel(con):
        raise SystemExit(f"faltam tabelas em {dir_parquet} (1º turno de 2026 por município e histórico)")
    dados = montar_dados(con)
    geo = ler_malha(malha)
    html = MODELO.read_text()
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(geo, separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
