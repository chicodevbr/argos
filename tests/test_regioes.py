"""Páginas de abstenção por região: leitura por seção e locais (fixtures reais do TSE, recortadas em
Varjão, Candangolândia e Cruzeiro, no DF), região por coordenada, agrupamento por município e a página."""

import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from apuracao.carga import regioes as malhas, secao
from apuracao.config import carregar
from apuracao.pagina import recortes, regioes

FIX = Path(__file__).parent / "fixtures"
H = FIX / "historico"
REGIOES_DF = FIX / "regioes-df-amostra.geojson"
CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"


def test_secoes_presidente_vem_do_nacional_e_so_da_uf():
    s = secao.secoes(H / "detalhe_votacao_secao_2026.zip", "df")
    assert len(s) == 28 and (s["cod_mun"] == "97012").all()               # a linha de SP ficou de fora
    assert (s["aptos"] == s["comparecimento"] + s["abstencoes"]).all()
    gov = secao.secoes(H / "detalhe_votacao_secao_2026.zip", "df", cargo="3")   # membro _DF.csv
    m = s.merge(gov, on=["cod_mun", "zona", "secao"], suffixes=("", "_gov"))
    assert len(m) == 28 and (m["aptos"] >= m["aptos_gov"]).all()           # presidente inclui o trânsito


def test_locais_nos_dois_formatos():
    l26 = secao.locais(H / "eleitorado_local_votacao_2026.zip", "df")      # membro por UF, vírgula decimal
    l22 = secao.locais(H / "eleitorado_local_votacao_2022.zip", "df")      # CSV nacional, ponto decimal
    for l in (l26, l22):
        assert len(l) == 14 and not l.duplicated(["cod_mun", "zona", "local"]).any()
        assert l["lat"].between(-16.1, -15.5).all() and l["lon"].between(-48.3, -47.3).all()
    assert secao._coordenada("-15,7067981") == -15.7067981 and secao._coordenada("-1") is None


def test_regiao_por_coordenada():
    regs = malhas.ler(REGIOES_DF, "ra_nome")
    l = secao.locais(H / "eleitorado_local_votacao_2026.zip", "df")
    ra = malhas.regiao_de(l["lon"], l["lat"], regs)
    assert pd.Series(ra).value_counts().to_dict() == {"CRUZEIRO": 8, "CANDANGOLÂNDIA": 5, "VARJÃO": 1}
    assert list(malhas.regiao_de([-47.0, np.nan], [-15.0, -15.8], regs)) == [None, None]


def test_buraco_no_poligono():
    quadrado = [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]], [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]]
    regs = [{"nome": "A", "props": {}, "geometria": {"type": "Polygon", "coordinates": quadrado}}]
    assert list(malhas.regiao_de([1, 5, 11], [1, 5, 5], regs)) == ["A", None, None]


def test_local_sem_coordenada_herda_do_outro_ano():
    ano = pd.DataFrame({"cod_mun": ["1", "1"], "zona": [1, 1], "local": [10, 11], "nome": ["ESCOLA X", "ESCOLA Y"],
                        "lat": [None, -15.8], "lon": [None, -47.9]})
    outro = pd.DataFrame({"cod_mun": ["1", "1"], "zona": [1, 1], "local": [10, 11], "nome": ["ESCOLA X", "OUTRO"],
                          "lat": [-15.7, -15.0], "lon": [-47.8, -47.0]})
    m = regioes._completar_coordenadas(ano, outro)
    assert m.loc[0, "lat"] == -15.7 and m.loc[0, "herdou"]
    assert m.loc[1, "lat"] == -15.8 and not m.loc[1, "herdou"]


def _historico(tmp_path):
    for ano in (2026, 2022):
        (tmp_path / str(ano)).mkdir(parents=True)
        for n in (f"detalhe_votacao_secao_{ano}.zip", f"eleitorado_local_votacao_{ano}.zip"):
            shutil.copy(H / n, tmp_path / str(ano) / n)
    return tmp_path


