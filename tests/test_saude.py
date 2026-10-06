"""Saúde da coleta local a partir do log do coletor."""

import json
from datetime import datetime, timedelta, timezone

from apuracao.app.saude import BYTES_FINAIS, ler_saude

AGORA = datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc)


def log(tmp_path, eventos):
    p = tmp_path / "coletor.jsonl"
    p.write_text("".join(json.dumps({"ts": (AGORA - timedelta(seconds=s)).isoformat(), "evento": ev, **extra}) + "\n"
                         for s, ev, extra in eventos))
    return p


def test_ativo(tmp_path):
    p = log(tmp_path, [(600, "inicio", {}), (90, "snapshot", {"url": "u/br.json"}),
                       (30, "resumo", {"requisicoes": 500, "snapshots": 40, "erros": 2})])
    s = ler_saude(p, AGORA)
    assert s.estado == "ok" and s.requisicoes == 500 and s.snapshots == 40 and s.erros == 2
    assert s.ultimo_snapshot == AGORA - timedelta(seconds=90)


def test_atencao_e_parado_por_tempo(tmp_path):
    assert ler_saude(log(tmp_path, [(600, "inicio", {}), (200, "resumo", {})]), AGORA).estado == "atencao"
    assert ler_saude(log(tmp_path, [(900, "inicio", {}), (400, "resumo", {})]), AGORA).estado == "parado"


def test_encerrado_mesmo_que_recente(tmp_path):
    s = ler_saude(log(tmp_path, [(600, "inicio", {}), (20, "resumo", {}), (10, "fim", {})]), AGORA)
    assert s.estado == "parado"


def test_reinicio_depois_do_fim_volta_a_ok(tmp_path):
    s = ler_saude(log(tmp_path, [(900, "inicio", {}), (800, "fim", {}), (60, "inicio", {}), (10, "resumo", {})]), AGORA)
    assert s.estado == "ok"


def test_ultimo_erro(tmp_path):
    p = log(tmp_path, [(600, "inicio", {}), (50, "erro", {"tipo": "timeout", "url": "x/ele2026/6258/dados/sp/sp-c0001-e006258-u.json"}),
                       (10, "resumo", {"erros": 1})])
    s = ler_saude(p, AGORA)
    assert "timeout" in s.ultimo_erro and "sp-c0001-e006258-u.json" in s.ultimo_erro


def test_sem_log(tmp_path):
    assert ler_saude(tmp_path / "nao_existe.jsonl", AGORA).estado == "ausente"


def test_log_grande_le_so_o_fim(tmp_path):
    p = tmp_path / "coletor.jsonl"
    velho = json.dumps({"ts": (AGORA - timedelta(hours=3)).isoformat(), "evento": "snapshot", "url": "u", "x": "y" * 200}) + "\n"
    with open(p, "w") as f:
        f.write(json.dumps({"ts": (AGORA - timedelta(hours=4)).isoformat(), "evento": "inicio"}) + "\n")
        for _ in range(2 * BYTES_FINAIS // len(velho)):
            f.write(velho)
        f.write(json.dumps({"ts": (AGORA - timedelta(seconds=5)).isoformat(), "evento": "resumo", "snapshots": 9}) + "\n")
    s = ler_saude(p, AGORA)
    assert s.estado == "ok" and s.snapshots == 9
