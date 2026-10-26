"""Página "do 1º ao 2º turno" por região: unidade das seções do 2º turno, agregação e frases. Sem rede."""

from pathlib import Path

import pandas as pd

from apuracao.pagina import turnos
from apuracao.pagina.regioes import Recorte


def _r():
    return Recorte(id="brasilia", titulo="t", rotulo="Distrito Federal", uf="df", cod_mun=None, sigla="DF",
                   no_lugar="No Distrito Federal", do_lugar="do DF", sujeito="o DF", nome_total="Distrito Federal",
                   unidade="região administrativa", unidades="regiões administrativas", abrev="RAs", todas="todas",
                   agrupar="coordenadas", malha=Path("x"), preposicao={"GAMA": "no"})


def _secoes(linhas):
    return pd.DataFrame(linhas, columns=["cod_mun", "zona", "secao", "local", "aptos", "comparecimento", "abstencoes"])


S1 = _secoes([("1", 1, 1, 10, 100, 80, 20), ("1", 1, 2, 10, 100, 70, 30), ("1", 2, 5, 20, 200, 150, 50)]).assign(
    unidade=["GAMA", "GAMA", "GUARÁ"])
# 2º turno: a seção 3 é nova, no mesmo local 10 (herda GAMA pelo local)
S2 = _secoes([("1", 1, 1, 10, 100, 85, 15), ("1", 1, 3, 10, 50, 40, 10), ("1", 2, 5, 20, 200, 140, 60)])


def _votos(linhas):
    return pd.DataFrame([(c, z, s, n, "", v) for c, z, s, n, v in linhas],
                        columns=["cod_mun", "zona", "secao", "numero", "nome", "votos"])


V1 = _votos([("1", 1, 1, 13, 30), ("1", 1, 1, 22, 40), ("1", 1, 1, 12, 5), ("1", 1, 1, 95, 5),
             ("1", 1, 2, 13, 20), ("1", 1, 2, 22, 40), ("1", 1, 2, 96, 10),
             ("1", 2, 5, 13, 90), ("1", 2, 5, 22, 50), ("1", 2, 5, 28, 10)])   # 28: fora dos válidos
V2 = _votos([("1", 1, 1, 13, 40), ("1", 1, 1, 22, 45),
             ("1", 1, 3, 13, 15), ("1", 1, 3, 22, 25),
             ("1", 2, 5, 13, 70), ("1", 2, 5, 22, 70)])


def test_unidade_no_2o_pela_secao_e_pelo_local():
    m = turnos.unidade_no_2o(S2, S1)
    assert list(m["unidade"]) == ["GAMA", "GAMA", "GUARÁ"]


def test_agregar_validos_brancos_e_outros():
    t1 = turnos.agregar(S1, V1, {12, 13, 22})
    assert t1.loc["GAMA"].to_dict() == {"aptos": 200, "comparecimento": 150, "abstencoes": 50, "validos": 135,
                                        "a": 50, "b": 80, "outros": 5, "brancos": 5, "nulos": 10}
    assert t1.loc["GUARÁ", "validos"] == 140   # o 28 fica fora


def test_montar_dados_e_frases():
    t1 = turnos.agregar(S1, V1, {12, 13, 22})
    t2 = turnos.agregar(turnos.unidade_no_2o(S2, S1), V2, {13, 22})
    d = turnos.montar_dados(_r(), 2026, t1, t2, {13: "Lula", 22: "Flávio Bolsonaro"})
    gama = next(u for u in d["ras"] if u["ra"] == "GAMA")
    assert gama["dif_a"] == 5 and gama["dif_b"] == -10 and gama["cresc_b"] == -12.5
    assert d["total"]["dif_a"] == -15 and d["meta"]["titulo"] == "Do 1º ao 2º turno em Brasília"
    t = d["textos"]
    assert t["lede"].startswith("No Distrito Federal, Flávio Bolsonaro venceu o 2º turno com 52,83%")
    assert "Lula perdeu 15 votos" in t["lede"] and "Flávio Bolsonaro ganhou 10 votos" in t["lede"]
    assert "Lula ganhou votos em 1 das 2 RAs; Flávio Bolsonaro ganhou votos em 1 das 2 RAs" in t["crescimento"]
    assert t["vitorias"] == "Flávio Bolsonaro venceu só no GAMA; Lula não venceu em nenhuma."
