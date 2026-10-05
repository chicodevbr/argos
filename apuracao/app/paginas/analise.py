"""Página "Análise do 1º turno": presidente, 2026 x 2022 (abstenção, brancos, nulos, votação)."""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from apuracao.app import analise
from apuracao.modelo import consultas

DIR_PARQUET = Path(os.environ.get("APURACAO_DIR_PARQUET", "data/parquet"))
alt.data_transformers.disable_max_rows()  # ~5.700 municípios no gráfico de dispersão

# Paleta validada (dataviz): ano anterior (referência) em cinza, 2026 no azul do slot 1.
PALETA = {
    "light": {"2022": "#8a8883", "2026": "#2a78d6", "texto": "#52514e"},
    "dark": {"2022": "#76746e", "2026": "#3987e5", "texto": "#c3c2b7"},
}


def tema() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def br(v, casas=2) -> str:
    return "–" if pd.isna(v) else f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


@st.cache_data(ttl=600, show_spinner=False)
def dados(dir_parquet: str) -> pd.DataFrame | None:
    con = consultas.conectar(dir_parquet)
    return analise.municipios(con) if analise.disponivel(con) else None


st.title("Análise do 1º turno — presidente")
st.caption("2026 comparado a 2022. Abstenção sobre o eleitorado das seções instaladas (comparecimento + "
           "abstenção); brancos e nulos sobre o comparecimento; votos sobre os válidos. 2026: arquivos de "
           "município do TSE (somam o total oficial). 2022: Portal de Dados Abertos do TSE.")

mun = dados(str(DIR_PARQUET))
if mun is None:
    st.info("Faltam dados: carregue o 1º turno de 2026 por município e o histórico de 2022 "
            "(ver docs/roteiro-noite-eleicao.md, véspera, item 5, e `python -m apuracao.carga --ano 2022`).")
    st.stop()

p = PALETA[tema()]
brasil = analise.agregar(mun).iloc[0]

# --- Brasil -----------------------------------------------------------------------------------------
st.subheader("Brasil")
c = st.columns(6)
for col, (rotulo, chave, inverso) in zip(c, [
    ("Comparecimento", None, False), ("Abstenção", "abst_pct", True), ("Brancos", "brancos_pct", True),
    ("Nulos", "nulos_pct", True), ("Lula (13)", "pct13", False), ("Nº 22", "pct22", False),
]):
    if chave is None:
        v26, v22 = 100 - brasil["abst_pct_2026"], 100 - brasil["abst_pct_2022"]
    else:
        v26, v22 = brasil[f"{chave}_2026"], brasil[f"{chave}_2022"]
    # Candidatos: variação neutra (cinza), sem sugerir que subir/cair é bom ou ruim.
    cor = "off" if chave in ("pct13", "pct22") else ("inverse" if inverso else "normal")
    col.metric(rotulo, f"{br(v26)}%", f"{br(v26 - v22)} pp vs 2022", delta_color=cor,
               help=f"2022: {br(v22)}%" + (" — 22 = Bolsonaro em 2022, Flávio Bolsonaro em 2026" if chave == "pct22" else ""))

# --- Abstenção por UF -------------------------------------------------------------------------------
st.subheader("Abstenção por UF")
uf = analise.agregar(mun, ["uf"])
uf["UF"] = uf["uf"].str.upper()
ext = uf[uf["uf"] == "zz"]
uf_br = uf[uf["uf"] != "zz"].sort_values("abst_pct_2026", ascending=False)
longo = uf_br.melt(id_vars=["UF", "var_abst"], value_vars=["abst_pct_2022", "abst_pct_2026"],
                   var_name="ano", value_name="abstencao")
longo["ano"] = longo["ano"].str[-4:]
ordem = list(uf_br["UF"])
y = alt.Y("UF:N", sort=ordem, title=None, axis=alt.Axis(labelOverlap=False))
regua = alt.Chart(uf_br).mark_rule(color=p["texto"], opacity=0.4).encode(
    y=y, x=alt.X("abst_pct_2022:Q", title="Abstenção (%)", scale=alt.Scale(zero=False),
                 axis=alt.Axis(tickCount=8, format=".0f")), x2="abst_pct_2026:Q")
pontos = alt.Chart(longo).mark_circle(size=90, opacity=1).encode(
    y=y, x="abstencao:Q",
    color=alt.Color("ano:N", title=None, scale=alt.Scale(domain=["2022", "2026"], range=[p["2022"], p["2026"]]),
                    legend=alt.Legend(orient="top")),
    tooltip=[alt.Tooltip("UF:N"), alt.Tooltip("ano:N", title="Ano"),
             alt.Tooltip("abstencao:Q", title="Abstenção (%)", format=".2f"),
             alt.Tooltip("var_abst:Q", title="Variação (pp)", format="+.2f")])
st.altair_chart((regua + pontos).properties(height=24 * len(ordem)), use_container_width=True)
if not ext.empty:
    e = ext.iloc[0]
    st.caption(f"Exterior (fora do gráfico para não achatar a escala): {br(e['abst_pct_2022'])}% em 2022 e "
               f"{br(e['abst_pct_2026'])}% em 2026.")

