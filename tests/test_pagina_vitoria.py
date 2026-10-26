"""Página "Como Lula venceu": frases calculadas a partir de dados sintéticos. Nenhum teste acessa a rede."""

from apuracao.pagina import vitoria
from apuracao.pagina.vitoria import COLUNAS, _mi, _pontos, no_uf


def test_formatos_em_portugues():
    assert _mi(1_600_000) == "1,6 milhão de votos" and _mi(-4_430_000) == "4,4 milhões de votos"
    assert _mi(208_492) == "208.492 votos"
    assert _pontos(1.1) == "1,1 ponto" and _pontos(2.7) == "2,7 pontos"
    assert _pontos(-2.7, True) == "−2,7 pontos" and _pontos(0.9, True) == "+0,9 ponto"
    assert no_uf("RS") == "no Rio Grande do Sul" and no_uf("SP") == "em São Paulo" and no_uf("BA") == "na Bahia"


def _soma(l26, f26, l22, b22, l1, v1, aptos, abst, aptos22, abst22):
    return vitoria._soma(__import__("pandas").DataFrame([{
        "l26": l26, "f26": f26, "l22": l22, "b22": b22, "l1": l1, "v1": v1,
        "aptos": aptos, "abst_n": abst, "aptos22": aptos22, "abst_n22": abst22}]))


def _dados():
    # 3 municípios: SP e RJ grandes (Lula cresce), RS (Lula cai e o 22 perde votos)
    linhas = [
        ["São Paulo", "SP", "3550308", "Sudeste", 200_000, 120_000, 60_000, 100_000, 80_000, 90_000, 180_000, 18.0, 20.0],
        ["Rio de Janeiro", "RJ", "3304557", "Sudeste", 150_000, 90_000, 40_000, 70_000, 60_000, 60_000, 130_000, 17.0, 21.0],
        ["Pelotas", "RS", "4314407", "Sul", 50_000, 15_000, 25_000, 18_000, 26_000, 12_000, 40_000, 22.0, 21.0],
    ]
    col = {c: i for i, c in enumerate(COLUNAS)}
    def soma_de(ls):
        return _soma(*(sum(l[col[k]] for l in ls) for k in ("l26", "f26", "l22", "b22", "l1", "v1", "eleitores")),
                     sum(l[col["eleitores"]] * l[col["abst26"]] / 100 for l in ls),
                     sum(l[col["eleitores"]] for l in ls), sum(l[col["eleitores"]] * l[col["abst22"]] / 100 for l in ls))
    ufs = {l[1]: soma_de([l]) for l in linhas}
    return {"colunas": COLUNAS, "municipios": linhas, "brasil": soma_de(linhas), "ufs": ufs,
            "regioes": {"Sudeste": soma_de(linhas[:2]), "Sul": soma_de(linhas[2:])},
            "serie": [{"ano": 2006, "turno": 2, "pct": 18.5}, {"ano": 2022, "turno": 2, "pct": 20.6},
                      {"ano": 2026, "turno": 2, "pct": 19.0}]}


def test_textos_calculados():
    t = vitoria.textos(_dados())
    assert t["lede"].startswith("Lula venceu com 64,29% dos votos válidos, 11,2 pontos acima de 2022")
    assert "Lula teve 37.000 votos a mais que em 2022, e o 22, 41.000 votos a menos." in t["lede"]  # sem "mas"
    assert "Rio Grande do Sul" in t["decisivo"] and "no Sul" in t["decisivo"]
    assert "a menor num 2º turno desde 2006 (18,50%)" in t["abstencao"]
    assert "2022. Em 3 dos 3 municípios o 22 teve menos votos" in t["flavio"]   # sem "Mas": no país também caiu
    assert "A maior queda: São Paulo (SP), com 20.000 votos a menos." in t["flavio"]
    assert t["cresceu"]["2022"].startswith("Lula teve percentual maior que em 2022 em 2 dos 3 municípios")
