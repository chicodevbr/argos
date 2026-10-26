"""Página "As pesquisas do 2º turno": cálculo dos válidos, última de cada instituto e frases. Sem rede."""

from apuracao.pagina import pesquisas

CSV = """instituto,campo_inicio,campo_fim,amostra,margem,lula,flavio,outros,fonte
A,2026-10-23,2026-10-24,2000,2,45,46,9,
B,2026-10-22,2026-10-24,1500,1,50,40,10,https://exemplo/b
A,2026-10-19,2026-10-21,2000,2,44,46,10,
"""


def _ler(tmp_path):
    arq = tmp_path / "p.csv"
    arq.write_text(CSV)
    return pesquisas.ler(arq)


def test_validos_ordem_e_ultima(tmp_path):
    ps = _ler(tmp_path)
    assert [(p["instituto"], p["fim"], p["ultima"]) for p in ps] == [
        ("A", "2026-10-24", True), ("B", "2026-10-24", True),
        ("A", "2026-10-21", False)]
    assert ps[0]["lula_v"] == round(100 * 45 / 91, 2) and ps[1]["lula_v"] == 55.56
    assert ps[0]["fonte"] == pesquisas.WIKIPEDIA and ps[1]["fonte"] == "https://exemplo/b"


def test_resumo_e_textos(tmp_path):
    ps = _ler(tmp_path)
    r = pesquisas.resumo(ps, 51.0)
    assert r["institutos"] == 2 and r["todas"]["n"] == 3 and r["ultimas"]["n"] == 2
    assert r["todas"]["flavio_frente"] == 2 and r["todas"]["lula_frente"] == 1
    # dentro da margem: só A (24/10): |51 - 49,45| = 1,55 <= 2
    assert r["todas"]["na_margem"] == 1
    t = pesquisas.textos(r)
    assert "3 pesquisas nacionais de 2 institutos" in t["lede"] and "2 mostraram Flávio à frente" in t["lede"]
    assert "Só 1 das 3 pesquisas" in t["media"]


def test_dados_do_repositorio_consistentes():
    ps = pesquisas.ler()
    assert len(ps) == 30 and sum(p["ultima"] for p in ps) == 20
    # campo encerrado de 20 a 24/10, ou divulgada nesses dias (com a nota)
    assert all(("2026-10-20" <= p["fim"] <= "2026-10-24" or p["nota"]) and p["inicio"] <= p["fim"] for p in ps)
    assert all(40 < p["lula"] < 55 and 40 < p["flavio"] < 55 and 0 < p["margem"] <= 3 for p in ps)
