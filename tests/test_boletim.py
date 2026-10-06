"""Boletim de urna: leitura do zip real, download pelo catálogo (servidor falso) e a
reconstrução da curva da noite. Fixtures: seções reais do 2º turno de 2022 (AC, AL, ZZ)."""

import io
import json
from pathlib import Path

import httpx
import pandas as pd

from apuracao.carga import boletim
from apuracao.coletor.log import LogJson
from apuracao.config import carregar
from apuracao.projecao import noite

FIX = Path(__file__).parent / "fixtures" / "historico"
CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"
ZIPS = ["bweb_2t_AC_311020221535.zip", "bweb_2t_AL_311020221535.zip", "bweb_2t_ZZ_311020221535.zip"]


def test_le_so_presidente_com_codigos_normalizados():
    al = boletim.ler_zip(FIX / "bweb_2t_AL_311020221535.zip")
    assert set(al["numero"]) == {13, 22, 95, 96}       # governador ficou de fora
    assert (al["cod_mun"].str.len() == 5).all() and (al["zona"] == "0001").all() and (al["secao"] == "0001").all()
    assert al["recebido"].iloc[0] == pd.Timestamp("2022-10-30 17:23:32")
    ac = boletim.ler_zip(FIX / "bweb_2t_AC_311020221535.zip")
    assert ac.groupby(["cod_mun", "zona", "secao"]).ngroups == 4
    assert set(ac["tipo"]) <= {"Nominal", "Branco", "Nulo"}


def test_mais_recente_por_uf():
    nomes = ["x/bweb_2t_AC_311020221535.zip", "x/bweb_2t_AC_011120221000.zip",
             "x/bweb_1t_AC_031020221000.zip", "x/bweb_2t_SP_311020221535.zip"]
    assert boletim._mais_recentes(nomes, 2) == ["x/bweb_2t_AC_011120221000.zip", "x/bweb_2t_SP_311020221535.zip"]


def _servidor(pedidos):
    base = "https://cdn.tse.jus.br/estatistica/sead/eleicoes/eleicoes2022/buweb/"
    recursos = [{"url": base + n} for n in ZIPS] + [{"url": base + "bweb_1t_AC_031020221000.zip"},
                                                    {"url": base + ZIPS[0] + ".sha512"}]

    def responder(req: httpx.Request) -> httpx.Response:
        pedidos.append(str(req.url))
        if "package_show" in str(req.url):
            if "2026" in str(req.url):
                return httpx.Response(404, json={"success": False})
            return httpx.Response(200, json={"success": True, "result": {"resources": recursos}})
        return httpx.Response(200, content=(FIX / req.url.path.rsplit("/", 1)[-1]).read_bytes())
    return httpx.MockTransport(responder)


def test_carrega_pelo_catalogo_e_apaga_zips(tmp_path):
    cfg = carregar(CONFIG)
    cfg.historico.dir = tmp_path / "raw"
    pedidos, saida = [], io.StringIO()
    destino = boletim.carregar(cfg, 2022, 2, tmp_path / "pq", LogJson(saida), guardar_zip=False,
                               transport=_servidor(pedidos))
    assert destino == tmp_path / "pq" / "hist_boletim" / "2022_t2.parquet"
    assert not any("bweb_1t" in p or "sha512" in p for p in pedidos)   # só o turno pedido
    assert list((tmp_path / "raw" / "2022" / "boletim").glob("*.zip")) == []
    df = pd.read_parquet(destino)
    assert set(df["uf"]) == {"ac", "al", "zz"} and (df["ano"] == 2022).all()
    # pacote ainda não publicado: avisa e não grava
    assert boletim.carregar(cfg, 2026, 1, tmp_path / "pq", LogJson(saida), transport=_servidor(pedidos)) is None
    assert "boletim_indisponivel" in saida.getvalue()


def test_curva_da_noite(tmp_path):
    cfg = carregar(CONFIG)
    cfg.historico.dir = tmp_path / "raw"
    boletim.carregar(cfg, 2022, 2, tmp_path / "pq", LogJson(io.StringIO()), transport=_servidor([]))
    c = noite.curva(tmp_path / "pq", 2022, 2, [13, 22])
    # o boletim do exterior chegou às 01h20, mas só entra na divulgação às 17h
    assert c["ts"].min() == pd.Timestamp("2022-10-30 17:01")
    assert c["pct_secoes"].is_monotonic_increasing and c["pct_secoes"].iloc[-1] == 100
    fim = c[c["ts"] == c["ts"].max()].set_index("numero")
    df = pd.read_parquet(tmp_path / "pq" / "hist_boletim" / "2022_t2.parquet")
    for n in (13, 22):
        assert fim.loc[n, "votos"] == df[(df["numero"] == n) & (df["tipo"] == "Nominal")]["votos"].sum()
    assert abs(fim["pct_validos"].sum() - 100) < 1e-9
    r = noite.ritmo_regioes(tmp_path / "pq", 2022, 2)
    assert set(r["regiao"].dropna()) == {"Norte", "Nordeste", "Exterior"}
    assert (r.groupby(["regiao", "uf"], dropna=False)["pct_secoes"].max() == 100).all()
    assert noite.disponiveis(tmp_path / "pq") == [(2022, 2)]


def test_viradas():
    ts = pd.date_range("2022-10-30 17:01", periods=4, freq="1min")
    c = pd.DataFrame({"ts": list(ts) * 2, "numero": [13] * 4 + [22] * 4,
                      "votos": [10, 20, 40, 60] + [15, 30, 35, 50]})
    assert noite.viradas(c, 13, 22) == [ts[2]]


def test_pagina_noite_renderiza(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest
    cfg = carregar(CONFIG)
    cfg.historico.dir = tmp_path / "raw"
    boletim.carregar(cfg, 2022, 2, tmp_path / "pq", LogJson(io.StringIO()), transport=_servidor([]))
    monkeypatch.setenv("APURACAO_DIR_PARQUET", str(tmp_path / "pq"))
    pagina = Path(__file__).parent.parent / "apuracao" / "app" / "paginas" / "noite.py"
    at = AppTest.from_file(str(pagina), default_timeout=30).run()
    assert not at.exception
    assert any("2026" in i.value for i in at.info)            # aviso: 1º turno de 2026 ainda não carregado
    assert [m.label for m in at.metric][:2] == ["Lula", "Jair Bolsonaro"]
