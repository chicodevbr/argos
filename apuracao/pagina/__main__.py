"""uv run python -m apuracao.pagina [--saida data/pagina/abstencao-1o-turno-2026.html]
uv run python -m apuracao.pagina --brasilia   (abstenção por região administrativa do DF)"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apuracao.config import carregar
from apuracao.pagina import brasilia
from apuracao.pagina.abstencao import gerar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.pagina")
    ap.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    ap.add_argument("--malha", type=Path, default=Path("data/raw/ibge/malha-municipios-minima.json"))
    ap.add_argument("--saida", type=Path, default=None)
    ap.add_argument("--brasilia", action="store_true", help="página por região administrativa do DF")
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    args = ap.parse_args(argv)
    if args.brasilia:
        saida = brasilia.gerar(carregar(args.config), args.dir_parquet,
                               args.saida or Path("data/pagina/abstencao-brasilia-2026.html"))
    else:
        saida = gerar(args.dir_parquet, args.malha, args.saida or Path("data/pagina/abstencao-1o-turno-2026.html"))
    print(f"página gerada: {saida} ({saida.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
