"""Malha municipal do IBGE (GeoJSON) para os mapas.

O arquivo baixado fica em data/raw/ibge/ como veio da API (dado bruto). Para desenhar,
`ler_malha` arredonda as coordenadas (3 casas ~ 110 m, suficiente na escala de um mapa
do Brasil) e devolve um FeatureCollection com `codarea` = código IBGE de 7 dígitos.

Orientação dos anéis: o IBGE segue o GeoJSON (RFC 7946: contorno externo anti-horário),
mas o d3-geo (usado pelo Vega/Altair e pela página compartilhável) trabalha na esfera e
espera o contorno externo no sentido HORÁRIO; ao contrário, cada município vira "o globo
menos o município" e o mapa sai como uma elipse cheia. `ler_malha` reorienta para o d3.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx

from apuracao.coletor.log import LogJson
from apuracao.config import Config


def baixar_malha(cfg: Config, log: LogJson, transport: httpx.BaseTransport | None = None) -> Path:
    destino = Path(cfg.ibge.arquivo)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(transport=transport, timeout=httpx.Timeout(60, read=300), follow_redirects=True) as c:
        resp = c.get(cfg.ibge.malha)
        resp.raise_for_status()
        dados = resp.json()  # falha aqui se não for JSON
    if dados.get("type") != "FeatureCollection" or not dados.get("features"):
        raise ValueError("malha do IBGE sem features")
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.write_bytes(resp.content)
    os.replace(tmp, destino)
    log.evento("malha_baixada", arquivo=str(destino), municipios=len(dados["features"]))
    return destino


def _anel(anel: list, casas: int) -> list:
    """Arredonda um anel e remove pontos consecutivos que ficaram iguais (mesmo desenho, menos bytes)."""
    saida = []
    for x, y in anel:
        pt = [round(x, casas), round(y, casas)]
        if not saida or pt != saida[-1]:
            saida.append(pt)
    if saida[0] != saida[-1]:
        saida.append(saida[0])
    return saida if len(saida) >= 4 else [[round(x, casas), round(y, casas)] for x, y in anel]


def area_assinada(anel: list) -> float:
    """Shoelace em lon/lat: > 0 = anti-horário, < 0 = horário."""
    return sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(anel, anel[1:])) / 2


def _poligono(aneis: list, casas: int) -> list:
    """Contorno externo horário e buracos anti-horários (convenção do d3-geo)."""
    saida = []
    for i, anel in enumerate(aneis):
        a = _anel(anel, casas)
        horario = area_assinada(a) < 0
        if (i == 0) != horario:
            a = a[::-1]
        saida.append(a)
    return saida


def _arredondar(coords, casas: int):
    """Polygon: lista de anéis; MultiPolygon: lista de polígonos."""
    if isinstance(coords[0][0][0], (int, float)):  # Polygon
        return _poligono(coords, casas)
    return [_poligono(p, casas) for p in coords]


def ler_malha(caminho: Path, casas: int = 3) -> dict:
    """FeatureCollection com coordenadas arredondadas e só a propriedade `codarea`."""
    bruto = json.loads(Path(caminho).read_text())
    feats = []
    for f in bruto["features"]:
        g = f["geometry"]
        feats.append({"type": "Feature", "properties": {"codarea": str(f["properties"]["codarea"])},
                      "geometry": {"type": g["type"], "coordinates": _arredondar(g["coordinates"], casas)}})
    return {"type": "FeatureCollection", "features": feats}
