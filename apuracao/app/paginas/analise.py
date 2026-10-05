"""Página "Análise do 1º turno": presidente, 2026 x um ano histórico (abstenção, brancos, nulos, votação)."""

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

# Paleta validada (dataviz): ano base (referência) em cinza, 2026 no azul do slot 1.
PALETA = {
    "light": {"base": "#8a8883", "atual": "#2a78d6", "texto": "#52514e"},
    "dark": {"base": "#76746e", "atual": "#3987e5", "texto": "#c3c2b7"},
}


def tema() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def br(v, casas=2) -> str:
    return "–" if pd.isna(v) else f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


@st.cache_data(ttl=600, show_spinner=False)
def anos(dir_parquet: str) -> list[int]:
    con = consultas.conectar(dir_parquet)
    return analise.anos_base(con) if analise.disponivel(con) else []


@st.cache_data(ttl=600, show_spinner=False)
def dados(dir_parquet: str, ano_base: int) -> tuple[pd.DataFrame, dict[str, str]]:
    con = consultas.conectar(dir_parquet)
    return analise.municipios(con, ano_base), analise.nomes(con, ano_base)


st.title("Análise do 1º turno — presidente")

disponiveis = anos(str(DIR_PARQUET))
if not disponiveis:
    st.info("Faltam dados: carregue o 1º turno de 2026 por município e o histórico "
            "(ver docs/roteiro-noite-eleicao.md, véspera, item 5, e `python -m apuracao.carga --ano 2022`).")
    st.stop()

ano = st.selectbox("Comparar 2026 com", disponiveis, index=0)
mun, nomes = dados(str(DIR_PARQUET), ano)
st.caption(f"2026 comparado a {ano}. Abstenção sobre o eleitorado das seções instaladas (comparecimento + "
           "abstenção); brancos e nulos sobre o comparecimento; votos sobre os válidos. 2026: arquivos de "
           f"município do TSE (somam o total oficial). {ano}: Portal de Dados Abertos do TSE (conferido com o "
           "resultado oficial).")

p = PALETA[tema()]
cores_anos = alt.Scale(domain=[str(ano), "2026"], range=[p["base"], p["atual"]])
brasil = analise.agregar(mun).iloc[0]
cand_pt = f"PT: {nomes['pt_base'].title()} → {nomes['pt_atual'].title()}"
cand_adv = f"Principal adversário: {nomes['adv_base'].title()} → {nomes['adv_atual'].title()}"

# --- Brasil -----------------------------------------------------------------------------------------
st.subheader("Brasil")
c = st.columns(6)
for col, (rotulo, chave, inverso, ajuda) in zip(c, [
    ("Comparecimento", None, False, ""), ("Abstenção", "abst_pct", True, ""), ("Brancos", "brancos_pct", True, ""),
    ("Nulos", "nulos_pct", True, ""), ("PT (13)", "pct_pt", False, cand_pt),
    ("Principal adversário", "pct_adv", False, cand_adv),
]):
    if chave is None:
        v_at, v_b = 100 - brasil["abst_pct_atual"], 100 - brasil["abst_pct_base"]
    else:
        v_at, v_b = brasil[f"{chave}_atual"], brasil[f"{chave}_base"]
    # Candidatos: variação neutra (cinza), sem sugerir que subir/cair é bom ou ruim.
    cor = "off" if chave in ("pct_pt", "pct_adv") else ("inverse" if inverso else "normal")
    col.metric(rotulo, f"{br(v_at)}%", f"{br(v_at - v_b)} pp vs {ano}", delta_color=cor,
               help=f"{ano}: {br(v_b)}%" + (f" — {ajuda}" if ajuda else ""))

