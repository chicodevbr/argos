"""uv run python -m apuracao.pagina [--saida data/pagina/abstencao-1o-turno-2026.html]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apuracao.pagina.abstencao import gerar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.pagina")
    ap.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    ap.add_argument("--malha", type=Path, default=Path("data/raw/ibge/malha-municipios-minima.json"))
    ap.add_argument("--saida", type=Path, default=Path("data/pagina/abstencao-1o-turno-2026.html"))
    args = ap.parse_args(argv)
    saida = gerar(args.dir_parquet, args.malha, args.saida)
    print(f"página gerada: {saida} ({saida.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
