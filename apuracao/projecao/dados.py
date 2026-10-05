"""Monta as entradas do modelo (base do 1º turno e observado do 2º) a partir das tabelas.

Formato comum (ver modelo.projetar):
- base:  chave, uf, regiao, a1, b1, vv1
- atual: chave, frac, a2, b2
`chave` = uf + código TSE do município com 5 dígitos (ex.: "sp71072"), igual à
`abrangencia` dos snapshots de município.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from apuracao.modelo.ea12 import REGIAO


def _com_regiao(df: pd.DataFrame) -> pd.DataFrame:
    df["regiao"] = df["uf"].map(REGIAO)
    return df


def _hist(dir_parquet: Path) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"CREATE VIEW v AS SELECT * FROM read_parquet('{Path(dir_parquet)}/hist_votacao/*.parquet')")
    return con


def base_historica(dir_parquet: Path, ano: int, cargo: int, num_a: int, num_b: int,
                   turno: int = 1) -> pd.DataFrame:
    """Votos de A e B e total de válidos por município (soma das zonas), sem voto em trânsito."""
    df = _hist(dir_parquet).execute(
        """
        SELECT uf || cod_mun_tse AS chave, uf,
               sum(votos_validos) FILTER (numero = $a) AS a1,
               sum(votos_validos) FILTER (numero = $b) AS b1,
               sum(votos_validos) AS vv1
        FROM v WHERE ano = $ano AND cargo = $cargo AND turno = $turno AND NOT transito
        GROUP BY ALL
        """,
        {"a": num_a, "b": num_b, "ano": ano, "cargo": cargo, "turno": turno},
    ).df()
    return _com_regiao(df.fillna({"a1": 0, "b1": 0}))


def verdade_historica(dir_parquet: Path, ano: int, cargo: int, num_a: int, num_b: int) -> pd.DataFrame:
    """Resultado final do 2º turno por município, no formato de `atual` com frac = 1."""
    b = base_historica(dir_parquet, ano, cargo, num_a, num_b, turno=2)
    return pd.DataFrame({"chave": b["chave"], "frac": 1.0, "a2": b["a1"], "b2": b["b1"]})


def _ultimos_mu(con, eleicao: int, cargo: int) -> str:
    return f"""
    SELECT * FROM snapshot_totais
    WHERE eleicao = {int(eleicao)} AND cargo = {int(cargo)} AND tpabr = 'mu'
    QUALIFY row_number() OVER (PARTITION BY abrangencia ORDER BY ts_tse DESC, ts_coleta DESC) = 1
    """


def base_ao_vivo(con: duckdb.DuckDBPyConnection, eleicao_t1: int, cargo: int,
                 num_a: int, num_b: int) -> pd.DataFrame:
    """Base do 1º turno a partir dos snapshots de município (carga do 1º turno)."""
    df = con.execute(
        f"""
        WITH t AS ({_ultimos_mu(con, eleicao_t1, cargo)})
        SELECT t.abrangencia AS chave, t.uf,
               sum(c.votos) FILTER (c.numero = $a AND c.destinacao = 'Válido') AS a1,
               sum(c.votos) FILTER (c.numero = $b AND c.destinacao = 'Válido') AS b1,
               any_value(t.votos_validos) AS vv1
        FROM t JOIN snapshot_candidatos c USING (arquivo_raw)
        GROUP BY ALL
        """,
        {"a": num_a, "b": num_b},
    ).df()
    return _com_regiao(df.fillna({"a1": 0, "b1": 0}))


def atual_ao_vivo(con: duckdb.DuckDBPyConnection, eleicao_t2: int, cargo: int,
                  num_a: int, num_b: int) -> pd.DataFrame:
    """Último snapshot de cada município no 2º turno: fração de seções e votos de A e B."""
    return con.execute(
        f"""
        WITH t AS ({_ultimos_mu(con, eleicao_t2, cargo)})
        SELECT t.abrangencia AS chave, coalesce(t.pct_secoes, 0) / 100 AS frac,
               coalesce(sum(c.votos) FILTER (c.numero = $a), 0) AS a2,
               coalesce(sum(c.votos) FILTER (c.numero = $b), 0) AS b2
        FROM t JOIN snapshot_candidatos c USING (arquivo_raw)
        GROUP BY ALL
        """,
        {"a": num_a, "b": num_b},
    ).df()