# --- Abstenção por UF -------------------------------------------------------------------------------
st.subheader("Abstenção por UF")
uf = analise.agregar(mun, ["uf"])
uf["UF"] = uf["uf"].str.upper()
ext = uf[uf["uf"] == "zz"]
uf_br = uf[uf["uf"] != "zz"].sort_values("abst_pct_atual", ascending=False)
ordem = list(uf_br["UF"])
longo = uf_br.melt(id_vars=["UF", "var_abst"], value_vars=["abst_pct_base", "abst_pct_atual"],
                   var_name="ano", value_name="abstencao")
longo["ano"] = longo["ano"].map({"abst_pct_base": str(ano), "abst_pct_atual": "2026"})
y = alt.Y("UF:N", sort=ordem, title=None, axis=alt.Axis(labelOverlap=False))
regua = alt.Chart(uf_br).mark_rule(color=p["texto"], opacity=0.4).encode(
    y=y, x=alt.X("abst_pct_base:Q", title="Abstenção (%)", scale=alt.Scale(zero=False),
                 axis=alt.Axis(tickCount=8, format=".0f")), x2="abst_pct_atual:Q")
pontos = alt.Chart(longo).mark_circle(size=90, opacity=1).encode(
    y=y, x="abstencao:Q",
    color=alt.Color("ano:N", title=None, scale=cores_anos, legend=alt.Legend(orient="top")),
    tooltip=[alt.Tooltip("UF:N"), alt.Tooltip("ano:N", title="Ano"),
             alt.Tooltip("abstencao:Q", title="Abstenção (%)", format=".2f"),
             alt.Tooltip("var_abst:Q", title="Variação (pp)", format="+.2f")])
st.altair_chart((regua + pontos).properties(height=24 * len(ordem)), use_container_width=True)
if not ext.empty:
    e = ext.iloc[0]
    st.caption(f"Exterior (fora do gráfico para não achatar a escala): {br(e['abst_pct_base'])}% em {ano} e "
               f"{br(e['abst_pct_atual'])}% em 2026.")

# --- Votação por UF ---------------------------------------------------------------------------------
st.subheader(f"Votação por UF: variação {ano} → 2026")
escolha_cand = st.radio("Candidato", [cand_pt, cand_adv], horizontal=True, label_visibility="collapsed")
k = "pt" if escolha_cand == cand_pt else "adv"
var = uf_br.assign(variacao=uf_br[f"var_{k}"]).sort_values("variacao", ascending=False)
# Ordem calculada aqui: com camadas filtradas, o Altair descarta um sort por campo.
ordem_var = list(var["UF"])
# Cor única e neutra: o sinal já está na direção da barra e no rótulo; vermelho/azul
# poderiam ser lidos como cores partidárias.
barras = alt.Chart(var).mark_bar(cornerRadiusEnd=3, color=p["base"]).encode(
    y=alt.Y("UF:N", sort=ordem_var, title=None, axis=alt.Axis(labelOverlap=False)),
    x=alt.X("variacao:Q", title="Variação (pontos percentuais dos votos válidos)"),
    tooltip=[alt.Tooltip("UF:N"), alt.Tooltip(f"pct_{k}_base:Q", title=f"{ano} (%)", format=".2f"),
             alt.Tooltip(f"pct_{k}_atual:Q", title="2026 (%)", format=".2f"),
             alt.Tooltip("variacao:Q", title="Variação (pp)", format="+.2f")])
texto = alt.Chart(var).encode(
    y=alt.Y("UF:N", sort=ordem_var), x="variacao:Q", text=alt.Text("variacao:Q", format="+.1f"))
positivos = texto.transform_filter("datum.variacao >= 0").mark_text(dx=4, align="left", color=p["texto"])
negativos = texto.transform_filter("datum.variacao < 0").mark_text(dx=-4, align="right", color=p["texto"])
st.altair_chart((barras + positivos + negativos).properties(height=24 * len(var)), use_container_width=True)

