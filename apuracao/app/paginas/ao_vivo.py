"""Página "Apuração ao vivo" (aberta por apuracao/app/main.py).

Lê data/parquet (gerado por `python -m apuracao.modelo construir --loop 15`).
Variáveis: APURACAO_DIR_PARQUET (padrão data/parquet), APURACAO_CONFIG
(padrão config/eleicoes.toml), APURACAO_ELEICAO (id da eleição aberta ao iniciar).
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from apuracao.app import dados
from apuracao.app.saude import ler_saude
from apuracao.config import carregar
from apuracao.modelo import consultas
from apuracao.modelo.anomalias import verificar
from apuracao.projecao import caminho, historico
from apuracao.projecao.ao_vivo import GOVERNADOR, projecao_por_uf, projetar_ao_vivo

DIR_PARQUET = Path(os.environ.get("APURACAO_DIR_PARQUET", "data/parquet"))
CONFIG = Path(os.environ.get("APURACAO_CONFIG", "config/eleicoes.toml"))
LOG_COLETOR = Path(os.environ.get("APURACAO_LOG_COLETOR", "logs/coletor.jsonl"))
HISTORICO_PROJECAO = Path(os.environ.get("APURACAO_HISTORICO_PROJECAO", "data/projecao/historico.jsonl"))
INTERVALO_S = 30

# Paleta categórica validada (slots 1-3), um passo por tema. Ver skill dataviz.
CORES = {
    "light": ["#2a78d6", "#eb6834", "#1baf7a"],
    "dark": ["#3987e5", "#d95926", "#199e70"],
}
CINZA = {"light": "#b5b3ad", "dark": "#5c5b56"}
TEXTO = {"light": "#52514e", "dark": "#c3c2b7"}  # texto secundário: rótulos nunca usam a cor da série



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


@st.cache_data(ttl=INTERVALO_S, show_spinner=False)
def projecao(_con, eleicao_id: str, cargo: int, abrangencia: str, _marca):
    """Recalcula só quando chega snapshot novo (_marca) ou a cada INTERVALO_S."""
    return projetar_ao_vivo(_con, cfg, cfg.eleicao_por_id(eleicao_id), cargo, abrangencia)


@st.cache_data(ttl=INTERVALO_S, show_spinner=False)
def caminho_apuracao(_con, eleicao_codigo: int, _proj, _marca):
    return caminho.prever_ao_vivo(_con, eleicao_codigo, _proj)


@st.cache_data(ttl=INTERVALO_S, show_spinner=False)
def anomalias(_con, _marca) -> pd.DataFrame:
    a = verificar(_con)
    return pd.DataFrame([vars(x) for x in a]) if a else pd.DataFrame()


def marca_dados(con, eleicao: int) -> str:
    return str(con.execute("SELECT max(ts_coleta) FROM snapshot_totais WHERE eleicao = ?", [eleicao]).fetchone()[0])


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
def mostrar_saude() -> None:
    """Responde se o NOSSO coletor está vivo (o horário do TSE não diz isso)."""
    s = ler_saude(LOG_COLETOR)
    if s.estado == "ausente":
        st.caption(f"Coleta local não detectada nesta máquina (sem `{LOG_COLETOR}`).")
        return
    minutos = (s.segundos_sem_atividade or 0) / 60
    partes = [f"última atividade há {int(s.segundos_sem_atividade or 0)} s" if minutos < 2
              else f"última atividade há {minutos:.0f} min"]
    if s.ultimo_snapshot:
        partes.append(f"último snapshot às {s.ultimo_snapshot - timedelta(hours=3):%H:%M:%S} (Brasília)")
    partes.append(f"{s.requisicoes:,} requisições · {s.snapshots:,} snapshots · {s.erros:,} erros".replace(",", "."))
    if s.ultimo_erro:
        partes.append(f"último erro: {s.ultimo_erro}")
    detalhe = " · ".join(partes)
    if s.estado == "parado":
        st.error(f"Coleta local parada. {detalhe}. Veja o terminal 1 (roteiro: \"Coleta local parou\").")
    elif s.estado == "atencao":
        st.badge("Coleta local sem atividade recente", color="orange")
        st.caption(detalhe)
    else:
        st.badge("Coleta local ativa", color="green")
        st.caption(detalhe)


def painel() -> None:
    mostrar_saude()
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

    an = anomalias(con, marca_dados(con, eleicao.codigo))
    if not an.empty:
        daqui = an[(an["eleicao"] == eleicao.codigo) & (an["cargo"] == cargo)]
        st.warning(f"⚠️ {len(an)} anomalia(s) nos dados publicados pelo TSE "
                   f"({len(daqui)} nesta eleição/cargo). Os snapshots brutos citados são a evidência.")
        with st.expander("Ver anomalias"):
            st.dataframe(an[["tipo", "eleicao", "cargo", "abrangencia", "detalhe", "arquivo_raw"]],
                         hide_index=True, use_container_width=True)

    marca = marca_dados(con, eleicao.codigo)
    proj = projecao(con, eleicao.id, cargo, abr, marca)
    if proj:
        mostrar_projecao(proj, cor_de, t, cargo, eleicao.codigo)
        if cargo != GOVERNADOR:
            mostrar_caminho(con, eleicao.codigo, cargo, proj, cor_de, t, marca)

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
            if proj:
                por_uf = projecao_por_uf(proj).rename(columns={"pct_a": f"projeção {proj.nome_a}"})
                por_uf[f"faixa {proj.nome_a}"] = [f"{pct(i)} – {pct(s)}" for i, s in
                                                    zip(por_uf["pct_a_inf"], por_uf["pct_a_sup"])]
                tab = tab.merge(por_uf.drop(columns=["pct_a_inf", "pct_a_sup"]), on="uf", how="left")
            tab["uf"] = tab["uf"].str.upper()
            st.dataframe(
                tab, hide_index=True, use_container_width=True,
                column_config={
                    "uf": st.column_config.TextColumn("UF"),
                    "pct_secoes": st.column_config.NumberColumn("% seções", format="%.2f"),
                    "atualizado": st.column_config.DatetimeColumn("Atualizado (Brasília)", format="HH:mm:ss"),
                    **{c: st.column_config.NumberColumn(f"{c} (% válidos)", format="%.2f")
                       for c in tab.columns if c not in ("uf", "pct_secoes", "atualizado")
                       and not c.startswith(("projeção", "faixa"))},
                    **{c: st.column_config.NumberColumn(f"{c} (%)", format="%.2f")
                       for c in tab.columns if c.startswith("projeção")},
                },
            )

    with st.expander("Tabela de candidatos"):
        st.dataframe(df_cand[["numero", "nome", "partido", "votos", "pct_validos", "eleito", "situacao"]],
                     hide_index=True, use_container_width=True)


def mostrar_projecao(proj, cor_de: dict, t: str, cargo: int, eleicao_codigo: int) -> None:
    r = proj.r
    st.markdown("**Projeção do resultado final**")
    pct_b, pct_b_inf, pct_b_sup = 100 - r.pct_a, 100 - r.pct_a_sup, 100 - r.pct_a_inf
    lider, prob = (proj.nome_a, r.prob_a_vence) if r.prob_a_vence >= 0.5 else (proj.nome_b, 1 - r.prob_a_vence)
    c = st.columns(4)
    c[0].metric(proj.nome_a, pct(r.pct_a), help="Mediana da projeção; faixa de 90% abaixo.")
    c[0].caption(f"faixa de 90%: {pct(r.pct_a_inf)} a {pct(r.pct_a_sup)}")
    c[1].metric(proj.nome_b, pct(pct_b), help="Mediana da projeção; faixa de 90% abaixo.")
    c[1].caption(f"faixa de 90%: {pct(pct_b_inf)} a {pct(pct_b_sup)}")
    c[2].metric("Probabilidade de vitória (modelo)", f"{100 * prob:.0f}%",
                help="Fração das simulações do modelo em que o candidato termina com mais votos válidos.")
    c[2].caption(f"de {lider}")
    c[3].metric("Eleitorado com dados", pct(r.pct_contado),
                help=f"Pelos votos válidos do 1º turno, em {proj.municipios_base} municípios/localidades.")

    faixas = pd.DataFrame({
        "nome": [proj.nome_a, proj.nome_b],
        "mediana": [r.pct_a, pct_b], "inf": [r.pct_a_inf, pct_b_inf], "sup": [r.pct_a_sup, pct_b_sup],
        "cor": [cor_de.get(proj.num_a, CINZA[t]), cor_de.get(proj.num_b, CINZA[t])],
    })
    faixas["rotulo"] = [f"{pct(m)} ({pct(i)} – {pct(s)})" for m, i, s in zip(faixas["mediana"], faixas["inf"], faixas["sup"])]
    lo = max(0, min(faixas["inf"].min(), 50) - 3)
    hi = min(100, max(faixas["sup"].max(), 50) + 3)
    eixo_x = alt.X("inf:Q", title="% dos votos válidos (projeção, faixa de 90%)",
                   scale=alt.Scale(domain=[lo, hi]), axis=alt.Axis(tickCount=6, format=".0f"))
    base = alt.Chart(faixas).encode(y=alt.Y("nome:N", title=None, axis=alt.Axis(labelLimit=220)))
    grafico = (
        base.mark_rule(strokeWidth=10, opacity=0.35, strokeCap="round").encode(
            x=eixo_x, x2="sup:Q", color=alt.Color("cor:N", scale=None))
        + base.mark_tick(thickness=3, size=22).encode(x="mediana:Q", color=alt.Color("cor:N", scale=None),
            tooltip=[alt.Tooltip("nome:N", title="Candidato"), alt.Tooltip("mediana:Q", format=".2f"),
                     alt.Tooltip("inf:Q", title="faixa de", format=".2f"),
                     alt.Tooltip("sup:Q", title="até", format=".2f")])
        + base.mark_text(align="left", dx=8, color=TEXTO[t]).encode(x="sup:Q", text="rotulo:N")
        + alt.Chart(pd.DataFrame({"x": [50]})).mark_rule(strokeDash=[4, 4], color=TEXTO[t]).encode(x="x:Q")
    ).properties(height=140, padding={"right": 140})
    st.altair_chart(grafico, use_container_width=True)
    if cargo == GOVERNADOR:
        st.warning("Governador é bem menos previsível que presidente: no backtest de 2022, com 25% do "
                   "eleitorado contado, o erro médio foi de 3,2 pontos e a faixa tinha ~9 pontos. "
                   "Leve a faixa a sério, não só a mediana.")
    else:
        st.caption("Modelo por município (1º turno + deslocamento observado nos já apurados da mesma UF/região). "
                   "Backtest com 2022: erro médio de 0,09 ponto; a faixa conteve o resultado real em todos os cenários "
                   "de ordem de chegada testados.")
        mostrar_historico(eleicao_codigo, proj, cor_de, t)


def hora(ts) -> str:
    """Horário UTC (sem fuso) -> 'HHhMM' de Brasília."""
    return f"{pd.Timestamp(ts) - pd.Timedelta(hours=3):%Hh%M}"


def milhoes(v: float) -> str:
    return f"{v / 1e6:,.1f} milhões".replace(".", ",") if v >= 1e6 else f"{v / 1e3:,.0f} mil".replace(",", ".")


def mostrar_caminho(con, eleicao_codigo: int, cargo: int, proj, cor_de: dict, t: str, marca) -> None:
    """Como a contagem oficial deve evoluir: o que falta, virada (se houver), horário e 2022."""
    st.markdown("**Caminho da apuração**")
    a, b = proj.nome_a, proj.nome_b
    c, falta = caminho_apuracao(con, eleicao_codigo, proj, marca)

    evo = dados.evolucao(con, eleicao_codigo, cargo, "br", [proj.num_a, proj.num_b])
    obs = pd.DataFrame(columns=["ts", "pct"])
    if not evo.empty:
        w = evo.pivot_table(index="ts_brasilia", columns="numero", values="votos", aggfunc="first").dropna()
        if proj.num_a in w and proj.num_b in w:
            tot = (w[proj.num_a] + w[proj.num_b]).where(lambda x: x > 0)
            obs = pd.DataFrame({"ts": w.index, "pct": (100 * w[proj.num_a] / tot).to_numpy()}).dropna()
    ja = caminho.viradas([f"{x:%Hh%M}" for x in obs["ts"]], obs["pct"].tolist()) if len(obs) > 1 else []
    if ja:
        st.caption(f"Nesta noite o líder da contagem já mudou às {', '.join(ja)} (Brasília).")

    # 1) O que falta (só da projeção; não depende de ritmo)
    if not falta.empty and falta.attrs.get("pct_a_contado") is not None:
        a_lidera = falta.attrs["pct_a_contado"] >= 50
        lider, atras = (a, b) if a_lidera else (b, a)
        br = falta.set_index("regiao").loc["Brasil"]
        precisa_a = falta.attrs["pct_a_precisa"]
        if br["votos_falta"] >= 1000 and precisa_a is not None:
            # fatias do candidato que está atrás
            precisa = precisa_a if not a_lidera else 100 - precisa_a
            proj_atras = (br["pct_a_falta"], br["pct_a_falta_inf"], br["pct_a_falta_sup"]) if not a_lidera else \
                (100 - br["pct_a_falta"], 100 - br["pct_a_falta_sup"], 100 - br["pct_a_falta_inf"])
            prob = (1 - proj.r.prob_a_vence) if a_lidera else proj.r.prob_a_vence
            st.markdown(
                f"{lider} lidera a contagem. Faltam cerca de **{milhoes(br['votos_falta'])}** de votos válidos; "
                f"para empatar, {atras} precisa de **{pct(precisa)}** deles. A projeção dá a {atras} "
                f"**{pct(proj_atras[0])}** do que falta (faixa de 90%: {pct(proj_atras[1])} a {pct(proj_atras[2])}). "
                f"**Chance de virada: {100 * prob:.0f}%.**")
            # 2) Horário, só com ritmo medido em todas as UFs
            if c is not None and prob >= 0.5 and c.virada_ts is not None:
                if c.ufs_sem_ritmo or c.pct_secoes_atual < 10:
                    motivo = (f"UFs sem ritmo medido ({', '.join(u.upper() for u in c.ufs_sem_ritmo)})"
                              if c.ufs_sem_ritmo else "menos de 10% das seções apuradas")
                    st.caption(f"Horário da virada não estimado: {motivo}.")
                else:
                    st.markdown(f"Horário provável da virada: **{hora(c.virada_ts)}** "
                                f"(entre {hora(c.virada_ts_inf)} e {hora(c.virada_ts_sup)}), com cerca de "
                                f"{c.virada_pct_secoes:.0f}% das seções.")
                    st.caption("O horário supõe que cada UF segue no ritmo dos últimos 30 min. Em simulações, "
                               "acertou dentro do intervalo, mas tende a sair 20-30 min cedo quando a virada "
                               "acontece no fim da apuração (que desacelera).")
            if c is not None and c.hora_99 is not None and not c.ufs_sem_ritmo and c.pct_secoes_atual >= 10:
                st.caption(f"Contagem deve chegar a 99% das seções por volta das {hora(c.hora_99)}.")
            tab = falta[(falta["regiao"] != "Brasil") & (falta["votos_falta"] >= 1000)].sort_values(
                "votos_falta", ascending=False)
            if not tab.empty:
                st.dataframe(
                    pd.DataFrame({
                        "Região": tab["regiao"], "Votos válidos a contar": [milhoes(v) for v in tab["votos_falta"]],
                        f"{a} no que falta": [pct(v) for v in tab["pct_a_falta"]],
                        "faixa de 90%": [f"{pct(i)} – {pct(s)}" for i, s in
                                         zip(tab["pct_a_falta_inf"], tab["pct_a_falta_sup"])],
                    }), hide_index=True, use_container_width=True)

    # 3) Gráfico: contagem até agora, previsão e 2022
    cor = cor_de.get(proj.num_a, CINZA[t])
    nome_obs, nome_prev = "2026", "2026 (previsão)"
    camadas = []
    linhas = [obs.assign(serie=nome_obs).rename(columns={"pct": "pct_a"})] if len(obs) else []
    if c is not None and not c.ufs_sem_ritmo:
        cv = c.curva.assign(ts=c.curva["ts"] - pd.Timedelta(hours=3), serie=nome_prev)
        camadas.append(alt.Chart(cv).mark_area(opacity=0.15, color=cor).encode(
            x="ts:T", y="pct_a_inf:Q", y2="pct_a_sup:Q"))
        linhas.append(cv[["ts", "pct_a", "serie"]])
    elif c is not None:
        st.caption("Curva prevista oculta enquanto houver UF sem ritmo medido "
                   f"({', '.join(u.upper() for u in c.ufs_sem_ritmo)}).")
    ref = caminho.referencia_2022(proj.num_a)
    nome_ref = None
    if not ref.empty and len(obs):
        dia = pd.Timestamp(obs["ts"].iloc[0]).normalize()
        nome_ref = "2022"
        linhas.append(pd.DataFrame({"ts": dia + pd.to_timedelta(ref["hora"] + ":00"),
                                    "pct_a": ref["pct_validos"], "serie": nome_ref}))
    if not linhas:
        return
    df = pd.concat(linhas, ignore_index=True)
    dominio = [n for n in (nome_obs, nome_prev, nome_ref) if n and n in set(df["serie"])]
    estilo = {nome_obs: (cor, [1, 0]), nome_prev: (cor, [6, 4]), nome_ref: (CINZA[t], [1, 0])}
    camadas.append(alt.Chart(df).mark_line(strokeWidth=2).encode(
        x=alt.X("ts:T", title="Horário (Brasília)", axis=alt.Axis(format="%H:%M")),
        y=alt.Y("pct_a:Q", title=f"{a}: % dos válidos contados", scale=alt.Scale(zero=False)),
        color=alt.Color("serie:N", title=None, legend=alt.Legend(orient="top"),
                        scale=alt.Scale(domain=dominio, range=[estilo[n][0] for n in dominio])),
        strokeDash=alt.StrokeDash("serie:N", legend=None,
                                  scale=alt.Scale(domain=dominio, range=[estilo[n][1] for n in dominio])),
        tooltip=[alt.Tooltip("ts:T", title="Horário", format="%H:%M"), alt.Tooltip("serie:N", title="Série"),
                 alt.Tooltip("pct_a:Q", title=f"{a} (%)", format=".2f")],
    ))
    camadas.append(alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(strokeDash=[4, 4], color=TEXTO[t]).encode(y="y:Q"))
    st.altair_chart(alt.layer(*camadas).properties(height=260), use_container_width=True)
    if nome_ref:
        v22 = caminho.viradas(ref["hora"].tolist(), ref["pct_validos"].tolist())
        st.caption(f"Cinza: 2º turno de 2022, candidato de mesmo número ({proj.num_a}), minuto a minuto "
                   f"(gráfico do g1 com dados do TSE). Em 2022 o líder da contagem mudou às "
                   f"{' e às '.join(h.replace(':', 'h') for h in v22)}. Faixa: 90% da previsão.")


def mostrar_historico(eleicao_codigo: int, proj, cor_de: dict, t: str) -> None:
    """Projeção ao longo da noite (gravada pelo terminal 2 a cada passada com dados novos)."""
    h = historico.ler(HISTORICO_PROJECAO, eleicao_codigo)
    if len(h) < 2:
        return
    h = h.assign(ts_brasilia=h["ts_dados"] - pd.Timedelta(hours=3))
    cor = cor_de.get(proj.num_a, CINZA[t])
    base = alt.Chart(h).encode(x=alt.X("ts_brasilia:T", title="Horário dos dados (Brasília)", axis=alt.Axis(format="%H:%M")))
    grafico = (
        base.mark_area(opacity=0.2, color=cor).encode(
            y=alt.Y("pct_a_inf:Q", title="% dos válidos", scale=alt.Scale(zero=False)),
            y2="pct_a_sup:Q")
        + base.mark_line(strokeWidth=2, color=cor).encode(
            y="pct_a:Q",
            tooltip=[alt.Tooltip("ts_brasilia:T", title="Dados de", format="%H:%M:%S"),
                     alt.Tooltip("pct_contado:Q", title="Eleitorado com dados (%)", format=".1f"),
                     alt.Tooltip("pct_a:Q", title="Mediana (%)", format=".2f"),
                     alt.Tooltip("pct_a_inf:Q", title="Faixa de", format=".2f"),
                     alt.Tooltip("pct_a_sup:Q", title="até", format=".2f"),
                     alt.Tooltip("prob_a_vence:Q", title=f"Prob. {proj.nome_a} vencer", format=".0%")])
        + alt.Chart(pd.DataFrame({"y": [50]})).mark_rule(strokeDash=[4, 4], color=TEXTO[t]).encode(y="y:Q")
    ).properties(height=220)
    st.markdown(f"**Projeção de {proj.nome_a} ao longo da noite**")
    st.altair_chart(grafico, use_container_width=True)


painel()
