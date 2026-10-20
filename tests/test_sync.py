"""Sincronizador com destino falso. Nenhum teste acessa a rede."""

import io
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apuracao.coletor import snapshot
from apuracao.coletor.log import LogJson
from apuracao.coletor.sync import DestinoPasta, DestinoR2, Sincronizador, pendentes


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


def test_passada_envia_em_paralelo(tmp_path):
    # 40 snapshots = 80 arquivos a 0,2 s cada: em série levaria 16 s.
    class DestinoLento(DestinoFalso):
        def enviar(self, chave, caminho):
            threading.Event().wait(0.2)
            super().enviar(chave, caminho)

    gravar(tmp_path, 40)
    destino = DestinoLento()
    sinc = Sincronizador(tmp_path, destino, LogJson(io.StringIO()))
    t0 = datetime.now()
    assert sinc.passada() == (80, 0)
    assert (datetime.now() - t0).total_seconds() < 3


def test_destino_r2_usa_put_object(tmp_path):
    class S3Falso:
        def __init__(self):
            self.chamadas = []

        def put_object(self, **kw):
            self.chamadas.append(kw)

    r2 = DestinoR2.__new__(DestinoR2)  # sem boto3 nem rede
    r2.bucket, r2.s3 = "b", S3Falso()
    gz = tmp_path / "x.json.gz"
    gz.write_bytes(b"\x1f\x8b dado")
    r2.enviar("raw/6257/br/x.json.gz", gz)
    assert r2.s3.chamadas == [{"Bucket": "b", "Key": "raw/6257/br/x.json.gz",
                               "Body": b"\x1f\x8b dado", "ContentType": "application/gzip"}]


def test_pendentes_chave_relativa_com_barra_no_fim(tmp_path):
    gravar(tmp_path, 1)
    chaves = {k for k, _ in pendentes(str(tmp_path) + "/", set())}
    assert chaves == {k for k, _ in pendentes(tmp_path, set())}
    assert all(k.startswith("6257/br/br-c0001-e006257-u/") for k in chaves)