# --- Municípios -------------------------------------------------------------------------------------
st.subheader("Municípios")
escolha_uf = st.selectbox("UF", ["Todas"] + sorted(uf_br["UF"]))
# comparação município a município: só os que existem nos dois anos
m = mun[mun["uf"] != "zz"].dropna(subset=[f"pct_{k}_base", f"pct_{k}_atual"])
if escolha_uf != "Todas":
    m = m[m["uf"] == escolha_uf.lower()]
quem_base, quem_atual = (nomes["pt_base"], nomes["pt_atual"]) if k == "pt" else (nomes["adv_base"], nomes["adv_atual"])
disp = alt.Chart(m).mark_circle(opacity=0.35, color=p["atual"]).encode(
    x=alt.X(f"pct_{k}_base:Q", title=f"{quem_base.title()} em {ano} (% válidos)", scale=alt.Scale(domain=[0, 100])),
    y=alt.Y(f"pct_{k}_atual:Q", title=f"{quem_atual.title()} em 2026 (% válidos)", scale=alt.Scale(domain=[0, 100])),
    size=alt.Size("eleitores_atual:Q", legend=None, scale=alt.Scale(range=[8, 400])),
    tooltip=[alt.Tooltip("nome:N", title="Município"), alt.Tooltip("uf:N", title="UF"),
             alt.Tooltip(f"pct_{k}_base:Q", title=f"{ano} (%)", format=".1f"),
             alt.Tooltip(f"pct_{k}_atual:Q", title="2026 (%)", format=".1f"),
             alt.Tooltip("eleitores_atual:Q", title="Eleitores (2026)", format=",d")])
diagonal = alt.Chart(pd.DataFrame({"x": [0, 100]})).mark_line(strokeDash=[4, 4], color=p["texto"]).encode(x="x:Q", y="x:Q")
st.altair_chart((diagonal + disp).properties(height=480), use_container_width=True)
st.caption(f"Cada ponto é um município presente nos dois anos (tamanho = eleitores em 2026). Acima da "
           f"diagonal: percentual maior em 2026 do que em {ano}.")

tabela = m[["nome", "uf", "eleitores_atual", "abst_pct_base", "abst_pct_atual", "var_abst",
            "pct_pt_base", "pct_pt_atual", "var_pt", "pct_adv_base", "pct_adv_atual", "var_adv"]].copy()
tabela["uf"] = tabela["uf"].str.upper()
pt_b, pt_a = nomes["pt_base"].split(" (")[0].title(), nomes["pt_atual"].split(" (")[0].title()
adv_b, adv_a = nomes["adv_base"].split(" (")[0].title(), nomes["adv_atual"].split(" (")[0].title()
st.dataframe(
    tabela.sort_values("eleitores_atual", ascending=False), hide_index=True, use_container_width=True,
    column_config={
        "nome": "Município", "uf": "UF",
        "eleitores_atual": st.column_config.NumberColumn("Eleitores 2026", format="%d"),
        "abst_pct_base": st.column_config.NumberColumn(f"Abst. {ano} %", format="%.1f"),
        "abst_pct_atual": st.column_config.NumberColumn("Abst. 2026 %", format="%.1f"),
        "var_abst": st.column_config.NumberColumn("Abst. var. pp", format="%+.1f"),
        "pct_pt_base": st.column_config.NumberColumn(f"{pt_b} {ano} %", format="%.1f"),
        "pct_pt_atual": st.column_config.NumberColumn(f"{pt_a} 2026 %", format="%.1f"),
        "var_pt": st.column_config.NumberColumn("PT var. pp", format="%+.1f"),
        "pct_adv_base": st.column_config.NumberColumn(f"{adv_b} {ano} %", format="%.1f"),
        "pct_adv_atual": st.column_config.NumberColumn(f"{adv_a} 2026 %", format="%.1f"),
        "var_adv": st.column_config.NumberColumn("Adversário var. pp", format="%+.1f"),
    },
)
st.caption("Clique no cabeçalho de uma coluna para ordenar (ex.: maiores variações de abstenção).")
