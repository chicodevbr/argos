"""Página "As pesquisas do 2º turno": as pesquisas nacionais de Lula x Flávio com campo encerrado nos últimos
5 dias antes da votação (20 a 24/10/2026), comparadas ao resultado oficial.

Os números das pesquisas vêm de pesquisas_2t_2026.csv (votos totais como divulgados; compilados da tabela da
Wikipédia "Opinion polling for the 2026 Brazilian presidential election" e conferidos com as reportagens da coluna
`fonte` quando há). O resultado vem das tabelas do TSE (vitoria.placar).

Votos válidos = Lula / (Lula + Flávio) sobre os votos totais publicados. Com totais inteiros, pode diferir em até
~0,5 ponto do válido que o instituto divulgou (ex.: Datafolha 20-22/10: 46 x 47 dá 49,5%; divulgado 50%).
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

DADOS = Path(__file__).with_name("pesquisas_2t_2026.csv")
MODELO = Path(__file__).with_name("modelo_pesquisas.html")
WIKIPEDIA = "https://en.wikipedia.org/wiki/Opinion_polling_for_the_2026_Brazilian_presidential_election"
TITULO = "As pesquisas do 2º turno"


def ler(caminho: Path = DADOS) -> list[dict]:
    linhas = []
    with open(caminho, newline="") as f:
        for r in csv.DictReader(f):
            l, fl = float(r["lula"]), float(r["flavio"])
            linhas.append({
                "instituto": r["instituto"], "inicio": r["campo_inicio"], "fim": r["campo_fim"],
                "amostra": int(r["amostra"]), "margem": float(r["margem"]),
                "lula": l, "flavio": fl, "outros": float(r["outros"]),
                "lula_v": round(100 * l / (l + fl), 2), "fonte": r["fonte"] or WIKIPEDIA,
            })
    # mais recente primeiro: fim do campo, depois início (campo mais curto = mais recente)
    linhas.sort(key=lambda p: (p["fim"], p["inicio"]), reverse=True)
    vistos = set()
    for p in linhas:   # a última de cada instituto entra na média principal
        p["ultima"] = p["instituto"] not in vistos
        vistos.add(p["instituto"])
    return linhas


def _media(ps: list[dict]) -> float:
    return sum(p["lula_v"] for p in ps) / len(ps)


def resumo(pesquisas: list[dict], lula_real: float) -> dict:
    ultimas = [p for p in pesquisas if p["ultima"]]
    def bloco(ps):
        m = _media(ps)
        return {"n": len(ps), "lula": round(m, 2), "flavio": round(100 - m, 2),
                "flavio_frente": sum(p["lula_v"] < 50 for p in ps), "lula_frente": sum(p["lula_v"] > 50 for p in ps),
                "erro_medio": round(sum(abs(lula_real - p["lula_v"]) for p in ps) / len(ps), 2),
                # erro na diferença entre os dois (Lula - Flávio), em pontos: o dobro do erro no válido de Lula
                "erro_vantagem": round(2 * (lula_real - m), 2),
                "na_margem": sum(abs(lula_real - p["lula_v"]) <= p["margem"] for p in ps),
                "max_lula": max(p["lula_v"] for p in ps)}
    return {"todas": bloco(pesquisas), "ultimas": bloco(ultimas), "lula_real": lula_real,
            "institutos": len(ultimas)}


def _n(v: float, c: int = 1) -> str:
    return f"{v:,.{c}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def textos(r: dict) -> dict:
    u, t, real = r["ultimas"], r["todas"], r["lula_real"]
    lede = (f"Nos últimos cinco dias antes da votação, {t['n']} pesquisas nacionais de {r['institutos']} institutos "
            f"mediram Lula contra Flávio Bolsonaro. {t['flavio_frente']} mostraram Flávio à frente nos votos válidos. "
            f"Lula venceu com {_n(real, 2)}%, mais do que qualquer uma delas apontava (a maior dava "
            f"{_n(t['max_lula'])}%).")
    media = (f"Na média da última pesquisa de cada instituto, Flávio tinha {_n(u['flavio'])}% e Lula "
             f"{_n(u['lula'])}% dos votos válidos. A diferença real entre os dois ficou {_n(abs(u['erro_vantagem']))} "
             f"pontos mais favorável a Lula do que a média indicava. Só {t['na_margem']} das {t['n']} pesquisas "
             f"tinham o resultado de Lula dentro da própria margem de erro.")
    return {"lede": lede, "media": media}


def montar(lula_real: float, caminho: Path = DADOS) -> dict:
    ps = ler(caminho)
    r = resumo(ps, lula_real)
    return {"pesquisas": ps, "resumo": r, "textos": textos(r), "wikipedia": WIKIPEDIA,
            "atualizado": date.today().strftime("%d/%m/%Y")}


def gerar(lula_real: float, saida: Path) -> Path:
    dados = montar(lula_real)
    html = MODELO.read_text().replace("__TITULO__", TITULO)
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
