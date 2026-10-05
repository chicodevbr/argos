"""Painel: consultas (dados.py) e fumaça do app com o AppTest do Streamlit. Sem rede."""

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from apuracao.app import dados
from apuracao.coletor import snapshot
from apuracao.coletor.log import LogJson
from apuracao.modelo import consultas
from apuracao.modelo.construir import construir

FIX = Path(__file__).parent / "fixtures"
MAIN = Path(__file__).parent.parent / "apuracao" / "app" / "main.py"
T0 = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)


def gravar(raw: Path, nome: str, uf: str, minuto: int, conteudo: bytes | None = None):
    snapshot.gravar(raw, 6257, uf, nome.removesuffix(".json"), conteudo or (FIX / nome).read_bytes(),
                    url="u", momento=T0.replace(minute=minuto), status=200, etag=None, last_modified=None)


@pytest.fixture
def pq(tmp_path):
    raw = tmp_path / "raw"
    for nome, uf in [("br-c0001-e006257-u.json", "br"), ("zz-c0001-e006257-u.json", "zz"),
                     ("sp-c0001-e006257-u.json", "sp"), ("sp71072-c0001-e006257-u.json", "sp")]:
        gravar(raw, nome, uf, 0)
    # segundo snapshot do br, mais tarde, para haver evolução
    j = json.loads((FIX / "br-c0001-e006257-u.json").read_bytes())
    j["hg"] = "03:30:00"
    gravar(raw, "br-c0001-e006257-u.json", "br", 30, json.dumps(j).encode())
    construir(raw, tmp_path / "pq", LogJson(io.StringIO()))
    return tmp_path / "pq"


def test_abrangencias_ordem(pq):
    con = consultas.conectar(pq)
    assert dados.abrangencias(con, 6257, 1) == ["br", "sp", "zz", "sp71072"]
    assert dados.abrangencias(con, 6257, 3) == []


def test_resumo_usa_snapshot_mais_recente_em_brasilia(pq):
    r = dados.resumo(consultas.conectar(pq), 6257, 1, "br")
    assert r["ts_brasilia"] == datetime(2026, 10, 5, 3, 30)


def test_destaques_ordenados_pelo_numero(pq):
    df = dados.candidatos(consultas.conectar(pq), 6257, 1, "br")
    assert len(df) == 12
    top3 = df.sort_values("votos", ascending=False).head(3)["numero"].tolist()
    assert dados.destaques(df) == sorted(top3)  # cor segue o número, não a posição


def test_evolucao_e_tabela_ufs(pq):
    con = consultas.conectar(pq)
    numeros = dados.destaques(dados.candidatos(con, 6257, 1, "br"))
    evo = dados.evolucao(con, 6257, 1, "br", numeros)
    assert evo["ts_brasilia"].nunique() == 2 and set(evo["numero"]) == set(numeros)
    tab = dados.tabela_ufs(con, 6257, 1, numeros)
    assert sorted(tab["uf"]) == ["sp", "zz"]
    assert len(tab.columns) == 3 + len(numeros)


def rodar_app(monkeypatch, dir_parquet: Path) -> AppTest:
    monkeypatch.setenv("APURACAO_DIR_PARQUET", str(dir_parquet))
    monkeypatch.setenv("APURACAO_CONFIG", str(Path(__file__).parent.parent / "config" / "eleicoes.toml"))
    at = AppTest.from_file(str(MAIN), default_timeout=30)
    at.run()
    return at


def test_app_sem_dados(monkeypatch, tmp_path):
    at = rodar_app(monkeypatch, tmp_path / "vazio")
    assert not at.exception
    assert "Sem dados" in at.info[0].value


def test_app_renderiza_1o_turno(monkeypatch, pq):
    monkeypatch.setenv("APURACAO_DIR_PARQUET", str(pq))
    monkeypatch.setenv("APURACAO_CONFIG", str(Path(__file__).parent.parent / "config" / "eleicoes.toml"))
    at = AppTest.from_file(str(MAIN), default_timeout=30)
    at.run()
    at.sidebar.selectbox[0].select_index(0).run()  # 2026-t1-federal
    assert not at.exception, at.exception
    rotulos = [m.label for m in at.metric]
    assert rotulos == ["Seções totalizadas", "Comparecimento", "Abstenção", "Brancos", "Nulos"]
    assert at.metric[0].value == "100,00%"
    assert at.metric[1].value == "78,92%"  # bate com o pc do TSE no arquivo br


def test_rotulos_sem_colisao():
    assert dados.rotulos_sem_colisao([47.0, 45.2, 2.9], 3) == [48.2, 45.2, 2.9]
    assert dados.rotulos_sem_colisao([50.1, 49.9], 3) == [52.9, 49.9]
    assert dados.rotulos_sem_colisao([60, 40], 3) == [60, 40]  # longe: não mexe
