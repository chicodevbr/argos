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

Ordem real (--ordem-real): com o boletim de urna do 2º turno carregado
(carga --boletim ANO:2), cada seção entra no horário em que o boletim chegou ao TSE.
O observado de cada município é a soma das seções já recebidas, e frac é a fração das
suas seções recebidas: sem ordem sorteada e sem supor que as parciais representam o
município. Na noite de 25/10/2026 o erro real foi 5-8x o das ordens sorteadas.
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

from apuracao.projecao.dados import base_historica, disputas_2o_turno, verdade_historica
from apuracao.projecao.modelo import Parametros, parametros_governador, parametros_presidente, projetar

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
          sementes: range = range(3), cortes=CORTES, excluir: tuple[str, ...] = ()) -> pd.DataFrame:
    alvo = 100 * verdade["a2"].sum() / (verdade["a2"].sum() + verdade["b2"].sum())
    linhas = []
    for semente in sementes:
        rng = np.random.default_rng(1000 + semente)
        for nome, ordem in ordens(base, capitais, semente).items():
            if nome in excluir:
                continue
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


FAIXAS_CONTADO = [0, 10, 15, 25, 45, 70, 95, 100.01]  # % contado (pct_contado), para comparar ordens


def secoes_boletim(dir_parquet: Path, ano: int, num_a: int, num_b: int) -> pd.DataFrame:
    """Uma linha por seção do 2º turno: chave, ts (chegada; antes das 17h conta como 17h,
    como em noite.py), a, b (votos de A e B)."""
    arq = Path(dir_parquet) / "hist_boletim" / f"{ano}_t2.parquet"
    return duckdb.connect().execute(f"""
        SELECT uf || cod_mun AS chave,
               greatest(min(recebido), CAST(CAST(min(recebido) AS DATE) || ' 17:00:00' AS TIMESTAMP)) AS ts,
               coalesce(sum(votos) FILTER (tipo = 'Nominal' AND numero = $a), 0) AS a,
               coalesce(sum(votos) FILTER (tipo = 'Nominal' AND numero = $b), 0) AS b
        FROM read_parquet('{arq}')
        WHERE recebido IS NOT NULL
        GROUP BY uf, cod_mun, zona, secao
    """, {"a": num_a, "b": num_b}).df()


def observar_real(secoes: pd.DataFrame, ts: pd.Timestamp) -> pd.DataFrame:
    """Município a município, o que tinha chegado até `ts`: frac = seções recebidas / seções
    do município; a2, b2 = votos dessas seções. Só municípios com alguma seção recebida."""
    total = secoes.groupby("chave").size()
    ja = secoes[secoes["ts"] <= ts].groupby("chave").agg(n=("a", "size"), a2=("a", "sum"), b2=("b", "sum"))
    ja["frac"] = ja["n"] / total.reindex(ja.index)
    return ja.reset_index()[["chave", "frac", "a2", "b2"]]


def rodar_real(base: pd.DataFrame, secoes: pd.DataFrame, params: Parametros,
               passo_min: int = 1, depois_99_min: int = 10) -> pd.DataFrame:
    """Projeção a cada `passo_min` minutos da noite real, até `depois_99_min` depois de 99%
    das seções recebidas (o resto da noite é a cauda, com projeção já exata, e só inflaria a
    cobertura). A verdade é o total do próprio boletim (para onde o observado converge)."""
    alvo = 100 * secoes["a"].sum() / (secoes["a"].sum() + secoes["b"].sum())
    fim = secoes["ts"].quantile(0.99) + pd.Timedelta(minutes=depois_99_min)
    horarios = pd.date_range(secoes["ts"].min().floor(f"{passo_min}min"),
                             fim.ceil(f"{passo_min}min"), freq=f"{passo_min}min")
    linhas = []
    for ts in horarios:
        obs = observar_real(secoes, ts)
        if obs.empty:
            continue
        r = projetar(base, obs, params)
        ingenuo = 100 * obs["a2"].sum() / max(obs["a2"].sum() + obs["b2"].sum(), 1)
        linhas.append({
            "ordem": "real", "ts": ts, "pct_contado": r.pct_contado,
            "pct_secoes": 100 * (secoes["ts"] <= ts).mean(), "verdade": alvo,
            "projecao": r.pct_a, "inf": r.pct_a_inf, "sup": r.pct_a_sup,
            "erro": r.pct_a - alvo, "largura": r.pct_a_sup - r.pct_a_inf,
            "erro_parcial_nacional": ingenuo - alvo,
            "cobre": r.pct_a_inf <= alvo <= r.pct_a_sup,
        })
    return pd.DataFrame(linhas)


