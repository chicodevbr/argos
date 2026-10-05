"""Mapa: classificação nas faixas e montagem do gráfico com a malha de amostra."""

from pathlib import Path

import altair as alt
import numpy as np
import pandas as pd

from apuracao.app import mapa
from apuracao.carga.ibge import ler_malha

FIX = Path(__file__).parent / "fixtures" / "malha-amostra.json"


def test_classificar_fronteiras_e_sem_dado():
    v = pd.Series([10.0, 15.0, 17.99, 21.0, 30.0, np.nan])
    assert list(mapa.classificar(v, mapa.ABSTENCAO)) == ["< 15%", "15–18%", "15–18%", "21–24%", "≥ 27%", "sem dado"]
    var = pd.Series([-5.0, -1.0, 0.0, 0.99, 3.0])
    assert list(mapa.classificar(var, mapa.VARIACAO)) == [
        "cai 3 pp ou mais", "estável (±1 pp)", "estável (±1 pp)", "estável (±1 pp)", "sobe 3 pp ou mais"]


def test_escalas_tem_uma_cor_por_faixa():
    for e in (mapa.ABSTENCAO, mapa.VARIACAO, mapa.VOTOS):
        assert len(e.rotulos) == len(e.limites) + 1 == len(e.claro) == len(e.escuro)


def test_grafico_monta_com_a_malha():
    geo = ler_malha(FIX)
    dados = pd.DataFrame({"cod_ibge": ["1200013", "3550308"], "nome": ["ACRELÂNDIA", "SÃO PAULO"],
                          "uf": ["ac", "sp"], "abst_pct_atual": [17.0, 23.4]})
    tt = [alt.Tooltip("nome:N"), alt.Tooltip("abst_pct_atual:Q")]
    for tema in ("light", "dark"):
        spec = mapa.grafico(geo, dados, "abst_pct_atual", mapa.ABSTENCAO, "Abstenção", tema, tt).to_dict()
        assert spec["mark"]["type"] == "geoshape"
        assert spec["projection"] == {"type": "conicEqualArea", "parallels": [-2, -22], "rotate": [54, 0, 0]}
        assert spec["encoding"]["color"]["scale"]["domain"][-1] == "sem dado"
