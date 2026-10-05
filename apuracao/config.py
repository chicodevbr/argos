"""Carrega e valida config/eleicoes.toml."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, model_validator


class Eleicao(BaseModel):
    id: str
    ciclo: str
    turno: int
    codigo: int
    cargos: list[str]
    confirmado: bool = False
    ufs: list[str] | None = None  # se definido, substitui coletor.ufs


class Urls(BaseModel):
    resultado: str
    resultado_municipio: str
    acompanhamento: str
    config_municipios: str


class Coletor(BaseModel):
    intervalo_uf_s: float
    intervalo_municipio_s: float
    timeout_s: float
    duracao_max_s: float
    dir_raw: Path
    ufs: list[str]
    cargos_nacionais: list[str] = ["presidente"]
    tipos: list[str] = ["resultado"]
    concorrencia: int = 8
    backoff_base_s: float = 2.0
    backoff_max_s: float = 120.0
    max_espera_404_s: float = 300.0
    max_req_s: float = 20.0
    municipios_reserva: Path | None = None


class Historico(BaseModel):
    base: str
    votacao: str
    detalhe: str
    dir: Path


class Ibge(BaseModel):
    malha: str
    arquivo: Path


class Config(BaseModel):
    ambientes: dict[str, str]
    cargos: dict[str, int]
    urls: Urls
    eleicao: list[Eleicao]
    coletor: Coletor
    historico: Historico
    ibge: Ibge | None = None

    @model_validator(mode="after")
    def _valida(self) -> Config:
        ids = [e.id for e in self.eleicao]
        if len(ids) != len(set(ids)):
            raise ValueError("ids de eleição repetidos")
        for e in self.eleicao:
            desconhecidos = set(e.cargos) - set(self.cargos)
            if desconhecidos:
                raise ValueError(f"{e.id}: cargos desconhecidos {desconhecidos}")
        return self

    def eleicao_por_id(self, id: str) -> Eleicao:
        for e in self.eleicao:
            if e.id == id:
                return e
        raise KeyError(id)

    def url_base(self, ambiente: str) -> str:
        return self.ambientes[ambiente].rstrip("/")

    def url_resultado(
        self, ambiente: str, eleicao: Eleicao, uf: str, cargo: str, cod_mun: str | None = None
    ) -> str:
        tipo = "resultado_municipio" if cod_mun else "resultado"
        return self.url(ambiente, eleicao, uf, cargo, tipo, cod_mun)

    def url(
        self,
        ambiente: str,
        eleicao: Eleicao,
        uf: str,
        cargo: str,
        tipo: str = "resultado",
        cod_mun: str | None = None,
    ) -> str:
        if eleicao.codigo <= 0:
            raise ValueError(f"{eleicao.id}: código de eleição ainda não definido")
        modelo = getattr(self.urls, tipo)
        caminho = modelo.format(
            ciclo=eleicao.ciclo,
            eleicao=eleicao.codigo,
            uf=uf.lower(),
            cod_mun=cod_mun,
            cargo=self.cargos[cargo],
        )
        return self.url_base(ambiente) + caminho


def carregar(caminho: str | Path) -> Config:
    with open(caminho, "rb") as f:
        return Config.model_validate(tomllib.load(f))
