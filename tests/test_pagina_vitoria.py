"""Página "Como Lula venceu": frases calculadas a partir de dados sintéticos. Nenhum teste acessa a rede."""

from apuracao.pagina import vitoria
from apuracao.pagina.vitoria import COLUNAS, _mi, _pontos, no_uf


def test_formatos_em_portugues():
    assert _mi(1_600_000) == "1,6 milhão de votos" and _mi(-4_430_000) == "4,4 milhões de votos"
    assert _mi(208_492) == "208.492 votos"
    assert _pontos(1.1) == "1,1 ponto" and _pontos(2.7) == "2,7 pontos"
    assert _pontos(-2.7, True) == "−2,7 pontos" and _pontos(0.9, True) == "+0,9 ponto"
    assert no_uf("RS") == "no Rio Grande do Sul" and no_uf("SP") == "em São Paulo" and no_uf("BA") == "na Bahia"


def _soma(l26, f26, l22, b22, l1, v1, aptos, abst, aptos22, abst22, aptos1, abst1):
    return vitoria._soma(__import__("pandas").DataFrame([{
        "l26": l26, "f26": f26, "l22": l22, "b22": b22, "l1": l1, "v1": v1, "aptos": aptos, "abst_n": abst,
        "aptos22": aptos22, "abst_n22": abst22, "aptos1": aptos1, "abst_n1": abst1}]))


def _dados():
    # 3 municípios: SP e RJ grandes (Lula cresce), RS (Lula cai e o 22 perde votos)
    linhas = [
        ["São Paulo", "SP", "3550308", "Sudeste", 200_000, 120_000, 60_000, 100_000, 80_000, 90_000, 180_000, 18.0, 20.0, 21.0],
        ["Rio de Janeiro", "RJ", "3304557", "Sudeste", 150_000, 90_000, 40_000, 70_000, 60_000, 60_000, 130_000, 17.0, 21.0, 20.0],
        ["Pelotas", "RS", "4314407", "Sul", 50_000, 15_000, 25_000, 18_000, 26_000, 12_000, 40_000, 22.0, 21.0, 19.0],
    ]
    col = {c: i for i, c in enumerate(COLUNAS)}
    def soma_de(ls):
        return _soma(*(sum(l[col[k]] for l in ls) for k in ("l26", "f26", "l22", "b22", "l1", "v1", "eleitores")),
                     sum(l[col["eleitores"]] * l[col["abst26"]] / 100 for l in ls),
                     sum(l[col["eleitores"]] for l in ls), sum(l[col["eleitores"]] * l[col["abst22"]] / 100 for l in ls),
                     sum(l[col["eleitores"]] for l in ls), sum(l[col["eleitores"]] * l[col["abst1"]] / 100 for l in ls))
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


def test_abstencao_entre_turnos():
    t = vitoria.textos(_dados())["turnos"]
    assert "diminuiu em 2 dos 3 municípios" in t
    assert "as maiores quedas foram em São Paulo (SP, −3,0 pontos) e Rio de Janeiro (RJ, −3,0 pontos)" in t
    assert "Em 1 estado ela subiu: Rio Grande do Sul (19,0% → 22,0%)." in t


def test_texto_da_serie_de_eleicoes():
    E = [{"ano": a, "vencedor": v, "margem": m, "bn2": bn} for a, v, m, bn in
         [(2002, "Lula", 19_400_000, 6.0), (2014, "Dilma", 3_460_000, 6.3), (2018, "Jair Bolsonaro", 10_760_000, 9.6),
          (2022, "Lula", 2_140_000, 4.6), (2026, "Lula", 4_430_000, 3.2)]]
    t = vitoria.eleicoes_txt(E)
    assert "Desde 2002, houve 5 segundos turnos" in t
    assert "4,4 milhões de votos de vantagem: mais que em 2014 e 2022, as disputas mais apertadas, e menos que nas outras 2." in t
    assert "3,20% dos votos no 2º turno de 2026, o menor percentual da série; o maior, em 2018 (9,60%)." in t


def test_texto_dos_dois_turnos():
    b = {"l1": 53_879_538, "f1": 56_104_503, "l26": 64_256_232, "f26": 59_826_041}
    t = vitoria.dois_turnos(b)
    assert t.startswith("No 1º turno, Flávio ficou na frente: Lula teve 53,9 milhões de votos e Flávio, 56,1 milhões")
    assert "Lula ganhou 10,4 milhões de votos e Flávio ganhou 3,7 milhões de votos. Lula cresceu 6,7 milhões a mais" in t
    uf = vitoria.dois_turnos({"l1": 500, "f1": 400, "l26": 450, "f26": 600}, vitoria.no_uf("RS"))
    assert uf.startswith("No Rio Grande do Sul, Lula ficou na frente no 1º turno: Lula teve 500 votos")
    assert "Lula perdeu 50 votos e Flávio ganhou 200 votos. Flávio cresceu 250 votos a mais" in uf
    assert "Os dados mostram" not in uf
    mantem = vitoria.dois_turnos({"l1": 50, "f1": 40, "l26": 60, "f26": 45})
    assert "virou" not in mantem and mantem.endswith("Os dados mostram quanto cada um cresceu, não de quais candidatos "
                                                     "vieram os votos novos: eles incluem eleitores de outros candidatos "
                                                     "e quem não tinha votado no 1º turno.") and "Lula manteve a frente" in mantem
