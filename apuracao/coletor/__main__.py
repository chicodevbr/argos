"""uv run python -m apuracao.coletor --config config/eleicoes.toml --ambiente simulado --eleicao 2026-t1-federal"""

from __future__ import annotations

import argparse
import asyncio
import signal
import sys
from pathlib import Path

from apuracao.config import carregar
from apuracao.coletor.alvos import montar_alvos
from apuracao.coletor.coletor import Parametros, rodar
from apuracao.coletor.log import LogJson


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.coletor")
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    ap.add_argument("--ambiente", choices=["oficial", "simulado"], required=True)
    ap.add_argument(
        "--eleicao", action="append", required=True,
        help="id da eleição em config/eleicoes.toml (pode repetir)",
    )
    ap.add_argument("--duracao", type=float, help="segundos; padrão: coletor.duracao_max_s")
    ap.add_argument("--dir-raw", type=Path, help="padrão: coletor.dir_raw")
    args = ap.parse_args(argv)

    cfg = carregar(args.config)
    eleicoes = [cfg.eleicao_por_id(i) for i in args.eleicao]
    alvos = montar_alvos(cfg, args.ambiente, eleicoes)
    c = cfg.coletor
    params = Parametros(
        dir_raw=args.dir_raw or c.dir_raw,
        timeout_s=c.timeout_s,
        concorrencia=c.concorrencia,
        backoff_base_s=c.backoff_base_s,
        backoff_max_s=c.backoff_max_s,
        max_espera_404_s=c.max_espera_404_s,
    )
    log = LogJson()
    for e in eleicoes:
        if not e.confirmado:
            log.evento("aviso", msg="código de eleição não confirmado", eleicao=e.id, codigo=e.codigo)

    async def principal() -> None:
        parar = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda s=sig: (log.evento("sinal", sinal=s.name), parar.set()))
        await rodar(alvos, params, log, parar, duracao_max_s=args.duracao or c.duracao_max_s)

    asyncio.run(principal())
    return 0


if __name__ == "__main__":
    sys.exit(main())
