"""Painel ao vivo da apuração.

uv run streamlit run apuracao/app/main.py

Lê data/parquet (gerado por `python -m apuracao.modelo construir --loop 15`).
Variáveis: APURACAO_DIR_PARQUET (padrão data/parquet), APURACAO_CONFIG
(padrão config/eleicoes.toml), APURACAO_ELEICAO (id da eleição aberta ao iniciar).
"""

from __future__ import annotations

import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from apuracao.app import dados
from apuracao.config import carregar
from apuracao.modelo import consultas

DIR_PARQUET = Path(os.environ.get("APURACAO_DIR_PARQUET", "data/parquet"))
CONFIG = Path(os.environ.get("APURACAO_CONFIG", "config/eleicoes.toml"))
INTERVALO_S = 30

# Paleta categórica validada (slots 1-3), um passo por tema. Ver skill dataviz.
CORES = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a"],
    "dark": ["#3987e5", "#d95926", "#199e70"],
}
CINZA = {"light": "#b5b3ad", "dark": "#5c5b56"}
TEXTO = {"light": "#52514e", "dark": "#c3c2b7"}  # texto secundário: rótulos nunca usam a cor da série

st.set_page_config(page_title="Apuração ao vivo", layout="wide")


def tema() -> str:
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


@st.cache_resource
def conexao(dir_parquet: str):
    # Uma conexão por processo: a 1ª consulta do DuckDB é lenta (~3s), as demais não.
    # As views reavaliam *.parquet a cada consulta, então parts novos entram sozinhos.
    return consultas.conectar(dir_parquet)


def pct(v) -> str:
    return "–" if v is None or pd.isna(v) else f"{v:,.2f}%".replace(",", "X").replace(".", ",").replace("X", ".")


def razao(a, b) -> float | None:
    """100 * a / b, ou None se algum valor faltar (campos vêm vazios antes da totalização)."""
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
        return None
    return 100 * a / b


def inteiro(v) -> str:
    return "–" if v is None or pd.isna(v) else f"{int(v):,}".replace(",", ".")


cfg = carregar(CONFIG)
eleicoes = [e for e in cfg.eleicao if e.codigo > 0]
nomes_cargo = {v: k.capitalize() for k, v in cfg.cargos.items()}

with st.sidebar:
    eleicao = st.selectbox(
        "Eleição", eleicoes, format_func=lambda e: f"{e.id} ({e.codigo})",
        # padrão: APURACAO_ELEICAO (id), senão a primeira de 2º turno
        index=next((i for i, e in enumerate(eleicoes) if e.id == os.environ.get("APURACAO_ELEICAO")),
                   next((i for i, e in enumerate(eleicoes) if e.turno == 2), 0)),
    )
    cargo_nome = st.selectbox("Cargo", eleicao.cargos)
    cargo = cfg.cargos[cargo_nome]