def resumo_por_contado(res: pd.DataFrame) -> pd.DataFrame:
    """Como `resumo`, mas por faixa de % contado: compara ordens com eixos diferentes
    (corte sorteado x horário real)."""
    faixa = pd.cut(res["pct_contado"], FAIXAS_CONTADO, right=False)
    return res.groupby(faixa, observed=True).agg(
        n=("erro", "size"), erro_medio=("erro", lambda e: e.abs().mean()),
        erro_max=("erro", lambda e: e.abs().max()), vies=("erro", "mean"),
        largura=("largura", "mean"), cobertura=("cobre", "mean"),
    ).round(3)


def resumo(res: pd.DataFrame) -> pd.DataFrame:
    return res.groupby("corte").agg(
        erro_medio=("erro", lambda e: e.abs().mean()), erro_max=("erro", lambda e: e.abs().max()),
        erro_max_parcial_nacional=("erro_parcial_nacional", lambda e: e.abs().max()),
        largura=("largura", "mean"), cobertura=("cobre", "mean"),
    ).round(3)


def rodar_governador(dir_parquet: Path, ano: int, params: Parametros, sementes: range = range(3)) -> pd.DataFrame:
    """Uma UF por vez (cada uma é uma eleição); grupos = porte do município dentro da UF."""
    capitais = capitais_de(dir_parquet)
    partes = []
    for uf, a, b in disputas_2o_turno(dir_parquet, ano, 3):
        base = base_historica(dir_parquet, ano, 3, a, b, uf=uf)
        verdade = verdade_historica(dir_parquet, ano, 3, a, b, uf=uf)
        res = rodar(base, verdade, capitais, params, sementes,
                    excluir=("nordeste_por_ultimo",))
        partes.append(res.assign(uf=uf, municipios=len(base)))
    return pd.concat(partes, ignore_index=True)


def medir_governador(dir_parquet: Path, ano: int, ufs: list[str] | None = None) -> tuple[float, float]:
    """(sd do deslocamento entre UFs, sd entre portes dentro da UF) no 2º turno de governador."""
    from apuracao.projecao.modelo import _logit, porte
    sw_uf, dev_porte = [], []
    for uf, a, b in disputas_2o_turno(dir_parquet, ano, 3):
        if ufs is not None and uf not in ufs:
            continue
        df = base_historica(dir_parquet, ano, 3, a, b, uf=uf).merge(
            verdade_historica(dir_parquet, ano, 3, a, b, uf=uf), on="chave")
        df = df[(df.a1 + df.b1 > 0) & (df.a2 + df.b2 > 0)]
        sw = _logit(df.a2 / (df.a2 + df.b2)) - _logit(df.a1 / (df.a1 + df.b1))
        w = df.a2 + df.b2
        m = np.average(sw, weights=w)
        sw_uf.append(m)
        for _, idx in df.groupby(porte(df.vv1)).groups.items():
            if len(idx) >= 3:
                dev_porte.append((np.average(sw[idx], weights=w[idx]) - m, w[idx].sum()))
    d = np.array(dev_porte)
    return float(np.std(sw_uf, ddof=1)), float(np.sqrt(np.average(d[:, 0] ** 2, weights=d[:, 1])))


