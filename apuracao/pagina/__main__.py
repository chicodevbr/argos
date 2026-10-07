"""uv run python -m apuracao.pagina [--saida data/pagina/abstencao-1o-turno-2026.html]
uv run python -m apuracao.pagina --recorte brasilia|rio|estado-rj   (abstenção por região; ver pagina/recortes.py)
uv run python -m apuracao.pagina --recorte rio --site site   (também grava site/<subpasta>/index.html completo,
    para hospedar fora do claude.ai, ex.: Netlify; --brasilia = --recorte brasilia)"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apuracao.config import carregar
from apuracao.pagina import perdas, regioes, votos
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


# Páginas do site estático (Netlify), na ordem da barra de navegação: (rótulo, subpasta).
SITE = [("Abstenção · Brasília", ""), ("Abstenção · Rio", "rio-de-janeiro"),
        ("Abstenção · Estado do Rio", "estado-do-rio"), ("Votos · Brasília", "votos")]

NAV_CSS = """<style>
.site-nav { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 20; display: flex; gap: 4px;
  overflow-x: auto; padding: 8px 20px; background: var(--superficie); border-bottom: 1px solid var(--linha);
  margin: -32px -20px 28px;  /* de borda a borda: desfaz o padding do body das páginas */
  font: 500 .85rem var(--texto); }
.site-nav a { color: var(--tinta-2); text-decoration: none; padding: 6px 10px; border-radius: var(--raio); white-space: nowrap; }
.site-nav a:hover { background: var(--fundo); color: var(--tinta); }
.site-nav a[aria-current="page"] { background: var(--tinta); color: var(--superficie); }
.site-nav a:focus-visible { outline: 2px solid var(--dado-atual); outline-offset: 2px; }
</style>
"""


def barra(atual: str) -> str:
    links = "".join(f'<a href="/{sub + "/" if sub else ""}"{' aria-current="page"' if sub == atual else ""}>{rot}</a>'
                    for rot, sub in SITE)
    return f'<nav class="site-nav" aria-label="Páginas">{links}</nav>\n'


def documento_completo(pagina: Path, destino: Path, nav: str | None = None) -> Path:
    """Copia a página como documento HTML completo (o <title> e o <style> dela ficam no <head>).
    `nav`: subpasta da página no site; acrescenta a barra com as páginas de SITE."""
    corpo = Path(pagina).read_text()
    i = corpo.index("</style>") + len("</style>")
    extra_head, topo = (NAV_CSS, barra(nav)) if nav is not None else ("", "")
    html = ESQUELETO + corpo[:i] + "\n" + extra_head + "</head>\n<body>\n" + topo + corpo[i:] + "\n</body>\n</html>\n"
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
    ap.add_argument("--votos", action="store_true", help="com --recorte: página de votos por candidato")
    ap.add_argument("--perdas", action="store_true", help="página \"Onde Lula perdeu votos\" (municípios, 2022 e 2014)")
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    ap.add_argument("--sem-barra", action="store_true", help="--site sem a barra de navegação entre as páginas")
    ap.add_argument("--site", type=Path, default=None,
                    help="também grava <pasta>/[subpasta do recorte/]index.html como documento completo")
    args = ap.parse_args(argv)
    recorte = "brasilia" if args.brasilia else args.recorte
    sub = ""
    if args.perdas:
        sub = "onde-lula-perdeu"
        saida = perdas.gerar(args.dir_parquet, args.malha, args.saida or Path("data/pagina/onde-lula-perdeu-2026.html"))
    elif recorte:
        cfg = carregar(args.config)
        r = RECORTES[recorte](cfg)
        if args.votos:
            sub = f"{r.subpasta}/votos".lstrip("/") if r.subpasta else "votos"
            saida = votos.gerar(r, cfg, args.dir_parquet,
                                args.saida or Path("data/pagina") / r.saida.replace("abstencao", "votos"))
        else:
            sub = r.subpasta
            saida = regioes.gerar(r, cfg, args.dir_parquet, args.saida or Path("data/pagina") / r.saida)
    else:
        saida = gerar(args.dir_parquet, args.malha, args.saida or Path("data/pagina/abstencao-1o-turno-2026.html"))
    print(f"página gerada: {saida} ({saida.stat().st_size / 1e6:.1f} MB)")
    if args.site:
        index = documento_completo(saida, args.site / sub / "index.html", None if args.sem_barra else sub)
        print(f"site: {index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
