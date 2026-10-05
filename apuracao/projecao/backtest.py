"""Backtest do modelo de projeção: 1º turno como base, 2º turno como verdade.

uv run python -m apuracao.projecao.backtest --ano 2022

Não há registro da ordem real de chegada dos votos por município em 2022, então ela
é simulada (CLAUDE.md: "simulando ordens de chegada diferentes"). Para cada ordem e
cada corte (fração do eleitorado já com dados):
- municípios antes do corte: apurados;
- os seguintes, até +10% do eleitorado: parciais, com fração de seções sorteada;
- o resto: não iniciados.
O observado de um parcial é a verdade final vezes a fração: supõe que as seções
já apuradas são representativas do município (limitação conhecida).
"""

from __future__ import annotations

import argparse
import itertools
import sys
from dataclasses import replace
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

from apuracao.projecao.dados import base_historica, verdade_historica
from apuracao.projecao.modelo import Parametros, projetar

CORTES = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90)
ORDEM_REGIOES = ["Sul", "Sudeste", "Centro-Oeste", "Norte", "Nordeste", "Exterior"]


def ordens(base: pd.DataFrame, capitais: set[str], seed: int) -> dict[str, np.ndarray]:
    """Índices de `base` na ordem em que os municípios 'chegam'."""
    rng = np.random.default_rng(seed)
    ruido = rng.random(len(base))
    reg = base["regiao"].map({r: i for i, r in enumerate(ORDEM_REGIOES)}).to_numpy()
    cap = base["chave"].isin(capitais).to_numpy()
    return {
        "aleatoria": rng.permutation(len(base)),
        "nordeste_por_ultimo": np.lexsort((ruido, reg)),
        "capitais_primeiro": np.lexsort((ruido, ~cap)),
        "grandes_primeiro": np.argsort(-base["vv1"].to_numpy() * (0.8 + 0.4 * ruido)),
        "pequenos_primeiro": np.argsort(base["vv1"].to_numpy() * (0.8 + 0.4 * ruido)),
    }


def observar(base: pd.DataFrame, verdade: pd.DataFrame, ordem: np.ndarray, corte: float,
             rng: np.random.Generator, faixa_parcial: float = 0.10) -> pd.DataFrame:
    v = base[["chave", "vv1"]].merge(verdade, on="chave", how="left").fillna(0.0)
    peso = v["vv1"].to_numpy()[ordem]
    acum = np.cumsum(peso) / peso.sum()
    frac = np.zeros(len(v))
    frac[ordem[acum <= corte]] = 1.0
    parciais = ordem[(acum > corte) & (acum <= corte + faixa_parcial)]
    frac[parciais] = rng.uniform(0.1, 0.9, len(parciais))
    return pd.DataFrame({"chave": v["chave"], "frac": frac,
                         "a2": v["a2"].to_numpy() * frac, "b2": v["b2"].to_numpy() * frac})


def rodar(base: pd.DataFrame, verdade: pd.DataFrame, capitais: set[str], params: Parametros,
          sementes: range = range(3), cortes=CORTES) -> pd.DataFrame:
    alvo = 100 * verdade["a2"].sum() / (verdade["a2"].sum() + verdade["b2"].sum())
    linhas = []
    for semente in sementes:
        rng = np.random.default_rng(1000 + semente)
        for nome, ordem in ordens(base, capitais, semente).items():
            for corte in cortes:
                obs = observar(base, verdade, ordem, corte, rng)
                r = projetar(base, obs, replace(params, seed=semente))
                # o que o CLAUDE.md proíbe: ler o percentual parcial nacional
                ingenuo = 100 * obs["a2"].sum() / max(obs["a2"].sum() + obs["b2"].sum(), 1)
                linhas.append({
                    "ordem": nome, "corte": corte, "semente": semente,
                    "pct_contado": r.pct_contado, "verdade": alvo, "projecao": r.pct_a,
                    "inf": r.pct_a_inf, "sup": r.pct_a_sup,
                    "erro": r.pct_a - alvo, "largura": r.pct_a_sup - r.pct_a_inf,
                    "erro_parcial_nacional": ingenuo - alvo,
                    "cobre": r.pct_a_inf <= alvo <= r.pct_a_sup,
                })
    return pd.DataFrame(linhas)


def resumo(res: pd.DataFrame) -> pd.DataFrame:
    return res.groupby("corte").agg(
        erro_medio=("erro", lambda e: e.abs().mean()), erro_max=("erro", lambda e: e.abs().max()),
        erro_max_parcial_nacional=("erro_parcial_nacional", lambda e: e.abs().max()),
        largura=("largura", "mean"), cobertura=("cobre", "mean"),
    ).round(3)


def capitais_de(dir_parquet: Path) -> set[str]:
    con = duckdb.connect()
    linhas = con.execute(
        f"SELECT uf || cod_tse FROM read_parquet('{Path(dir_parquet)}/municipios.parquet') WHERE capital"
    ).fetchall()
    return {l[0] for l in linhas}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.projecao.backtest")
    ap.add_argument("--ano", type=int, default=2022)
    ap.add_argument("--num-a", type=int, default=22, help="candidato A (2022: Bolsonaro)")
    ap.add_argument("--num-b", type=int, default=13, help="candidato B (2022: Lula)")
    ap.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    ap.add_argument("--taus", default="0,0.01,0.02,0.03,0.05")
    ap.add_argument("--n-sim", type=int, default=400)
    args = ap.parse_args(argv)

    base = base_historica(args.dir_parquet, args.ano, 1, args.num_a, args.num_b)
    verdade = verdade_historica(args.dir_parquet, args.ano, 1, args.num_a, args.num_b)
    capitais = capitais_de(args.dir_parquet)
    pd.set_option("display.width", 160)
    for tau in (float(t) for t in args.taus.split(",")):
        res = rodar(base, verdade, capitais, Parametros(tau=tau, n_sim=args.n_sim))
        print(f"\n=== tau = {tau} | cobertura geral {res['cobre'].mean():.0%} | "
              f"erro absoluto médio {res['erro'].abs().mean():.2f} pp")
        print(resumo(res).to_string())
        pior = res.loc[res["erro"].abs().idxmax()]
        print("erro máximo por ordem (modelo x parcial nacional):")
        print(res.groupby("ordem").agg(modelo=("erro", lambda e: e.abs().max()),
                                       parcial_nacional=("erro_parcial_nacional", lambda e: e.abs().max())).round(2).to_string())
        print(f"pior caso: {pior['ordem']} em {pior['corte']:.0%}: erro {pior['erro']:+.2f} pp "
              f"(faixa {pior['inf']:.2f}-{pior['sup']:.2f}, verdade {pior['verdade']:.2f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