@st.fragment(run_every=INTERVALO_S)
def painel() -> None:
    if not list(DIR_PARQUET.glob("snapshot_totais/*.parquet")):
        st.info(f"Sem dados em `{DIR_PARQUET}` ainda. Rode o coletor e "
                "`python -m apuracao.modelo construir --loop 15`.")
        return
    # Conexão nova se ainda não havia tabelas quando foi criada.
    con = conexao(str(DIR_PARQUET))
    if not consultas.tem(con, "snapshot_totais"):
        conexao.clear()
        con = conexao(str(DIR_PARQUET))

    opcoes = dados.abrangencias(con, eleicao.codigo, cargo)
    if not opcoes:
        st.info("Ainda não há snapshots desta eleição e cargo.")
        return
    abr = st.selectbox("Abrangência", opcoes, format_func=lambda a: a.upper(), key="abrangencia")

    r = dados.resumo(con, eleicao.codigo, cargo, abr)
    df_cand = dados.candidatos(con, eleicao.codigo, cargo, abr)
    numeros = dados.destaques(df_cand)
    t = tema()
    cor_de = {n: CORES[t][i] for i, n in enumerate(numeros)}

    st.subheader(f"{nomes_cargo.get(cargo, cargo)} · {abr.upper()} · {eleicao.turno}º turno")
    st.caption(f"Atualizado pelo TSE às {r['ts_brasilia']:%d/%m %H:%M:%S} (Brasília) · "
               f"andamento: {r['andamento']} · totalização final: {'sim' if r['totalizacao_final'] else 'não'}")
    if not r["divulga"]:
        st.warning("O TSE ainda não liberou a divulgação dos votos (dv = n): os votos vêm zerados.")

    esi = r["eleitorado_instaladas"]
    tv = r["votos_total"]
    cols = st.columns(5)
    cols[0].metric("Seções totalizadas", pct(r["pct_secoes"]),
                   help=f"{inteiro(r['secoes_totalizadas'])} de {inteiro(r['secoes_total'])}")
    cols[1].metric("Comparecimento", pct(razao(r["comparecimento"], esi)),
                   help=f"{inteiro(r['comparecimento'])} eleitores (sobre o eleitorado das seções instaladas)")
    cols[2].metric("Abstenção", pct(razao(r["abstencao"], esi)),
                   help=f"{inteiro(r['abstencao'])} eleitores")
    cols[3].metric("Brancos", pct(razao(r["brancos"], tv)), help=inteiro(r["brancos"]))
    cols[4].metric("Nulos", pct(razao(r["nulos"], tv)), help=inteiro(r["nulos"]))

    if df_cand.empty:
        return

    esq, dir_ = st.columns([2, 3])
    with esq:
        st.markdown("**Votos válidos por candidato**")
        barras = df_cand.assign(
            cor=[cor_de.get(int(n), CINZA[t]) for n in df_cand["numero"]],
            rotulo=[pct(p) for p in df_cand["pct_validos"]],
        )
        base = alt.Chart(barras).encode(
            y=alt.Y("nome:N", sort="-x", title=None, axis=alt.Axis(labelLimit=220)),
            x=alt.X("pct_validos:Q", title="% dos votos válidos", scale=alt.Scale(domain=[0, 100])),
            tooltip=[alt.Tooltip("nome:N", title="Candidato"), alt.Tooltip("partido:N", title="Partido"),
                     alt.Tooltip("votos:Q", title="Votos", format=",d"),
                     alt.Tooltip("pct_validos:Q", title="% válidos", format=".2f")],
        )
        grafico = base.mark_bar(cornerRadiusEnd=4, height={"band": 0.6}).encode(
            color=alt.Color("cor:N", scale=None)
        ) + base.mark_text(align="left", dx=4, color=TEXTO[t]).encode(text="rotulo:N")
        st.altair_chart(grafico, use_container_width=True)

    with dir_:
        st.markdown("**Evolução durante a apuração**")
        evo = dados.evolucao(con, eleicao.codigo, cargo, abr, numeros)
        if evo["ts_brasilia"].nunique() < 2:
            st.caption("A evolução aparece a partir do segundo snapshot.")
        else:
            nomes = evo.drop_duplicates("numero").set_index("numero")["nome"]
            dominio = [nomes[n] for n in numeros if n in nomes]
            escala = alt.Scale(domain=dominio, range=[cor_de[n] for n in numeros if n in nomes])
            base_l = alt.Chart(evo).encode(
                x=alt.X("ts_brasilia:T", title="Horário (Brasília)", axis=alt.Axis(format="%H:%M")),
                y=alt.Y("pct_validos:Q", title="% dos votos válidos"),
            )
            # rótulo direto na ponta de cada linha (texto em cor neutra; a linha carrega a identidade)
            ultimo = evo[evo["ts_brasilia"] == evo["ts_brasilia"].max()].copy()
            faixa = max(evo["pct_validos"].max(), 1)
            ultimo["y_rotulo"] = dados.rotulos_sem_colisao(ultimo["pct_validos"].tolist(), faixa * 0.06)
            ultimo["rotulo"] = [pct(v) for v in ultimo["pct_validos"]]
            rotulos = alt.Chart(ultimo).mark_text(align="left", dx=6, color=TEXTO[t]).encode(
                x="ts_brasilia:T", y="y_rotulo:Q", text="rotulo:N",
            )
            linhas = base_l.mark_line(strokeWidth=2).encode(
                color=alt.Color("nome:N", title=None, scale=escala, legend=alt.Legend(orient="top")),
                tooltip=[alt.Tooltip("ts_brasilia:T", title="Horário", format="%H:%M:%S"),
                         alt.Tooltip("nome:N", title="Candidato"),
                         alt.Tooltip("pct_validos:Q", title="% válidos", format=".2f"),
                         alt.Tooltip("pct_secoes:Q", title="% seções", format=".2f")],
            )
            st.altair_chart((linhas + rotulos).properties(padding={"right": 40}), use_container_width=True)

    if abr == "br":
        st.markdown("**Por UF**")
        tab = dados.tabela_ufs(con, eleicao.codigo, cargo, numeros)
        if not tab.empty:
            tab["uf"] = tab["uf"].str.upper()
            st.dataframe(
                tab, hide_index=True, use_container_width=True,
                column_config={
                    "uf": st.column_config.TextColumn("UF"),
                    "pct_secoes": st.column_config.NumberColumn("% seções", format="%.2f"),
                    "atualizado": st.column_config.DatetimeColumn("Atualizado (Brasília)", format="HH:mm:ss"),
                    **{c: st.column_config.NumberColumn(f"{c} (% válidos)", format="%.2f")
                       for c in tab.columns if c not in ("uf", "pct_secoes", "atualizado")},
                },
            )

    with st.expander("Tabela de candidatos"):
        st.dataframe(df_cand[["numero", "nome", "partido", "votos", "pct_validos", "eleito", "situacao"]],
                     hide_index=True, use_container_width=True)


painel()
