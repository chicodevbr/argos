"""Histórico da projeção: grava com dados novos, não duplica, lê em ordem."""

import json
from pathlib import Path

from apuracao.config import carregar
from apuracao.modelo import consultas
from apuracao.projecao import historico
from tests.apoio_2t import montar_pq_2t

CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"


def test_registra_uma_vez_por_dado_novo(tmp_path):
    pq = montar_pq_2t(tmp_path)
    cfg, arq = carregar(CONFIG), tmp_path / "hist.jsonl"
    assert historico.registrar(consultas.conectar(pq), cfg, arq, n_sim=200) == 1
    assert historico.registrar(consultas.conectar(pq), cfg, arq, n_sim=200) == 0  # nada novo
    linha = json.loads(arq.read_text().splitlines()[0])
    assert linha["eleicao"] == 6258 and linha["nome_a"] == "LULA" and linha["pct_a_inf"] <= linha["pct_a"] <= linha["pct_a_sup"]
    df = historico.ler(arq, 6258)
    assert len(df) == 1 and historico.ler(arq, 9999).empty


def test_falha_na_projecao_nao_derruba_a_construcao(tmp_path):
    import io
    from apuracao.coletor.log import LogJson
    from apuracao.modelo.__main__ import registrar_projecao
    saida = io.StringIO()
    registrar_projecao(tmp_path / "nao_existe", tmp_path / "config_inexistente.toml", tmp_path / "h.jsonl", LogJson(saida))
    assert "projecao_erro" in saida.getvalue()
