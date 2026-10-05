"""Consultas da página de análise: 1º turno de presidente, 2026 x um ano histórico (2022, 2018, 2014).

Mesmas definições nos dois anos, sempre a partir dos totais por município:
- abstenção % = abstenção / (comparecimento + abstenção)  [eleitorado das seções instaladas]
- comparecimento % = 100 - abstenção %
- brancos % e nulos % = sobre o comparecimento (nulos = nulos + nulos técnicos)
- votos de um candidato % = sobre os votos válidos
2026: snapshots de município da eleição 6257 (a soma bate com o arquivo BR do TSE).
Anos históricos: dados abertos (hist_comparecimento / hist_votacao), conferidos com o resultado oficial.
Voto em trânsito (2014: 73.706 votos válidos) é incluído e fica no município onde foi
depositado (capitais): sem ele, os totais de 2014 não batem com o oficial.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from apuracao.modelo import consultas
from apuracao.modelo.ea12 import REGIAO

ELEICAO_2026_T1 = 6257
# Dois principais candidatos a presidente no 1º turno de cada ano: (PT, principal adversário).
# Números conferidos com os resultados oficiais (votos idênticos aos publicados pelo TSE).
PRINCIPAIS = {2014: (13, 45), 2018: (13, 17), 2022: (13, 22), 2026: (13, 22)}


def disponivel(con: duckdb.DuckDBPyConnection) -> bool:
    return all(consultas.tem(con, t) for t in
               ("snapshot_totais", "snapshot_candidatos", "hist_votacao", "hist_comparecimento"))


def anos_base(con: duckdb.DuckDBPyConnection) -> list[int]:
    """Anos históricos carregados (1º turno de presidente), do mais recente ao mais antigo."""
    anos = con.execute("SELECT DISTINCT ano FROM hist_comparecimento WHERE turno = 1 AND cargo = 1").fetchall()
    return sorted((a[0] for a in anos if a[0] in PRINCIPAIS), reverse=True)


def nomes(con: duckdb.DuckDBPyConnection, ano_base: int) -> dict[str, str]:
    """Nome de urna dos candidatos comparados, lido dos dados: chaves pt_base, adv_base, pt_atual, adv_atual."""
    pt_b, adv_b = PRINCIPAIS[ano_base]
    pt_a, adv_a = PRINCIPAIS[2026]
    hist = dict(con.execute("SELECT numero, any_value(nome) FROM hist_votacao WHERE ano = ? AND turno = 1 "
                            "AND cargo = 1 AND numero IN (?, ?) GROUP BY numero", [ano_base, pt_b, adv_b]).fetchall())
    atual = dict(con.execute(f"SELECT numero, any_value(nome) FROM snapshot_candidatos WHERE eleicao = {ELEICAO_2026_T1} "
                             "AND cargo = 1 AND numero IN (?, ?) GROUP BY numero", [pt_a, adv_a]).fetchall())
    return {"pt_base": f"{hist.get(pt_b, '?')} ({pt_b})", "adv_base": f"{hist.get(adv_b, '?')} ({adv_b})",
            "pt_atual": f"{atual.get(pt_a, '?')} ({pt_a})", "adv_atual": f"{atual.get(adv_a, '?')} ({adv_a})"}


def municipios(con: duckdb.DuckDBPyConnection, ano_base: int = 2022) -> pd.DataFrame:
    """Uma linha por município (e localidade do exterior): ano base x 2026 lado a lado.

    Colunas com sufixo _base (ano_base) e _atual (2026); candidatos como pt / adv. Inclui
    municípios que só existem num dos anos (com o outro lado vazio), para os totais de cada
    ano baterem com o oficial; comparações município a município usam só os que têm os dois.
    """
    pt_b, adv_b = PRINCIPAIS[ano_base]
    pt_a, adv_a = PRINCIPAIS[2026]
    df = con.execute(f"""
    WITH t26 AS (
        SELECT * FROM snapshot_totais WHERE eleicao = {ELEICAO_2026_T1} AND cargo = 1 AND tpabr = 'mu'
        QUALIFY row_number() OVER (PARTITION BY abrangencia ORDER BY ts_tse DESC, ts_coleta DESC) = 1
    ),
    atual AS (
        SELECT t.uf, t.cod_mun, t.comparecimento AS comp_atual, t.abstencao AS abst_atual,
               t.brancos AS brancos_atual, t.nulos AS nulos_atual, t.votos_validos AS vv_atual,
               sum(c.votos) FILTER (c.numero = {pt_a}) AS vpt_atual, sum(c.votos) FILTER (c.numero = {adv_a}) AS vadv_atual
        FROM t26 t JOIN snapshot_candidatos c USING (arquivo_raw) GROUP BY ALL
    ),
    c_base AS (
        SELECT uf, cod_mun_tse AS cod_mun, sum(comparecimento) AS comp_base, sum(abstencao) AS abst_base,
               sum(brancos) AS brancos_base, sum(nulos) AS nulos_base
        FROM hist_comparecimento WHERE ano = {int(ano_base)} AND turno = 1 AND cargo = 1 GROUP BY ALL
    ),
    v_base AS (
        SELECT uf, cod_mun_tse AS cod_mun, sum(votos_validos) AS vv_base,
               sum(votos_validos) FILTER (numero = {pt_b}) AS vpt_base,
               sum(votos_validos) FILTER (numero = {adv_b}) AS vadv_base
        FROM hist_votacao WHERE ano = {int(ano_base)} AND turno = 1 AND cargo = 1 GROUP BY ALL
    )
    -- FULL OUTER: municípios/localidades que só existem num dos anos entram nos totais
    -- daquele ano (em 2014, 30 deles ficariam de fora e o total não bateria com o oficial).
    SELECT * EXCLUDE (cod_tse, uf_m), m.nome, m.capital, m.cod_ibge
    FROM atual
    FULL OUTER JOIN c_base USING (uf, cod_mun)
    FULL OUTER JOIN v_base USING (uf, cod_mun)
    LEFT JOIN (SELECT uf AS uf_m, cod_tse, nome, capital, cod_ibge FROM municipios) m ON m.uf_m = uf AND m.cod_tse = cod_mun
    """).df()
    df["regiao"] = df["uf"].map(REGIAO)
    return _taxas(df)


def _taxas(df: pd.DataFrame) -> pd.DataFrame:
    for p in ("base", "atual"):
        base = df[f"comp_{p}"] + df[f"abst_{p}"]
        df[f"eleitores_{p}"] = base
        df[f"abst_pct_{p}"] = 100 * df[f"abst_{p}"] / base
        df[f"brancos_pct_{p}"] = 100 * df[f"brancos_{p}"] / df[f"comp_{p}"]
        df[f"nulos_pct_{p}"] = 100 * df[f"nulos_{p}"] / df[f"comp_{p}"]
        for c in ("pt", "adv"):
            df[f"pct_{c}_{p}"] = 100 * df[f"v{c}_{p}"] / df[f"vv_{p}"]
    df["var_abst"] = df["abst_pct_atual"] - df["abst_pct_base"]
    for c in ("pt", "adv"):
        df[f"var_{c}"] = df[f"pct_{c}_atual"] - df[f"pct_{c}_base"]
    return df


SOMAVEIS = [f"{c}_{p}" for p in ("base", "atual")
            for c in ("comp", "abst", "brancos", "nulos", "vv", "vpt", "vadv")]


def agregar(mun: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Soma os municípios (por UF, região ou Brasil inteiro) e recalcula as taxas."""
    if por:
        g = mun.groupby(por, as_index=False)[SOMAVEIS].sum(min_count=1)
    else:
        g = mun[SOMAVEIS].sum(min_count=1).to_frame().T
    return _taxas(g)
