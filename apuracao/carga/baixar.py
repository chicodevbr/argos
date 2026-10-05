"""Baixa os zips de dados abertos de um ano para data/raw/historico/<ano>/.

Não baixa de novo se o arquivo local já tem o tamanho anunciado pelo servidor.
Download em .tmp + os.replace: um download interrompido nunca vira um zip truncado.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx

from apuracao.coletor.log import LogJson
from apuracao.config import Config


def baixar_ano(cfg: Config, ano: int, log: LogJson, transport: httpx.BaseTransport | None = None) -> list[Path]:
    h = cfg.historico
    destino_dir = Path(h.dir) / str(ano)
    destino_dir.mkdir(parents=True, exist_ok=True)
    saida = []
    with httpx.Client(transport=transport, timeout=httpx.Timeout(60, read=300), follow_redirects=True) as c:
        for modelo in (h.votacao, h.detalhe):
            url = h.base + modelo.format(ano=ano)
            destino = destino_dir / url.rsplit("/", 1)[-1]
            tamanho = c.head(url).headers.get("Content-Length")
            if destino.exists() and tamanho and destino.stat().st_size == int(tamanho):
                log.evento("historico_ja_baixado", arquivo=str(destino))
                saida.append(destino)
                continue
            tmp = destino.with_name(destino.name + ".tmp")
            with c.stream("GET", url) as resp, open(tmp, "wb") as f:
                resp.raise_for_status()
                for bloco in resp.iter_bytes(1 << 20):
                    f.write(bloco)
            os.replace(tmp, destino)
            log.evento("historico_baixado", arquivo=str(destino), bytes=destino.stat().st_size)
            saida.append(destino)
    return saida
