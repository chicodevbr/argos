"""Página "A noite da apuração": como a contagem evoluiu em eleições passadas, reconstruída
a partir do boletim de urna (horário em que cada seção chegou ao TSE).

Carga: uv run python -m apuracao.carga --boletim 2022:2   (e 2026:1 quando o TSE publicar)
"""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from apuracao.projecao import noite

DIR_PARQUET = Path(os.environ.get("APURACAO_DIR_PARQUET", "data/parquet"))

# Candidatos: slots 1-2 da paleta categórica validada; regiões: slots 3-8 (validados à
# parte; no tema claro 3 cores ficam abaixo de 3:1 -> tabela com os mesmos números).
CORES = {"light": ["#2a78d6", "#eb6834"], "dark": ["#3987e5", "#d95926"]}
CORES_REG = {"light": ["#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"],
             "dark": ["#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]}
REGIOES = ["Centro-Oeste", "Norte", "Sul", "Sudeste", "Nordeste", "Exterior"]
TEXTO = {"light": "#52514e", "dark": "#c3c2b7"}


def tema() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def hm(ts) -> str:
    return f"{pd.Timestamp(ts):%Hh%M}"


def br(v, casas=2) -> str:
    return f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


@st.cache_data(show_spinner=False)
def carregar(dir_parquet: str, ano: int, turno: int):
    cands = noite.nomes(Path(dir_parquet), ano, turno)
    top = sorted(cands["numero"].head(2).astype(int).tolist())
    return (cands, top, noite.curva(Path(dir_parquet), ano, turno, top),
            noite.ritmo_regioes(Path(dir_parquet), ano, turno))


st.header("A noite da apuração")
st.caption("Reconstruída a partir do boletim de urna do TSE: cada seção entra na contagem no horário "
           "em que o boletim chegou ao TSE (horário de Brasília). Em 2022 a reconstrução bate com a "
           "curva publicada na noite com cerca de 1 minuto de diferença.")

disp = noite.disponiveis(DIR_PARQUET)
if (2026, 1) not in disp:
    st.info("O boletim de urna do 1º turno de 2026 ainda não foi carregado (o TSE publica alguns dias "
            "depois do turno). Para carregar quando sair:\n\n"
            "`python -m apuracao.carga --boletim 2026:1 --sem-guardar-zip`")
if not disp:
    st.stop()

ano, turno = st.selectbox("Eleição", disp, format_func=lambda x: f"{x[0]} · {x[1]}º turno (presidente)")
t = tema()
cands, top, c, ritmo = carregar(str(DIR_PARQUET), ano, turno)
nome_de = dict(zip(cands["numero"].astype(int), cands["nome"].str.title()))
cor_de = {n: CORES[t][i] for i, n in enumerate(top)}

# --- Horários-chave ---------------------------------------------------------------
sec = c.drop_duplicates("ts")[["ts", "pct_secoes"]]
quando = {p: sec.loc[sec["pct_secoes"] >= p, "ts"].min() for p in (50, 90, 99)}
vir = noite.viradas(c, *top) if len(top) == 2 else []
validos_total = cands["votos"].sum()
cols = st.columns(4)
for col, n in zip(cols[:2], top):
    v = cands.set_index("numero").loc[n, "votos"]
    col.metric(nome_de[n], f"{br(100 * v / validos_total)}%", help=f"{br(v, 0)} votos (resultado final)")
cols[2].metric("Metade das seções às", hm(quando[50]), help=f"90% às {hm(quando[90])}; 99% às {hm(quando[99])}")
cols[3].metric("Mudanças de liderança", len(vir), help="Entre os dois mais votados, na contagem acumulada.")
if vir:
    st.markdown("A liderança mudou às " + ", ".join(
        f"**{hm(v)}** ({br(sec.loc[sec['ts'] == v, 'pct_secoes'].iloc[0], 0)}% das seções)" for v in vir) + ".")

# --- Curva dos candidatos ---------------------------------------------------------
st.subheader("Votos válidos ao longo da noite")
dados = c.assign(nome=c["numero"].map(nome_de))
dominio = [nome_de[n] for n in top]
escala = alt.Scale(domain=dominio, range=[cor_de[n] for n in top])
eixo_x = alt.X("ts:T", title="Horário (Brasília)", axis=alt.Axis(format="%H:%M"))
lim = dados[dados["ts"] <= quando[99] + pd.Timedelta(minutes=30)]
ult = lim[lim["ts"] == lim["ts"].max()].assign(rotulo=lambda d: d["nome"] + " " + d["pct_validos"].map(lambda v: f"{br(v)}%"))
linhas = alt.Chart(lim).mark_line(strokeWidth=2).encode(
    x=eixo_x,
    y=alt.Y("pct_validos:Q", title="% dos válidos contados", scale=alt.Scale(zero=False)),
    color=alt.Color("nome:N", title=None, scale=escala, legend=alt.Legend(orient="top")),
    tooltip=[alt.Tooltip("ts:T", title="Horário", format="%H:%M"), alt.Tooltip("nome:N", title="Candidato"),
             alt.Tooltip("pct_validos:Q", title="% válidos", format=".2f"),
             alt.Tooltip("pct_secoes:Q", title="% seções", format=".1f")],
)
rotulos = alt.Chart(ult).mark_text(align="left", dx=6, color=TEXTO[t]).encode(x="ts:T", y="pct_validos:Q", text="rotulo:N")
regra = alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(strokeDash=[4, 4], color=TEXTO[t]).encode(y="y:Q")
camadas = [linhas, rotulos] + ([regra] if turno == 2 else [])
st.altair_chart(alt.layer(*camadas).properties(height=320, padding={"right": 150}), use_container_width=True)
st.caption("Os primeiros minutos oscilam muito: poucas seções contadas, e de regiões específicas.")

# --- Ritmo por região ---------------------------------------------------------------
st.subheader("Quem chega primeiro: seções recebidas por região")
reg = ritmo[ritmo["uf"].isna() & (ritmo["ts"] <= quando[99] + pd.Timedelta(minutes=30))]
presentes = [r for r in REGIOES if r in set(reg["regiao"])]
esc_reg = alt.Scale(domain=presentes, range=CORES_REG[t][: len(presentes)])
# Sem rótulo direto (as linhas se cruzam e convergem em 100%): legenda + a tabela abaixo,
# que também cobre as cores com contraste < 3:1 no tema claro.
graf_reg = alt.layer(
    alt.Chart(reg).mark_line(strokeWidth=2).encode(
        x=eixo_x, y=alt.Y("pct_secoes:Q", title="% das seções da região", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("regiao:N", title=None, scale=esc_reg, legend=alt.Legend(orient="top")),
        tooltip=[alt.Tooltip("ts:T", title="Horário", format="%H:%M"), alt.Tooltip("regiao:N", title="Região"),
                 alt.Tooltip("pct_secoes:Q", title="% seções", format=".0f")]),
).properties(height=300)
st.altair_chart(graf_reg, use_container_width=True)

marcos = [pd.Timestamp(f"{quando[50]:%Y-%m-%d} {h}") for h in ("17:30", "18:00", "18:30", "19:00", "19:30", "20:00")]
tab = (reg[reg["ts"].isin(marcos)].pivot_table(index="regiao", columns="ts", values="pct_secoes")
       .reindex(presentes))
tab.columns = [f"{c:%Hh%M}" for c in tab.columns]
tab.index.name = "Região"
metade = reg[reg["pct_secoes"] >= 50].groupby("regiao")["ts"].min().reindex(presentes)
tab.insert(0, "metade às", [hm(x) if pd.notna(x) else "–" for x in metade])
st.dataframe(tab.round(0), use_container_width=True,
             column_config={c: st.column_config.NumberColumn(c, format="%d%%") for c in tab.columns if c != "metade às"})
pais = metade.drop("Exterior", errors="ignore").dropna()
primeira, ultima = pais.idxmin(), pais.idxmax()
st.caption(f"Primeira região a passar da metade das seções: {primeira} ({hm(pais[primeira])}); "
           f"última: {ultima} ({hm(pais[ultima])}). O exterior fica de fora da comparação: boletins "
           "de outros fusos chegam antes das 17h e entram todos na abertura da divulgação.")
