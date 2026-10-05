"""Log estruturado (uma linha JSON por evento) e contadores do coletor."""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import IO, Any


class LogJson:
    def __init__(self, saida: IO[str] | None = None):
        self.saida = saida or sys.stdout
        self.contadores: Counter[str] = Counter()

    def evento(self, evento: str, **campos: Any) -> None:
        linha = {"ts": datetime.now(timezone.utc).isoformat(), "evento": evento, **campos}
        try:
            self.saida.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")
            self.saida.flush()
        except Exception:
            pass  # log nunca derruba o coletor

    def contar(self, chave: str, n: int = 1) -> None:
        self.contadores[chave] += n

    def resumo(self) -> None:
        self.evento("resumo", **dict(self.contadores))
