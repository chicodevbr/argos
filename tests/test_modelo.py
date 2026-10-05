"""Parsers EA20/EA12 e construção das tabelas, contra fixtures reais do 1º turno de 2026."""

import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apuracao.coletor import snapshot
from apuracao.coletor.log import LogJson
from apuracao.modelo import consultas, ea12, ea20
from apuracao.modelo.construir import compactar, construir, construir_municipios

FIX = Path(__file__).parent / "fixtures"


def ler(nome: str) -> bytes:
    return (FIX / nome).read_bytes()


# --- EA20 ---------------------------------------------------------------------


def test_ea20_brasil_presidente():
    r = ea20.parse(ler("br-c0001-e006257-u.json"))
    assert (r.eleicao, r.turno, r.cargo, r.tpabr, r.cdabr) == (6257, 1, 1, "br", "br")
    assert r.ts_tse == datetime(2026, 10, 5, 5, 59, 31, tzinfo=timezone.utc)  # 02:59:31 em Brasília
    assert r.secoes_total == r.secoes_totalizadas == 499248 and r.pct_secoes == 100
    assert (r.eleitorado, r.comparecimento, r.abstencao) == (158745502, 125275835, 33469244)
    # c e a são relativos às seções instaladas (esi), não ao eleitorado total (te)
    assert r.eleitorado_instaladas == r.comparecimento + r.abstencao
    assert r.eleitorado_instaladas < r.eleitorado
    assert r.votos_total == r.votos_validos + r.brancos + r.nulos  # van = vansj = 0 neste arquivo
    assert len(r.candidatos) == 12
    assert sum(c.votos for c in r.candidatos) == r.votos_validos
    flavio = next(c for c in r.candidatos if c.numero == 22)
    assert flavio.nome == "FLAVIO BOLSONARO" and flavio.partido == "PL"
    assert flavio.votos == 56104503 and flavio.pct_tse == pytest.approx(47.027772356)
    assert flavio.pct_validos == pytest.approx(47.027772356, abs=1e-6)


def test_ea20_municipio_governador_com_totalizacao_final():
    r = ea20.parse(ler("sp71072-c0003-e006259-u.json"))
    assert (r.tpabr, r.cdabr, r.cargo) == ("mu", "71072", 3)
    assert r.totalizacao_final and r.andamento == "f"
    eleito = [c for c in r.candidatos if c.eleito]
    assert [c.nome for c in eleito] == ["TARCÍSIO"] and eleito[0].situacao == "Eleito"


@pytest.mark.parametrize("arquivo", [p.name for p in FIX.glob("*-u.json")])
def test_ea20_todas_as_fixtures(arquivo):
    r = ea20.parse(ler(arquivo))
    assert r.candidatos and r.ts_tse and r.votos_validos
    assert sum(c.votos for c in r.candidatos if c.destinacao == "Válido") == r.votos_validos


def test_ea20_campos_vazios_antes_da_totalizacao():
    j = json.loads(ler("br-c0001-e006257-u.json"))
    j["and"] = "n"
    for bloco in ("s", "e", "v"):
        j[bloco] = {k: "" for k in j[bloco]}
    for agr in j["carg"][0]["agr"]:
        for par in agr["par"]:
            for c in par["cand"]:
                c["vap"] = c["pvap"] = c["pvapn"] = ""
    r = ea20.parse(j)
    assert r.andamento == "n" and r.votos_validos is None and r.pct_secoes is None
    assert all(c.votos is None and c.pct_validos is None for c in r.candidatos)


def test_ea20_decimal_com_virgula():
    assert ea20.decimal("47,027772356") == pytest.approx(47.027772356)
    assert ea20.decimal("100") == 100.0
    assert ea20.decimal("") is None


# --- EA12 ---------------------------------------------------------------------


def test_ea12_municipios():
    ms = ea12.parse(ler("mun-e006257-cm.json"))
    assert len(ms) == 5757
    brasil = [m for m in ms if m.uf != "zz"]
    assert len(brasil) == 5571 and sum(m.capital for m in brasil) == 27
    assert all(len(m.cod_tse) == 5 for m in ms)
    sp = next(m for m in ms if m.cod_tse == "71072")
    assert (sp.nome, sp.uf, sp.regiao, sp.capital) == ("SÃO PAULO", "sp", "Sudeste", True)
    assert sp.cod_ibge == "3550308"
    assert all(m.cod_ibge is None and m.regiao == "Exterior" for m in ms if m.uf == "zz")


# --- construção ---------------------------------------------------------------


def gravar_fixture(dir_raw: Path, nome: str, uf: str, momento: datetime, conteudo: bytes | None = None) -> None:
    eleicao = int(nome.split("-e")[1][:6])
    snapshot.gravar(dir_raw, eleicao, uf, nome.removesuffix(".json"), conteudo or ler(nome),
                    url=f"https://x/{nome}", momento=momento, status=200, etag=None, last_modified=None)


