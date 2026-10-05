"""Mapas por município (coropléticos) para o painel: escalas, classificação e o gráfico Altair.

Faixas fixas (e não calculadas pelos dados) para a legenda ter números redondos e ser
comparável entre anos. Cores:
- sequencial: um tom (azul da paleta validada), claro -> escuro. No tema escuro a ordem se
  inverte, para os valores baixos se fundirem com o fundo escuro.
- divergente: azul (cai) x vermelho (sobe) com cinza neutro no meio. Só para abstenção; para
  votação de candidatos não usamos vermelho/azul (seriam lidos como cores partidárias).
Município sem dado: cinza mais claro, com rótulo "sem dado".
"""

from __future__ import annotations

from dataclasses import dataclass

import altair as alt
import numpy as np
import pandas as pd

# Albers (cônica equivalente) com os parâmetros do IBGE para o Brasil: meridiano central -54°,
# paralelos-padrão -2° e -22°. Área igual e sem a inclinação da Equal Earth, que é centrada em
# Greenwich e cisalha o Brasil (~54° a oeste do centro).
PROJECAO = {"type": "conicEqualArea", "parallels": [-2, -22], "rotate": [54, 0, 0]}

SEQUENCIAL_CLARO = ["#b7d3f6", "#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]


@dataclass(frozen=True)
class Escala:
    limites: tuple[float, ...]      # fronteiras internas entre as faixas
    rotulos: tuple[str, ...]        # um por faixa (len(limites) + 1)
    claro: tuple[str, ...]
    escuro: tuple[str, ...]


ABSTENCAO = Escala(
    limites=(15, 18, 21, 24, 27),
    rotulos=("< 15%", "15–18%", "18–21%", "21–24%", "24–27%", "≥ 27%"),
    claro=tuple(SEQUENCIAL_CLARO), escuro=tuple(reversed(SEQUENCIAL_CLARO)),
)
VARIACAO = Escala(
    limites=(-3, -1, 1, 3),
    rotulos=("cai 3 pp ou mais", "cai 1 a 3 pp", "estável (±1 pp)", "sobe 1 a 3 pp", "sobe 3 pp ou mais"),
    claro=("#1c5cab", "#86b6ef", "#f0efec", "#f2a3a2", "#b3302f"),
    escuro=("#6da7ec", "#1c5cab", "#383835", "#8f3433", "#e66767"),
)
VOTOS = Escala(
    limites=(20, 35, 50, 65, 80),
    rotulos=("< 20%", "20–35%", "35–50%", "50–65%", "65–80%", "≥ 80%"),
    claro=tuple(SEQUENCIAL_CLARO), escuro=tuple(reversed(SEQUENCIAL_CLARO)),
)
# "sem dado" mais escuro que o cinza "estável" da escala divergente, para não se confundirem
SEM_DADO = {"light": "#a9a7a1", "dark": "#55554f"}
CONTORNO = {"light": "#ffffff", "dark": "#111317"}


def classificar(valores: pd.Series, escala: Escala) -> pd.Series:
    """Rótulo da faixa de cada valor; NaN vira 'sem dado'. Fronteira pertence à faixa de cima."""
    idx = np.searchsorted(np.array(escala.limites), valores.to_numpy(dtype=float), side="right")
    rot = np.array(escala.rotulos, dtype=object)[np.minimum(idx, len(escala.rotulos) - 1)]
    return pd.Series(np.where(valores.isna(), "sem dado", rot), index=valores.index)


def grafico(malha: dict, dados: pd.DataFrame, campo: str, escala: Escala, titulo_legenda: str,
            tema: str, tooltip: list[alt.Tooltip], altura: int = 640) -> alt.Chart:
    """`dados` precisa da coluna cod_ibge (7 dígitos) e de `campo`."""
    d = dados.copy()
    d["faixa"] = classificar(d[campo], escala)
    cores = list(escala.claro if tema == "light" else escala.escuro) + [SEM_DADO[tema]]
    dominio = list(escala.rotulos) + ["sem dado"]
    campos = sorted({"faixa", campo, *[t.to_dict()["field"] for t in tooltip]})
    return (
        alt.Chart(alt.Data(values=malha["features"]))
        .mark_geoshape(stroke=CONTORNO[tema], strokeWidth=0.15)
        .transform_lookup(lookup="properties.codarea", from_=alt.LookupData(d, "cod_ibge", campos))
        .transform_calculate(faixa="datum.faixa || 'sem dado'")
        .encode(
            color=alt.Color("faixa:N", title=titulo_legenda,
                            scale=alt.Scale(domain=dominio, range=cores),
                            legend=alt.Legend(orient="bottom", direction="horizontal", columns=len(dominio),
                                              symbolType="square", symbolSize=180, symbolStrokeWidth=0)),
            tooltip=tooltip,
        )
        .project(**PROJECAO)
        .properties(height=altura)
    )
