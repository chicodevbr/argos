"""Leitura das tabelas Parquet via DuckDB (usada pelo painel e pela projeção).

As views fazem SELECT DISTINCT: linhas idênticas podem aparecer em dobro por um
instante durante a compactação, ou se a construção cair entre gravar candidatos
e totais. Como são idênticas, DISTINCT resolve sem afetar nada.
"""

from __future__ import annotations

from pathlib import Path

import duckdb


def conectar(dir_parquet: Path | str) -> duckdb.DuckDBPyConnection:
    d = Path(dir_parquet)
    con = duckdb.connect()
    for tabela in ("snapshot_totais", "snapshot_candidatos"):
        if list((d / tabela).glob("*.parquet")):
            con.execute(
                f"CREATE VIEW {tabela} AS SELECT DISTINCT * FROM read_parquet('{d / tabela}/*.parquet')"
            )
    for tabela in ("hist_votacao", "hist_comparecimento"):
        if list((d / tabela).glob("*.parquet")):
            con.execute(f"CREATE VIEW {tabela} AS SELECT * FROM read_parquet('{d / tabela}/*.parquet')")
    if (d / "municipios.parquet").exists():
        con.execute(f"CREATE VIEW municipios AS SELECT * FROM read_parquet('{d / 'municipios.parquet'}')")
    return con


def tem(con: duckdb.DuckDBPyConnection, tabela: str) -> bool:
    return bool(con.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [tabela]
    ).fetchone()[0])


ULTIMOS_TOTAIS = """
SELECT * FROM snapshot_totais
WHERE eleicao = $eleicao AND cargo = $cargo
QUALIFY row_number() OVER (PARTITION BY abrangencia ORDER BY ts_tse DESC, ts_coleta DESC) = 1
"""

ULTIMOS_CANDIDATOS = """
WITH ult AS (
  SELECT arquivo_raw FROM snapshot_totais
  WHERE eleicao = $eleicao AND cargo = $cargo AND abrangencia = $abrangencia
  ORDER BY ts_tse DESC, ts_coleta DESC LIMIT 1
)
SELECT c.* FROM snapshot_candidatos c JOIN ult USING (arquivo_raw)
ORDER BY c.votos DESC NULLS LAST
"""

EVOLUCAO = """
SELECT t.ts_tse, t.pct_secoes, c.nome, c.numero, c.votos, c.pct_validos
FROM snapshot_totais t
JOIN snapshot_candidatos c USING (arquivo_raw)
WHERE t.eleicao = $eleicao AND t.cargo = $cargo AND t.abrangencia = $abrangencia
ORDER BY t.ts_tse
"""
