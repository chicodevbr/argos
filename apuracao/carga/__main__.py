"""uv run python -m apuracao.carga --ano 2022 [--sem-download]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apuracao.carga.baixar import baixar_ano
from apuracao.carga.tse_csv import carregar_ano
from apuracao.coletor.log import LogJson
from apuracao.config import carregar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.carga")
    ap.add_argument("--ano", type=int, action="append", required=True)
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    ap.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    ap.add_argument("--sem-download", action="store_true", help="usa só os zips já baixados")
    args = ap.parse_args(argv)
    cfg, log = carregar(args.config), LogJson()
    for ano in args.ano:
        if not args.sem_download:
            baixar_ano(cfg, ano, log)
        carregar_ano(ano, cfg.historico.dir, args.dir_parquet, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
