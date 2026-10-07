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


# --- Página de votos por candidato ---------------------------------------------------------------
def _historico_votos(tmp_path):
    h = _historico(tmp_path)
    for ano in (2026, 2022):
        shutil.copy(H / f"votacao_secao_{ano}_BR.zip", h / str(ano) / f"votacao_secao_{ano}_BR.zip")
    return h


CANDIDATOS = {2026: {22: "Flávio Bolsonaro", 13: "Lula", 55: "Ronaldo Caiado", 14: "Renan Santos",
                     70: "Escritor Augusto Cury", 30: "Zema", 80: "Samara", 16: "Hertz Dias", 27: "Clariana Barão",
                     29: "Rui Costa Pimenta", 21: "Edmilson Costa", 35: "Veterinário Wilson Grassi"},
              2022: {22: "Jair Bolsonaro", 13: "Lula", 15: "Simone Tebet", 12: "Ciro Gomes", 30: "Felipe d'Avila",
                     44: "Soraya Thronicke", 14: "Padre Kelmon", 80: "Léo Péricles", 21: "Sofia Manzano",
                     27: "Constituinte Eymael", 16: "Vera"}}


def test_votos_por_secao_so_da_uf():
    v = secao.votos(H / "votacao_secao_2026_BR.zip", "df")
    assert (v["cod_mun"] == "97012").all() and v[["zona", "secao"]].drop_duplicates().shape[0] == 28
    assert {95, 96, 13, 22} <= set(v["numero"])                              # brancos, nulos e candidatos


def test_dados_da_pagina_de_votos(tmp_path):
    from apuracao.pagina import votos
    h = _historico_votos(tmp_path)
    r = _df(carregar(CONFIG))
    sec = regioes.secoes_por_unidade(r, h)
    vv = {a: secao.votos(h / str(a) / f"votacao_secao_{a}_BR.zip", "df") for a in (2026, 2022)}
    d = votos.montar_dados(r, sec, vv, CANDIDATOS)
    for a in ("2026", "2022"):
        cs = d["anos"][a]["candidatos"]
        assert abs(sum(c["pct"] for c in cs) - 100) < 0.01 and cs == sorted(cs, key=lambda c: -c["votos"])
        for u in d["ras"]:
            assert abs(sum(u[a]["pct"].values()) - 100) < 0.01
            assert abs(u[a]["margem"] - (u[a]["pct"][13] - u[a]["pct"][22])) < 1e-9
        assert d["anos"][a]["validos"] == sum(u[a]["validos"] for u in d["ras"])
    # votos de quem não está entre os válidos oficiais ficam fora e são listados: o nº 28 tem votos reais
    # nestas seções e não é válido em 2026; tirando o 55 da lista, ele também passa a ficar de fora
    assert [c["numero"] for c in d["anos"]["2026"]["fora_dos_validos"]] == [28]
    sem_55 = {**CANDIDATOS, 2026: {k: v for k, v in CANDIDATOS[2026].items() if k != 55}}
    d2 = votos.montar_dados(r, sec, vv, sem_55)
    assert [c["numero"] for c in d2["anos"]["2026"]["fora_dos_validos"]] == [28, 55]
    assert "Lula" in d["textos"]["lede"] and "Flávio Bolsonaro" in d["textos"]["comparacao"]
    assert [u["2026"]["margem"] for u in d["ras"]] == sorted(u["2026"]["margem"] for u in d["ras"])


def test_nome_urna():
    from apuracao.pagina.votos import nome_urna
    assert nome_urna("FLAVIO BOLSONARO") == "Flávio Bolsonaro" and nome_urna("ESCRITOR AUGUSTO CURY") == "Escritor Augusto Cury"


def test_barra_de_navegacao_do_site(tmp_path):
    from apuracao.pagina.__main__ import SITE, documento_completo
    pagina = tmp_path / "p.html"
    pagina.write_text("<title>T</title>\n<style>body{}</style>\n<main>conteúdo</main>\n")
    html = documento_completo(pagina, tmp_path / "votos" / "index.html", nav="votos").read_text()
    nav = html[html.index('<nav class="site-nav"'):html.index("</nav>")]
    assert html.count('<nav class="site-nav"') == 1 and nav.count('aria-current="page"') == 1
    assert '<a href="/votos/" aria-current="page">' in html and '<a href="/">' in html
    subs = [p[1] for p in SITE]
    assert len(subs) == len(set(subs)) and "" not in subs           # subpastas únicas; a raiz é a página inicial
    assert '<a href="/">Início</a>' in html
    sem = documento_completo(pagina, tmp_path / "x" / "index.html").read_text()
    assert "site-nav" not in sem


