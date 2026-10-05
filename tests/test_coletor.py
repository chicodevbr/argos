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
from apuracao.coletor.coletor import (
    Alvo, Limitador, Parametros, buscar, espera_ausente, espera_backoff, rodar,
)
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
    # max_req_s=0 desliga o limitador global; ele tem testes próprios abaixo
    return Parametros(dir_raw=tmp_path / "raw", timeout_s=1, backoff_base_s=0.01, backoff_max_s=0.05,
                      max_req_s=0)


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
    assert a.falhas == 0 and a.ausencias == 1


def test_404_seguidos_contam_e_zeram_quando_publica(params):
    s = ServidorFalso()
    s.programar(URL, 404, 404, 404, (b"v1", '"e1"'))
    a, log = alvo(), LogJson(io.StringIO())
    for _ in range(3):
        buscar_sync(s, a, params, log)
    assert a.ausencias == 3
    assert buscar_sync(s, a, params, log) == "novo"
    assert a.ausencias == 0
    eventos = [json.loads(l)["evento"] for l in log.saida.getvalue().splitlines()]
    assert eventos.count("ausente") == 1  # loga só o início da sequência
    assert "publicado" in eventos


def test_espera_ausente_dobra_e_respeita_teto():
    assert 24 <= espera_ausente(1, 30, 300) <= 30
    assert 48 <= espera_ausente(2, 30, 300) <= 60
    assert 96 <= espera_ausente(3, 30, 300) <= 120
    assert 240 <= espera_ausente(20, 30, 300) <= 300


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


def test_rodar_espaca_requisicoes_de_arquivo_ausente(params):
    """Arquivo que só dá 404 recebe bem menos requisições que um que existe."""
    ausente = "https://teste/ausente.json"
    s = ServidorFalso()
    s.programar(URL, (b"x", '"e"'))
    s.programar(ausente, 404)
    params.escalonar_inicio = False
    params.max_espera_404_s = 10

    async def _():
        await rodar([alvo(URL, 0.01), alvo(ausente, 0.01)], params, LogJson(io.StringIO()),
                    asyncio.Event(), duracao_max_s=0.4, transport=s.transport())

    asyncio.run(_())
    n_ok = sum(str(r.url) == URL for r in s.pedidos)
    n_404 = sum(str(r.url) == ausente for r in s.pedidos)
    assert n_404 <= 8  # 0,01 -> 0,02 -> 0,04 -> ... soma passa de 0,4s em ~6 tentativas
    assert n_ok > 3 * n_404


def test_limitador_espaca_requisicoes():
    async def _():
        lim = Limitador(50)  # 1 a cada 20ms
        t0 = asyncio.get_running_loop().time()
        await asyncio.gather(*(lim.esperar() for _ in range(11)))
        return asyncio.get_running_loop().time() - t0

    assert asyncio.run(_()) >= 0.19  # 10 intervalos de 20ms


def test_rodar_respeita_teto_de_requisicoes(params):
    s = ServidorFalso()
    urls = [f"https://teste/{i}.json" for i in range(20)]
    for u in urls:
        s.programar(u, (b"x", '"e"'))
    params.escalonar_inicio, params.max_req_s, params.concorrencia = False, 40, 20

    async def _():
        await rodar([alvo(u, 0.001) for u in urls], params, LogJson(io.StringIO()),
                    asyncio.Event(), duracao_max_s=0.5, transport=s.transport())

    asyncio.run(_())
    assert len(s.pedidos) <= 0.5 * 40 + 2  # nunca passa do teto, mesmo com 20 alvos e concorrência 20


