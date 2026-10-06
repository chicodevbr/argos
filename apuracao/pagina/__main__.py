"""uv run python -m apuracao.pagina [--saida data/pagina/abstencao-1o-turno-2026.html]
uv run python -m apuracao.pagina --recorte brasilia|rio|estado-rj   (abstenção por região; ver pagina/recortes.py)
uv run python -m apuracao.pagina --recorte rio --site site   (também grava site/<subpasta>/index.html completo,
    para hospedar fora do claude.ai, ex.: Netlify; --brasilia = --recorte brasilia)"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apuracao.config import carregar
from apuracao.pagina import regioes
from apuracao.pagina.recortes import RECORTES
from apuracao.pagina.abstencao import gerar


# O claude.ai embrulha a página num esqueleto ao publicar (doctype, charset, viewport e um reset
# mínimo). Fora dele (Netlify, GitHub Pages...), o arquivo precisa trazer isso.
ESQUELETO = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<style>body { margin: 0; } img { max-width: 100%; } [hidden] { display: none !important; }</style>
"""


def documento_completo(pagina: Path, destino: Path) -> Path:
    """Copia a página como documento HTML completo (o <title> e o <style> dela ficam no <head>)."""
    corpo = Path(pagina).read_text()
    i = corpo.index("</style>") + len("</style>")
    html = ESQUELETO + corpo[:i] + "\n</head>\n<body>\n" + corpo[i:] + "\n</body>\n</html>\n"
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(html)
    return destino


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.pagina")
    ap.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    ap.add_argument("--malha", type=Path, default=Path("data/raw/ibge/malha-municipios-minima.json"))
    ap.add_argument("--saida", type=Path, default=None)
    ap.add_argument("--recorte", choices=sorted(RECORTES), help="página por região (Brasília, cidade ou estado do Rio)")
    ap.add_argument("--brasilia", action="store_true", help="atalho para --recorte brasilia")
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    ap.add_argument("--site", type=Path, default=None,
                    help="também grava <pasta>/[subpasta do recorte/]index.html como documento completo")
    args = ap.parse_args(argv)
    recorte = "brasilia" if args.brasilia else args.recorte
    sub = ""
    if recorte:
        cfg = carregar(args.config)
        r = RECORTES[recorte](cfg)
        sub = r.subpasta
        saida = regioes.gerar(r, cfg, args.dir_parquet, args.saida or Path("data/pagina") / r.saida)
    else:
        saida = gerar(args.dir_parquet, args.malha, args.saida or Path("data/pagina/abstencao-1o-turno-2026.html"))
    print(f"página gerada: {saida} ({saida.stat().st_size / 1e6:.1f} MB)")
    if args.site:
        index = documento_completo(saida, args.site / sub / "index.html")
        print(f"site: {index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
