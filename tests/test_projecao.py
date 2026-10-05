"""Modelo de projeção com cenários sintéticos de resposta conhecida."""

import numpy as np
import pandas as pd
import pytest

from apuracao.projecao.modelo import Parametros, projetar


def mundo(n=400, swing=0.3, razao=0.95, seed=1):
    """Municípios em 2 regiões/4 UFs; verdade do 2º turno = 1º turno + deslocamento uniforme."""
    rng = np.random.default_rng(seed)
    uf = np.array(["aa", "bb", "cc", "dd"])[rng.integers(0, 4, n)]
    regiao = np.where(np.isin(uf, ["aa", "bb"]), "R1", "R2")
    vv1 = rng.integers(2_000, 200_000, n).astype(float)
    p1 = rng.uniform(0.2, 0.8, n)
    a1, b1 = vv1 * 0.9 * p1, vv1 * 0.9 * (1 - p1)  # 10% em outros candidatos
    p2 = 1 / (1 + np.exp(-(np.log(p1 / (1 - p1)) + swing)))
    vv2 = vv1 * razao
    base = pd.DataFrame({"chave": [f"m{i}" for i in range(n)], "uf": uf, "regiao": regiao,
                         "a1": a1, "b1": b1, "vv1": vv1})
    verdade = pd.DataFrame({"chave": base["chave"], "a2": vv2 * p2, "b2": vv2 * (1 - p2)})
    return base, verdade


def pct_verdade(v):
    return 100 * v["a2"].sum() / (v["a2"].sum() + v["b2"].sum())


def observar(verdade, frac):
    o = verdade.copy()
    o["frac"] = frac
    o["a2"] *= frac
    o["b2"] *= frac
    return o


def test_tudo_apurado_e_exato():
    base, v = mundo()
    r = projetar(base, observar(v, 1.0))
    assert r.pct_a == pytest.approx(pct_verdade(v))
    assert r.pct_a_sup - r.pct_a_inf < 1e-9 and r.pct_contado == pytest.approx(100)


def test_nada_contado_usa_1o_turno_com_faixa_larga():
    base, v = mundo()
    r = projetar(base, v.iloc[:0].assign(frac=[]))
    p1 = 100 * base["a1"].sum() / (base["a1"] + base["b1"]).sum()
    assert r.pct_contado == 0
    assert abs(r.pct_a - p1) < 1.5  # sem informação: ~ disputa direta do 1º turno
    assert r.pct_a_sup - r.pct_a_inf > 4


def test_metade_contada_recupera_deslocamento_uniforme():
    base, v = mundo(swing=0.3)
    frac = np.where(np.arange(len(v)) % 2 == 0, 1.0, 0.0)
    r = projetar(base, observar(v, frac))
    alvo = pct_verdade(v)
    assert abs(r.pct_a - alvo) < 0.3
    assert r.pct_a_inf <= alvo <= r.pct_a_sup
    assert 40 < r.pct_contado < 60


def test_parcial_extrapola_o_proprio_municipio():
    base, v = mundo(n=50)
    frac = np.ones(len(v))
    frac[0] = 0.4
    r = projetar(base, observar(v, frac), Parametros(fator_parcial=0.0, tau=0.0))
    assert r.pct_a == pytest.approx(pct_verdade(v), abs=1e-6)


def test_regiao_inteira_sem_dados_alarga_a_faixa():
    """R1 inteira apurada, R2 nada, e o deslocamento de R2 é bem diferente (0,1 x 0,5).
    O modelo não tem como saber o de R2: o que se exige é que a faixa reflita a
    variação típica entre regiões (sd_entre_min) e por isso cubra a verdade."""
    base, v1 = mundo(swing=0.1, seed=2)
    _, v2 = mundo(swing=0.5, seed=2)
    em_r1 = (base["regiao"] == "R1").to_numpy()
    v = v1.copy()
    v.loc[~em_r1, ["a2", "b2"]] = v2.loc[~em_r1, ["a2", "b2"]]
    obs = observar(v, np.where(em_r1, 1.0, 0.0))
    estreito = projetar(base, obs, Parametros(sd_entre_min={"regiao": 0.01, "uf": 0.01}, tau=0))
    largo = projetar(base, obs, Parametros(sd_entre_min={"regiao": 0.3, "uf": 0.1}, tau=0))
    assert not (estreito.pct_a_inf <= pct_verdade(v) <= estreito.pct_a_sup)  # sem variação entre grupos: erra com confiança
    assert largo.pct_a_inf <= pct_verdade(v) <= largo.pct_a_sup
    assert (largo.pct_a_sup - largo.pct_a_inf) > 3 * (estreito.pct_a_sup - estreito.pct_a_inf)


def test_sem_niveis_um_grupo_so():
    base, v = mundo()
    frac = np.where(np.arange(len(v)) % 3 == 0, 1.0, 0.0)
    r = projetar(base, observar(v, frac), Parametros(niveis=[]))
    assert abs(r.pct_a - pct_verdade(v)) < 0.5 and len(r.por_grupo) == 1


def test_mesma_semente_mesmo_resultado():
    base, v = mundo()
    o = observar(v, np.where(np.arange(len(v)) % 4 == 0, 1.0, 0.0))
    assert projetar(base, o).pct_a_inf == projetar(base, o).pct_a_inf


# --- backtest: simulação da chegada -------------------------------------------

from apuracao.projecao.backtest import observar as observar_chegada, ordens, resumo, rodar


def test_ordens_sao_permutacoes_e_respeitam_o_criterio():
    base, _ = mundo(n=200)
    base["regiao"] = np.where(base["uf"].isin(["aa", "bb"]), "Nordeste", "Sul")
    os_ = ordens(base, capitais={"m0", "m1"}, seed=0)
    for o in os_.values():
        assert sorted(o) == list(range(len(base)))
    assert set(os_["capitais_primeiro"][:2]) == {0, 1}
    ne = (base["regiao"] == "Nordeste").to_numpy()[os_["nordeste_por_ultimo"]]
    assert not ne[: (~ne).sum()].any()  # nenhum do Nordeste antes de acabar o Sul


def test_observar_corte_e_parciais():
    base, v = mundo(n=300)
    ordem = np.arange(len(base))
    obs = observar_chegada(base, v, ordem, 0.5, np.random.default_rng(0))
    peso_apurado = base["vv1"][obs["frac"] == 1].sum() / base["vv1"].sum()
    assert 0.45 < peso_apurado <= 0.5
    parciais = obs[(obs["frac"] > 0) & (obs["frac"] < 1)]
    assert len(parciais) > 0 and parciais["frac"].between(0.1, 0.9).all()
    assert (obs["a2"] <= v["a2"] + 1e-9).all()


def test_backtest_sintetico_cobre_e_supera_parcial_nacional():
    base, v = mundo(n=300, swing=0.2)
    base["regiao"] = np.where(base["uf"].isin(["aa", "bb"]), "Nordeste", "Sul")
    res = rodar(base, v, capitais=set(), params=Parametros(n_sim=200), sementes=range(1), cortes=(0.25, 0.5))
    assert res["cobre"].mean() >= 0.9
    assert res["erro"].abs().max() < res["erro_parcial_nacional"].abs().max()
    assert set(resumo(res).index) == {0.25, 0.5}
