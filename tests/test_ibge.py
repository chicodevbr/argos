"""Malha municipal do IBGE: leitura com arredondamento e download (sem rede)."""

import io
import json
from pathlib import Path

import httpx
import pytest

from apuracao.carga.ibge import baixar_malha, ler_malha
from apuracao.coletor.log import LogJson
from apuracao.config import carregar

FIX = Path(__file__).parent / "fixtures" / "malha-amostra.json"
CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"


def aneis(geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return [anel for poly in polys for anel in poly]


def test_ler_malha_arredonda_e_mantem_aneis_fechados():
    m = ler_malha(FIX, casas=3)
    assert {f["properties"]["codarea"] for f in m["features"]} == {"1200013", "1200401", "3550308", "5300108"}
    for f in m["features"]:
        for anel in aneis(f["geometry"]):
            assert anel[0] == anel[-1] and len(anel) >= 4
            assert all(round(x, 3) == x and round(y, 3) == y for x, y in anel)
            assert all(a != b for a, b in zip(anel, anel[1:]))  # sem pontos repetidos seguidos


def test_ler_malha_orienta_aneis_para_o_d3():
    """Contorno externo horário (área assinada < 0), buracos anti-horários; o IBGE vem ao contrário."""
    from apuracao.carga.ibge import area_assinada
    bruto = json.loads(FIX.read_text())
    externo_ibge = bruto["features"][0]["geometry"]["coordinates"]
    externo_ibge = externo_ibge[0] if bruto["features"][0]["geometry"]["type"] == "Polygon" else externo_ibge[0][0]
    assert area_assinada(externo_ibge) > 0  # o arquivo do IBGE é anti-horário (GeoJSON)
    for f in ler_malha(FIX)["features"]:
        g = f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        for poly in polys:
            assert area_assinada(poly[0]) < 0
            assert all(area_assinada(buraco) > 0 for buraco in poly[1:])


def _cfg(tmp_path):
    cfg = carregar(CONFIG)
    return cfg.model_copy(update={"ibge": cfg.ibge.model_copy(update={"arquivo": tmp_path / "malha.json"})})


def test_baixar_malha_grava_o_bruto(tmp_path):
    corpo = FIX.read_bytes()
    t = httpx.MockTransport(lambda req: httpx.Response(200, content=corpo))
    destino = baixar_malha(_cfg(tmp_path), LogJson(io.StringIO()), transport=t)
    assert destino.read_bytes() == corpo


def test_baixar_malha_recusa_resposta_sem_municipios(tmp_path):
    t = httpx.MockTransport(lambda req: httpx.Response(200, content=b'{"type": "FeatureCollection", "features": []}'))
    with pytest.raises(ValueError, match="sem features"):
        baixar_malha(_cfg(tmp_path), LogJson(io.StringIO()), transport=t)
    assert not (tmp_path / "malha.json").exists()
