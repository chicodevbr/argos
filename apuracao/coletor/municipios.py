"""Baixa a configuração de municípios (EA12, `mun-e<eleicao>-cm.json`) de uma eleição.

O arquivo é gravado em data/raw/{eleicao}/config/... como qualquer snapshot (dado
bruto é a fonte da verdade) e devolvido já parseado para montar os alvos de município.
Se o download falhar, usa o último gravado em disco; senão, a lista de reserva do
repositório (coletor.municipios_reserva); senão devolve None e o coletor segue só
com os arquivos br/UF.
"""

from __future__ import annotations

import gzip
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from apuracao.coletor import snapshot
from apuracao.coletor.log import LogJson
from apuracao.config import Config, Eleicao
from apuracao.modelo import ea12

UF_CONFIG = "config"


def url_config(cfg: Config, ambiente: str, eleicao: Eleicao) -> str:
    caminho = cfg.urls.config_municipios.format(ciclo=eleicao.ciclo, eleicao=eleicao.codigo)
    return cfg.url_base(ambiente) + caminho


def carregar(
    cfg: Config, ambiente: str, eleicao: Eleicao, dir_raw: Path, log: LogJson,
    transport: httpx.BaseTransport | None = None, tentativas: int = 3, espera_s: float = 2.0,
) -> list[ea12.Municipio] | None:
    url = url_config(cfg, ambiente, eleicao)
    arquivo = url.rsplit("/", 1)[-1].removesuffix(".json")
    with httpx.Client(transport=transport, timeout=30, follow_redirects=True,
                      headers={"User-Agent": "apuracao-coletor/0.1"}) as cliente:
        for n in range(1, tentativas + 1):
            try:
                resp = cliente.get(url)
                if resp.status_code == 200:
                    municipios = ea12.parse(resp.content)
                    snapshot.gravar(dir_raw, eleicao.codigo, UF_CONFIG, arquivo, resp.content,
                                    url=url, momento=datetime.now(timezone.utc), status=200,
                                    etag=resp.headers.get("ETag"),
                                    last_modified=resp.headers.get("Last-Modified"))
                    log.evento("config_municipios", eleicao=eleicao.id, municipios=len(municipios))
                    return municipios
                log.evento("config_municipios_erro", eleicao=eleicao.id, status=resp.status_code, tentativa=n)
                if resp.status_code == 404:
                    break  # não insistir em 404 (o TSE pode bloquear o IP)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.evento("config_municipios_erro", eleicao=eleicao.id, erro=repr(e), tentativa=n)
            if n < tentativas:
                time.sleep(espera_s * n)

    pasta = snapshot.diretorio(dir_raw, eleicao.codigo, UF_CONFIG, arquivo)
    gravados = sorted(pasta.glob(f"*{snapshot.SUFIXO_DADO}")) if pasta.is_dir() else []
    for caminho in reversed(gravados):
        try:
            municipios = ea12.parse(gzip.decompress(caminho.read_bytes()))
            log.evento("config_municipios_do_disco", eleicao=eleicao.id, arquivo=str(caminho))
            return municipios
        except Exception:
            continue
    reserva = cfg.coletor.municipios_reserva
    if reserva and Path(reserva).is_file():
        try:
            municipios = ea12.parse(Path(reserva).read_bytes())
            log.evento("config_municipios_reserva", eleicao=eleicao.id, arquivo=str(reserva),
                       municipios=len(municipios))
            return municipios
        except Exception as e:
            log.evento("config_municipios_erro", eleicao=eleicao.id, reserva=str(reserva), erro=repr(e))
    log.evento("config_municipios_indisponivel", eleicao=eleicao.id,
               msg="seguindo só com arquivos br/UF")
    return None