def test_uma_vez_busca_cada_alvo_e_encerra_sozinho(params):
    instavel = "https://teste/instavel.json"
    s = ServidorFalso()
    s.programar(URL, (b"x", '"e"'))
    s.programar(instavel, 503, 503, (b"y", '"f"'))
    s.programar("https://teste/ausente.json", 404)
    params.uma_vez = True
    log = LogJson(io.StringIO())

    async def _():
        alvos = [alvo(URL, 30), alvo(instavel, 30), alvo("https://teste/ausente.json", 30)]
        await asyncio.wait_for(rodar(alvos, params, log, asyncio.Event(), transport=s.transport()), timeout=5)

    asyncio.run(_())  # termina sem sinal nem duração máxima
    por_url = {}
    for r in s.pedidos:
        por_url[str(r.url)] = por_url.get(str(r.url), 0) + 1
    assert por_url == {URL: 1, instavel: 3, "https://teste/ausente.json": 1}  # erro é retentado; 404 não
    assert len(snapshots(params.dir_raw)) == 2
    assert "uma_vez_concluida" in log.saida.getvalue()


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


def test_montar_alvos_2o_turno_so_ufs_com_disputa():
    cfg = carregar(CONFIG)
    gov = montar_alvos(cfg, "oficial", [cfg.eleicao_por_id("2026-t2-estadual")])
    pres = montar_alvos(cfg, "oficial", [cfg.eleicao_por_id("2026-t2-federal")])
    assert sorted(a.uf for a in gov) == ["ac", "am", "df", "es", "rj", "rn", "to"]
    assert len(pres) == 29  # presidente: br, zz e as 27 UFs


# --- municípios: porteiro, alvos e config -------------------------------------

from apuracao.coletor import municipios as cfg_municipios
from apuracao.coletor.alvos import montar_alvos_municipios
from apuracao.modelo import ea12

FIX = Path(__file__).parent / "fixtures"


def test_espera_ausente_nunca_abaixo_do_intervalo():
    assert espera_ausente(5, 600, 300) >= 480  # município: intervalo 600 > teto 300


def test_porteiro_municipio_so_comeca_depois_da_uf(params):
    uf_ok, uf_404 = "https://teste/sp.json", "https://teste/rj.json"
    mun_sp, mun_rj = "https://teste/sp71072.json", "https://teste/rj60011.json"
    s = ServidorFalso()
    s.programar(uf_ok, 404, (b"uf", '"e"'))  # UF publica na 2ª tentativa
    s.programar(uf_404, 404)                 # UF nunca publica
    s.programar(mun_sp, (b"mun", '"m"'))
    s.programar(mun_rj, (b"mun", '"m"'))
    params.escalonar_inicio, params.max_espera_404_s = False, 0.02
    alvos = [
        Alvo(url=uf_ok, eleicao=1, uf="sp", arquivo="sp", intervalo_s=0.01, abre="1/sp"),
        Alvo(url=uf_404, eleicao=1, uf="rj", arquivo="rj", intervalo_s=0.01, abre="1/rj"),
        Alvo(url=mun_sp, eleicao=1, uf="sp", arquivo="sp71072", intervalo_s=0.01, espera="1/sp"),
        Alvo(url=mun_rj, eleicao=1, uf="rj", arquivo="rj60011", intervalo_s=0.01, espera="1/rj"),
    ]

    async def _():
        await rodar(alvos, params, LogJson(io.StringIO()), asyncio.Event(),
                    duracao_max_s=0.3, transport=s.transport())

    asyncio.run(_())
    pedidos = [str(r.url) for r in s.pedidos]
    assert mun_rj not in pedidos  # UF sem publicação: município nunca é consultado
    assert mun_sp in pedidos and pedidos.index(mun_sp) > pedidos.index(uf_ok, 1)


def test_porteiro_sem_alvo_que_abra_e_erro(params):
    a = Alvo(url=URL, eleicao=1, uf="sp", arquivo="x", intervalo_s=1, espera="1/sp")
    with pytest.raises(ValueError, match="portões"):
        asyncio.run(rodar([a], params, LogJson(io.StringIO()), asyncio.Event(), duracao_max_s=0.1))


