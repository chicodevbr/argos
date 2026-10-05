"""Consultas do painel ao vivo (sem Streamlit, para poder testar).

Horários: as tabelas guardam UTC; o painel mostra horário de Brasília (UTC-3).
"""

from __future__ import annotations

import duckdb
import pandas as pd

from apuracao.modelo import consultas

BRASILIA = "INTERVAL 3 HOUR"
MAX_SERIES = 3  # linhas coloridas no gráfico de evolução; o resto fica fora/cinza


def abrangencias(con: duckdb.DuckDBPyConnection, eleicao: int, cargo: int) -> list[str]:
    """Abrangências com dados: br, UFs, exterior (zz) e por fim municípios."""
    if not consultas.tem(con, "snapshot_totais"):
        return []
    linhas = con.execute(
        "SELECT DISTINCT abrangencia FROM snapshot_totais WHERE eleicao = ? AND cargo = ?",
        [eleicao, cargo],
    ).fetchall()
    return sorted((l[0] for l in linhas), key=lambda a: (a != "br", len(a) > 2, a == "zz", a))


def resumo(con, eleicao: int, cargo: int, abrangencia: str) -> dict | None:
    if not consultas.tem(con, "snapshot_totais"):
        return None
    df = con.execute(
        f"""SELECT *, ts_tse - {BRASILIA} AS ts_brasilia FROM snapshot_totais
            WHERE eleicao = ? AND cargo = ? AND abrangencia = ?
            ORDER BY ts_tse DESC, ts_coleta DESC LIMIT 1""",
        [eleicao, cargo, abrangencia],
    ).df()
    return None if df.empty else df.iloc[0].to_dict()


def candidatos(con, eleicao: int, cargo: int, abrangencia: str) -> pd.DataFrame:
    if not consultas.tem(con, "snapshot_candidatos"):
        return pd.DataFrame()
    return con.execute(
        consultas.ULTIMOS_CANDIDATOS,
        {"eleicao": eleicao, "cargo": cargo, "abrangencia": abrangencia},
    ).df()


def destaques(df_cand: pd.DataFrame, n: int = MAX_SERIES) -> list[int]:
    """Números dos n candidatos mais votados, ordenados pelo NÚMERO.

    A cor de cada um sai da posição nesta lista; ordenar pelo número (e não pelo
    voto) faz a cor seguir o candidato mesmo quando a liderança muda.
    """
    if df_cand.empty:
        return []
    top = df_cand.sort_values("votos", ascending=False).head(n)["numero"]
    return sorted(int(x) for x in top)


def evolucao(con, eleicao: int, cargo: int, abrangencia: str, numeros: list[int]) -> pd.DataFrame:
    if not numeros or not consultas.tem(con, "snapshot_candidatos"):
        return pd.DataFrame()
    df = con.execute(
        f"SELECT *, ts_tse - {BRASILIA} AS ts_brasilia FROM ({consultas.EVOLUCAO})",
        {"eleicao": eleicao, "cargo": cargo, "abrangencia": abrangencia},
    ).df()
    return df[df["numero"].isin(numeros)]


def tabela_ufs(con, eleicao: int, cargo: int, numeros: list[int]) -> pd.DataFrame:
    """Última situação de cada UF (e exterior): % seções e % válidos dos destaques."""
    if not consultas.tem(con, "snapshot_totais"):
        return pd.DataFrame()
    df = con.execute(
        f"""
        WITH ult AS ({consultas.ULTIMOS_TOTAIS})
        SELECT ult.uf, ult.pct_secoes, ult.ts_tse - {BRASILIA} AS atualizado,
               c.nome, c.numero, c.pct_validos
        FROM ult JOIN snapshot_candidatos c USING (arquivo_raw)
        WHERE ult.tpabr = 'uf'
        """,
        {"eleicao": eleicao, "cargo": cargo},
    ).df()
    if df.empty:
        return df
    df = df[df["numero"].isin(numeros)]
    tabela = df.pivot_table(index=["uf", "pct_secoes", "atualizado"], columns="nome",
                            values="pct_validos").reset_index()
    tabela.columns.name = None
    return tabela.sort_values("uf").reset_index(drop=True)


def rotulos_sem_colisao(valores: list[float], separacao: float) -> list[float]:
    """Posições y para rótulos na ponta das linhas, afastadas pelo menos `separacao`.

    Mantém a ordem dos valores e desloca só o necessário (para cima), para que
    rótulos de linhas muito próximas (ex.: 2º turno perto de 50/50) não se sobreponham.
    """
    ordem = sorted(range(len(valores)), key=lambda i: valores[i])
    pos = list(valores)
    anterior = None
    for i in ordem:
        if anterior is not None and pos[i] < anterior + separacao:
            pos[i] = anterior + separacao
        anterior = pos[i]
    return pos