def validar_governador_uma_fora(dir_parquet: Path, ano: int, tau: float, n_sim: int,
                                folga: float = 1.3) -> pd.DataFrame:
    """Leave-one-out: para cada UF, mede os parâmetros nas outras e testa nela.
    `folga` reproduz a margem usada nos parâmetros finais (0,111 -> 0,15; 0,39 -> 0,5)."""
    disputas = disputas_2o_turno(dir_parquet, ano, 3)
    capitais = capitais_de(dir_parquet)
    partes = []
    for uf, a, b in disputas:
        outras = [u for u, *_ in disputas if u != uf]
        sd_uf, sd_porte = medir_governador(dir_parquet, ano, outras)
        params = Parametros(niveis=["porte"], sd_entre_min={"porte": folga * sd_porte},
                            sd_entre_min_razao={"porte": 0.05}, sd_prior_nacional=folga * sd_uf,
                            tau=tau, n_sim=n_sim)
        base = base_historica(dir_parquet, ano, 3, a, b, uf=uf)
        verdade = verdade_historica(dir_parquet, ano, 3, a, b, uf=uf)
        res = rodar(base, verdade, capitais, params, excluir=("nordeste_por_ultimo",))
        partes.append(res.assign(uf=uf, sd_uf=sd_uf, sd_porte=sd_porte))
    return pd.concat(partes, ignore_index=True)


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
    ap.add_argument("--cargo", choices=["presidente", "governador"], default="presidente")
    ap.add_argument("--uma-fora", action="store_true", help="governador: validação deixando uma UF de fora")
    ap.add_argument("--ordem-real", action="store_true",
                    help="presidente: ordem real de chegada (boletim de urna do 2º turno carregado)")
    args = ap.parse_args(argv)
    pd.set_option("display.width", 160)

    if args.cargo == "governador" and args.uma_fora:
        tau = float(args.taus.split(",")[0])
        res = validar_governador_uma_fora(args.dir_parquet, args.ano, tau, args.n_sim)
        print(f"\n=== governador {args.ano}, uma UF de fora | tau = {tau} | cobertura geral "
              f"{res['cobre'].mean():.0%} | erro absoluto médio {res['erro'].abs().mean():.2f} pp")
        print(resumo(res).to_string())
        print(res.groupby("uf").agg(sd_uf=("sd_uf", "first"), sd_porte=("sd_porte", "first"),
                                    erro_max=("erro", lambda e: e.abs().max()),
                                    cobertura=("cobre", "mean")).round(3).to_string())
        return 0

    if args.cargo == "governador":
        for tau in (float(t) for t in args.taus.split(",")):
            res = rodar_governador(args.dir_parquet, args.ano, parametros_governador(tau=tau, n_sim=args.n_sim))
            print(f"\n=== governador {args.ano} | tau = {tau} | cobertura geral {res['cobre'].mean():.0%} | "
                  f"erro absoluto médio {res['erro'].abs().mean():.2f} pp")
            print(resumo(res).to_string())
            print("\npor UF:")
            print(res.groupby("uf").agg(
                municipios=("municipios", "first"), verdade=("verdade", "first"),
                erro_max=("erro", lambda e: e.abs().max()),
                erro_max_parcial=("erro_parcial_nacional", lambda e: e.abs().max()),
                cobertura=("cobre", "mean"), largura_25=("largura", lambda l: l[res.loc[l.index, "corte"] == 0.25].mean()),
            ).round(2).to_string())
        return 0

    base = base_historica(args.dir_parquet, args.ano, 1, args.num_a, args.num_b)
    if args.ordem_real:
        secoes = secoes_boletim(args.dir_parquet, args.ano, args.num_a, args.num_b)
        for tau in (float(t) for t in args.taus.split(",")):
            res = rodar_real(base, secoes, parametros_presidente(tau=tau, n_sim=args.n_sim))
            print(f"\n=== ordem real {args.ano} | tau = {tau} | cobertura geral {res['cobre'].mean():.0%} | "
                  f"erro absoluto médio {res['erro'].abs().mean():.2f} pp | verdade {res['verdade'].iloc[0]:.3f}")
            print(resumo_por_contado(res).to_string())
        return 0
    verdade = verdade_historica(args.dir_parquet, args.ano, 1, args.num_a, args.num_b)
    capitais = capitais_de(args.dir_parquet)
    for tau in (float(t) for t in args.taus.split(",")):
        res = rodar(base, verdade, capitais, parametros_presidente(tau=tau, n_sim=args.n_sim))
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
