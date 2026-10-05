"""Polling dos JSONs do TSE.

Cada alvo (um arquivo JSON) roda no seu próprio laço assíncrono, com intervalo
próprio, início escalonado e backoff independente. Erro em um alvo nunca afeta
os outros nem derruba o processo.

Resultados possíveis de uma busca:
- novo       200 com conteúdo diferente do último: grava snapshot
- igual      200 com conteúdo idêntico ao último (CDN ignorou ETag): não grava
- nao_mod    304: não grava
- ausente    404: arquivo ainda não publicado. As instruções do TSE avisam que
             muitos 404 podem bloquear o IP, então a espera dobra a cada 404
             seguido (até max_espera_404_s) e volta ao normal no 1º 200/304.
- erro       5xx, 429, timeout, erro de rede ou outro status: backoff
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import httpx

from apuracao.coletor import snapshot
from apuracao.coletor.log import LogJson


@dataclass
class Alvo:
    url: str
    eleicao: int
    uf: str
    arquivo: str
    intervalo_s: float

    # estado da última resposta válida
    etag: str | None = None
    last_modified: str | None = None
    sha256: str | None = None
    falhas: int = 0
    ausencias: int = 0  # 404 seguidos
    # Porteiro: arquivos de município só começam depois que o arquivo da UF
    # respondeu 200/304 uma vez. Antes da publicação, só os ~36 arquivos de UF
    # tomam 404 (com backoff), e não os milhares de municípios.
    abre: str | None = None      # chave que este alvo libera ao ter sucesso (ex.: "6258/sp")
    espera: str | None = None    # chave que este alvo aguarda antes da 1ª requisição
    _restaurado: bool = field(default=False, repr=False)


@dataclass
class Parametros:
    dir_raw: Path
    timeout_s: float = 10.0
    concorrencia: int = 8
    backoff_base_s: float = 2.0
    backoff_max_s: float = 120.0
    max_espera_404_s: float = 300.0
    intervalo_resumo_s: float = 60.0
    escalonar_inicio: bool = True
    # Teto global de requisições/s (o TSE permite 100 por IP e bloqueia 10 min
    # quem passa). Com milhares de arquivos de município, picos chegariam perto.
    max_req_s: float = 20.0
    # Busca cada alvo até 1 resultado sem erro e encerra (carga de eleição já apurada).
    uma_vez: bool = False


class Limitador:
    """Espaça o início das requisições em pelo menos 1/taxa segundos (todas as tarefas)."""

    def __init__(self, taxa: float):
        self.intervalo = 1 / taxa if taxa > 0 else 0.0
        self._proximo = 0.0
        self._trava = asyncio.Lock()

    async def esperar(self) -> None:
        if not self.intervalo:
            return
        async with self._trava:
            agora = time.monotonic()
            if self._proximo > agora:
                await asyncio.sleep(self._proximo - agora)
            self._proximo = max(agora, self._proximo) + self.intervalo


def espera_backoff(falhas: int, base: float, maximo: float) -> float:
    """Backoff exponencial com jitter: uniforme em [teto/2, teto], teto = min(max, base*2^(n-1))."""
    teto = min(maximo, base * 2 ** max(falhas - 1, 0))
    return random.uniform(teto / 2, teto)


def espera_ausente(ausencias: int, intervalo: float, maximo: float) -> float:
    """Espera após 404: intervalo * 2^(n-1), com jitter de até 20%.

    Teto = max(maximo, intervalo): um arquivo ausente nunca é consultado com mais
    frequência que o intervalo normal dele (municípios têm intervalo > maximo).
    """
    teto = min(max(maximo, intervalo), intervalo * 2 ** max(ausencias - 1, 0))
    return random.uniform(teto * 0.8, teto)


def _restaurar(alvo: Alvo, dir_raw: Path) -> None:
    """Na primeira busca, recupera ETag/hash do último snapshot em disco."""
    if alvo._restaurado:
        return
    alvo._restaurado = True
    meta = snapshot.ultimo_meta(dir_raw, alvo.eleicao, alvo.uf, alvo.arquivo)
    if meta:
        alvo.etag, alvo.last_modified, alvo.sha256 = meta.etag, meta.last_modified, meta.sha256


async def buscar(
    cliente: httpx.AsyncClient,
    alvo: Alvo,
    params: Parametros,
    log: LogJson,
    agora: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> str:
    """Faz uma requisição condicional para o alvo e devolve o resultado."""
    _restaurar(alvo, params.dir_raw)
    cabecalhos = {}
    if alvo.etag:
        cabecalhos["If-None-Match"] = alvo.etag
    if alvo.last_modified:
        cabecalhos["If-Modified-Since"] = alvo.last_modified

    log.contar("requisicoes")
    momento = agora()
    t0 = time.monotonic()
    try:
        resp = await cliente.get(alvo.url, headers=cabecalhos, timeout=params.timeout_s)
        conteudo = resp.content
    except httpx.TimeoutException as e:
        return _falha(alvo, log, "timeout", erro=type(e).__name__)
    except httpx.HTTPError as e:
        return _falha(alvo, log, "rede", erro=f"{type(e).__name__}: {e}")
    ms = round((time.monotonic() - t0) * 1000)

    if resp.status_code == 304:
        alvo.falhas = alvo.ausencias = 0
        log.contar("nao_mod")
        return "nao_mod"
    if resp.status_code == 404:
        alvo.falhas = 0
        alvo.ausencias += 1
        log.contar("ausente")
        if alvo.ausencias == 1:
            log.evento("ausente", url=alvo.url)
        return "ausente"
    if resp.status_code != 200:
        return _falha(alvo, log, "http", status=resp.status_code)

    if alvo.ausencias:
        log.evento("publicado", url=alvo.url, apos_404=alvo.ausencias)
    alvo.falhas = alvo.ausencias = 0
    etag = resp.headers.get("ETag")
    last_mod = resp.headers.get("Last-Modified")
    hash_ = snapshot.sha256(conteudo)
    if hash_ == alvo.sha256:
        alvo.etag, alvo.last_modified = etag or alvo.etag, last_mod or alvo.last_modified
        log.contar("igual")
        return "igual"

    try:
        caminho = snapshot.gravar(
            params.dir_raw, alvo.eleicao, alvo.uf, alvo.arquivo, conteudo,
            url=alvo.url, momento=momento, status=resp.status_code,
            etag=etag, last_modified=last_mod,
        )
    except OSError as e:
        # Não atualiza ETag/hash: na próxima volta o servidor manda de novo.
        log.contar("erros")
        log.contar("erro_gravacao")
        log.evento("erro_gravacao", url=alvo.url, erro=str(e))
        return "erro"

    alvo.etag, alvo.last_modified, alvo.sha256 = etag, last_mod, hash_
    log.contar("snapshots")
    log.evento("snapshot", url=alvo.url, caminho=str(caminho), bytes=len(conteudo), ms=ms)
    return "novo"


def _falha(alvo: Alvo, log: LogJson, tipo: str, **campos) -> str:
    alvo.falhas += 1
    log.contar("erros")
    log.contar(f"erro_{tipo}")
    log.evento("erro", tipo=tipo, url=alvo.url, falhas=alvo.falhas, **campos)
    return "erro"


async def _esperar(parar: asyncio.Event, segundos: float) -> None:
    try:
        await asyncio.wait_for(parar.wait(), timeout=max(segundos, 0))
    except TimeoutError:
        pass


async def _laco_alvo(
    cliente: httpx.AsyncClient,
    alvo: Alvo,
    params: Parametros,
    log: LogJson,
    sem: asyncio.Semaphore,
    parar: asyncio.Event,
    limitador: Limitador | None = None,
    portoes: dict[str, asyncio.Event] | None = None,
) -> None:
    if alvo.espera and portoes is not None:
        portao = portoes[alvo.espera]
        espera_portao = asyncio.ensure_future(portao.wait())
        espera_parar = asyncio.ensure_future(parar.wait())
        await asyncio.wait([espera_portao, espera_parar], return_when=asyncio.FIRST_COMPLETED)
        espera_portao.cancel()
        espera_parar.cancel()
        if parar.is_set():
            return
    if params.escalonar_inicio and not params.uma_vez:
        await _esperar(parar, random.uniform(0, alvo.intervalo_s))
    while not parar.is_set():
        try:
            async with sem:
                if parar.is_set():
                    break
                if limitador:
                    await limitador.esperar()
                    if parar.is_set():  # o sinal pode ter chegado durante a espera na fila
                        break
                resultado = await buscar(cliente, alvo, params, log)
        except Exception as e:  # rede de segurança: nada derruba o laço
            log.contar("erros")
            log.evento("erro_inesperado", url=alvo.url, erro=repr(e))
            resultado = "erro"
            alvo.falhas += 1
        if alvo.abre and portoes is not None and resultado in ("novo", "igual", "nao_mod"):
            portoes[alvo.abre].set()
        if params.uma_vez and resultado != "erro":
            return
        if resultado == "erro":
            espera = espera_backoff(alvo.falhas, params.backoff_base_s, params.backoff_max_s)
        elif resultado == "ausente":
            espera = espera_ausente(alvo.ausencias, alvo.intervalo_s, params.max_espera_404_s)
        else:
            espera = alvo.intervalo_s
        await _esperar(parar, espera)


async def rodar(
    alvos: list[Alvo],
    params: Parametros,
    log: LogJson,
    parar: asyncio.Event,
    duracao_max_s: float | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> None:
    """Roda o coletor até `parar` ser sinalizado ou `duracao_max_s` esgotar."""
    log.evento("inicio", alvos=len(alvos), dir_raw=str(params.dir_raw))
    limites = httpx.Limits(max_connections=params.concorrencia)
    async with httpx.AsyncClient(
        transport=transport, limits=limites, follow_redirects=True,
        headers={"User-Agent": "apuracao-coletor/0.1"},
    ) as cliente:
        sem = asyncio.Semaphore(params.concorrencia)
        limitador = Limitador(params.max_req_s)
        chaves = {a.abre for a in alvos if a.abre}
        sem_porteiro = {a.espera for a in alvos if a.espera} - chaves
        if sem_porteiro:
            raise ValueError(f"alvos aguardam portões que nenhum alvo abre: {sorted(sem_porteiro)}")
        portoes = {k: asyncio.Event() for k in chaves}
        tarefas = [
            asyncio.create_task(_laco_alvo(cliente, a, params, log, sem, parar, limitador, portoes))
            for a in alvos
        ]

        async def resumos():
            while not parar.is_set():
                await _esperar(parar, params.intervalo_resumo_s)
                if not parar.is_set():
                    log.resumo()

        async def cronometro():
            if duracao_max_s is not None:
                await _esperar(parar, duracao_max_s)
                if not parar.is_set():
                    log.evento("duracao_max_atingida", segundos=duracao_max_s)
                    parar.set()

        espera_parar = asyncio.create_task(parar.wait())
        extras = [asyncio.create_task(resumos()), asyncio.create_task(cronometro()), espera_parar]
        if params.uma_vez and tarefas:
            todas = asyncio.gather(*tarefas, return_exceptions=True)
            await asyncio.wait([todas, espera_parar], return_when=asyncio.FIRST_COMPLETED)
            if not parar.is_set():
                log.evento("uma_vez_concluida", alvos=len(tarefas))
                parar.set()
        else:
            await parar.wait()
        # Dá até timeout+5s para requisições em andamento terminarem e gravarem.
        _, pendentes = await asyncio.wait(tarefas, timeout=params.timeout_s + 5)
        for t in [*pendentes, *extras]:
            t.cancel()
        await asyncio.gather(*tarefas, *extras, return_exceptions=True)
    log.resumo()
    log.evento("fim")
