"""Páginas "votos por zona eleitoral" e "quem venceu em cada RA": agregação, frases e conferência da RA. Sem rede."""

from pathlib import Path

import pandas as pd

from apuracao.pagina import ras, zonas
from apuracao.pagina.regioes import Recorte


def _r():
    return Recorte(id="brasilia", titulo="t", rotulo="Distrito Federal", uf="df", cod_mun=None, sigla="DF",
                   no_lugar="No Distrito Federal", do_lugar="do DF", sujeito="o DF", nome_total="Distrito Federal",
                   unidade="região administrativa", unidades="regiões administrativas", abrev="RAs", todas="todas",
                   agrupar="coordenadas", malha=Path("x"), preposicao={"LAGO NORTE": "no", "PARANOÁ": "no"})


NOMES = {13: "Lula", 22: "Flávio Bolsonaro"}
# zona 2 junta PARANOÁ (Flávio) e LAGO NORTE (Lula por 5 votos); zona 1 é só do PLANO (Lula)
SEC = pd.DataFrame(
    [("1", 1, 1, 10, 100, 90, 60, 20, 1, 1, "PLANO"),
     ("1", 2, 1, 20, 100, 90, 30, 50, 2, 1, "PARANOÁ"),
     ("1", 2, 2, 20, 100, 90, 30, 50, 1, 1, "PARANOÁ"),
     ("1", 2, 3, 30, 100, 90, 45, 40, 1, 1, "LAGO NORTE")],
    columns=["cod_mun", "zona", "secao", "local", "aptos", "comparecimento", "a", "b", "brancos", "nulos", "unidade"])
LOC = pd.DataFrame([("1", 1, 10, "ESCOLA A", "PLANO", -15.79, -47.88), ("1", 2, 20, "ESCOLA B", "PARANOA", -15.77, -47.78),
                    ("1", 2, 30, "COLEGIO C", "LAGO NORTE", -15.73, -47.84)],
                   columns=["cod_mun", "zona", "local", "nome", "bairro", "lat", "lon"])


def test_zonas_juntam_ras_e_escondem_vitorias():
    d = zonas.montar_dados(_r(), SEC, LOC, NOMES)
    z2 = next(z for z in d["zonas"] if z["z"] == 2)
    assert (z2["a"], z2["b"], z2["se"]) == (105, 140, 3) and [p["ra"] for p in z2["ras"]] == ["PARANOÁ", "LAGO NORTE"]
    assert d["total"]["za"] == 1 and d["total"]["zb"] == 1 and d["total"]["mistas"] == 1
    assert d["textos"]["lede"] == ("No Distrito Federal, Lula venceu em 1 das 2 zonas eleitorais e Flávio Bolsonaro "
                                   "em 1 (2ª). 1 das 2 zonas junta mais de uma região administrativa.")
    assert "na 2ª zona, Flávio Bolsonaro venceu por 14,3 pontos, mas Lula ganhou na parte do LAGO NORTE" in d["textos"]["escondido"]
    assert len(d["pontos"]) == 3


def _quadrado(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def test_checar_move_locais_perto_do_limite():
    # LAGO NORTE e PARANOÁ lado a lado; o local do LAGO NORTE fica a ~50 m do limite (lon −47.80)
    regs = [{"nome": "LAGO NORTE", "geometria": _quadrado(-47.90, -15.80, -47.80, -15.70)},
            {"nome": "PARANOÁ", "geometria": _quadrado(-47.80, -15.80, -47.70, -15.70)},
            {"nome": "PLANO", "geometria": _quadrado(-48.00, -15.90, -47.90, -15.80)}]
    loc = LOC.assign(unidade=["PLANO", "PARANOÁ", "LAGO NORTE"],
                     lon=[-47.95, -47.75, -47.8005], lat=[-15.85, -15.75, -15.75])
    c = ras.checar(_r(), SEC, loc, regs)
    # mover o COLEGIO C para o PARANOÁ deixaria o LAGO NORTE sem votos: não conta
    assert c["duvidosos"] == 1 and c["trocas"] == 0 and c["viradas"] == []
    d = ras.montar_dados(_r(), SEC, loc, NOMES, c)
    assert [r["ra"] for r in d["ras"]][0] == "PLANO" and d["total"]["ra_a"] == 2 and d["total"]["ra_b"] == 1
    assert d["textos"]["lede"].endswith("e Flávio Bolsonaro em 1: PARANOÁ. Por zona eleitoral, Flávio Bolsonaro vence em 1 das 2.")
    assert "LAGO NORTE (Lula por 5 votos)" in d["textos"]["firmeza"] and "nenhum vencedor muda" in d["textos"]["firmeza"]


def test_quadro_invertido_por_zona():
    # Lula vence com folga numa zona e perde por pouco em duas: mais votos, menos zonas
    sec = pd.DataFrame([("1", 1, 1, 10, 100, 100, 90, 10, 0, 0, "PLANO"),
                        ("1", 2, 1, 20, 100, 100, 45, 55, 0, 0, "PARANOÁ"),
                        ("1", 3, 1, 30, 100, 100, 45, 55, 0, 0, "LAGO NORTE")], columns=SEC.columns)
    loc = LOC.assign(zona=[1, 2, 3])
    d = zonas.montar_dados(_r(), sec, loc, NOMES)
    assert d["textos"]["lede"].startswith("No Distrito Federal, Flávio Bolsonaro venceu em 2 das 3 zonas eleitorais e "
                                          "Lula em 1 (1ª), embora Lula tenha tido mais votos no total.")
    c = {"locais": 3, "duvidosos": 0, "trocas": 0, "viradas": [], "perto_m": 100, "atencao": [], "atencao_m": 300}
    t = ras.montar_dados(_r(), sec, loc, NOMES, c)["textos"]["lede"]
    assert "o quadro se inverte: Flávio Bolsonaro vence em 2 das 3 zonas, embora Lula tenha tido mais votos" in t
