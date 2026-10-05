"""Projeção durante a apuração, a partir das tabelas Parquet (usada pelo painel).

- Presidente: projeção nacional (todos os municípios e localidades do exterior),
  com grupos região -> UF; a tabela por UF sai de `por_grupo`.
- Governador: uma UF por vez, com grupos de porte dentro da UF.
A base é o 1º turno equivalente (mesmo ciclo e cargos, turno 1) já carregado por
município; os candidatos A e B são os dois do 2º turno (A = menor número), que têm o
mesmo número no 1º turno.
"""

from __future__ import annotations

from dataclasses import dataclass

import duckdb
import pandas as pd

from apuracao.config import Config, Eleicao
from apuracao.modelo import consultas
from apuracao.projecao.dados import atual_ao_vivo, base_ao_vivo
from apuracao.projecao.modelo import Projecao, parametros_governador, parametros_presidente, projetar

PRESIDENTE, GOVERNADOR = 1, 3


@dataclass
class ProjecaoAoVivo:
    num_a: int
    num_b: int
    nome_a: str
    nome_b: str
    r: Projecao
    municipios_base: int


def eleicao_base(cfg: Config, eleicao: Eleicao) -> Eleicao | None:
    """O 1º turno que serve de base para uma eleição de 2º turno."""
    for e in cfg.eleicao:
        if e.turno == 1 and e.ciclo == eleicao.ciclo and set(e.cargos) == set(eleicao.cargos) and e.codigo > 0:
            return e
    return None


def candidatos_2t(con: duckdb.DuckDBPyConnection, eleicao: int, cargo: int, abrangencia: str
                  ) -> list[tuple[int, str]]:
    """(número, nome) dos candidatos no snapshot mais recente da abrangência, por número."""
    df = con.execute(consultas.ULTIMOS_CANDIDATOS,
                     {"eleicao": eleicao, "cargo": cargo, "abrangencia": abrangencia}).df()
    return sorted((int(n), nome) for n, nome in zip(df["numero"], df["nome"]))


def projetar_ao_vivo(con: duckdb.DuckDBPyConnection, cfg: Config, eleicao: Eleicao, cargo: int,
                     abrangencia: str, n_sim: int = 1000) -> ProjecaoAoVivo | None:
    """Projeção para a abrangência, ou None quando não se aplica ou faltam dados."""
    if eleicao.turno != 2 or not consultas.tem(con, "snapshot_candidatos"):
        return None
    if cargo == PRESIDENTE and abrangencia != "br":
        return None
    if cargo == GOVERNADOR and len(abrangencia) != 2:
        return None
    base_e = eleicao_base(cfg, eleicao)
    cands = candidatos_2t(con, eleicao.codigo, cargo, abrangencia)
    if base_e is None or len(cands) != 2:
        return None
    (a, nome_a), (b, nome_b) = cands

    base = base_ao_vivo(con, base_e.codigo, cargo, a, b)
    if cargo == GOVERNADOR:
        base = base[base["uf"] == abrangencia]
    if base.empty:
        return None  # 1º turno por município não carregado
    atual = atual_ao_vivo(con, eleicao.codigo, cargo, a, b)
    params = (parametros_presidente if cargo == PRESIDENTE else parametros_governador)(n_sim=n_sim)
    return ProjecaoAoVivo(a, b, nome_a, nome_b, projetar(base, atual, params), len(base))


def projecao_por_uf(p: ProjecaoAoVivo) -> pd.DataFrame:
    """Presidente: projeção de A por UF (grupos 'regiao|uf')."""
    g = p.r.por_grupo.copy()
    g["uf"] = g["grupo"].str.split("|").str[-1]
    return g[["uf", "pct_a", "pct_a_inf", "pct_a_sup"]]
