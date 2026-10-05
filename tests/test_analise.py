"""Página de análise: taxas com as mesmas definições nos dois anos, contra dados reais."""

import io
import shutil
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from apuracao.app import analise
from apuracao.carga.tse_csv import carregar_ano
from apuracao.coletor import snapshot
from apuracao.coletor.log import LogJson
from apuracao.modelo import consultas
from apuracao.modelo.construir import construir, construir_municipios
from datetime import datetime, timezone

FIX = Path(__file__).parent / "fixtures"
MAIN = Path(__file__).parent.parent / "apuracao" / "app" / "main.py"


@pytest.fixture
def pq(tmp_path):
    """2026: Acrelândia (fixture municipal real); 2022: amostra real do AC (dados abertos)."""
    raw = tmp_path / "raw"
    snapshot.gravar(raw, 6257, "ac", "ac01120-c0001-e006257-u", (FIX / "ac01120-c0001-e006257-u.json").read_bytes(),
                    url="u", momento=datetime(2026, 10, 5, tzinfo=timezone.utc), status=200, etag=None, last_modified=None)
    construir(raw, tmp_path / "pq", LogJson(io.StringIO()))
    construir_municipios(FIX / "mun-e006257-cm.json", tmp_path / "pq")
    (tmp_path / "hist" / "2022").mkdir(parents=True)
    for z in (FIX / "historico").glob("*.zip"):
        shutil.copy(z, tmp_path / "hist" / "2022" / z.name)
    carregar_ano(2022, tmp_path / "hist", tmp_path / "pq", LogJson(io.StringIO()))
    return tmp_path / "pq"


def test_taxas_do_municipio(pq):
    con = consultas.conectar(pq)
    assert analise.disponivel(con)
    m = analise.municipios(con, 2022)
    acre = m[m["cod_mun"] == "01120"].iloc[0]
    assert acre["nome"] == "ACRELÂNDIA"
    # 2026: mesmas contas do arquivo do TSE
    assert acre["abst_pct_atual"] == pytest.approx(100 * acre["abst_atual"] / (acre["comp_atual"] + acre["abst_atual"]))
    assert 0 < acre["pct_pt_atual"] < 100 and 0 < acre["pct_adv_atual"] < 100
    assert acre["var_abst"] == pytest.approx(acre["abst_pct_atual"] - acre["abst_pct_base"])


def test_ano_base_e_nomes_vem_dos_dados(pq):
    con = consultas.conectar(pq)
    assert analise.anos_base(con) == [2022]
    assert analise.nomes(con, 2022) == {"pt_base": "LULA (13)", "adv_base": "JAIR BOLSONARO (22)",
                                        "pt_atual": "LULA (13)", "adv_atual": "FLAVIO BOLSONARO (22)"}


def test_municipio_so_de_um_ano_entra_no_total_daquele_ano(pq):
    """Full outer join: a amostra de 2022 tem Rio Branco, que não está na base de 2026 do teste."""
    con = consultas.conectar(pq)
    m = analise.municipios(con, 2022)
    # (a amostra de votação também traz localidades do exterior que a de detalhe não traz:
    # aparecem só com votos, como deve ser num full outer join)
    so_base = m[m["comp_atual"].isna() & m["comp_base"].notna()]
    assert list(so_base["cod_mun"]) == ["01392"]  # Rio Branco
    total = analise.agregar(m).iloc[0]
    assert total["comp_base"] == pytest.approx(m["comp_base"].sum())


def test_agregar_recalcula_taxas_pela_soma():
    import pandas as pd
    m = pd.DataFrame({"uf": ["aa", "aa"], **{c: [1.0, 1.0] for c in analise.SOMAVEIS}})
    m["comp_atual"], m["abst_atual"] = [80.0, 10.0], [20.0, 90.0]  # 20% e 90% -> soma 110/200 = 55%
    g = analise.agregar(m, ["uf"]).iloc[0]
    assert g["abst_pct_atual"] == pytest.approx(55.0)  # média ponderada, não média simples (55 = 110/200)


def test_pagina_de_analise_renderiza(pq, monkeypatch):
    monkeypatch.setenv("APURACAO_DIR_PARQUET", str(pq))
    monkeypatch.setenv("APURACAO_CONFIG", str(Path(__file__).parent.parent / "config" / "eleicoes.toml"))
    at = AppTest.from_file(str(MAIN), default_timeout=60)
    at.run()
    at.switch_page("paginas/analise.py").run()
    assert not at.exception, at.exception
    assert [m.label for m in at.metric][:2] == ["Comparecimento", "Abstenção"]


def test_pagina_sem_dados_avisa(tmp_path, monkeypatch):
    monkeypatch.setenv("APURACAO_DIR_PARQUET", str(tmp_path / "vazio"))
    at = AppTest.from_file(str(MAIN), default_timeout=60)
    at.run()
    at.switch_page("paginas/analise.py").run()
    assert not at.exception and "Faltam dados" in at.info[0].value
