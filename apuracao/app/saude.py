"""Saúde da coleta local, lida do fim de logs/coletor.jsonl (o coletor registra um "resumo"
a cada 60 s mesmo quando nada muda). Responde: o NOSSO coletor está vivo?

Estados:
- ok:        atividade há menos de 2 min
- atencao:   de 2 a 5 min sem atividade
- parado:    mais de 5 min sem atividade, ou o coletor registrou "fim" depois do último "inicio"
- ausente:   não há log (painel rodando sem coleta local nesta máquina)
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

LIMITE_ATENCAO_S = 120
LIMITE_PARADO_S = 300
BYTES_FINAIS = 256 * 1024  # o log passa de dezenas de MB na noite: lê só o fim


@dataclass
class Saude:
    estado: str
    segundos_sem_atividade: float | None = None
    ultimo_snapshot: datetime | None = None
    requisicoes: int = 0
    snapshots: int = 0
    erros: int = 0
    ultimo_erro: str = ""


def _eventos_finais(caminho: Path) -> list[dict]:
    with open(caminho, "rb") as f:
        f.seek(0, 2)
        tamanho = f.tell()
        f.seek(max(0, tamanho - BYTES_FINAIS))
        bruto = f.read().decode("utf-8", errors="replace")
    linhas = bruto.splitlines()
    if tamanho > BYTES_FINAIS:
        linhas = linhas[1:]  # a primeira pode ter vindo cortada
    eventos = []
    for linha in linhas:
        if linha.startswith("{"):
            try:
                eventos.append(json.loads(linha))
            except json.JSONDecodeError:
                continue
    return eventos


def ler_saude(caminho: Path, agora: datetime | None = None) -> Saude:
    caminho = Path(caminho)
    if not caminho.exists():
        return Saude("ausente")
    agora = agora or datetime.now(timezone.utc)
    eventos = [e for e in _eventos_finais(caminho) if "ts" in e and "evento" in e]
    if not eventos:
        return Saude("ausente")

    ultimo = datetime.fromisoformat(eventos[-1]["ts"])
    sem_atividade = (agora - ultimo).total_seconds()
    resumo = next((e for e in reversed(eventos) if e["evento"] == "resumo"), {})
    snap = next((e for e in reversed(eventos) if e["evento"] == "snapshot"), None)
    erro = next((e for e in reversed(eventos) if e["evento"].startswith("erro")), None)
    ordem = [e["evento"] for e in eventos if e["evento"] in ("inicio", "fim")]
    encerrado = bool(ordem) and ordem[-1] == "fim"

    if encerrado or sem_atividade > LIMITE_PARADO_S:
        estado = "parado"
    elif sem_atividade > LIMITE_ATENCAO_S:
        estado = "atencao"
    else:
        estado = "ok"
    texto_erro = ""
    if erro:
        detalhe = erro.get("tipo") or erro["evento"]
        texto_erro = f"{detalhe} em {erro.get('url', '').rsplit('/', 1)[-1]} às " \
                     f"{datetime.fromisoformat(erro['ts']).astimezone(timezone.utc):%H:%M:%S} UTC"
    return Saude(
        estado=estado,
        segundos_sem_atividade=sem_atividade,
        ultimo_snapshot=datetime.fromisoformat(snap["ts"]) if snap else None,
        requisicoes=int(resumo.get("requisicoes", 0)),
        snapshots=int(resumo.get("snapshots", 0)),
        erros=int(resumo.get("erros", 0)),
        ultimo_erro=texto_erro,
    )