# --- Votação por UF ---------------------------------------------------------------------------------
st.subheader("Votação por UF: variação 2022 → 2026")
cand = st.radio("Candidato", ["Lula (13)", "Nº 22 (Bolsonaro → Flávio)"], horizontal=True, label_visibility="collapsed")
n = 13 if cand.startswith("Lula") else 22
var = uf_br.assign(variacao=uf_br[f"var{n}"]).sort_values("variacao", ascending=False)
# Ordem calculada aqui: com camadas filtradas, o Altair descarta um sort por campo.
ordem_var = list(var["UF"])
# Cor única e neutra: o sinal já está na direção da barra e no rótulo; vermelho/azul
# poderiam ser lidos como cores partidárias.
barras = alt.Chart(var).mark_bar(cornerRadiusEnd=3, color=p["2022"]).encode(
    y=alt.Y("UF:N", sort=ordem_var, title=None, axis=alt.Axis(labelOverlap=False)),
    x=alt.X("variacao:Q", title=f"Variação de {cand} (pontos percentuais dos votos válidos)"),
    tooltip=[alt.Tooltip("UF:N"), alt.Tooltip(f"pct{n}_2022:Q", title="2022 (%)", format=".2f"),
             alt.Tooltip(f"pct{n}_2026:Q", title="2026 (%)", format=".2f"),
             alt.Tooltip("variacao:Q", title="Variação (pp)", format="+.2f")])
texto = alt.Chart(var).encode(
    y=alt.Y("UF:N", sort=ordem_var), x="variacao:Q", text=alt.Text("variacao:Q", format="+.1f"))
positivos = texto.transform_filter("datum.variacao >= 0").mark_text(dx=4, align="left", color=p["texto"])
negativos = texto.transform_filter("datum.variacao < 0").mark_text(dx=-4, align="right", color=p["texto"])
st.altair_chart((barras + positivos + negativos).properties(height=24 * len(var)), use_container_width=True)

# --- Municípios -------------------------------------------------------------------------------------
st.subheader("Municípios")
ufs = ["Todas"] + sorted(uf_br["UF"])
escolha = st.selectbox("UF", ufs)
m = mun[mun["uf"] != "zz"].dropna(subset=[f"pct{n}_2022", f"pct{n}_2026"])
if escolha != "Todas":
    m = m[m["uf"] == escolha.lower()]
disp = alt.Chart(m).mark_circle(opacity=0.35, color=p["2026"]).encode(
    x=alt.X(f"pct{n}_2022:Q", title=f"{cand} em 2022 (% válidos)", scale=alt.Scale(domain=[0, 100])),
    y=alt.Y(f"pct{n}_2026:Q", title=f"{cand} em 2026 (% válidos)", scale=alt.Scale(domain=[0, 100])),
    size=alt.Size("eleitores_2026:Q", legend=None, scale=alt.Scale(range=[8, 400])),
    tooltip=[alt.Tooltip("nome:N", title="Município"), alt.Tooltip("uf:N", title="UF"),
             alt.Tooltip(f"pct{n}_2022:Q", title="2022 (%)", format=".1f"),
             alt.Tooltip(f"pct{n}_2026:Q", title="2026 (%)", format=".1f"),
             alt.Tooltip("eleitores_2026:Q", title="Eleitores (2026)", format=",d")])
diagonal = alt.Chart(pd.DataFrame({"x": [0, 100]})).mark_line(strokeDash=[4, 4], color=p["texto"]).encode(x="x:Q", y="x:Q")
st.altair_chart((diagonal + disp).properties(height=480), use_container_width=True)
st.caption("Cada ponto é um município (tamanho = eleitores em 2026). Acima da diagonal: o candidato "
           "teve percentual maior em 2026 do que em 2022.")

tabela = m[["nome", "uf", "eleitores_2026", "abst_pct_2022", "abst_pct_2026", "var_abst",
            "pct13_2022", "pct13_2026", "var13", "pct22_2022", "pct22_2026", "var22"]].copy()
tabela["uf"] = tabela["uf"].str.upper()
st.dataframe(
    tabela.sort_values("eleitores_2026", ascending=False), hide_index=True, use_container_width=True,
    column_config={
        "nome": "Município", "uf": "UF",
        "eleitores_2026": st.column_config.NumberColumn("Eleitores 2026", format="%d"),
        "abst_pct_2022": st.column_config.NumberColumn("Abst. 2022 %", format="%.1f"),
        "abst_pct_2026": st.column_config.NumberColumn("Abst. 2026 %", format="%.1f"),
        "var_abst": st.column_config.NumberColumn("Abst. var. pp", format="%+.1f"),
        "pct13_2022": st.column_config.NumberColumn("Lula 2022 %", format="%.1f"),
        "pct13_2026": st.column_config.NumberColumn("Lula 2026 %", format="%.1f"),
        "var13": st.column_config.NumberColumn("Lula var. pp", format="%+.1f"),
        "pct22_2022": st.column_config.NumberColumn("Nº 22 2022 %", format="%.1f"),
        "pct22_2026": st.column_config.NumberColumn("Nº 22 2026 %", format="%.1f"),
        "var22": st.column_config.NumberColumn("Nº 22 var. pp", format="%+.1f"),
    },
)
st.caption("Clique no cabeçalho de uma coluna para ordenar (ex.: maiores variações de abstenção).")
