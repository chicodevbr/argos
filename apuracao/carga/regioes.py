"""Regiões dentro de um município (RAs do DF, RAs da cidade do Rio...): malha oficial e a região
de cada ponto.

O TSE trata Brasília e o Rio como um município só. Para falar de Ceilândia ou da Tijuca, cada seção
recebe a região do seu local de votação, pelas coordenadas do local (eleitorado_local_votacao) dentro
dos polígonos oficiais. O bairro do cadastro do TSE não serve: no DF, "SETOR LESTE" etc. existem em
várias RAs e o Gama nem aparece com esse nome.

Ponto no polígono por paridade de cruzamentos (ray casting), vetorizado em numpy, sem dependência
nova. Buracos (anéis internos) são respeitados.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import numpy as np

from apuracao.coletor.log import LogJson
from apuracao.config import Config


def baixar(url: str, destino: Path, log: LogJson, transport: httpx.BaseTransport | None = None) -> Path:
    """Baixa um GeoJSON (FeatureCollection) ou JSON de feições do ArcGIS e grava em `destino`."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(transport=transport, timeout=httpx.Timeout(60, read=300), follow_redirects=True) as c:
        resp = c.get(url)
        resp.raise_for_status()
        dados = resp.json()
    if not dados.get("features"):
        raise ValueError(f"{url}: resposta sem features")
    tmp = destino.with_name(destino.name + ".tmp")
    tmp.write_bytes(resp.content)
    os.replace(tmp, destino)
    log.evento("regioes_baixadas", arquivo=str(destino), feicoes=len(dados["features"]))
    return destino


def baixar_df(cfg: Config, log: LogJson, transport: httpx.BaseTransport | None = None) -> list[Path]:
    return [baixar(cfg.df.regioes, cfg.df.arquivo, log, transport)]


def baixar_rio(cfg: Config, log: LogJson, transport: httpx.BaseTransport | None = None) -> list[Path]:
    return [baixar(cfg.rio.regioes, cfg.rio.arquivo, log, transport),
            baixar(cfg.rio.bairros, cfg.rio.arquivo_bairros, log, transport)]


def ler(caminho: Path, campo: str) -> list[dict]:
    """Features com `nome` (propriedade `campo`, como publicada), as propriedades e a geometria."""
    feats = json.loads(Path(caminho).read_text())["features"]
    return [{"nome": f["properties"][campo], "props": f["properties"], "geometria": f["geometry"]}
            for f in feats]


def _no_anel(anel: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    xs, ys = anel[:, 0], anel[:, 1]
    dentro = np.zeros(len(x), dtype=bool)
    j = len(anel) - 1
    for i in range(len(anel)):
        dy = ys[j] - ys[i]
        cruza = ((ys[i] > y) != (ys[j] > y)) & (x < (xs[j] - xs[i]) * (y - ys[i]) / (dy if dy else 1e-300) + xs[i])
        dentro ^= cruza
        j = i
    return dentro


def _poligonos(g: dict) -> list:
    return [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]


def regiao_de(lon, lat, regioes: list[dict]) -> np.ndarray:
    """Nome da RA de cada ponto (None fora de todas ou sem coordenada)."""
    lon = np.asarray(lon, dtype=float)
    lat = np.asarray(lat, dtype=float)
    valido = ~(np.isnan(lon) | np.isnan(lat))
    saida = np.array([None] * len(lon), dtype=object)
    for r in regioes:
        dentro = np.zeros(len(lon), dtype=bool)
        for p in _poligonos(r["geometria"]):
            externo = np.array(p[0], dtype=float)
            # caixa envolvente primeiro: a maioria dos pontos está longe de cada RA
            caixa = valido & (lon >= externo[:, 0].min()) & (lon <= externo[:, 0].max()) \
                & (lat >= externo[:, 1].min()) & (lat <= externo[:, 1].max())
            if not caixa.any():
                continue
            idx = np.flatnonzero(caixa)
            d = _no_anel(externo, lon[idx], lat[idx])
            for buraco in p[1:]:
                d &= ~_no_anel(np.array(buraco, dtype=float), lon[idx], lat[idx])
            dentro[idx] |= d
        saida[dentro & (saida == None)] = r["nome"]  # noqa: E711 (comparação elemento a elemento)
    return saida
