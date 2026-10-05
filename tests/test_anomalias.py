"""Detector de anomalias: uma anomalia plantada por tipo, a partir de JSONs reais."""

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from apuracao.coletor import snapshot
from apuracao.coletor.log import LogJson
from apuracao.modelo import consultas
from apuracao.modelo.anomalias import verificar
from apuracao.modelo.construir import construir

FIX = Path(__file__).parent / "fixtures"
T0 = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)
BR = json.loads((FIX / "br-c0001-e006257-u.json").read_bytes())


def gravar(raw, j, minuto, uf="br", nome="br-c0001-e006257-u"):
    snapshot.gravar(raw, 6257, uf, nome, json.dumps(j).encode(), url="u",
                    momento=T0 + timedelta(minutes=minuto), status=200, etag=None, last_modified=None)


def versao(hg, pct=None, mais_votos=0, menos_votos_flavio=0):
    j = json.loads(json.dumps(BR))
    j["hg"] = hg
    if pct is not None:
        j["s"]["pstn"] = str(pct).replace(".", ",")
    for agr in j["carg"][0]["agr"]:
        for par in agr["par"]:
            for c in par.get("cand", []):
                if c["n"] == "22":
                    c["vap"] = str(int(c["vap"]) + mais_votos - menos_votos_flavio)
    j["v"]["vv"] = str(int(j["v"]["vv"]) + mais_votos - menos_votos_flavio)
    return j


def tipos(tmp_path, raw):
    construir(raw, tmp_path / "pq", LogJson(io.StringIO()))
    return [a.tipo for a in verificar(consultas.conectar(tmp_path / "pq"))]


def test_sequencia_normal_sem_anomalias(tmp_path):
    raw = tmp_path / "raw"
    gravar(raw, versao("02:00:00", pct=90), 0)
    gravar(raw, versao("02:30:00", pct=95, mais_votos=10), 1)
    gravar(raw, versao("02:59:31", pct=100, mais_votos=20), 2)
    assert tipos(tmp_path, raw) == []


def test_versao_antiga_servida_depois(tmp_path):
    raw = tmp_path / "raw"
    gravar(raw, versao("02:30:00", pct=95), 0)
    gravar(raw, versao("02:00:00", pct=95), 1)  # coletada depois, gerada antes
    assert "versao_antiga" in tipos(tmp_path, raw)


def test_secoes_diminuem(tmp_path):
    raw = tmp_path / "raw"
    gravar(raw, versao("02:00:00", pct=95), 0)
    gravar(raw, versao("02:30:00", pct=90), 1)
    assert tipos(tmp_path, raw) == ["secoes_diminuem"]


def test_votos_diminuem(tmp_path):
    raw = tmp_path / "raw"
    gravar(raw, versao("02:00:00", pct=95), 0)
    gravar(raw, versao("02:30:00", pct=96, menos_votos_flavio=1000), 1)
    assert tipos(tmp_path, raw) == ["votos_diminuem"]


def test_validos_nao_batem(tmp_path):
    raw = tmp_path / "raw"
    j = versao("02:00:00")
    j["v"]["vv"] = str(int(j["v"]["vv"]) + 5)
    gravar(raw, j, 0)
    assert tipos(tmp_path, raw) == ["validos_nao_batem"]


def test_comparecimento_excede(tmp_path):
    raw = tmp_path / "raw"
    j = versao("02:00:00")
    j["e"]["c"] = str(int(j["e"]["esi"]) + 1)
    gravar(raw, j, 0)
    assert "comparecimento_excede" in tipos(tmp_path, raw)


@pytest.mark.parametrize("diferenca,esperado", [(0, False), (1, True)])
def test_soma_das_ufs_no_fim(tmp_path, diferenca, esperado):
    """28 arquivos de UF (cópias do de SP, 100%) e um BR = 28 x SP (+ diferença)."""
    raw = tmp_path / "raw"
    sp = json.loads((FIX / "sp-c0001-e006257-u.json").read_bytes())
    ufs = [f"u{i:01d}" if i < 10 else f"{chr(97 + i - 10)}x" for i in range(28)]
    for uf in ufs:
        gravar(raw, sp, 0, uf=uf, nome=f"{uf}-c0001-e006257-u")
    vap = {c["n"]: int(c["vap"]) for a in sp["carg"][0]["agr"] for p in a["par"] for c in p.get("cand", [])}
    br = json.loads(json.dumps(BR))
    for a in br["carg"][0]["agr"]:
        for p in a["par"]:
            for c in p.get("cand", []):
                c["vap"] = str(28 * vap.get(c["n"], 0) + (diferenca if c["n"] == "13" else 0))
    gravar(raw, br, 0)
    assert ("soma_ufs_nao_bate" in tipos(tmp_path, raw)) is esperado


def test_log_de_anomalia_sai_uma_vez_e_painel_avisa(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from apuracao.modelo.__main__ import registrar_anomalias

    raw = tmp_path / "raw"
    gravar(raw, versao("02:00:00", pct=95), 0)
    gravar(raw, versao("02:30:00", pct=90), 1)  # seções diminuem
    construir(raw, tmp_path / "pq", LogJson(io.StringIO()))

    saida = io.StringIO()
    log, vistas = LogJson(saida), set()
    registrar_anomalias(tmp_path / "pq", log, vistas)
    registrar_anomalias(tmp_path / "pq", log, vistas)  # 2ª passada: nada novo
    eventos = [json.loads(l) for l in saida.getvalue().splitlines()]
    assert [e["tipo"] for e in eventos if e["evento"] == "anomalia"] == ["secoes_diminuem"]

    monkeypatch.setenv("APURACAO_DIR_PARQUET", str(tmp_path / "pq"))
    monkeypatch.setenv("APURACAO_CONFIG", str(Path(__file__).parent.parent / "config" / "eleicoes.toml"))
    monkeypatch.setenv("APURACAO_ELEICAO", "2026-t1-federal")
    at = AppTest.from_file(str(Path(__file__).parent.parent / "apuracao/app/main.py"), default_timeout=60)
    at.run()
    assert not at.exception, at.exception
    assert any("anomalia" in w.value for w in at.warning)
