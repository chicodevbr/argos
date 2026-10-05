"""Gravação de snapshots brutos.

Layout: {dir_raw}/{eleicao}/{uf}/{arquivo}/{ts_coleta_utc}.json.gz
Ao lado de cada snapshot fica um {ts_coleta_utc}.meta.json com os metadados
HTTP (url, ETag, Last-Modified, sha256). O conteúdo é gravado exatamente como
veio do TSE; o timestamp de atualização do próprio JSON fica dentro dele e é
extraído pelo parser, não aqui.

Garantias:
- Nunca sobrescreve: o arquivo final é criado com os.link, que falha se já existir.
- Escrita atômica: grava em .tmp e só então publica o nome final. Um processo
  morto no meio da escrita deixa no máximo um .tmp órfão, nunca um .json.gz truncado.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

SUFIXO_DADO = ".json.gz"
SUFIXO_META = ".meta.json"


@dataclass(frozen=True)
class Meta:
    url: str
    ts_coleta: str
    status: int
    etag: str | None
    last_modified: str | None
    sha256: str
    bytes: int


def ts_arquivo(momento: datetime) -> str:
    """Timestamp UTC ordenável e seguro para nome de arquivo: 20261025T201530.123456Z"""
    return momento.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def sha256(conteudo: bytes) -> str:
    return hashlib.sha256(conteudo).hexdigest()


def diretorio(dir_raw: Path, eleicao: int, uf: str, arquivo: str) -> Path:
    return Path(dir_raw) / str(eleicao) / uf / arquivo


def _publicar(destino: Path, dados: bytes) -> bool:
    """Grava `dados` em `destino` sem nunca sobrescrever. False se já existir."""
    tmp = destino.with_name(f".{destino.name}.{os.getpid()}.tmp")
    with open(tmp, "wb") as f:
        f.write(dados)
        f.flush()
        os.fsync(f.fileno())
    try:
        os.link(tmp, destino)
        return True
    except FileExistsError:
        return False
    finally:
        tmp.unlink(missing_ok=True)


def gravar(
    dir_raw: Path,
    eleicao: int,
    uf: str,
    arquivo: str,
    conteudo: bytes,
    *,
    url: str,
    momento: datetime,
    status: int,
    etag: str | None,
    last_modified: str | None,
) -> Path:
    """Grava um snapshot novo e devolve o caminho do .json.gz."""
    pasta = diretorio(dir_raw, eleicao, uf, arquivo)
    pasta.mkdir(parents=True, exist_ok=True)
    base = ts_arquivo(momento)
    dado_gz = gzip.compress(conteudo, mtime=0)

    # Colisão de timestamp é praticamente impossível (microssegundos), mas se
    # acontecer, ganha sufixo em vez de sobrescrever.
    nome, n = base, 0
    while not _publicar(pasta / f"{nome}{SUFIXO_DADO}", dado_gz):
        n += 1
        nome = f"{base}-{n}"

    meta = Meta(
        url=url,
        ts_coleta=momento.astimezone(timezone.utc).isoformat(),
        status=status,
        etag=etag,
        last_modified=last_modified,
        sha256=sha256(conteudo),
        bytes=len(conteudo),
    )
    _publicar(pasta / f"{nome}{SUFIXO_META}", json.dumps(asdict(meta)).encode())
    return pasta / f"{nome}{SUFIXO_DADO}"


def ultimo_meta(dir_raw: Path, eleicao: int, uf: str, arquivo: str) -> Meta | None:
    """Metadados do snapshot mais recente já gravado (para retomar após reinício)."""
    pasta = diretorio(dir_raw, eleicao, uf, arquivo)
    if not pasta.is_dir():
        return None
    metas = sorted(p for p in pasta.iterdir() if p.name.endswith(SUFIXO_META))
    for p in reversed(metas):
        try:
            return Meta(**json.loads(p.read_text()))
        except (OSError, ValueError, TypeError):
            continue
    return None
