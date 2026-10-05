#!/usr/bin/env bash
# Roda o coletor e o sincronizador lado a lado. Usado pelo GitHub Actions e
# pela cópia local redundante.
#
#   AMBIENTE=oficial ELEICOES="2026-t2-federal 2026-t2-estadual" scripts/coleta.sh
#
# Variáveis:
#   AMBIENTE      oficial | simulado (obrigatória)
#   ELEICOES      ids de config/eleicoes.toml separados por espaço (obrigatória)
#   DURACAO       segundos (padrão: coletor.duracao_max_s do config)
#   DIR_RAW       padrão data/raw
#   R2_*          se R2_BUCKET estiver definida, sincroniza com o R2
#   SYNC_PASTA    senão, se definida, copia para essa pasta
#
# SIGTERM/SIGINT neste script é repassado aos dois processos. O coletor para,
# e o sincronizador faz uma última passada antes de sair.
set -uo pipefail

: "${AMBIENTE:?defina AMBIENTE}"
: "${ELEICOES:?defina ELEICOES}"
DIR_RAW="${DIR_RAW:-data/raw}"
LOGS="${LOGS:-logs}"
PY="${PY:-.venv/bin/python}"   # python direto (sem uv run) para os sinais chegarem sem intermediário
mkdir -p "$DIR_RAW" "$LOGS"

args=(--ambiente "$AMBIENTE" --dir-raw "$DIR_RAW")
for e in $ELEICOES; do args+=(--eleicao "$e"); done
[[ -n "${DURACAO:-}" ]] && args+=(--duracao "$DURACAO")

# Valida a configuração antes de começar (falha rápido, ex.: código ainda 0).
"$PY" - "$ELEICOES" <<'EOF' || exit 2
import os, sys
from apuracao.config import carregar

def erro(msg):
    # No GitHub Actions, ::error:: vira anotação visível no resumo do run.
    print(f"::error::{msg}" if os.environ.get("GITHUB_ACTIONS") else f"ERRO: {msg}")
    sys.exit(1)

cfg = carregar("config/eleicoes.toml")
for i in sys.argv[1].split():
    try:
        e = cfg.eleicao_por_id(i)
    except KeyError:
        erro(f"eleição '{i}' não existe em config/eleicoes.toml")
    if e.codigo <= 0:
        erro(f"eleição {i} sem código definido em config/eleicoes.toml")
EOF

SYNC_PID=""
if [[ -n "${R2_BUCKET:-}" ]]; then
  "$PY" -m apuracao.coletor.sync --dir-raw "$DIR_RAW" >>"$LOGS/sync.jsonl" 2>&1 &
  SYNC_PID=$!
elif [[ -n "${SYNC_PASTA:-}" ]]; then
  "$PY" -m apuracao.coletor.sync --dir-raw "$DIR_RAW" --destino-pasta "$SYNC_PASTA" >>"$LOGS/sync.jsonl" 2>&1 &
  SYNC_PID=$!
else
  echo "aviso: sem R2_BUCKET nem SYNC_PASTA; snapshots ficam só em $DIR_RAW" >&2
fi

# Substituição de processo (e não pipe) para que $! seja o PID do python.
"$PY" -m apuracao.coletor "${args[@]}" > >(tee -a "$LOGS/coletor.jsonl") 2>&1 &
COLETOR_PID=$!

trap 'kill -TERM "$COLETOR_PID" 2>/dev/null || true' TERM INT

# O trap interrompe o wait; repete até o coletor realmente sair.
while :; do
  wait "$COLETOR_PID"; STATUS=$?
  kill -0 "$COLETOR_PID" 2>/dev/null || break
done

if [[ -n "$SYNC_PID" ]]; then
  kill -TERM "$SYNC_PID" 2>/dev/null || true
  wait "$SYNC_PID" || echo "aviso: sincronizador saiu com erro" >&2
fi
exit "$STATUS"
