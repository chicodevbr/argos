"""uv run python -m apuracao.pagina [--saida data/pagina/abstencao-1o-turno-2026.html]
uv run python -m apuracao.pagina --recorte brasilia|rio|estado-rj   (abstenção por região; ver pagina/recortes.py)
uv run python -m apuracao.pagina --recorte brasilia|rio --turnos --site site   (1º x 2º turno por região; pagina/turnos.py)
uv run python -m apuracao.pagina --recorte brasilia --urnas --site site   (quem venceu em cada urna; pagina/urnas.py)
uv run python -m apuracao.pagina --recorte brasilia --zonas --site site   (votos por zona eleitoral; pagina/zonas.py)
uv run python -m apuracao.pagina --recorte brasilia --ras --site site   (quem venceu em cada RA x zona; pagina/ras.py)
uv run python -m apuracao.pagina --pesquisas --site site   (pesquisas do 2º turno x resultado; ver pagina/pesquisas.py)
uv run python -m apuracao.pagina --recorte rio --site site   (também grava site/<subpasta>/index.html completo,
    para hospedar fora do claude.ai, ex.: Netlify; --brasilia = --recorte brasilia)"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from apuracao.config import carregar
from apuracao.pagina import perdas, pesquisas, ras, regioes, turnos, urnas, vitoria, votos, zonas
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


# Páginas do site estático (Netlify): (rótulo na barra, subpasta, grupo, título, descrição, tema). A raiz é a
# página inicial (--inicio), que mostra DESTAQUE em cima, com o placar, e as outras agrupadas por turno e lugar,
# na ordem desta lista. Endereços já divulgados não mudam: rio-de-janeiro/, estado-do-rio/, votos/.
# Brasília foi a raiz até 07/10/2026 e passou para brasilia/. Em brasilia/ e rio-de-janeiro/, a abstenção do
# 1º turno deu lugar ao 1º x 2º turno (pagina/turnos.py), a pedido do usuário em 26/10/2026.
DESTAQUE = "como-lula-venceu"
# Na página inicial, cada página mostra uma frase dela mesma: (chave em D.textos, frases i:j), lida do HTML já
# gerado em site/, para a inicial não repetir números à mão. Gere as páginas antes de --inicio.
# Onde há barrinha de vitórias (RAs, urnas), a frase fala de outra coisa.
FATOS = {"como-lula-venceu": ("decisivo", 0, 2), "pesquisas": ("media", 0, 2), "brasilia": ("lede", 0, 2),
         "brasilia/urnas": ("lede", 1, 2), "brasilia/zonas": ("lede", 1, 2), "brasilia/ras": ("lede", 1, 2),
         "rio-de-janeiro/ras": ("lede", 1, 2), "rio-de-janeiro/zonas": ("lede", 1, 2), "rio-de-janeiro": ("lede", 0, 2)}
SITE = [
    ("Como Lula venceu", "como-lula-venceu", "2º turno · Brasil", "Como Lula venceu em 2026",
     "Onde a vantagem cresceu, onde o 22 perdeu votos e a abstenção, nos 5.570 municípios, comparados a 2022.",
     "Votos"),
    ("Pesquisas", "pesquisas", "2º turno · Brasil", "As pesquisas do 2º turno",
     "As 30 pesquisas dos cinco dias antes da votação, de 20 institutos, comparadas ao resultado.", "Pesquisas"),
    ("RAs · Brasília", "brasilia/ras", "2º turno · Brasília", "Quem venceu em cada RA de Brasília",
     "As 35 regiões administrativas do DF, comparadas à contagem por zona eleitoral.", "Votos"),
    ("1º x 2º turno · Brasília", "brasilia", "2º turno · Brasília", "Do 1º ao 2º turno em Brasília",
     "Votos de Lula e Flávio e abstenção nas 35 RAs do DF, no 1º e no 2º turno.", "Votos"),
    ("Urnas · Brasília", "brasilia/urnas", "2º turno · Brasília", "Quem venceu em cada urna de Brasília",
     "As 6.969 urnas do DF no 2º turno, nos 622 locais de votação, no mapa.", "Votos"),
    ("Zonas · Brasília", "brasilia/zonas", "2º turno · Brasília", "Os votos por zona eleitoral em Brasília",
     "As 19 zonas eleitorais do DF no 2º turno e as RAs que cada uma junta.", "Votos"),
    ("RAs · Rio", "rio-de-janeiro/ras", "2º turno · Rio de Janeiro", "Quem venceu em cada RA do Rio",
     "As regiões administrativas da cidade, comparadas à contagem por zona eleitoral.", "Votos"),
    ("1º x 2º turno · Rio", "rio-de-janeiro", "2º turno · Rio de Janeiro", "Do 1º ao 2º turno no Rio",
     "Votos de Lula e Flávio e abstenção nas RAs da cidade, no 1º e no 2º turno, com os bairros.", "Votos"),
    ("Zonas · Rio", "rio-de-janeiro/zonas", "2º turno · Rio de Janeiro", "Os votos por zona eleitoral no Rio",
     "As zonas eleitorais da cidade no 2º turno e as RAs que cada uma junta.", "Votos"),
    ("Onde Lula perdeu no 1º turno", "onde-lula-perdeu", "1º turno · Brasil", "Onde Lula perdeu votos no 1º turno",
     "Os 5.570 municípios, comparados a 2022 (Lula) e a 2014 (Dilma).", "Votos"),
    ("Votos · Brasília", "votos", "1º turno · Brasília", "Votos em Brasília",
     "Todos os candidatos a presidente nas RAs do DF, 2026 e 2022.", "Votos"),
    ("Onde Lula perdeu no 1º turno · Brasília", "onde-lula-perdeu/brasilia", "1º turno · Brasília",
     "Onde Lula perdeu votos em Brasília no 1º turno",
     "As 35 RAs do DF, comparadas a 2022 (Lula) e a 2014 (Dilma).", "Votos"),
    ("Abstenção · Estado do Rio", "estado-do-rio", "1º turno · Rio de Janeiro", "Abstenção no Estado do Rio",
     "Os 92 municípios do estado, comparados a 2022.", "Abstenção"),
]

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
    itens = [("Início", "")] + [(rot, sub) for rot, sub, *_ in SITE]
    links = "".join(f'<a href="/{sub + "/" if sub else ""}"{' aria-current="page"' if sub == atual else ""}>{rot}</a>'
                    for rot, sub in itens)
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


def dados_pagina(arq: Path) -> dict | None:
    """O `const D = {...};` de uma página gerada (JSON numa linha só); None se o arquivo não existe ou não tem."""
    if not Path(arq).exists():
        return None
    m = re.search(r"const D = (\{.*?\});\n", Path(arq).read_text())
    return json.loads(m.group(1)) if m else None


def frases(texto: str, i: int, j: int) -> str:
    """Frases i:j de um texto (corta em ponto seguido de maiúscula ou número; "4.430.191" e "51,79" não cortam)."""
    return " ".join(re.split(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú0-9])", texto.strip())[i:j])


def vitorias_pagina(d: dict) -> dict | None:
    """Em quantas unidades (RAs, urnas) cada um venceu, para a barrinha do cartão: 1º x 2º turno ou urnas."""
    if d.get("ras") and "t2" in d["ras"][0]:
        ms = [r["t2"]["margem"] for r in d["ras"] if r["t2"]["margem"] is not None]
        return {"a": sum(m > 0 for m in ms), "b": sum(m < 0 for m in ms), "empates": sum(m == 0 for m in ms),
                "de": d["meta"]["abrev"]}
    if d.get("ras") and "dif" in d["ras"][0]:
        t = d["total"]
        return {"a": t["ra_a"], "b": t["ra_b"], "empates": t["ras"] - t["ra_a"] - t["ra_b"], "de": "RAs"}
    if d.get("zonas"):
        t = d["total"]
        return {"a": t["za"], "b": t["zb"], "empates": t["zonas"] - t["za"] - t["zb"], "de": "zonas"}
    if "pontos" in d and "total" in d:
        t = d["total"]
        return {"a": t["sa"], "b": t["sb"], "empates": t["empates"], "de": "urnas"}
    return None


def inicio(destino: Path, placar: dict | None = None) -> Path:
    """Página inicial do site a partir de SITE: o resultado do 2º turno em cima (placar, se dado), as páginas do
    2º turno com uma frase de cada (FATOS) e as do 1º turno numa lista curta. Documento completo, sem a barra (os
    cartões já são a navegação)."""
    from datetime import date
    modelo = Path(__file__).with_name("modelo_inicio.html").read_text()
    paginas = []
    for _, sub, g, tit, desc, tema in SITE:
        d = dados_pagina(Path(destino).parent / sub / "index.html")
        chave = FATOS.get(sub)
        fato = frases(d["textos"][chave[0]], *chave[1:]) if d and chave and chave[0] in d.get("textos", {}) else None
        paginas.append({"sub": sub, "grupo": g, "titulo": tit, "descricao": desc, "tema": tema,
                        "destaque": sub == DESTAQUE, "fato": fato, "vitorias": vitorias_pagina(d) if d else None,
                        "lede": d["textos"].get("lede") if d and sub == DESTAQUE else None})
    dados = {"paginas": paginas, "placar": placar, "atualizado": date.today().strftime("%d/%m/%Y")}
    corpo = modelo.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False) + ";")
    if "__DADOS__" in corpo:
        raise RuntimeError("modelo da página inicial sem o marcador")
    tmp = Path(destino).with_name(".inicio-corpo.html")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(corpo)
    try:
        return documento_completo(tmp, destino)   # sem a barra: os botões já são a navegação
    finally:
        tmp.unlink()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.pagina")
    ap.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    ap.add_argument("--malha", type=Path, default=Path("data/raw/ibge/malha-municipios-minima.json"))
    ap.add_argument("--saida", type=Path, default=None)
    ap.add_argument("--recorte", choices=sorted(RECORTES), help="página por região (Brasília, cidade ou estado do Rio)")
    ap.add_argument("--brasilia", action="store_true", help="atalho para --recorte brasilia")
    ap.add_argument("--votos", action="store_true", help="com --recorte: página de votos por candidato")
    ap.add_argument("--turnos", action="store_true", help="com --recorte: 1º x 2º turno por região")
    ap.add_argument("--urnas", action="store_true", help="com --recorte: quem venceu em cada urna (2º turno)")
    ap.add_argument("--zonas", action="store_true", help="com --recorte: votos por zona eleitoral (2º turno)")
    ap.add_argument("--ras", action="store_true", help="com --recorte: quem venceu em cada RA x zona (2º turno)")
    ap.add_argument("--ano", type=int, default=2026, help="com --turnos: ano (2022 para conferir)")
    ap.add_argument("--inicio", action="store_true", help="com --site: página inicial com os links (raiz do site)")
    ap.add_argument("--perdas", action="store_true", help="página \"Onde Lula perdeu votos\" (municípios, 2022 e 2014)")
    ap.add_argument("--vitoria", action="store_true", help="página \"Como Lula venceu em 2026\" (2º turno, municípios)")
    ap.add_argument("--pesquisas", action="store_true", help="página \"As pesquisas do 2º turno\" (x resultado)")
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    ap.add_argument("--sem-barra", action="store_true", help="--site sem a barra de navegação entre as páginas")
    ap.add_argument("--site", type=Path, default=None,
                    help="também grava <pasta>/[subpasta do recorte/]index.html como documento completo")
    args = ap.parse_args(argv)
    recorte = "brasilia" if args.brasilia else args.recorte
    sub = ""
    if args.inicio:
        if not args.site:
            ap.error("--inicio precisa de --site")
        index = inicio(args.site / "index.html", vitoria.placar(args.dir_parquet, carregar(args.config)))
        print(f"site: {index}")
        return 0
    if args.perdas and recorte:
        cfg = carregar(args.config)
        r = RECORTES[recorte](cfg)
        sub = f"onde-lula-perdeu/{r.subpasta}"
        saida = perdas.gerar_regioes(r, cfg, args.dir_parquet,
                                     args.saida or Path(f"data/pagina/onde-lula-perdeu-{r.id}-2026.html"))
    elif args.vitoria:
        sub = "como-lula-venceu"
        saida = vitoria.gerar(args.dir_parquet, args.malha, args.saida or Path("data/pagina/como-lula-venceu-2026.html"),
                              args.config)
    elif args.pesquisas:
        sub = "pesquisas"
        res = vitoria.placar(args.dir_parquet, carregar(args.config))
        if res is None:
            ap.error("sem o resultado do 2º turno em --dir-parquet")
        saida = pesquisas.gerar(res["lula"], args.saida or Path("data/pagina/pesquisas-2o-turno-2026.html"))
    elif args.perdas:
        sub = "onde-lula-perdeu"
        saida = perdas.gerar(args.dir_parquet, args.malha, args.saida or Path("data/pagina/onde-lula-perdeu-2026.html"))
    elif recorte:
        cfg = carregar(args.config)
        r = RECORTES[recorte](cfg)
        if args.ras:
            sub = f"{r.subpasta}/ras"
            saida = ras.gerar(r, cfg, args.dir_parquet, args.saida or Path(f"data/pagina/ras-{r.id}-2026.html"))
        elif args.zonas:
            sub = f"{r.subpasta}/zonas"
            saida = zonas.gerar(r, cfg, args.dir_parquet, args.saida or Path(f"data/pagina/zonas-{r.id}-2026.html"))
        elif args.urnas:
            sub = f"{r.subpasta}/urnas"
            saida = urnas.gerar(r, cfg, args.dir_parquet, args.saida or Path(f"data/pagina/urnas-{r.id}-2026.html"))
        elif args.turnos:
            sub = r.subpasta   # substitui a página de abstenção do 1º turno (endereço já divulgado)
            saida = turnos.gerar(r, cfg, args.dir_parquet,
                                 args.saida or Path(f"data/pagina/turnos-{r.id}-{args.ano}.html"), args.ano)
        elif args.votos:
            sub = "votos" if r.id == "brasilia" else f"{r.subpasta}/votos"   # /votos/ já divulgado
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
