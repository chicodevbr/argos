"""Detecção de anomalias nos dados publicados pelo TSE (não do nosso lado).

Roda sobre as tabelas Parquet (snapshot_totais / snapshot_candidatos), sem tocar
no coletor. Cada anomalia aponta o snapshot bruto (`arquivo_raw`) que a evidencia;
o dado bruto em data/raw é a prova.

Tipos:
- versao_antiga: coletado depois, mas com ts_tse anterior a um já coletado
  (servidor do CDN desatualizado servindo versão velha).
- secoes_diminuem: % de seções totalizadas cai entre versões (na ordem do TSE).
- votos_diminuem: votos de um candidato caem entre versões (na ordem do TSE).
- validos_nao_batem: soma dos votos "Válido" dos candidatos != votos válidos do arquivo.
- comparecimento_excede: comparecimento > eleitorado das seções instaladas, ou
  total de votos > comparecimento.
- soma_ufs_nao_bate: com tudo 100% apurado, soma de UFs + exterior != Brasil (presidente).
  Só no fim: durante a apuração os arquivos são gerados em momentos diferentes.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb

from apuracao.modelo import consultas


@dataclass(frozen=True)
class Anomalia:
    tipo: str
    eleicao: int
    cargo: int
    abrangencia: str
    arquivo_raw: str
    detalhe: str


_POR_ORDEM_TSE = "PARTITION BY eleicao, cargo, abrangencia ORDER BY ts_tse, ts_coleta"

CONSULTAS = {
    "versao_antiga": """
        SELECT eleicao, cargo, abrangencia, arquivo_raw,
               'ts_tse ' || strftime(ts_tse, '%H:%M:%S') || ' < já visto ' || strftime(max_antes, '%H:%M:%S') AS detalhe
        FROM (SELECT *, max(ts_tse) OVER (PARTITION BY eleicao, cargo, abrangencia ORDER BY ts_coleta
                     ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING) AS max_antes
              FROM snapshot_totais)
        WHERE ts_tse < max_antes
    """,
    "secoes_diminuem": f"""
        SELECT eleicao, cargo, abrangencia, arquivo_raw,
               'seções ' || round(antes, 4) || '% -> ' || round(pct_secoes, 4) || '%' AS detalhe
        FROM (SELECT *, lag(pct_secoes) OVER ({_POR_ORDEM_TSE}) AS antes FROM snapshot_totais)
        WHERE pct_secoes < antes - 1e-9
    """,
    "votos_diminuem": """
        SELECT eleicao, cargo, abrangencia, arquivo_raw,
               nome || ' (' || numero || '): ' || antes || ' -> ' || votos || ' votos' AS detalhe
        FROM (SELECT c.*, t.ts_tse AS ts_t, t.ts_coleta AS tc_t,
                     lag(c.votos) OVER (PARTITION BY c.eleicao, c.cargo, c.abrangencia, c.sq_cand
                                        ORDER BY t.ts_tse, t.ts_coleta) AS antes
              FROM snapshot_candidatos c JOIN snapshot_totais t USING (arquivo_raw))
        WHERE votos < antes
    """,
    "validos_nao_batem": """
        SELECT t.eleicao, t.cargo, t.abrangencia, t.arquivo_raw,
               'soma dos candidatos ' || s.soma || ' != válidos ' || t.votos_validos AS detalhe
        FROM snapshot_totais t
        JOIN (SELECT arquivo_raw, sum(votos) FILTER (destinacao = 'Válido') AS soma
              FROM snapshot_candidatos GROUP BY arquivo_raw) s USING (arquivo_raw)
        WHERE t.divulga AND t.votos_validos IS NOT NULL AND s.soma IS NOT NULL AND s.soma <> t.votos_validos
    """,
    "comparecimento_excede": """
        SELECT eleicao, cargo, abrangencia, arquivo_raw,
               CASE WHEN comparecimento > eleitorado_instaladas
                    THEN 'comparecimento ' || comparecimento || ' > eleitorado das seções instaladas ' || eleitorado_instaladas
                    ELSE 'total de votos ' || votos_total || ' > comparecimento ' || comparecimento END AS detalhe
        FROM snapshot_totais
        WHERE comparecimento > eleitorado_instaladas OR votos_total > comparecimento
    """,
    "soma_ufs_nao_bate": f"""
        WITH ult AS (
            SELECT * FROM snapshot_totais WHERE cargo = 1 AND tpabr IN ('br', 'uf')
            QUALIFY row_number() OVER (PARTITION BY eleicao, abrangencia ORDER BY ts_tse DESC, ts_coleta DESC) = 1
        ),
        completas AS (  -- eleições em que o BR e todas as UFs estão 100%
            SELECT eleicao FROM ult GROUP BY eleicao
            HAVING bool_and(pct_secoes >= 100) AND count(*) FILTER (abrangencia = 'br') = 1
               AND count(*) FILTER (tpabr = 'uf') >= 28
        ),
        votos AS (
            SELECT u.eleicao, u.abrangencia, u.arquivo_raw, c.numero, c.nome, c.votos
            FROM ult u JOIN completas USING (eleicao) JOIN snapshot_candidatos c USING (arquivo_raw)
        )
        SELECT b.eleicao, 1 AS cargo, 'br' AS abrangencia, b.arquivo_raw,
               b.nome || ': Brasil ' || b.votos || ' != soma UFs+exterior ' || u.soma AS detalhe
        FROM (SELECT * FROM votos WHERE abrangencia = 'br') b
        JOIN (SELECT eleicao, numero, sum(votos) AS soma FROM votos WHERE abrangencia <> 'br'
              GROUP BY ALL) u USING (eleicao, numero)
        WHERE b.votos <> u.soma
    """,
}


def verificar(con: duckdb.DuckDBPyConnection) -> list[Anomalia]:
    if not (consultas.tem(con, "snapshot_totais") and consultas.tem(con, "snapshot_candidatos")):
        return []
    saida = []
    for tipo, sql in CONSULTAS.items():
        for eleicao, cargo, abrangencia, arquivo_raw, detalhe in con.execute(sql).fetchall():
            saida.append(Anomalia(tipo, eleicao, cargo, abrangencia, arquivo_raw, detalhe))
    return saida
