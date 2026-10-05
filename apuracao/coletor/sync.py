"""Sincroniza data/raw com o Cloudflare R2, em processo separado do coletor.

uv run python -m apuracao.coletor.sync --dir-raw data/raw

Snapshots são imutáveis (o coletor nunca sobrescreve), então cada arquivo é
enviado uma única vez. Os já enviados ficam registrados em
{dir_raw}/.sync-enviados (uma chave por linha, só acréscimo); arquivo que falhou
é tentado de novo na próxima passada. Ao receber SIGTERM/SIGINT, faz uma última
passada completa antes de sair.

Credenciais via ambiente (nunca no código nem no log):
  R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

from apuracao.coletor.log import LogJson
from apuracao.coletor.snapshot import SUFIXO_DADO, SUFIXO_META

REGISTRO = ".sync-enviados"


class Destino(Protocol):
    def enviar(self, chave: str, caminho: Path) -> None: ...


class DestinoR2:
    """R2 via API compatível com S3 (boto3)."""

    def __init__(self, account_id: str, access_key: str, secret_key: str, bucket: str):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
            config=Config(retries={"max_attempts": 3, "mode": "standard"},
                          connect_timeout=10, read_timeout=30),
        )

    @classmethod
    def do_ambiente(cls) -> DestinoR2:
        faltando = [v for v in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
                    if not os.environ.get(v)]
        if faltando:
            raise SystemExit(f"variáveis de ambiente ausentes: {', '.join(faltando)}")
        return cls(os.environ["R2_ACCOUNT_ID"], os.environ["R2_ACCESS_KEY_ID"],
                   os.environ["R2_SECRET_ACCESS_KEY"], os.environ["R2_BUCKET"])

    def enviar(self, chave: str, caminho: Path) -> None:
        tipo = "application/gzip" if caminho.name.endswith(".gz") else "application/json"
        self.s3.upload_file(str(caminho), self.bucket, chave, ExtraArgs={"ContentType": tipo})


class DestinoPasta:
    """Cópia para outra pasta local (redundância ou testes manuais)."""

    def __init__(self, raiz: Path):
        self.raiz = Path(raiz)

    def enviar(self, chave: str, caminho: Path) -> None:
        import shutil

        destino = self.raiz / chave
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(caminho, destino)


def pendentes(dir_raw: Path, enviados: set[str]) -> list[tuple[str, Path]]:
    """Arquivos publicados pelo coletor ainda não enviados, do mais antigo ao mais novo."""
    saida = []
    for p in Path(dir_raw).rglob("*"):
        nome = p.name
        if nome.startswith(".") or not (nome.endswith(SUFIXO_DADO) or nome.endswith(SUFIXO_META)):
            continue  # ignora .tmp do coletor, o registro e qualquer outra coisa
        chave = p.relative_to(dir_raw).as_posix()
        if chave not in enviados:
            saida.append((chave, p))
    saida.sort(key=lambda x: x[1].name)
    return saida


class Sincronizador:
    def __init__(self, dir_raw: Path, destino: Destino, log: LogJson,
                 prefixo: str = "", workers: int = 8):
        self.dir_raw = Path(dir_raw)
        self.destino = destino
        self.log = log
        self.prefixo = prefixo.strip("/")
        self.workers = workers
        self.registro = self.dir_raw / REGISTRO
        self.enviados = self._ler_registro()
        self._trava = threading.Lock()

    def _ler_registro(self) -> set[str]:
        try:
            return {l for l in self.registro.read_text().splitlines() if l}
        except FileNotFoundError:
            return set()

    def _marcar(self, chave: str) -> None:
        with self._trava:
            self.enviados.add(chave)
            with open(self.registro, "a") as f:
                f.write(chave + "\n")

    def _enviar_um(self, chave: str, caminho: Path) -> bool:
        destino = f"{self.prefixo}/{chave}" if self.prefixo else chave
        try:
            self.destino.enviar(destino, caminho)
        except Exception as e:
            self.log.contar("sync_erros")
            self.log.evento("sync_erro", chave=chave, erro=f"{type(e).__name__}: {e}")
            return False
        self._marcar(chave)
        self.log.contar("sync_enviados")
        return True

    def passada(self) -> tuple[int, int]:
        """Envia tudo que está pendente. Devolve (enviados, falhas)."""
        self.dir_raw.mkdir(parents=True, exist_ok=True)
        lista = pendentes(self.dir_raw, self.enviados)
        if not lista:
            return 0, 0
        with ThreadPoolExecutor(self.workers) as ex:
            resultados = list(ex.map(lambda x: self._enviar_um(*x), lista))
        ok = sum(resultados)
        self.log.evento("sync_passada", enviados=ok, falhas=len(resultados) - ok)
        return ok, len(resultados) - ok

    def rodar(self, parar: threading.Event, intervalo_s: float = 15.0) -> None:
        self.log.evento("sync_inicio", dir_raw=str(self.dir_raw), ja_enviados=len(self.enviados))
        while not parar.is_set():
            try:
                self.passada()
            except Exception as e:  # nada derruba o laço
                self.log.contar("sync_erros")
                self.log.evento("sync_erro_inesperado", erro=repr(e))
            parar.wait(intervalo_s)
        # Passada final: o coletor pode ter gravado algo depois da última.
        try:
            self.passada()
        finally:
            self.log.resumo()
            self.log.evento("sync_fim")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.coletor.sync")
    ap.add_argument("--dir-raw", type=Path, default=Path("data/raw"))
    ap.add_argument("--prefixo", default="raw", help="prefixo das chaves no bucket")
    ap.add_argument("--intervalo", type=float, default=15.0)
    ap.add_argument("--destino-pasta", type=Path, help="copia para uma pasta em vez do R2")
    args = ap.parse_args(argv)

    destino = DestinoPasta(args.destino_pasta) if args.destino_pasta else DestinoR2.do_ambiente()
    sinc = Sincronizador(args.dir_raw, destino, LogJson(), prefixo=args.prefixo)
    parar = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda s, _f: (sinc.log.evento("sinal", sinal=signal.Signals(s).name), parar.set()))
    sinc.rodar(parar, args.intervalo)
    return 0


if __name__ == "__main__":
    sys.exit(main())
