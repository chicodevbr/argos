"""Página "quem venceu em cada urna": votos por seção, locais (um ponto por prédio) e frases. Sem rede."""

from pathlib import Path

import pandas as pd

from apuracao.pagina import urnas
from apuracao.pagina.regioes import Recorte

R = Recorte(id="brasilia", titulo="t", rotulo="Distrito Federal", uf="df", cod_mun=None, sigla="DF",
            no_lugar="No Distrito Federal", do_lugar="do DF", sujeito="o DF", nome_total="Distrito Federal",
            unidade="região administrativa", unidades="regiões administrativas", abrev="RAs", todas="todas",
            agrupar="coordenadas", malha=Path("x"), nomes={"PLANO PILOTO": "Plano Piloto", "GAMA": "Gama"},
            preposicao={"Plano Piloto": "no", "Gama": "no"})

S = pd.DataFrame([("1", 1, 1, 10, 500, 400), ("1", 1, 2, 10, 500, 420), ("1", 2, 9, 30, 400, 300),
                  ("1", 3, 4, 50, 1500, 1200)],
                 columns=["cod_mun", "zona", "secao", "local", "aptos", "comparecimento"])
V = pd.DataFrame([(c, z, s, n, "", v) for c, z, s, n, v in [
    ("1", 1, 1, 13, 250), ("1", 1, 1, 22, 140), ("1", 1, 1, 95, 10),
    ("1", 1, 2, 13, 200), ("1", 1, 2, 22, 200), ("1", 1, 2, 96, 20),
    ("1", 2, 9, 13, 100), ("1", 2, 9, 22, 190),
    ("1", 3, 4, 13, 300), ("1", 3, 4, 22, 880)]], columns=["cod_mun", "zona", "secao", "numero", "nome", "votos"])
# o local 10 da zona 1 e o 30 da zona 2 são o mesmo prédio (nome e coordenadas iguais)
L = pd.DataFrame([("1", 1, 10, "ESCOLA CLASSE 01", "ASA NORTE", -15.7, -47.9, "PLANO PILOTO"),
                  ("1", 2, 30, "ESCOLA CLASSE 01", "ASA NORTE", -15.7, -47.9, "PLANO PILOTO"),
                  ("1", 3, 50, "CEF 02 DO GAMA", "SETOR LESTE", -16.0, -48.1, "GAMA")],
                 columns=["cod_mun", "zona", "local", "nome", "bairro", "lat", "lon", "unidade"])


def test_secoes_e_locais():
    s = urnas.por_secao(S, V)
    assert s.loc[0, ["a", "b", "brancos", "nulos"]].tolist() == [250, 140, 10, 0]
    loc = urnas.por_local(s, L).set_index("nome")
    esc = loc.loc["ESCOLA CLASSE 01"]
    assert esc["se"] == 3 and esc["zonas"] == [1, 2] and (esc["a"], esc["b"]) == (550, 530)
    assert (esc["sa"], esc["sb"]) == (1, 1)   # uma urna empatada (200 x 200)


def test_dados_e_frases():
    s = urnas.por_secao(S, V)
    d = urnas.montar_dados(R, s, urnas.por_local(s, L), {13: "Lula", 22: "Flávio Bolsonaro"})
    t = d["total"]
    assert (t["sa"], t["sb"], t["empates"], t["la"], t["lb"]) == (1, 2, 1, 1, 1)
    assert d["pontos"][0]["n"] == "CEF 02 do Gama" and d["pontos"][0]["ra"] == "Gama"
    assert d["textos"]["lede"].startswith("No Distrito Federal, Flávio Bolsonaro venceu o 2º turno com 62,39%")
    assert "e 1 terminou empatada" in d["textos"]["lede"]
    assert "maior vantagem de Lula foi em Escola Classe 01, no Plano Piloto" in d["textos"]["destaque"]
