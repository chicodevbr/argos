"""Histórico da projeção durante a noite (presidente, nível Brasil).

O processo `modelo construir --loop` chama `registrar` depois de cada passada com dados
novos: calcula a projeção e acrescenta uma linha a data/projecao/historico.jsonl. Serve
ao gráfico "projeção ao longo da noite" do painel e, depois da eleição, para medir quanto
e quando a projeção acertou. Só grava quando há dados novos da eleição (marca = maior
ts_coleta), então reiniciar o processo ou repetir passadas não duplica linhas.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from apuracao.config import Config
from apuracao.modelo import consultas
from apuracao.projecao import caminho
from apuracao.projecao.ao_vivo import PRESIDENTE, projetar_ao_vivo

ARQUIVO = Path("data/projecao/historico.jsonl")


def _ultimas_marcas(caminho: Path) -> dict[int, str]:
    marcas: dict[int, str] = {}
    if Path(caminho).exists():
        for linha in Path(caminho).read_text().splitlines():
            try:
                r = json.loads(linha)
                marcas[r["eleicao"]] = r["marca"]
            except (json.JSONDecodeError, KeyError):
                continue
    return marcas


def registrar(con, cfg: Config, caminho: Path = ARQUIVO, n_sim: int = 1000) -> int:
    """Calcula e grava a projeção de cada eleição de 2º turno com presidente. Devolve linhas gravadas."""
    if not consultas.tem(con, "snapshot_totais"):
        return 0
    caminho = Path(caminho)
    marcas = _ultimas_marcas(caminho)
    gravadas = 0
    for e in cfg.eleicao:
        if e.turno != 2 or e.codigo <= 0 or "presidente" not in e.cargos:
            continue
        marca, ts_dados = con.execute(
            "SELECT max(ts_coleta), max(ts_tse) FROM snapshot_totais WHERE eleicao = ?", [e.codigo]).fetchone()
        if marca is None or str(marca) == marcas.get(e.codigo):
            continue
        p = projetar_ao_vivo(con, cfg, e, PRESIDENTE, "br", n_sim=n_sim)
        if p is None:
            continue
        linha = {
            "ts_calculo": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ts_dados": str(ts_dados), "marca": str(marca), "eleicao": e.codigo, "abrangencia": "br",
            "num_a": p.num_a, "nome_a": p.nome_a, "num_b": p.num_b, "nome_b": p.nome_b,
            "pct_contado": round(p.r.pct_contado, 3), "pct_a": round(p.r.pct_a, 3),
            "pct_a_inf": round(p.r.pct_a_inf, 3), "pct_a_sup": round(p.r.pct_a_sup, 3),
            "prob_a_vence": round(p.r.prob_a_vence, 4),
        }
        linha.update(_caminho(con, e.codigo, p))
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a") as f:
            f.write(json.dumps(linha, ensure_ascii=False) + "\n")
        gravadas += 1
    return gravadas


def _caminho(con, eleicao: int, p) -> dict:
    """Previsão de virada (horários em UTC); vazio se não der para prever. Nunca levanta erro."""
    try:
        c, _ = caminho.prever_ao_vivo(con, eleicao, p)
    except Exception:
        return {}
    if c is None:
        return {}
    iso = lambda ts: ts.isoformat(timespec="minutes") if ts is not None else None  # noqa: E731
    return {"prob_virada": round(c.prob_virada, 4), "virada_ts": iso(c.virada_ts),
            "virada_ts_inf": iso(c.virada_ts_inf), "virada_ts_sup": iso(c.virada_ts_sup),
            "hora_99": iso(c.hora_99), "ufs_sem_ritmo": c.ufs_sem_ritmo}


def ler(caminho: Path, eleicao: int) -> pd.DataFrame:
    """Linhas da eleição, em ordem de horário dos dados (ts_dados em UTC, sem fuso)."""
    if not Path(caminho).exists():
        return pd.DataFrame()
    linhas = []
    for l in Path(caminho).read_text().splitlines():
        try:
            r = json.loads(l)
        except json.JSONDecodeError:
            continue
        if r.get("eleicao") == eleicao:
            linhas.append(r)
    if not linhas:
        return pd.DataFrame()
    df = pd.DataFrame(linhas)
    df["ts_dados"] = pd.to_datetime(df["ts_dados"])
    return df.sort_values("ts_dados").reset_index(drop=True)
