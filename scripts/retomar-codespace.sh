#!/usr/bin/env bash
# Religa no Codespace o que estiver parado: coletor (2º turno), construção das
# tabelas em loop e painel Streamlit. Pode rodar quantas vezes quiser: o que
# já estiver de pé não é duplicado.
#
#   scripts/retomar-codespace.sh
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p logs

[[ -x .venv/bin/python ]] || uv sync --frozen

# [x]... no padrão evita que o pgrep encontre a si mesmo ou a este script.
if pgrep -f "[a]puracao.coletor --config" >/dev/null; then
  echo "coletor: já rodando"
else
  AMBIENTE=oficial ELEICOES="2026-t2-federal 2026-t2-estadual" MUNICIPIOS=1 DURACAO=36000 \
    nohup setsid scripts/coleta.sh >>logs/codespace.out 2>&1 </dev/null &
  echo "coletor: iniciado"
fi

if pgrep -f "[a]puracao.modelo construir" >/dev/null; then
  echo "tabelas: já rodando"
else
  nohup setsid uv run python -m apuracao.modelo construir --loop 15 >>logs/construir.out 2>&1 </dev/null &
  echo "tabelas: iniciado"
fi

if pgrep -f "[s]treamlit run apuracao" >/dev/null; then
  echo "painel: já rodando"
else
  nohup setsid uv run streamlit run apuracao/app/main.py --server.headless true --server.port 8501 \
    >>logs/streamlit.out 2>&1 </dev/null &
  echo "painel: iniciado"
fi

echo "painel: https://${CODESPACE_NAME:-SEU-CODESPACE}-8501.${GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN:-app.github.dev}"
