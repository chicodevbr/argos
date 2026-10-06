"""Reconstrução da noite da apuração a partir do boletim de urna (carga/boletim.py).

Cada seção entra na contagem no horário em que o boletim chegou ao TSE (DT_BU_RECEBIDO,
horário de Brasília). Boletins que chegam antes da abertura da divulgação (17h de
Brasília; ex.: exterior) entram às 17h.

Validação com o 2º turno de 2022: totais iguais aos oficiais (60.345.999 x 58.206.354,
472.028 seções) e a curva bate com a publicada pelo g1 na noite (gráfico minuto a minuto,
conferido em 06/10/2026) com 1 min de defasagem (tempo entre receber e divulgar): erro médio
0,007 ponto depois das 17h30; virada às 18h43 no boletim, 18h44 no g1.

`referencia` dá ao painel ao vivo a curva de um 2º turno passado para comparar.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from apuracao.modelo.ea12 import REGIAO

ABERTURA = "17:00:00"  # início da divulgação (horário de Brasília)


def disponiveis(dir_parquet: Path) -> list[tuple[int, int]]:
    """(ano, turno) com boletim carregado, do mais recente ao mais antigo."""
    saida = []
    for p in (Path(dir_parquet) / "hist_boletim").glob("*_t*.parquet"):
        ano, turno = p.stem.split("_t")
        saida.append((int(ano), int(turno)))
    return sorted(saida, reverse=True)


def _secoes(dir_parquet: Path, ano: int, turno: int) -> str:
    arq = Path(dir_parquet) / "hist_boletim" / f"{ano}_t{turno}.parquet"
    return f"""
    SELECT uf, cod_mun, zona, secao,
           greatest(min(recebido), CAST(CAST(min(recebido) AS DATE) || ' {ABERTURA}' AS TIMESTAMP)) AS ts,
           any_value(aptos) AS aptos,
           sum(votos) FILTER (tipo = 'Nominal') AS validos
    FROM read_parquet('{arq}')
    WHERE recebido IS NOT NULL
    GROUP BY uf, cod_mun, zona, secao
    """


def curva(dir_parquet: Path, ano: int, turno: int, numeros: list[int] | None = None,
          passo_min: int = 1) -> pd.DataFrame:
    """Contagem acumulada por minuto: ts (Brasília), pct_secoes, e por candidato votos e
    pct_validos (formato longo: uma linha por ts e candidato). `numeros` = None: todos."""
    arq = Path(dir_parquet) / "hist_boletim" / f"{ano}_t{turno}.parquet"
    con = duckdb.connect()
    con.execute(f"CREATE TEMP VIEW s AS {_secoes(dir_parquet, ano, turno)}")
    con.execute(f"""
        CREATE TEMP VIEW v AS
        SELECT b.numero, s.ts, b.votos FROM read_parquet('{arq}') b
        JOIN s USING (uf, cod_mun, zona, secao) WHERE b.tipo = 'Nominal'
    """)
    passo = f"INTERVAL {int(passo_min)} MINUTE"
    sec = con.execute(f"""
        SELECT time_bucket({passo}, ts) + {passo} AS ts, count(*) AS n FROM s GROUP BY 1 ORDER BY 1
    """).df()
    total_secoes = sec["n"].sum()
    sec["pct_secoes"] = 100 * sec["n"].cumsum() / total_secoes
    vot = con.execute(f"""
        SELECT time_bucket({passo}, ts) + {passo} AS ts, numero, sum(votos) AS votos FROM v GROUP BY ALL
    """).df()
    largo = vot.pivot_table(index="ts", columns="numero", values="votos", aggfunc="sum")
    largo = largo.reindex(sec["ts"]).fillna(0).cumsum()
    validos = largo.sum(axis=1)
    if numeros is not None:
        largo = largo[[n for n in numeros if n in largo.columns]]
    longo = largo.reset_index().melt(id_vars="ts", var_name="numero", value_name="votos")
    longo["pct_validos"] = 100 * longo["votos"] / longo["ts"].map(validos).where(lambda x: x > 0)
    longo = longo.merge(sec[["ts", "pct_secoes"]], on="ts")
    return longo.dropna(subset=["pct_validos"]).sort_values(["ts", "numero"]).reset_index(drop=True)


def ritmo_regioes(dir_parquet: Path, ano: int, turno: int, passo_min: int = 5) -> pd.DataFrame:
    """% das seções de cada região (e UF) já recebidas ao longo da noite: ts, regiao, uf, pct_secoes."""
    con = duckdb.connect()
    df = con.execute(f"""
        WITH s AS ({_secoes(dir_parquet, ano, turno)})
        SELECT uf, time_bucket(INTERVAL {int(passo_min)} MINUTE, ts) + INTERVAL {int(passo_min)} MINUTE AS ts,
               count(*) AS n
        FROM s GROUP BY ALL
    """).df()
    df["regiao"] = df["uf"].map(REGIAO)
    grade = pd.MultiIndex.from_product([sorted(df["uf"].unique()), sorted(df["ts"].unique())], names=["uf", "ts"])
    uf = df.set_index(["uf", "ts"])["n"].reindex(grade, fill_value=0).groupby(level="uf").cumsum().reset_index()
    uf["regiao"] = uf["uf"].map(REGIAO)
    total_uf = uf.groupby("uf")["n"].transform("max")
    uf["pct_secoes"] = 100 * uf["n"] / total_uf
    reg = uf.groupby(["regiao", "ts"])["n"].sum().reset_index()
    reg["pct_secoes"] = 100 * reg["n"] / reg.groupby("regiao")["n"].transform("max")
    return pd.concat([reg.assign(uf=None), uf], ignore_index=True)[["ts", "regiao", "uf", "pct_secoes"]]


def nomes(dir_parquet: Path, ano: int, turno: int) -> pd.DataFrame:
    """numero, nome e votos finais dos candidatos (votos nominais), do mais votado ao menos."""
    arq = Path(dir_parquet) / "hist_boletim" / f"{ano}_t{turno}.parquet"
    return duckdb.connect().execute(f"""
        SELECT numero, any_value(nome) AS nome, sum(votos) AS votos FROM read_parquet('{arq}')
        WHERE tipo = 'Nominal' GROUP BY numero ORDER BY votos DESC
    """).df()


def viradas(c: pd.DataFrame, num_a: int, num_b: int) -> list[pd.Timestamp]:
    """Horários em que o líder entre A e B muda (curva de `curva`)."""
    w = c[c["numero"].isin([num_a, num_b])].pivot_table(index="ts", columns="numero", values="votos")
    if num_a not in w or num_b not in w:
        return []
    frente = (w[num_a] > w[num_b]).astype(int)
    validos = (w[num_a] + w[num_b]) > 0
    frente = frente[validos]
    return list(frente.index[frente.diff().fillna(0) != 0])


def referencia(dir_parquet: Path, ano_atual: int, num_a: int, num_b: int) -> tuple[int, pd.DataFrame] | None:
    """Curva do 2º turno mais recente carregado (antes de `ano_atual`) em que A e B disputaram,
    para comparar com a noite atual: (ano, DataFrame com hora = horário do dia como timedelta,
    pct_a = fatia de A em A + B, pct_secoes). Vai até 30 min depois de 99% das seções.
    None se não houver boletim carregado com os dois números."""
    for ano, turno in disponiveis(dir_parquet):
        if turno != 2 or ano >= ano_atual:
            continue
        c = curva(dir_parquet, ano, turno, [num_a, num_b])
        if set(c["numero"]) != {num_a, num_b}:
            continue
        w = c.pivot_table(index="ts", columns="numero", values="votos")
        sec = c.drop_duplicates("ts").set_index("ts")["pct_secoes"]
        df = pd.DataFrame({"ts": w.index, "pct_a": (100 * w[num_a] / (w[num_a] + w[num_b])).to_numpy(),
                           "pct_secoes": sec.reindex(w.index).to_numpy()}).dropna()
        fim = df.loc[df["pct_secoes"] >= 99, "ts"].min() + pd.Timedelta(minutes=30)
        df = df[df["ts"] <= fim]
        df["hora"] = df["ts"] - df["ts"].dt.normalize()
        return ano, df.drop(columns="ts").reset_index(drop=True)
    return None
