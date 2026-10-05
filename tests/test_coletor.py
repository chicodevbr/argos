"""Coletor contra servidor falso. Nenhum teste acessa a rede."""

import asyncio
import gzip
import io
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from apuracao.coletor import snapshot
from apuracao.coletor.alvos import montar_alvos
from apuracao.coletor.coletor import Alvo, Parametros, buscar, espera_backoff, rodar
from apuracao.coletor.log import LogJson
from apuracao.config import carregar

CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"
URL = "https://teste/ele2026/6257/dados/br/br-c0001-e006257-u.json"


class ServidorFalso:
    """Responde conforme um roteiro por URL; respeita If-None-Match quando há ETag."""

    def __init__(self):
        self.roteiro: dict[str, list] = {}
        self.pedidos: list[httpx.Request] = []

    def programar(self, url: str, *respostas):
        self.roteiro.setdefault(url, []).extend(respostas)

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.pedidos.append(req)
        fila = self.roteiro.get(str(req.url), [])
        acao = fila.pop(0) if len(fila) > 1 else (fila[0] if fila else 404)
        if acao == "timeout":
            raise httpx.ReadTimeout("simulado", request=req)
        if acao == "explode":
            raise ValueError("bug inesperado")
        if isinstance(acao, int):
            return httpx.Response(acao)
        corpo, etag = acao
        if etag and req.headers.get("If-None-Match") == etag:
            return httpx.Response(304)
        headers = {"ETag": etag} if etag else {}
        return httpx.Response(200, content=corpo, headers=headers)

    def transport(self):
        return httpx.MockTransport(self)


def alvo(url=URL, intervalo=30.0) -> Alvo:
    return Alvo(url=url, eleicao=6257, uf="br", arquivo=url.rsplit("/", 1)[-1][:-5], intervalo_s=intervalo)


def snapshots(dir_raw: Path) -> list[Path]:
    return sorted(dir_raw.rglob("*.json.gz"))


def buscar_sync(servidor, a, params, log=None):
    async def _():
        async with httpx.AsyncClient(transport=servidor.transport()) as c:
            return await buscar(c, a, params, log or LogJson(io.StringIO()))
    return asyncio.run(_())


@pytest.fixture
def params(tmp_path):
    return Parametros(dir_raw=tmp_path / "raw", timeout_s=1, backoff_base_s=0.01, backoff_max_s=0.05)


# --- snapshot -----------------------------------------------------------------


def test_gravar_cria_gz_e_meta(tmp_path):
    momento = datetime(2026, 10, 25, 20, 15, 30, 123456, tzinfo=timezone.utc)
    p = snapshot.gravar(
        tmp_path, 6257, "br", "br-c0001-e006257-u", b'{"a":1}',
        url=URL, momento=momento, status=200, etag='"x"', last_modified=None,
    )
    assert p == tmp_path / "6257/br/br-c0001-e006257-u/20261025T201530.123456Z.json.gz"
    assert gzip.decompress(p.read_bytes()) == b'{"a":1}'
    meta = json.loads(p.with_name("20261025T201530.123456Z.meta.json").read_text())
    assert meta["etag"] == '"x"' and meta["ts_coleta"].startswith("2026-10-25T20:15:30")
    assert not list(p.parent.glob("*.tmp"))


def test_gravar_nunca_sobrescreve(tmp_path):
    momento = datetime(2026, 10, 25, 20, 0, tzinfo=timezone.utc)
    kw = dict(url=URL, momento=momento, status=200, etag=None, last_modified=None)
    p1 = snapshot.gravar(tmp_path, 1, "br", "arq", b"um", **kw)
    p2 = snapshot.gravar(tmp_path, 1, "br", "arq", b"dois", **kw)
    assert p1 != p2
    assert gzip.decompress(p1.read_bytes()) == b"um"
    assert gzip.decompress(p2.read_bytes()) == b"dois"


# --- uma busca ----------------------------------------------------------------


def test_200_grava_e_304_nao_grava(params):
    s = ServidorFalso()
    s.programar(URL, (b'{"v":1}', '"e1"'))
    a = alvo()
    assert buscar_sync(s, a, params) == "novo"
    assert buscar_sync(s, a, params) == "nao_mod"
    assert s.pedidos[1].headers["If-None-Match"] == '"e1"'
    assert len(snapshots(params.dir_raw)) == 1


