"""Sincronizador com destino falso. Nenhum teste acessa a rede."""

import io
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apuracao.coletor import snapshot
from apuracao.coletor.log import LogJson
from apuracao.coletor.sync import DestinoPasta, Sincronizador, pendentes


class DestinoFalso:
    def __init__(self, falhar: set[str] = frozenset()):
        self.falhar = set(falhar)
        self.recebidos: list[str] = []

    def enviar(self, chave: str, caminho: Path) -> None:
        if any(f in chave for f in self.falhar):
            raise ConnectionError("R2 fora do ar")
        self.recebidos.append(chave)


def gravar(dir_raw: Path, n: int, arquivo="br-c0001-e006257-u") -> None:
    base = datetime(2026, 10, 25, 20, tzinfo=timezone.utc)
    for i in range(n):
        snapshot.gravar(dir_raw, 6257, "br", arquivo, f"v{i}".encode(), url="u",
                        momento=base + timedelta(seconds=i), status=200, etag=None, last_modified=None)


def test_envia_dado_e_meta_e_ignora_tmp(tmp_path):
    gravar(tmp_path, 2)
    (tmp_path / "6257/br/br-c0001-e006257-u/.lixo.123.tmp").write_bytes(b"x")
    destino = DestinoFalso()
    s = Sincronizador(tmp_path, destino, LogJson(io.StringIO()), prefixo="raw")
    assert s.passada() == (4, 0)
    assert all(c.startswith("raw/6257/br/") for c in destino.recebidos)
    assert not any(c.endswith(".tmp") for c in destino.recebidos)


def test_nao_reenvia_mesmo_apos_reinicio(tmp_path):
    gravar(tmp_path, 1)
    Sincronizador(tmp_path, DestinoFalso(), LogJson(io.StringIO())).passada()
    gravar(tmp_path, 2, arquivo="sp-c0001-e006257-u")
    destino = DestinoFalso()
    novo = Sincronizador(tmp_path, destino, LogJson(io.StringIO()))  # novo processo
    assert novo.passada() == (4, 0)
    assert all("sp-c0001" in c for c in destino.recebidos)


def test_falha_e_retentada_na_proxima_passada(tmp_path):
    gravar(tmp_path, 1)
    destino = DestinoFalso(falhar={".json.gz"})
    log = LogJson(io.StringIO())
    s = Sincronizador(tmp_path, destino, log)
    assert s.passada() == (1, 1)  # meta sobe, dado falha
    assert log.contadores["sync_erros"] == 1
    destino.falhar.clear()
    assert s.passada() == (1, 0)
    assert s.passada() == (0, 0)


def test_rodar_faz_passada_final_ao_parar(tmp_path):
    destino = DestinoFalso()
    s = Sincronizador(tmp_path, destino, LogJson(io.StringIO()))
    parar = threading.Event()
    t = threading.Thread(target=s.rodar, args=(parar, 60))
    t.start()
    gravar(tmp_path, 1)  # gravado depois da primeira passada
    parar.set()
    t.join(timeout=5)
    assert not t.is_alive()
    assert len(destino.recebidos) == 2


def test_destino_pasta(tmp_path):
    gravar(tmp_path / "raw", 1)
    s = Sincronizador(tmp_path / "raw", DestinoPasta(tmp_path / "copia"), LogJson(io.StringIO()))
    s.passada()
    assert len(list((tmp_path / "copia").rglob("*.json.gz"))) == 1
    assert pendentes(tmp_path / "raw", s.enviados) == []
