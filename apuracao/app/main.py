"""Painel da apuração: navegação entre as páginas.

uv run streamlit run apuracao/app/main.py

Variáveis: APURACAO_DIR_PARQUET (padrão data/parquet), APURACAO_CONFIG
(padrão config/eleicoes.toml), APURACAO_ELEICAO (id da eleição aberta ao iniciar).
"""

import streamlit as st

st.set_page_config(page_title="Apuração", layout="wide")
st.navigation([
    st.Page("paginas/ao_vivo.py", title="Apuração ao vivo", default=True),
    st.Page("paginas/analise.py", title="Análise do 1º turno"),
    st.Page("paginas/noite.py", title="A noite da apuração"),
]).run()
