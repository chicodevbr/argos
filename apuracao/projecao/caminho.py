"""Caminho da apuração (presidente, 2º turno): como a contagem oficial deve evoluir até o
fim, e se e quando o candidato que está atrás deve passar à frente (virada).

A projeção (modelo.py) diz onde a contagem termina. Aqui se estima o caminho até lá:
- Ritmo de cada UF: quanto das seções a UF totalizou nos últimos `janela_min` minutos
  (padrão 30), medido nos arquivos de UF (coletados a cada ~30 s).
- O que falta em cada UF chega no ritmo atual dela, em linha reta até 100%. O ritmo é
  remedido a cada atualização. Não há registro de % apurado por horário em 2022 para
  calibrar um formato de curva; uma queda exponencial foi testada e descartada: trata UFs
  que estão no começo (ainda acelerando) como se já desacelerassem, e joga o fim delas
  para depois da madrugada. Consequência da reta: no começo da noite (ritmo ainda subindo)
  o horário previsto tende a sair tarde; perto do fim (cauda lenta), cedo. UF sem ritmo
  medido (ainda não começou ou parada na janela) usa a mediana das outras, com mais
  incerteza, e só (re)começa depois de um atraso sorteado entre agora e o tempo já
  decorrido de apuração: se ficou parada até aqui, pode continuar parada por um tempo.
- Votos que faltam na UF = votos finais da UF na projeção (cada simulação) - já contados.
  Supõe que, dentro da UF, o que falta chega em proporção: a ordem capital/interior
  dentro da UF não é modelada. A ordem ENTRE UFs (ex.: Nordeste mais lento) é.
- Incerteza do ritmo: multiplicador lognormal comum a todas as UFs e outro por UF.

Validação na noite REAL do 2º turno de 2022 (reconstruída do boletim de urna, ver
noite.py; virada real às 18h44): com 11% das seções, previu 19h13 (18h19-20h35); com 21%,
18h38; 32%, 18h33; 44%, 18h34; 59%, 18h39. Ou seja, de 5 a 11 min cedo a partir de 20%
das seções, e a curva dos 15-60 min seguintes dentro da faixa em todos os cortes a partir
de 11%. Com 3% das seções erra por horas (o painel esconde o horário abaixo de 10%).

Validação anterior, em simulação:
- Dado o progresso real de cada UF, a curva prevista erra no máximo ~0,15 ponto (noite do
  ensaio geral): a composição vinda da projeção está certa; o erro é todo de ritmo.
- Mundo sintético "como 2022" (todas as UFs em paralelo, em curva S, Nordeste e Norte
  mais lentos): curva dentro da faixa em ~100% dos casos, erro médio 0,14 ponto; horário da
  virada dentro do intervalo de 80% em quase todos, mas adiantado ~20-30 min (a reta não
  vê a cauda lenta do fim).
- Ensaio geral (regiões apuradas em blocos, uma depois da outra): o horário não é
  previsível; o painel esconde o horário enquanto houver UF sem ritmo medido.
`o_que_falta` não depende de ritmo e vale em qualquer caso.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

COMPLETA = 0.9999  # fração de seções a partir da qual a UF é tratada como apurada


@dataclass
class Caminho:
    agora: datetime              # horário (UTC, sem fuso) dos dados mais recentes de UF
    pct_a_atual: float           # fatia de A nos válidos já contados (soma das UFs)
    pct_secoes_atual: float
    curva: pd.DataFrame          # ts, pct_secoes, pct_a, pct_a_inf, pct_a_sup (contagem prevista)
    prob_virada: float           # simulações em que o líder atual da contagem termina atrás
    virada_ts: datetime | None   # mediana do horário da virada (entre as simulações com virada)
    virada_ts_inf: datetime | None
    virada_ts_sup: datetime | None
    virada_pct_secoes: float | None
    hora_99: datetime | None     # quando a contagem deve chegar a 99% das seções (mediana)
    ufs_sem_ritmo: list[str]


def series_uf(con: duckdb.DuckDBPyConnection, eleicao: int, cargo: int, num_a: int, num_b: int) -> pd.DataFrame:
    """Snapshots dos arquivos de UF: uf, ts (ts_tse), frac, secoes_total, a, b (votos contados)."""
    return con.execute(
        """
        SELECT t.abrangencia AS uf, t.ts_tse AS ts, coalesce(t.pct_secoes, 0) / 100 AS frac,
               any_value(t.secoes_total) AS secoes_total,
               coalesce(sum(c.votos) FILTER (c.numero = $a), 0) AS a,
               coalesce(sum(c.votos) FILTER (c.numero = $b), 0) AS b
        FROM snapshot_totais t JOIN snapshot_candidatos c USING (arquivo_raw)
        WHERE t.eleicao = $eleicao AND t.cargo = $cargo AND t.tpabr = 'uf'
        GROUP BY t.abrangencia, t.ts_tse, t.pct_secoes, t.arquivo_raw
        ORDER BY uf, ts
        """,
        {"eleicao": eleicao, "cargo": cargo, "a": num_a, "b": num_b},
    ).df()


def inicio_apuracao(serie: pd.DataFrame) -> pd.Timestamp:
    """Último horário em que nenhuma UF tinha seção totalizada (ou o primeiro dado, se já havia)."""
    com = serie[serie["frac"] > 0]
    if com.empty:
        return serie["ts"].max()
    antes = serie[serie["ts"] < com["ts"].min()]
    return antes["ts"].max() if len(antes) else com["ts"].min()


def _ritmos(serie: pd.DataFrame, agora: pd.Timestamp, janela_min: float | None) -> pd.DataFrame:
    """Por UF: estado atual e k = ritmo / fração que falta (fração do que falta apurada por minuto;
    inf = UF completa, NaN = sem ritmo)."""
    linhas = []
    t0 = inicio_apuracao(serie)
    for uf, g in serie.groupby("uf"):
        g = g.sort_values("ts")
        atual = g.iloc[-1]
        p = float(atual["frac"])
        if janela_min is None:  # ritmo médio desde o início da apuração
            dt, p_ref = (agora - t0).total_seconds() / 60, 0.0
        else:
            antes = g[g["ts"] <= agora - pd.Timedelta(minutes=janela_min)]
            ref = antes.iloc[-1] if len(antes) else g.iloc[0]
            dt, p_ref = (agora - ref["ts"]).total_seconds() / 60, float(ref["frac"])
        k = np.nan
        if p >= COMPLETA:
            k = np.inf
        elif dt >= 2 and p > p_ref:
            k = (p - p_ref) / dt / (1 - p)
        linhas.append({"uf": uf, "frac": p, "secoes_total": float(atual["secoes_total"] or 0),
                       "a": float(atual["a"]), "b": float(atual["b"]), "k": k})
    return pd.DataFrame(linhas)


def prever(sim_a: np.ndarray, sim_b: np.ndarray, ufs: list[str], serie: pd.DataFrame,
           janela_min: float | None = 30, passo_min: float = 2, horizonte_h: float = 8,
           sd_ritmo_comum: float = 0.3, sd_ritmo_uf: float = 0.3, sd_ritmo_sem_medida: float = 0.6,
           seed: int | None = 0) -> Caminho | None:
    """
    sim_a, sim_b: votos finais simulados por UF (n_sim x len(ufs)), da projeção.
    serie: saída de `series_uf`. Devolve None sem dados suficientes para medir ritmo.
    """
    if serie.empty:
        return None
    agora = serie["ts"].max()
    r = _ritmos(serie, agora, janela_min).set_index("uf")
    r = r.reindex([u for u in ufs if u in r.index])
    if r.empty:
        return None
    col = [ufs.index(u) for u in r.index]
    fa, fb = sim_a[:, col], sim_b[:, col]
    n, nu = fa.shape
    rng = np.random.default_rng(seed)

    ca, cb = r["a"].to_numpy(), r["b"].to_numpy()
    p = r["frac"].to_numpy()
    st = r["secoes_total"].to_numpy()
    k = r["k"].to_numpy()
    medido = np.isfinite(k)
    sem_ritmo = r["k"].isna().to_numpy()
    k_ref = float(np.median(k[medido])) if medido.any() else np.nan
    if not np.isfinite(k_ref):
        return None  # nenhuma UF com ritmo medido (todas completas ou paradas)
    k = np.where(sem_ritmo, k_ref, k)
    sd = np.where(sem_ritmo, sd_ritmo_sem_medida, sd_ritmo_uf)
    mult = np.exp(rng.normal(0, sd_ritmo_comum, (n, 1)) + rng.normal(0, 1, (n, nu)) * sd)
    k_sim = np.where(np.isinf(k), np.inf, k * mult)
    decorrido = max((agora - inicio_apuracao(serie)).total_seconds() / 60, 30)
    atraso = np.where(sem_ritmo, rng.uniform(0, decorrido, (n, nu)), 0.0)

    ra, rb = np.clip(fa - ca, 0, None), np.clip(fb - cb, 0, None)
    completa = p >= COMPLETA
    ra[:, completa], rb[:, completa] = 0.0, 0.0

    passos = np.arange(0, horizonte_h * 60 + passo_min, passo_min)
    a0, b0 = ca.sum(), cb.sum()
    lider_a = a0 >= b0
    pct_a = np.empty((len(passos), n))
    pct_sec = np.empty((len(passos), n))
    for j, tau in enumerate(passos):
        with np.errstate(invalid="ignore"):
            g = np.where(np.isinf(k_sim), 1.0, np.minimum(1.0, k_sim * np.maximum(0.0, tau - atraso)))
        at, bt = a0 + (ra * g).sum(axis=1), b0 + (rb * g).sum(axis=1)
        pct_a[j] = 100 * at / np.maximum(at + bt, 1)
        pct_sec[j] = 100 * (st * (p + (1 - p) * g)).sum(axis=1) / max(st.sum(), 1)

    final_a = a0 + ra.sum(axis=1)
    final_b = b0 + rb.sum(axis=1)
    virou = (final_a >= final_b) != lider_a
    # primeiro passo em que o líder da contagem muda (por simulação)
    troca = (pct_a >= 50) != lider_a
    tem_troca = troca.any(axis=0)
    idx_troca = np.where(tem_troca, troca.argmax(axis=0), -1)

    ts = [agora + pd.Timedelta(minutes=float(m)) for m in passos]
    curva = pd.DataFrame({
        "ts": ts,
        "pct_secoes": np.median(pct_sec, axis=1),
        "pct_a": np.median(pct_a, axis=1),
        "pct_a_inf": np.quantile(pct_a, 0.05, axis=1),
        "pct_a_sup": np.quantile(pct_a, 0.95, axis=1),
    })
    fim = np.flatnonzero(curva["pct_secoes"].to_numpy() >= 99.9)
    if len(fim):
        curva = curva.iloc[: fim[0] + 1]
    acima_99 = np.flatnonzero(np.median(pct_sec, axis=1) >= 99)

    vt = vi = vs = vp = None
    com = virou & tem_troca
    if com.any():
        minutos = passos[idx_troca[com]]
        # arredonda ao segundo: quantis dão frações de minuto e to_pydatetime avisaria (log do modelo)
        vt, vi, vs = ((agora + pd.Timedelta(minutes=float(np.quantile(minutos, q)))).round("s") for q in (0.5, 0.1, 0.9))
        vp = float(np.median(pct_sec[idx_troca[com], np.flatnonzero(com)]))

    return Caminho(
        agora=agora.to_pydatetime(),
        pct_a_atual=float(100 * a0 / max(a0 + b0, 1)),
        pct_secoes_atual=float(100 * (st * p).sum() / max(st.sum(), 1)),
        curva=curva,
        prob_virada=float(virou.mean()),
        virada_ts=vt.to_pydatetime() if vt is not None else None,
        virada_ts_inf=vi.to_pydatetime() if vi is not None else None,
        virada_ts_sup=vs.to_pydatetime() if vs is not None else None,
        virada_pct_secoes=vp,
        hora_99=ts[acima_99[0]].to_pydatetime() if len(acima_99) else None,
        ufs_sem_ritmo=[u for u, s in zip(r.index, sem_ritmo) if s],
    )


def o_que_falta(sim_a: np.ndarray, sim_b: np.ndarray, ufs: list[str], serie: pd.DataFrame) -> pd.DataFrame:
    """Votos válidos que faltam contar (projeção final - contado nos arquivos de UF) e a fatia de A
    neles, por região e no total ("Brasil"). Não depende de ritmo: sai só da projeção.

    Colunas: regiao, votos_falta (mediana), pct_a_falta (mediana), pct_a_falta_inf, pct_a_falta_sup.
    """
    from apuracao.modelo.ea12 import REGIAO
    if serie.empty:
        return pd.DataFrame()
    ult = serie.sort_values("ts").groupby("uf").last()
    ca = np.array([float(ult["a"].get(u, 0.0)) for u in ufs])
    cb = np.array([float(ult["b"].get(u, 0.0)) for u in ufs])
    ra, rb = np.clip(sim_a - ca, 0, None), np.clip(sim_b - cb, 0, None)
    regioes = np.array([REGIAO.get(u, "?") for u in ufs])
    linhas = []
    for nome in [*dict.fromkeys(sorted(regioes)), "Brasil"]:
        m = np.ones(len(ufs), bool) if nome == "Brasil" else regioes == nome
        fa, fb = ra[:, m].sum(axis=1), rb[:, m].sum(axis=1)
        tot = fa + fb
        pa = 100 * fa / np.where(tot > 0, tot, 1)
        linhas.append({"regiao": nome, "votos_falta": float(np.median(tot)),
                       "pct_a_falta": float(np.median(pa)), "pct_a_falta_inf": float(np.quantile(pa, 0.05)),
                       "pct_a_falta_sup": float(np.quantile(pa, 0.95))})
    df = pd.DataFrame(linhas)
    a0, b0 = ca.sum(), cb.sum()
    total = df.iloc[-1]["votos_falta"]
    # fatia do que falta que A precisa para empatar: a0 + x*R = b0 + (1-x)*R
    df.attrs["pct_a_precisa"] = float(100 * (b0 - a0 + total) / (2 * total)) if total > 0 else None
    df.attrs["pct_a_contado"] = float(100 * a0 / (a0 + b0)) if a0 + b0 > 0 else None
    return df


def prever_ao_vivo(con: duckdb.DuckDBPyConnection, eleicao: int, proj, cargo: int = 1,
                   **kw) -> tuple[Caminho | None, pd.DataFrame]:
    """(caminho, o que falta) para a ProjecaoAoVivo de presidente `proj` (grupos 'regiao|uf')."""
    r = proj.r
    if r.sim_a_grupo is None:
        return None, pd.DataFrame()
    ufs = r.por_grupo["grupo"].str.split("|").str[-1].tolist()
    serie = series_uf(con, eleicao, cargo, proj.num_a, proj.num_b)
    return (prever(r.sim_a_grupo, r.sim_b_grupo, ufs, serie, **kw),
            o_que_falta(r.sim_a_grupo, r.sim_b_grupo, ufs, serie))


def viradas(horas: list[str], pct: list[float]) -> list[str]:
    """Horários em que a curva cruza 50% (líder muda)."""
    return [h for h, p0, p1 in zip(horas[1:], pct, pct[1:]) if (p0 >= 50) != (p1 >= 50)]
