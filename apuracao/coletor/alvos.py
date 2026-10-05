"""Monta a lista de arquivos (alvos) a monitorar a partir da configuração."""

from __future__ import annotations

from apuracao.config import Config, Eleicao
from apuracao.coletor.coletor import Alvo


def montar_alvos(cfg: Config, ambiente: str, eleicoes: list[Eleicao]) -> list[Alvo]:
    """Arquivos br/zz/UF de cada eleição e cargo.

    Arquivos por município ficam para depois: dependem do parser do arquivo de
    configuração de municípios (-cm.json), que exige a especificação do TSE.
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
                        )
                    )
    return alvos