T0 = datetime(2026, 10, 5, 6, 0, tzinfo=timezone.utc)


@pytest.fixture
def raw(tmp_path):
    d = tmp_path / "raw"
    gravar_fixture(d, "br-c0001-e006257-u.json", "br", T0)
    gravar_fixture(d, "zz-c0001-e006257-u.json", "zz", T0)
    gravar_fixture(d, "sp71072-c0001-e006257-u.json", "sp", T0)
    return d


def test_construir_e_consultar(raw, tmp_path):
    pq, log = tmp_path / "pq", LogJson(io.StringIO())
    assert construir(raw, pq, log) == 3
    con = consultas.conectar(pq)
    abr = {r[0]: r[1] for r in con.execute("SELECT abrangencia, cod_mun FROM snapshot_totais").fetchall()}
    assert abr == {"br": None, "zz": None, "sp71072": "71072"}
    ts_coleta, ts_tse = con.execute(
        "SELECT ts_coleta, ts_tse FROM snapshot_totais WHERE abrangencia = 'br'").fetchone()
    assert ts_coleta == T0.replace(tzinfo=None) and ts_tse == datetime(2026, 10, 5, 5, 59, 31)
    cands = con.execute(consultas.ULTIMOS_CANDIDATOS,
                        {"eleicao": 6257, "cargo": 1, "abrangencia": "br"}).fetchall()
    assert len(cands) == 12


def test_construir_incremental_e_reconstrucao(raw, tmp_path):
    pq, log = tmp_path / "pq", LogJson(io.StringIO())
    construir(raw, pq, log)
    assert construir(raw, pq, log) == 0  # nada novo
    # snapshot novo do br, com votos alterados e geração mais recente
    j = json.loads(ler("br-c0001-e006257-u.json"))
    j["hg"] = "03:30:00"
    gravar_fixture(raw, "br-c0001-e006257-u.json", "br", T0.replace(minute=30), json.dumps(j).encode())
    assert construir(raw, pq, log) == 1
    con = consultas.conectar(pq)
    ult = con.execute(consultas.ULTIMOS_TOTAIS, {"eleicao": 6257, "cargo": 1}).fetchall()
    assert len(ult) == 3  # um por abrangência
    assert con.execute("SELECT count(*) FROM snapshot_totais").fetchone()[0] == 4

    # reconstrução do zero dá o mesmo resultado
    pq2 = tmp_path / "pq2"
    construir(raw, pq2, log)
    con2 = consultas.conectar(pq2)
    for t in ("snapshot_totais", "snapshot_candidatos"):
        assert con.execute(f"SELECT count(*) FROM {t}").fetchone() == con2.execute(f"SELECT count(*) FROM {t}").fetchone()


def test_compactar_nao_duplica(raw, tmp_path):
    pq, log = tmp_path / "pq", LogJson(io.StringIO())
    construir(raw, pq, log)
    gravar_fixture(raw, "br-c0001-e006257-u.json", "br", T0.replace(minute=1),
                   ler("br-c0001-e006257-u.json").replace(b'"hg" : "02:59:31"', b'"hg" : "03:00:00"'))
    construir(raw, pq, log)
    antes = consultas.conectar(pq).execute("SELECT count(*) FROM snapshot_candidatos").fetchone()[0]
    compactar(pq, acima_de=1)
    assert len(list((pq / "snapshot_totais").glob("*.parquet"))) == 1
    assert consultas.conectar(pq).execute("SELECT count(*) FROM snapshot_candidatos").fetchone()[0] == antes


def test_snapshot_corrompido_nao_derruba(raw, tmp_path):
    gravar_fixture(raw, "br-c0001-e006257-u.json", "br", T0.replace(minute=5), b"{nao e json")
    log = LogJson(io.StringIO())
    falhas: set[str] = set()
    assert construir(raw, tmp_path / "pq", log, ignorar=falhas) == 3
    assert log.contadores["modelo_erros"] == 1 and len(falhas) == 1
    assert construir(raw, tmp_path / "pq", log, ignorar=falhas) == 0
    assert log.contadores["modelo_erros"] == 1  # não repete o erro


def test_municipios_parquet(tmp_path):
    assert construir_municipios(FIX / "mun-e006257-cm.json", tmp_path) == 5757
    con = consultas.conectar(tmp_path)
    assert con.execute("SELECT nome FROM municipios WHERE cod_ibge = '3550308'").fetchone() == ("SÃO PAULO",)


def test_municipios_parquet_de_snapshot_bruto_gz(tmp_path):
    gz = tmp_path / "cm.json.gz"
    gz.write_bytes(gzip.compress(ler("mun-e006257-cm.json")))
    assert construir_municipios(gz, tmp_path / "pq") == 5757
