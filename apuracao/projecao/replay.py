"""Replay de uma noite de 2º turno a partir dos snapshots: refaz a projeção de presidente com o
código do painel (base_ao_vivo, atual_ao_vivo, projetar), usando em cada momento só os
snapshots já coletados (ts_coleta <= momento).

uv run python -m apuracao.projecao.replay --dir-parquet data/noite-2t/replay/parquet \\
    --historico data/noite-2t/codespace/data/projecao/historico.jsonl

Serve para (1) conferir que a projeção gravada na noite é reproduzível a partir do dado bruto
e (2) testar parâmetros (tau) contra uma noite real, como o backtest --ordem-real faz com o
boletim de urna. A verdade é a fatia de A em A + B no último snapshot BR do 2º turno.

Momentos: as `marca` do histórico da projeção (quando --historico é dado, para comparar com
o que o painel mostrou) ou uma grade a cada --passo minutos.

Em 25/10/2026: diferença média de 0,02 pp entre o replay e o painel depois de 19h, e tau 0,02
foi o menor valor com 100% de cobertura (como na ordem real de 2022).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import pandas as pd

from apuracao.config import Config, carregar
from apuracao.projecao import historico
from apuracao.projecao.ao_vivo import PRESIDENTE, candidatos_2t, eleicao_base
from apuracao.projecao.backtest import resumo_por_contado
from apuracao.projecao.dados import atual_ao_vivo, base_ao_vivo
from apuracao.projecao.modelo import Parametros, parametros_presidente, projetar


class Noite:
    """Tabelas da noite (2º turno) e da base (1º turno) numa conexão DuckDB, com a view
    `snapshot_totais` recortável por horário de coleta."""

    def __init__(self, dir_t2: Path, dir_base: Path, cod_t1: int, cod_t2: int):
        self.cod_t1, self.cod_t2 = cod_t1, cod_t2
        self.con = duckdb.connect()
        tot = "read_parquet('{}/snapshot_totais/*.parquet', union_by_name=true)"
        cand = "read_parquet('{}/snapshot_candidatos/*.parquet', union_by_name=true)"
        self.con.execute(f"""
            CREATE TABLE tot AS
            SELECT * FROM {tot.format(dir_base)} WHERE eleicao = {int(cod_t1)} AND cargo = {PRESIDENTE}
            UNION ALL BY NAME
            SELECT * FROM {tot.format(dir_t2)} WHERE eleicao = {int(cod_t2)} AND cargo = {PRESIDENTE}
        """)
        self.con.execute(f"""
            CREATE TABLE snapshot_candidatos AS
            SELECT * FROM {cand.format(dir_base)} WHERE eleicao = {int(cod_t1)}
            UNION ALL BY NAME
            SELECT * FROM {cand.format(dir_t2)} WHERE eleicao = {int(cod_t2)}
        """)
        self.ate(None)
        (self.num_a, self.nome_a), (self.num_b, self.nome_b) = candidatos_2t(self.con, cod_t2, PRESIDENTE, "br")
        self.base = base_ao_vivo(self.con, cod_t1, PRESIDENTE, self.num_a, self.num_b)

    def ate(self, momento: pd.Timestamp | str | None) -> None:
        """Deixa visíveis só os snapshots do 2º turno coletados até `momento` (UTC); None = todos."""
        filtro = "" if momento is None else \
            f"WHERE eleicao = {int(self.cod_t1)} OR ts_coleta <= TIMESTAMP '{pd.Timestamp(momento)}'"
        self.con.execute(f"CREATE OR REPLACE VIEW snapshot_totais AS SELECT * FROM tot {filtro}")

    def verdade(self) -> tuple[float, float]:
        """(fatia de A em A + B, % de seções) no último snapshot BR do 2º turno."""
        self.ate(None)
        linhas = self.con.execute(f"""
            WITH ult AS (SELECT arquivo_raw, pct_secoes FROM tot WHERE eleicao = {int(self.cod_t2)} AND abrangencia = 'br'
                         ORDER BY ts_tse DESC, ts_coleta DESC LIMIT 1)
            SELECT numero, votos, pct_secoes FROM snapshot_candidatos JOIN ult USING (arquivo_raw)
            WHERE numero IN ({self.num_a}, {self.num_b})
        """).fetchall()
        votos = {n: v for n, v, _ in linhas}
        return 100 * votos[self.num_a] / (votos[self.num_a] + votos[self.num_b]), float(linhas[0][2])

    def momentos(self, passo_min: int) -> list[pd.Timestamp]:
        ini, fim = self.con.execute(
            f"SELECT min(ts_coleta), max(ts_coleta) FROM tot WHERE eleicao = {int(self.cod_t2)} AND tpabr = 'mu'"
        ).fetchone()
        return list(pd.date_range(pd.Timestamp(ini).ceil(f"{passo_min}min"), fim, freq=f"{passo_min}min"))

    def projetar_em(self, momento, params: Parametros):
        self.ate(momento)
        return projetar(self.base, atual_ao_vivo(self.con, self.cod_t2, PRESIDENTE, self.num_a, self.num_b), params)


def rodar(noite: Noite, momentos: list, params: Parametros, verdade: float) -> pd.DataFrame:
    """Uma linha por momento, nas colunas do backtest (resumo_por_contado funciona igual)."""
    linhas = []
    for m in momentos:
        r = noite.projetar_em(m, params)
        linhas.append({
            "momento": pd.Timestamp(m), "pct_contado": r.pct_contado, "verdade": verdade,
            "projecao": r.pct_a, "inf": r.pct_a_inf, "sup": r.pct_a_sup,
            "erro": r.pct_a - verdade, "largura": r.pct_a_sup - r.pct_a_inf,
            "cobre": r.pct_a_inf <= verdade <= r.pct_a_sup,
        })
    return pd.DataFrame(linhas)


def folga_minima(res: pd.DataFrame) -> float:
    """Menor distância da verdade à borda da faixa, em fração da meia-largura (< 0: fora)."""
    dentro = pd.concat([res["verdade"] - res["inf"], res["sup"] - res["verdade"]], axis=1).min(axis=1)
    return float((dentro / (res["largura"] / 2)).min())


def comparar(res: pd.DataFrame, hist: pd.DataFrame) -> pd.DataFrame:
    """Replay x painel, momento a momento (hist = historico.ler, momentos = suas marcas)."""
    h = hist.assign(momento=pd.to_datetime(hist["marca"]))
    d = res.merge(h[["momento", "pct_contado", "pct_a", "pct_a_inf", "pct_a_sup"]],
                  on="momento", suffixes=("", "_painel"))
    return pd.DataFrame({
        "momento": d["momento"], "painel_com_dados": d["pct_contado_painel"] > 0,
        "dif_contado": d["pct_contado"] - d["pct_contado_painel"],
        "dif_projecao": d["projecao"] - d["pct_a"], "dif_inf": d["inf"] - d["pct_a_inf"],
        "dif_sup": d["sup"] - d["pct_a_sup"],
    })


def _eleicoes(cfg: Config, eleicao_id: str) -> tuple[int, int]:
    e = cfg.eleicao_por_id(eleicao_id)
    base = eleicao_base(cfg, e)
    if e.turno != 2 or base is None:
        raise SystemExit(f"{eleicao_id}: precisa ser um 2º turno com 1º turno de base no config")
    return base.codigo, e.codigo


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.projecao.replay")
    ap.add_argument("--dir-parquet", type=Path, required=True, help="tabelas com os snapshots da noite")
    ap.add_argument("--dir-parquet-base", type=Path, default=Path("data/parquet"),
                    help="tabelas com o 1º turno por município (base)")
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    ap.add_argument("--eleicao", default="2026-t2-federal")
    ap.add_argument("--historico", type=Path, help="historico.jsonl da noite: usa suas marcas e compara")
    ap.add_argument("--passo", type=int, default=3,
                    help="sem --historico: minutos entre momentos; com: usa 1 de cada N marcas")
    ap.add_argument("--taus", default="0.02")
    ap.add_argument("--n-sim", type=int, default=1000, help="1000 = o mesmo do painel")
    ap.add_argument("--verdade", type=float,
                    help="fatia final de A em A + B (%%), se as tabelas não chegam a 100%% das seções")
    args = ap.parse_args(argv)
    pd.set_option("display.width", 160)

    cod_t1, cod_t2 = _eleicoes(carregar(args.config), args.eleicao)
    noite = Noite(args.dir_parquet, args.dir_parquet_base, cod_t1, cod_t2)
    verdade, pct_secoes = noite.verdade()
    if args.verdade is not None:
        verdade = args.verdade
    elif pct_secoes < 100:
        print(f"aviso: o último snapshot BR tem {pct_secoes:.2f}% das seções; a verdade pode mudar um pouco "
              "(use --verdade com o resultado final)")
    hist = historico.ler(args.historico, cod_t2) if args.historico else pd.DataFrame()
    if not hist.empty:
        hist = hist[hist["abrangencia"] == "br"].iloc[:: args.passo]
        momentos = list(pd.to_datetime(hist["marca"]))
    else:
        momentos = noite.momentos(args.passo)
    print(f"{noite.nome_a} ({noite.num_a}) x {noite.nome_b} ({noite.num_b}) | verdade {verdade:.3f} | "
          f"{len(momentos)} momentos | base: {len(noite.base)} municípios")

    for tau in (float(t) for t in args.taus.split(",")):
        res = rodar(noite, momentos, parametros_presidente(tau=tau, n_sim=args.n_sim), verdade)
        print(f"\n=== tau = {tau} | cobertura {res['cobre'].mean():.0%} | folga mínima {folga_minima(res):.2f} | "
              f"erro absoluto médio {res['erro'].abs().mean():.2f} pp")
        print(resumo_por_contado(res).to_string())
        if not hist.empty and tau == parametros_presidente().tau:
            d = comparar(res, hist)
            c = d[d["painel_com_dados"]]  # antes disso o painel ainda não tinha municípios do 2º turno
            print(f"replay x painel (tau do painel, {len(c)} de {len(d)} momentos com dados no painel): "
                  f"|dif projeção| média {c['dif_projecao'].abs().mean():.3f}, máx {c['dif_projecao'].abs().max():.3f}; "
                  f"% contado diferente em {(c['dif_contado'].abs() > 0.001).sum()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