def _df(cfg):
    cfg.df.arquivo = REGIOES_DF
    return recortes.brasilia(cfg)


def test_dados_e_textos_por_coordenada(tmp_path):
    r = _df(carregar(CONFIG))
    por_ano = regioes.secoes_por_unidade(r, _historico(tmp_path))
    assert all(m["unidade"].notna().all() for m in por_ano.values())
    d = regioes.montar_dados(r, por_ano, abst_brasil_2026=21.08)
    assert {u["ra"] for u in d["ras"]} == {"Cruzeiro", "Candangolândia", "Varjão"}
    assert [u["pct"] for u in d["ras"]] == sorted((u["pct"] for u in d["ras"]), reverse=True)
    assert d["df"]["2026"]["aptos"] == sum(u["aptos"] for u in d["ras"]) and d["df"]["2026"]["secoes"] == 28
    lede = d["textos"]["lede"]
    assert regioes.em(r, d["ras"][0]["ra"]) in lede and regioes.em(r, d["ras"][-1]["ra"]) in lede
    assert regioes.em(r, "Varjão") == "no Varjão" and regioes.em(r, "Ceilândia") == "em Ceilândia"
    assert "o DF ficou" in d["textos"]["contexto_brasil"]       # o lugar é o sujeito da comparação
    assert d["meta"]["cortes"] == [16, 17.5, 18.5, 19.5, 21] and len(d["meta"]["rotulos"]) == 6


def test_agrupar_por_municipio(tmp_path):
    cfg = carregar(CONFIG)
    r = replace(recortes.estado_rj(cfg), uf="df", cod_mun=None)   # mesmo modo, com as fixtures do DF
    mun = pd.DataFrame({"cod_tse": ["97012"], "cod_ibge": ["5300108"], "nome": ["BRASÍLIA"]})
    d = regioes.montar_dados(r, regioes.secoes_por_unidade(r, _historico(tmp_path), mun), None)
    assert [u["chave"] for u in d["ras"]] == ["5300108"] and "contexto_brasil" not in d["textos"]
    assert d["ras"][0]["aptos"] == d["df"]["2026"]["aptos"]
    malha = regioes.malha_pagina(r, FIX / "malha-amostra.json")
    assert [f["properties"]["chave"] for f in malha["features"]] == ["5300108"]


def test_cortes_e_nomes():
    assert regioes.cortes_por_quantil([20, 21, 22, 23, 24, 25, 26]) == [21.0, 22.0, 23.0, 24.0, 25.0]
    assert regioes.cortes_por_quantil([20.0] * 6 + [30.0]) == [20.0]               # repetidos somem
    assert regioes._rotulos([18.5, 20]) == ["abaixo de 18,5%", "18,5% a 20%", "20% ou mais"]
    assert recortes.nome_proprio("SÃO JOÃO DE MERITI") == "São João de Meriti"
    assert recortes.nome_proprio("RIO DE JANEIRO") == "Rio de Janeiro"
    assert len(recortes.NOMES_DF) == 35 and len(set(recortes.NOMES_DF.values())) == 35
    assert {f["nome"] for f in malhas.ler(REGIOES_DF, "ra_nome")} <= set(recortes.NOMES_DF)


def test_gera_pagina_e_site(tmp_path):
    from apuracao.pagina.__main__ import documento_completo
    cfg = carregar(CONFIG)
    cfg.historico.dir = _historico(tmp_path / "h")
    r = _df(cfg)
    saida = regioes.gerar(r, cfg, tmp_path / "sem_parquet", tmp_path / "p.html")
    html = saida.read_text()
    assert "__DADOS__" not in html and "__MALHA__" not in html and "<title>Abstenção em Brasília</title>" in html and "<h1>Abstenção em Brasília</h1>" in html
    assert '"brasil_2026":null' in html and '"sem_secoes":[]' in html
    site = documento_completo(saida, tmp_path / "site" / "x" / "index.html").read_text()
    assert site.startswith("<!doctype html>") and site.rstrip().endswith("</html>") and site.count("<body>") == 1
