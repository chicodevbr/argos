"""Monta a lista de arquivos (alvos) a monitorar a partir da configuração."""

from __future__ import annotations

from apuracao.config import Config, Eleicao
from apuracao.coletor.coletor import Alvo
from apuracao.modelo.ea12 import Municipio


def montar_alvos(cfg: Config, ambiente: str, eleicoes: list[Eleicao]) -> list[Alvo]:
    """Arquivos br/zz/UF de cada eleição e cargo.

    Os arquivos de resultado de UF abrem o portão dos municípios daquela UF.
    """
    c = cfg.coletor
    alvos: list[Alvo] = []
    for eleicao in eleicoes:
        for cargo in eleicao.cargos:
            ufs = list(eleicao.ufs if eleicao.ufs is not None else c.ufs)
            if cargo in c.cargos_nacionais:
                ufs = ["br", "zz", *ufs]
            for uf in ufs:
                for tipo in c.tipos:
                    url = cfg.url(ambiente, eleicao, uf, cargo, tipo)
                    arquivo = url.rsplit("/", 1)[-1].removesuffix(".json")
                    alvos.append(
                        Alvo(
                            url=url,
                            eleicao=eleicao.codigo,
                            uf=uf,
                            arquivo=arquivo,
                            intervalo_s=c.intervalo_uf_s,
                            abre=portao(eleicao, uf) if tipo == "resultado" else None,
                        )
                    )
    return alvos


def portao(eleicao: Eleicao, uf: str) -> str:
    return f"{eleicao.codigo}/{uf}"


def montar_alvos_municipios(
    cfg: Config, ambiente: str, eleicao: Eleicao, municipios: list[Municipio]
) -> list[Alvo]:
    """Um arquivo por município (e localidade do exterior, para cargos nacionais).

    Cada um aguarda o arquivo da sua UF responder antes da 1ª requisição (ver Alvo.espera).
    """
    c = cfg.coletor
    alvos: list[Alvo] = []
    for cargo in eleicao.cargos:
        ufs = set(eleicao.ufs if eleicao.ufs is not None else c.ufs)
        if cargo in c.cargos_nacionais:
            ufs.add("zz")
        for m in municipios:
            if m.uf not in ufs:
                continue
            url = cfg.url(ambiente, eleicao, m.uf, cargo, "resultado_municipio", m.cod_tse)
            alvos.append(Alvo(
                url=url,
                eleicao=eleicao.codigo,
                uf=m.uf,
                arquivo=url.rsplit("/", 1)[-1].removesuffix(".json"),
                intervalo_s=c.intervalo_municipio_s,
                espera=portao(eleicao, m.uf),
            ))
    return alvos