# --- "Onde Lula perdeu votos" por região -----------------------------------------------------------
def test_faixas_do_mapa_pelos_dados():
    from apuracao.pagina.perdas import faixas
    misto = faixas([-3.3, -2.6, -2.2, -1.3, -1.2, -1.0, -0.9, -0.3, -0.2, -0.1, -0.1, -0.05,
                    0.1, 0.3, 0.4, 0.5, 0.7, 1.2, 1.3, 1.7, 2.0, 2.4, 2.8, 3.6, 4.5, 5.8])
    assert 0 in misto["cortes"] and misto["cores"][0] == "q4" and misto["cores"][-1] == "a4"
    for f in (misto, faixas([2, 3, 12, 14, 16, 18, 22, 25, 27, 29, 11, 13]), faixas([-2]), faixas([1, 2])):
        assert len(f["rotulos"]) == len(f["cores"]) == len(f["cortes"]) + 1
        assert f["cortes"] == sorted(f["cortes"])
    so_altas = faixas([2, 3, 12, 14, 16, 18, 22, 25, 27, 29, 11, 13])
    assert all(c.startswith("a") for c in so_altas["cores"]) and not any("caiu" in r for r in so_altas["rotulos"])
    assert faixas([-2]) == {"cortes": [], "cores": ["q4"], "rotulos": ["caiu"]}
    # mediana das quedas ~0,95 -> corte em 1: singular "1 ponto"
    um = faixas([-2, -1.8, -1.5, -1.2, -1.1, -1.0, -0.9, -0.8, -0.5, -0.4, -0.3, -0.2])
    assert um["cortes"] == [-1.0] and um["rotulos"][0] == "caiu 1 ponto ou mais"


def test_onde_lula_perdeu_por_ra(tmp_path, monkeypatch):
    from apuracao.pagina import perdas, votos
    h = _historico_votos(tmp_path)
    (h / "2014").mkdir()
    for n in ("detalhe_votacao_secao_2014.zip", "eleitorado_local_votacao_2014.zip", "votacao_secao_2014_BR.zip"):
        shutil.copy(H / n, h / "2014" / n)
    cand_2014 = {13: "Dilma", 45: "Aécio Neves", 40: "Marina Silva", 50: "Luciana Genro", 20: "Pastor Everaldo",
                 43: "Eduardo Jorge", 28: "Levy Fidelix", 16: "Zé Maria", 27: "Eymael", 21: "Mauro Iasi", 29: "Rui Costa Pimenta"}
    monkeypatch.setattr(votos, "candidatos_validos", lambda *a: {**CANDIDATOS, 2014: cand_2014})
    cfg = carregar(CONFIG)
    cfg.historico.dir = h
    r = _df(cfg)
    d = perdas.montar_dados_regioes(r, cfg, tmp_path / "sem_parquet")
    col = {c: i for i, c in enumerate(d["colunas"])}
    assert {m[col["nome"]] for m in d["municipios"]} == {"Cruzeiro", "Candangolândia", "Varjão"}
    assert d["bases"]["2014"]["nome_base"] == "Dilma" and d["bases"]["2022"]["nome_base"] == "Lula"
    for b in ("2022", "2014"):
        x = d["bases"][b]
        assert x["votos_atual"] == sum(m[col["votos2026"]] for m in d["municipios"])
        assert x["votos_base"] == sum(m[col[f"votos{b}"]] for m in d["municipios"])
        f = d["faixas"][b]
        assert len(f["rotulos"]) == len(f["cores"]) == len(f["cortes"]) + 1
        assert d["textos"][b]["lede"].startswith("No Distrito Federal, Lula teve")
    assert d["meta"]["titulo"] == "Onde Lula perdeu votos em Brasília" and d["meta"]["mostrar_uf"] is False
    assert set(d["grupos"]) == {m[col["nome"]] for m in d["municipios"]}