def test_conteudo_novo_gera_arquivo_novo_sem_apagar_antigo(params):
    s = ServidorFalso()
    s.programar(URL, (b"v1", '"e1"'), (b"v2", '"e2"'))
    a = alvo()
    buscar_sync(s, a, params)
    buscar_sync(s, a, params)
    conteudos = [gzip.decompress(p.read_bytes()) for p in snapshots(params.dir_raw)]
    assert conteudos == [b"v1", b"v2"]


def test_200_identico_sem_etag_nao_grava(params):
    s = ServidorFalso()
    s.programar(URL, (b"mesmo", None))
    a = alvo()
    assert buscar_sync(s, a, params) == "novo"
    assert buscar_sync(s, a, params) == "igual"
    assert len(snapshots(params.dir_raw)) == 1


def test_404_ausente(params):
    s = ServidorFalso()
    s.programar(URL, 404)
    a = alvo()
    assert buscar_sync(s, a, params) == "ausente"
    assert a.falhas == 0


@pytest.mark.parametrize("acao,contador", [(503, "erro_http"), (429, "erro_http"), ("timeout", "erro_timeout")])
def test_erros_incrementam_falhas_e_recuperam(params, acao, contador):
    s = ServidorFalso()
    s.programar(URL, acao, acao, (b"ok", '"e"'))
    a, log = alvo(), LogJson(io.StringIO())
    assert buscar_sync(s, a, params, log) == "erro"
    assert buscar_sync(s, a, params, log) == "erro"
    assert a.falhas == 2 and log.contadores[contador] == 2
    assert not snapshots(params.dir_raw)
    assert buscar_sync(s, a, params, log) == "novo"
    assert a.falhas == 0


def test_retoma_etag_do_disco_apos_reinicio(params):
    s = ServidorFalso()
    s.programar(URL, (b"v1", '"e1"'))
    buscar_sync(s, alvo(), params)
    novo_processo = alvo()  # estado em memória zerado
    assert buscar_sync(s, novo_processo, params) == "nao_mod"
    assert s.pedidos[-1].headers["If-None-Match"] == '"e1"'


def test_backoff_cresce_e_respeita_teto():
    assert 0.5 <= espera_backoff(1, 1, 100) <= 1
    assert 4 <= espera_backoff(4, 1, 100) <= 8
    assert espera_backoff(50, 1, 100) <= 100


# --- laço completo ------------------------------------------------------------


def test_rodar_isola_erros_e_respeita_duracao(params):
    ruim = "https://teste/ruim.json"
    bug = "https://teste/bug.json"
    s = ServidorFalso()
    s.programar(URL, *[(f"v{i}".encode(), f'"e{i}"') for i in range(1000)])
    s.programar(ruim, 500)
    s.programar(bug, "explode")
    params.escalonar_inicio = False
    log = LogJson(io.StringIO())
    alvos = [alvo(URL, 0.02), alvo(ruim, 0.02), alvo(bug, 0.02)]

    async def _():
        parar = asyncio.Event()
        await asyncio.wait_for(
            rodar(alvos, params, log, parar, duracao_max_s=0.3, transport=s.transport()),
            timeout=5,
        )

    asyncio.run(_())
    assert len(snapshots(params.dir_raw)) >= 3
    assert log.contadores["erro_http"] >= 2
    assert log.contadores["erros"] > log.contadores["erro_http"]  # inclui o "explode"
    eventos = [json.loads(l)["evento"] for l in log.saida.getvalue().splitlines()]
    assert "duracao_max_atingida" in eventos and eventos[-1] == "fim"


def test_rodar_para_rapido_ao_sinal(params):
    s = ServidorFalso()
    s.programar(URL, (b"x", '"e"'))
    log = LogJson(io.StringIO())

    async def _():
        parar = asyncio.Event()
        tarefa = asyncio.create_task(rodar([alvo(URL, 30)], params, log, parar, transport=s.transport()))
        await asyncio.sleep(0.05)
        parar.set()  # o mesmo que o handler de SIGTERM faz
        await asyncio.wait_for(tarefa, timeout=2)

    asyncio.run(_())


# --- alvos --------------------------------------------------------------------


def test_montar_alvos():
    cfg = carregar(CONFIG)
    pres = montar_alvos(cfg, "oficial", [cfg.eleicao_por_id("2026-t1-federal")])
    gov = montar_alvos(cfg, "oficial", [cfg.eleicao_por_id("2026-t1-estadual")])
    assert len(pres) == 29 and {"br", "zz"} <= {a.uf for a in pres}
    assert len(gov) == 27 and "br" not in {a.uf for a in gov}
    assert pres[0].arquivo == "br-c0001-e006257-u"