def test_montar_alvos_municipios():
    cfg = carregar(CONFIG)
    ms = ea12.parse((FIX / "mun-e006257-cm.json").read_bytes())
    pres = montar_alvos_municipios(cfg, "oficial", cfg.eleicao_por_id("2026-t1-federal"), ms)
    assert len(pres) == 5757  # 5.571 municípios + 186 localidades do exterior
    nomes = {a.arquivo for a in pres}
    assert {"sp71072-c0001-e006257-u", "ac01120-c0001-e006257-u"} <= nomes  # = nomes reais das fixtures
    assert all(a.espera == f"6257/{a.uf}" for a in pres)
    gov2 = montar_alvos_municipios(cfg, "oficial", cfg.eleicao_por_id("2026-t2-estadual"), ms)
    ufs2 = {"ac", "am", "df", "es", "rj", "rn", "to"}
    assert {a.uf for a in gov2} == ufs2 and len(gov2) == sum(m.uf in ufs2 for m in ms)
    # todo portão aguardado por município é aberto por algum alvo de UF
    uf_alvos = montar_alvos(cfg, "oficial", [cfg.eleicao_por_id("2026-t1-federal")])
    assert {a.espera for a in pres} <= {a.abre for a in uf_alvos}


def _transporte(*respostas):
    fila = list(respostas)
    pedidos = []

    def h(req):
        pedidos.append(req)
        r = fila.pop(0) if len(fila) > 1 else fila[0]
        return r if isinstance(r, httpx.Response) else httpx.Response(r)
    return httpx.MockTransport(h), pedidos


def test_config_municipios_baixa_e_grava_bruto(tmp_path):
    cfg = carregar(CONFIG)
    e = cfg.eleicao_por_id("2026-t1-federal")
    t, pedidos = _transporte(httpx.Response(200, content=(FIX / "mun-e006257-cm.json").read_bytes()))
    ms = cfg_municipios.carregar(cfg, "oficial", e, tmp_path, LogJson(io.StringIO()), transport=t)
    assert len(ms) == 5757
    assert str(pedidos[0].url).endswith("/ele2026/6257/config/mun-e006257-cm.json")
    assert len(list((tmp_path / "6257" / "config").rglob("*.json.gz"))) == 1


def test_config_municipios_falha_usa_disco_e_404_nao_insiste(tmp_path):
    cfg = carregar(CONFIG)
    e = cfg.eleicao_por_id("2026-t1-federal")
    log = LogJson(io.StringIO())
    # sem nada em disco, sem reserva e 404: uma tentativa só, devolve None
    sem_reserva = cfg.model_copy(update={"coletor": cfg.coletor.model_copy(update={"municipios_reserva": None})})
    t, pedidos = _transporte(404)
    assert cfg_municipios.carregar(sem_reserva, "oficial", e, tmp_path, log, transport=t, espera_s=0) is None
    assert len(pedidos) == 1
    # baixa com sucesso uma vez, depois o servidor cai: usa a cópia do disco
    t, _ = _transporte(httpx.Response(200, content=(FIX / "mun-e006257-cm.json").read_bytes()))
    cfg_municipios.carregar(cfg, "oficial", e, tmp_path, log, transport=t)
    t, pedidos = _transporte(503)
    ms = cfg_municipios.carregar(cfg, "oficial", e, tmp_path, log, transport=t, espera_s=0)
    assert len(ms) == 5757 and len(pedidos) == 3


def test_config_municipios_usa_reserva_do_repo(tmp_path):
    cfg = carregar(CONFIG)
    e = cfg.eleicao_por_id("2026-t2-federal")
    t, pedidos = _transporte(404)  # -cm.json do 2º turno ainda não publicado, disco vazio
    log = LogJson(io.StringIO())
    ms = cfg_municipios.carregar(cfg, "oficial", e, tmp_path, log, transport=t, espera_s=0)
    assert len(ms) == 5757 and len(pedidos) == 1
    assert "config_municipios_reserva" in log.saida.getvalue()
