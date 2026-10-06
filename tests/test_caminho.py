"""Caminho da apuração: ritmo por UF, previsão de virada, curva de referência de 2022."""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from apuracao.config import carregar
from apuracao.modelo import consultas
from apuracao.projecao.ao_vivo import projetar_ao_vivo
from apuracao.projecao.caminho import prever, prever_ao_vivo, viradas
from tests.apoio_2t import montar_pq_2t

T0 = datetime(2026, 10, 25, 21, 0)
SEM_RUIDO = dict(sd_ritmo_comum=0, sd_ritmo_uf=0, sd_ritmo_sem_medida=0)


def serie(linhas):
    """linhas: (uf, minutos depois de T0, frac, a, b); 100 seções por UF."""
    return pd.DataFrame([{"uf": u, "ts": pd.Timestamp(T0) + pd.Timedelta(minutes=m), "frac": f,
                          "secoes_total": 100, "a": a, "b": b} for u, m, f, a, b in linhas])


def finais(n, *por_uf):
    """Votos finais iguais em todas as simulações: por_uf = (a, b) de cada UF."""
    a = np.tile([x for x, _ in por_uf], (n, 1)).astype(float)
    b = np.tile([y for _, y in por_uf], (n, 1)).astype(float)
    return a, b


def test_virada_no_horario_da_conta():
    # x: rápida (50%, ritmo 1%/min), A na frente; y: lenta (10%, 0,25%/min), B bem na frente.
    s = serie([("x", 0, 0.3, 180, 120), ("x", 20, 0.5, 300, 200),
               ("y", 0, 0.05, 15, 35), ("y", 20, 0.1, 30, 70)])
    a, b = finais(50, (600, 400), (300, 700))
    c = prever(a, b, ["x", "y"], s, janela_min=20, passo_min=1, **SEM_RUIDO)

    assert round(c.pct_a_atual, 6) == 55.0 and c.prob_virada == 1.0
    # conta: diferença A - B = 60 + 100*gx(t) - 360*gy(t); g = fração do que falta já apurada,
    # no ritmo atual: x termina em 50 min, y em 360 min
    t = np.arange(0, 600, 0.01)
    dif = 60 + 100 * np.minimum(1, t / 50) - 360 * np.minimum(1, t / 360)
    minuto = t[np.argmax(dif < 0)]
    previsto = (c.virada_ts - (T0 + pd.Timedelta(minutes=20))).total_seconds() / 60
    assert abs(previsto - minuto) <= 1
    assert c.virada_ts_inf == c.virada_ts == c.virada_ts_sup  # sem incerteza de ritmo
    # a curva termina no resultado final projetado, quando y termina
    assert abs(c.curva["pct_a"].iloc[-1] - 45.0) < 0.01
    # 99% nacional: x em 100% e y em 98% (0,1 + 0,9 t/360 = 0,98 -> t = 352)
    assert c.hora_99 == T0 + pd.Timedelta(minutes=20 + 352)


def test_sem_virada_quando_o_lider_termina_na_frente():
    s = serie([("x", 0, 0.3, 180, 120), ("x", 20, 0.5, 300, 200),
               ("y", 0, 0.05, 25, 25), ("y", 20, 0.1, 50, 50)])
    a, b = finais(50, (600, 400), (500, 500))
    c = prever(a, b, ["x", "y"], s, **SEM_RUIDO)
    assert c.prob_virada == 0 and c.virada_ts is None
    assert c.curva["pct_a"].min() > 50


def test_uf_sem_ritmo_usa_a_mediana_e_incerteza_vira_faixa():
    # z ainda não começou: usa o ritmo das outras
    s = serie([("x", 0, 0.3, 180, 120), ("x", 20, 0.5, 300, 200),
               ("y", 0, 0.05, 15, 35), ("y", 20, 0.1, 30, 70),
               ("z", 0, 0.0, 0, 0), ("z", 20, 0.0, 0, 0)])
    a, b = finais(400, (600, 400), (300, 700), (100, 100))
    c = prever(a, b, ["x", "y", "z"], s)
    assert c.ufs_sem_ritmo == ["z"]
    assert c.virada_ts_inf < c.virada_ts < c.virada_ts_sup
    assert (c.curva["pct_a_inf"] <= c.curva["pct_a"]).all() and (c.curva["pct_a"] <= c.curva["pct_a_sup"]).all()
    assert c.curva["pct_secoes"].is_monotonic_increasing


def test_uf_completa_nao_muda_e_sem_ritmo_nenhum_nao_preve():
    s = serie([("x", 0, 1.0, 600, 400), ("x", 20, 1.0, 600, 400)])
    a, b = finais(20, (600, 400))
    assert prever(a, b, ["x"], s) is None  # tudo apurado: nada a prever
    assert prever(a, b, ["x"], serie([])) is None


def test_viradas_por_rotulo():
    assert viradas(["17h00", "17h08", "18h44"], [57.3, 47.4, 50.01]) == ["17h08", "18h44"]


def test_ao_vivo_sem_arquivos_de_uf_nao_preve(tmp_path):
    pq = montar_pq_2t(tmp_path)
    cfg = carregar(Path(__file__).parent.parent / "config" / "eleicoes.toml")
    con = consultas.conectar(pq)
    p = projetar_ao_vivo(con, cfg, cfg.eleicao_por_id("2026-t2-federal"), 1, "br", n_sim=100)
    assert p.r.sim_a_grupo.shape == (100, 2)
    c, falta = prever_ao_vivo(con, 6258, p)
    assert c is None and falta.empty


def test_o_que_falta_por_regiao_e_quanto_precisa():
    from apuracao.projecao.caminho import o_que_falta
    # sp (Sudeste) contado 300 x 200 de 600 x 400; ba (Nordeste) contado 30 x 70 de 700 x 300
    s = serie([("sp", 20, 0.5, 300, 200), ("ba", 20, 0.1, 30, 70)])
    a, b = finais(10, (600, 400), (700, 300))
    f = o_que_falta(a, b, ["sp", "ba"], s).set_index("regiao")
    assert f.loc["Sudeste", "votos_falta"] == 500 and f.loc["Sudeste", "pct_a_falta"] == 60
    assert f.loc["Nordeste", "votos_falta"] == 900 and round(f.loc["Nordeste", "pct_a_falta"], 4) == round(100 * 670 / 900, 4)
    assert f.loc["Brasil", "votos_falta"] == 1400
    # A tem 330 x 270: precisa de (270 - 330 + 1400) / 2800 do que falta para empatar
    assert round(f.attrs["pct_a_precisa"], 6) == round(100 * 1340 / 2800, 6)
