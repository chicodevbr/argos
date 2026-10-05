"""Consultas da página de análise: 1º turno de presidente, 2026 x 2022.

Mesmas definições nos dois anos, sempre a partir dos totais por município:
- abstenção % = abstenção / (comparecimento + abstenção)  [eleitorado das seções instaladas]
- comparecimento % = 100 - abstenção %
- brancos % e nulos % = sobre o comparecimento (nulos = nulos + nulos técnicos)
- votos de um candidato % = sobre os votos válidos
2026: snapshots de município da eleição 6257 (a soma bate com o arquivo BR do TSE).
2022: dados abertos (hist_comparecimento / hist_votacao), conferidos com o resultado oficial.
"""

from __future__ import annotations

import duckdb
import pandas as pd

from apuracao.modelo import consultas
from apuracao.modelo.ea12 import REGIAO

ELEICAO_2026_T1 = 6257
NUMEROS = (13, 22)  # 13 = Lula nas duas; 22 = Bolsonaro (2022) e Flávio Bolsonaro (2026)


def disponivel(con: duckdb.DuckDBPyConnection) -> bool:
    return all(consultas.tem(con, t) for t in
               ("snapshot_totais", "snapshot_candidatos", "hist_votacao", "hist_comparecimento"))


def municipios(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Uma linha por município (e localidade do exterior) com os dois anos lado a lado."""
    df = con.execute(f"""
    WITH t26 AS (
        SELECT * FROM snapshot_totais WHERE eleicao = {ELEICAO_2026_T1} AND cargo = 1 AND tpabr = 'mu'
        QUALIFY row_number() OVER (PARTITION BY abrangencia ORDER BY ts_tse DESC, ts_coleta DESC) = 1
    ),
    a26 AS (
        SELECT t.uf, t.cod_mun, t.comparecimento AS comp_2026, t.abstencao AS abst_2026,
               t.brancos AS brancos_2026, t.nulos AS nulos_2026, t.votos_validos AS vv_2026,
               sum(c.votos) FILTER (c.numero = 13) AS v13_2026, sum(c.votos) FILTER (c.numero = 22) AS v22_2026
        FROM t26 t JOIN snapshot_candidatos c USING (arquivo_raw) GROUP BY ALL
    ),
    c22 AS (
        SELECT uf, cod_mun_tse AS cod_mun, sum(comparecimento) AS comp_2022, sum(abstencao) AS abst_2022,
               sum(brancos) AS brancos_2022, sum(nulos) AS nulos_2022
        FROM hist_comparecimento WHERE ano = 2022 AND turno = 1 AND cargo = 1 AND NOT transito GROUP BY ALL
    ),
    v22 AS (
        SELECT uf, cod_mun_tse AS cod_mun, sum(votos_validos) AS vv_2022,
               sum(votos_validos) FILTER (numero = 13) AS v13_2022, sum(votos_validos) FILTER (numero = 22) AS v22_2022
        FROM hist_votacao WHERE ano = 2022 AND turno = 1 AND cargo = 1 AND NOT transito GROUP BY ALL
    )
    SELECT a26.*, c22.comp_2022, c22.abst_2022, c22.brancos_2022, c22.nulos_2022,
           v22.vv_2022, v22.v13_2022, v22.v22_2022, m.nome, m.capital
    FROM a26
    LEFT JOIN c22 USING (uf, cod_mun)
    LEFT JOIN v22 USING (uf, cod_mun)
    LEFT JOIN municipios m ON m.uf = a26.uf AND m.cod_tse = a26.cod_mun
    """).df()
    df["regiao"] = df["uf"].map(REGIAO)
    return _taxas(df)


def _taxas(df: pd.DataFrame) -> pd.DataFrame:
    for ano in (2022, 2026):
        base = df[f"comp_{ano}"] + df[f"abst_{ano}"]
        df[f"eleitores_{ano}"] = base
        df[f"abst_pct_{ano}"] = 100 * df[f"abst_{ano}"] / base
        df[f"brancos_pct_{ano}"] = 100 * df[f"brancos_{ano}"] / df[f"comp_{ano}"]
        df[f"nulos_pct_{ano}"] = 100 * df[f"nulos_{ano}"] / df[f"comp_{ano}"]
        for n in NUMEROS:
            df[f"pct{n}_{ano}"] = 100 * df[f"v{n}_{ano}"] / df[f"vv_{ano}"]
    df["var_abst"] = df["abst_pct_2026"] - df["abst_pct_2022"]
    for n in NUMEROS:
        df[f"var{n}"] = df[f"pct{n}_2026"] - df[f"pct{n}_2022"]
    return df


SOMAVEIS = [f"{c}_{a}" for a in (2022, 2026)
            for c in ("comp", "abst", "brancos", "nulos", "vv", "v13", "v22")]


def agregar(mun: pd.DataFrame, por: list[str] | None = None) -> pd.DataFrame:
    """Soma os municípios (por UF, região ou Brasil inteiro) e recalcula as taxas."""
    if por:
        g = mun.groupby(por, as_index=False)[SOMAVEIS].sum(min_count=1)
    else:
        g = mun[SOMAVEIS].sum(min_count=1).to_frame().T
    return _taxas(g)
