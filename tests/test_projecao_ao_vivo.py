"""Projeção a partir das tabelas (snapshots reais de 1º turno + 2º turno sintético)."""

from pathlib import Path

import pytest

from apuracao.config import carregar
from apuracao.modelo import consultas
from apuracao.projecao.ao_vivo import eleicao_base, projecao_por_uf, projetar_ao_vivo
from tests.apoio_2t import montar_pq_2t

CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"


@pytest.fixture
def pq(tmp_path):
    return montar_pq_2t(tmp_path)


def test_eleicao_base():
    cfg = carregar(CONFIG)
    assert eleicao_base(cfg, cfg.eleicao_por_id("2026-t2-federal")).id == "2026-t1-federal"
    assert eleicao_base(cfg, cfg.eleicao_por_id("2026-t2-estadual")).id == "2026-t1-estadual"


def test_projecao_presidente(pq):
    cfg = carregar(CONFIG)
    con = consultas.conectar(pq)
    p = projetar_ao_vivo(con, cfg, cfg.eleicao_por_id("2026-t2-federal"), 1, "br", n_sim=300)
    assert (p.num_a, p.num_b) == (13, 22) and (p.nome_a, p.nome_b) == ("LULA", "FLAVIO BOLSONARO")
    assert p.municipios_base == 2
    assert p.r.pct_a_inf <= p.r.pct_a <= p.r.pct_a_sup
    assert 0 < p.r.pct_contado < 100  # SP com dados, AC não
    assert set(projecao_por_uf(p)["uf"]) == {"sp", "ac"}


def test_projecao_nao_se_aplica(pq):
    cfg = carregar(CONFIG)
    con = consultas.conectar(pq)
    t2 = cfg.eleicao_por_id("2026-t2-federal")
    assert projetar_ao_vivo(con, cfg, cfg.eleicao_por_id("2026-t1-federal"), 1, "br") is None  # 1º turno
    assert projetar_ao_vivo(con, cfg, t2, 1, "sp") is None    # presidente só no nível Brasil
    assert projetar_ao_vivo(con, cfg, t2, 3, "br") is None    # governador só por UF
