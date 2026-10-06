"""Abstenção por região administrativa do DF: leitura por seção e locais (fixtures reais do TSE,
recortadas em Varjão, Candangolândia e Cruzeiro), RA por coordenada e a página."""

import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from apuracao.carga import regioes_df, secao
from apuracao.config import carregar
from apuracao.pagina import brasilia

FIX = Path(__file__).parent / "fixtures"
H = FIX / "historico"
REGIOES = FIX / "regioes-df-amostra.geojson"
CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"


def test_secoes_presidente_vem_do_nacional_e_so_da_uf():
    s = secao.secoes(H / "detalhe_votacao_secao_2026.zip", "df")
    assert len(s) == 28 and not s.duplicated(["zona", "secao"]).any()   # a linha de SP ficou de fora
    assert (s["aptos"] == s["comparecimento"] + s["abstencoes"]).all()
    gov = secao.secoes(H / "detalhe_votacao_secao_2026.zip", "df", cargo="3")   # membro _DF.csv
    m = s.merge(gov, on=["zona", "secao"], suffixes=("", "_gov"))
    assert len(m) == 28
    # presidente inclui os eleitores em trânsito: nunca menos aptos que governador
    assert (m["aptos"] >= m["aptos_gov"]).all()


def test_locais_nos_dois_formatos():
    l26 = secao.locais(H / "eleitorado_local_votacao_2026.zip", "df")      # membro por UF, vírgula decimal
    l22 = secao.locais(H / "eleitorado_local_votacao_2022.zip", "df")      # CSV nacional, ponto decimal
    for l in (l26, l22):
        assert len(l) == 14 and not l.duplicated(["zona", "local"]).any()  # um por local, só 1º turno
        assert l["lat"].between(-16.1, -15.5).all() and l["lon"].between(-48.3, -47.3).all()
    assert secao._coordenada("-15,7067981") == -15.7067981 and secao._coordenada("-1") is None


def test_regiao_por_coordenada():
    regs = regioes_df.ler(REGIOES)
    l = secao.locais(H / "eleitorado_local_votacao_2026.zip", "df")
    ra = regioes_df.regiao_de(l["lon"], l["lat"], regs)
    assert pd.Series(ra).value_counts().to_dict() == {"CRUZEIRO": 8, "CANDANGOLÂNDIA": 5, "VARJÃO": 1}
    fora = regioes_df.regiao_de([-47.0, np.nan], [-15.0, -15.8], regs)    # longe do DF; sem coordenada
    assert list(fora) == [None, None]


def test_buraco_no_poligono():
    quadrado = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]
    regs = [{"nome": "A", "numero": 1, "geometria": {"type": "Polygon", "coordinates": quadrado}}]
    assert list(regioes_df.regiao_de([1, 5, 11], [1, 5, 5], regs)) == ["A", None, None]


def test_local_sem_coordenada_herda_do_outro_ano():
    ano = pd.DataFrame({"zona": [1, 1], "local": [10, 11], "nome": ["ESCOLA X", "ESCOLA Y"],
                        "bairro": ["", ""], "lat": [None, -15.8], "lon": [None, -47.9]})
    outro = pd.DataFrame({"zona": [1, 1], "local": [10, 11], "nome": ["ESCOLA X", "OUTRO NOME"],
                          "bairro": ["", ""], "lat": [-15.7, -15.0], "lon": [-47.8, -47.0]})
    m = brasilia._completar_coordenadas(ano, outro)
    assert m.loc[0, "lat"] == -15.7 and m.loc[0, "herdou"]
    assert m.loc[1, "lat"] == -15.8 and not m.loc[1, "herdou"]             # já tinha: não troca


def _historico(tmp_path):
    for ano in (2026, 2022):
        (tmp_path / str(ano)).mkdir(parents=True)
        for n in (f"detalhe_votacao_secao_{ano}.zip", f"eleitorado_local_votacao_{ano}.zip"):
            shutil.copy(H / n, tmp_path / str(ano) / n)
    return tmp_path


def test_dados_e_textos_da_pagina(tmp_path):
    por_ano = brasilia.secoes_com_ra(_historico(tmp_path), REGIOES)
    for a, m in por_ano.items():
        assert m["ra"].notna().all(), a
    d = brasilia.montar_dados(por_ano, abst_brasil_2026=21.08)
    assert {r["ra"] for r in d["ras"]} == {"Cruzeiro", "Candangolândia", "Varjão"}
    assert [r["pct"] for r in d["ras"]] == sorted((r["pct"] for r in d["ras"]), reverse=True)
    df26 = d["df"]["2026"]
    assert df26["aptos"] == sum(r["aptos"] for r in d["ras"]) and df26["secoes"] == 28
    maior, menor = d["ras"][0], d["ras"][-1]
    lede = d["textos"]["lede"]
    assert brasilia.em(maior["ra"]) in lede and brasilia.em(menor["ra"]) in lede
    assert brasilia.em("Varjão") == "no Varjão" and brasilia.em("Ceilândia") == "em Ceilândia"
    assert "pontos abaixo" in d["textos"]["contexto_brasil"] or "pontos acima" in d["textos"]["contexto_brasil"]


def test_gera_pagina(tmp_path):
    cfg = carregar(CONFIG)
    cfg.historico.dir = _historico(tmp_path / "h")
    cfg.df.arquivo = REGIOES
    saida = brasilia.gerar(cfg, tmp_path / "sem_parquet", tmp_path / "p.html")
    html = saida.read_text()
    assert "__DADOS__" not in html and "__MALHA__" not in html and "Abstenção em Brasília" in html
    assert '"brasil_2026":null' in html     # sem tabelas do 1º turno: a página sai sem o indicador Brasil


def test_nomes_cobrem_as_35_ras():
    assert len(brasilia.NOMES) == 35 and len(set(brasilia.NOMES.values())) == 35
    assert {f["nome"] for f in regioes_df.ler(REGIOES)} <= set(brasilia.NOMES)
