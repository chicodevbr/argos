"""Gerador da página compartilhável: monta com fixtures e as frases só afirmam o que os dados mostram."""

import json
from pathlib import Path

import pandas as pd
import pytest

from apuracao.pagina import abstencao
from tests.test_analise import pq  # noqa: F401  (fixture: 2026 Acrelândia + amostra real de 2022)

FIX = Path(__file__).parent / "fixtures"


def _dados_da_pagina(html: str) -> dict:
    i = html.index("const D = ") + len("const D = ")
    return json.loads(html[i:html.index(";\nconst MALHA")])


def test_gera_pagina_com_dados_e_malha(pq, tmp_path):  # noqa: F811
    saida = abstencao.gerar(pq, FIX / "malha-amostra.json", tmp_path / "pagina.html")
    html = saida.read_text()
    assert "__DADOS__" not in html and "__MALHA__" not in html
    d = _dados_da_pagina(html)
    assert set(d["brasil"]) == {"2022", "2026"}
    assert d["colunas_municipio"][-1] == "ibge" and d["municipios"][0][-1] == "1200013"
    assert set(d["textos"]) == {"lede", "titulo_serie", "titulo_exterior", "nota_turnos_uf"}
    assert '"codarea":"1200013"' in html


def _base(series: dict, exterior: float = 62.7):
    brasil = {a: {"abst": v, "abstencoes": 33_500_000, "eleitores": 1, "brancos": 1, "nulos": 1} for a, v in series.items()}
    return {"brasil": brasil, "exterior": {2026: exterior},
            "entre_turnos": {"correlacao": [0.7, 0.91], "governador_consistente": False, "governador_sinal": 0}}


def _municipios(n_sobe: int, n_cai: int, uf_top: str = "mg"):
    linhas = [{"eleitores_atual": 200_000, "abst_pct_atual": 22.0, "abst_pct_base": 20.0, "uf": "sp"}] * n_sobe
    linhas += [{"eleitores_atual": 200_000, "abst_pct_atual": 18.0, "abst_pct_base": 20.0, "uf": "sp"}] * n_cai
    linhas += [{"eleitores_atual": 5_000, "abst_pct_atual": 40.0, "abst_pct_base": 39.0, "uf": uf_top}] * 10
    return pd.DataFrame(linhas)


def test_frases_quando_os_dados_confirmam():
    t = abstencao._textos(_base({2014: 19.4, 2018: 20.3, 2022: 20.9, 2026: 21.1}), _municipios(20, 10))
    assert t["titulo_serie"] == "A abstenção sobe a cada eleição"
    assert "a maior das quatro últimas eleições presidenciais" in t["lede"]
    assert "subiu em dois terços das cidades com 100 mil eleitores ou mais (20 de 30)" in t["lede"]
    assert "10 dos 10 maiores percentuais estão em municípios de Minas Gerais" in t["lede"]
    assert t["titulo_exterior"] == "No exterior, mais de 60% não votam"


def test_frases_mudam_quando_os_dados_nao_confirmam():
    t = abstencao._textos(_base({2014: 19.4, 2018: 21.5, 2022: 20.9, 2026: 21.1}, exterior=45.0), _municipios(9, 21))
    assert t["titulo_serie"] != "A abstenção sobe a cada eleição"  # 2022 caiu
    assert "a maior das" not in t["lede"]                              # 2018 foi maior
    assert "caiu em 70% das cidades com 100 mil eleitores ou mais (21 de 30)" in t["lede"]
    assert t["titulo_exterior"] == "Abstenção no exterior"


@pytest.mark.parametrize("parte,total,esperado", [(149, 221, "dois terços"), (50, 100, "metade"), (37, 100, "37%")])
def test_fracao_por_extenso_so_quando_proxima(parte, total, esperado):
    assert abstencao._fracao(parte, total) == esperado


def test_frases_com_dado_ausente():
    t = abstencao._textos(_base({2022: 20.9, 2026: 21.1}, exterior=None), _municipios(20, 10))
    assert t["titulo_exterior"] == "Abstenção no exterior"
    assert abstencao._r(pd.NA) is None and abstencao._r(float("nan")) is None and abstencao._r(1.234) == 1.23


def test_pagina_onde_lula_perdeu(pq, tmp_path):  # noqa: F811
    from apuracao.pagina import perdas
    html = perdas.gerar(pq, FIX / "malha-amostra.json", tmp_path / "perdas.html").read_text()
    d = _dados_da_pagina(html)
    assert set(d["bases"]) == {"2022"}                # só 2022 na base de teste: o botão de 2014 some
    col = {c: i for i, c in enumerate(d["colunas"])}
    m = d["municipios"][0]
    assert m[col["ibge"]] == "1200013" and m[col["pct2014"]] is None
    x = d["bases"]["2022"]
    assert x["votos_atual"] == sum(r[col["votos2026"]] for r in d["municipios"])
    lede = d["textos"]["2022"]["lede"]
    assert ("a menos" in lede) == (x["pct_atual"] < x["pct_base"]) and "." not in lede.split("%")[0][-3:]
    assert d["textos"]["2022"]["grandes"] == ""      # nenhuma cidade de 100 mil+: sem a frase
