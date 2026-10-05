"""Constrói as tabelas Parquet a partir do bruto.

uv run python -m apuracao.modelo construir [--loop 15]
uv run python -m apuracao.modelo municipios --cm tests/fixtures/mun-e006257-cm.json
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

from apuracao.coletor.log import LogJson
from apuracao.modelo import consultas
from apuracao.modelo.anomalias import verificar
from apuracao.modelo.construir import construir, construir_municipios


def registrar_anomalias(dir_parquet: Path, log: LogJson, vistas: set) -> None:
    """Loga cada anomalia nova uma vez (evento "anomalia"); nunca derruba a construção."""
    try:
        for a in verificar(consultas.conectar(dir_parquet)):
            chave = (a.tipo, a.arquivo_raw, a.detalhe)
            if chave not in vistas:
                vistas.add(chave)
                log.contar("anomalias")
                log.evento("anomalia", tipo=a.tipo, eleicao=a.eleicao, cargo=a.cargo,
                           abrangencia=a.abrangencia, detalhe=a.detalhe, arquivo_raw=a.arquivo_raw)
    except Exception as e:
        log.evento("anomalia_erro_verificacao", erro=repr(e))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.modelo")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("construir", help="processa snapshots novos de data/raw")
    c.add_argument("--dir-raw", type=Path, default=Path("data/raw"))
    c.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    c.add_argument("--loop", type=float, help="repete a cada N segundos até SIGTERM/SIGINT")
    m = sub.add_parser("municipios", help="gera municipios.parquet a partir de um -cm.json (EA12)")
    m.add_argument("--cm", type=Path, required=True)
    m.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    args = ap.parse_args(argv)

    log = LogJson()
    if args.cmd == "municipios":
        n = construir_municipios(args.cm, args.dir_parquet)
        log.evento("municipios", total=n)
        return 0

    vistas: set = set()
    if not args.loop:
        construir(args.dir_raw, args.dir_parquet, log)
        registrar_anomalias(args.dir_parquet, log, vistas)
        return 0

    parar = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: parar.set())
    falhas: set[str] = set()
    while not parar.is_set():
        try:
            if construir(args.dir_raw, args.dir_parquet, log, ignorar=falhas):
                registrar_anomalias(args.dir_parquet, log, vistas)
        except Exception as e:  # nada derruba o laço
            log.evento("modelo_erro_inesperado", erro=repr(e))
        parar.wait(args.loop)
    log.resumo()
    return 0


if __name__ == "__main__":
    sys.exit(main())
