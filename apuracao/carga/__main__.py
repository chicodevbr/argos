"""uv run python -m apuracao.carga --ano 2022 [--sem-download]
uv run python -m apuracao.carga --malha   (malha municipal do IBGE, para os mapas)"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apuracao.carga.baixar import baixar_ano
from apuracao.carga.ibge import baixar_malha
from apuracao.carga.tse_csv import carregar_ano
from apuracao.coletor.log import LogJson
from apuracao.config import carregar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.carga")
    ap.add_argument("--ano", type=int, action="append", default=[])
    ap.add_argument("--malha", action="store_true", help="baixa a malha municipal do IBGE")
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    ap.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    ap.add_argument("--sem-download", action="store_true", help="usa só os zips já baixados")
    args = ap.parse_args(argv)
    cfg, log = carregar(args.config), LogJson()
    if not args.ano and not args.malha:
        ap.error("informe --ano e/ou --malha")
    if args.malha:
        baixar_malha(cfg, log)
    for ano in args.ano:
        if not args.sem_download:
            baixar_ano(cfg, ano, log)
        carregar_ano(ano, cfg.historico.dir, args.dir_parquet, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
