from pathlib import Path

import pytest

from apuracao.config import carregar

CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"


@pytest.fixture
def cfg():
    return carregar(CONFIG)


def test_carrega_config_do_repo(cfg):
    assert {"oficial", "simulado"} <= cfg.ambientes.keys()
    assert cfg.cargos["presidente"] == 1
    assert cfg.coletor.duracao_max_s < 6 * 3600


def test_url_resultado_br(cfg):
    e = cfg.eleicao_por_id("2026-t1-federal")
    url = cfg.url_resultado("oficial", e, "BR", "presidente")
    assert url == (
        "https://resultados.tse.jus.br/oficial"
        "/ele2026/6257/dados/br/br-c0001-e006257-u.json"
    )


def test_url_resultado_municipio(cfg):
    e = cfg.eleicao_por_id("2026-t1-estadual")
    url = cfg.url_resultado("simulado", e, "sp", "governador", cod_mun="71072")
    assert url.endswith("/ele2026/6259/dados/sp/sp71072-c0003-e006259-u.json")


def test_codigo_nao_definido_falha(cfg):
    e = cfg.eleicao_por_id("2026-t2-federal").model_copy(update={"codigo": 0})
    with pytest.raises(ValueError, match="não definido"):
        cfg.url_resultado("oficial", e, "br", "presidente")


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize(
    "eleicao,uf,cargo,cod_mun",
    [
        ("2026-t1-federal", "br", "presidente", None),
        ("2026-t1-federal", "zz", "presidente", None),
        ("2026-t1-estadual", "sp", "governador", None),
        ("2026-t1-federal", "sp", "presidente", "71072"),
        ("2026-t1-estadual", "sp", "governador", "71072"),
    ],
)
def test_urls_batem_com_arquivos_reais(cfg, eleicao, uf, cargo, cod_mun):
    url = cfg.url_resultado("oficial", cfg.eleicao_por_id(eleicao), uf, cargo, cod_mun)
    assert (FIXTURES / url.rsplit("/", 1)[-1]).is_file()


def test_url_config_municipios_bate_com_arquivo_real(cfg):
    e = cfg.eleicao_por_id("2026-t1-federal")
    caminho = cfg.urls.config_municipios.format(ciclo=e.ciclo, eleicao=e.codigo)
    assert (FIXTURES / caminho.rsplit("/", 1)[-1]).is_file()


def test_codigos_2o_turno_batem_com_config_oficial(cfg):
    """O código do 2º turno no toml é o `cdt2` da eleição de 1º turno no ele-c.json oficial."""
    import json

    ele = json.loads((FIXTURES / "ele-c.json").read_text())
    cdt2 = {e["cd"]: e["cdt2"] for pl in ele["pl"] if pl["c"] == "ele2026" for e in pl["e"]}
    for t1, t2 in [("2026-t1-federal", "2026-t2-federal"), ("2026-t1-estadual", "2026-t2-estadual")]:
        assert cdt2[str(cfg.eleicao_por_id(t1).codigo)] == str(cfg.eleicao_por_id(t2).codigo)
