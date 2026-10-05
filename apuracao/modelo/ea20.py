"""Parser do EA20 (arquivo de resultado unificado, `-u.json`).

Referência: docs/tse/tse-ea20-arquivo-de-resultado-unificado.pdf. Lê só os campos
usados nas tabelas; o resto do JSON fica no dado bruto.

Convenções do TSE tratadas aqui:
- Todos os valores vêm como texto; números podem vir vazios ("") antes da totalização.
- Percentuais usam vírgula decimal ("47,027772356").
- Datas/horas (dg/hg, dt/ht) estão em horário de Brasília. A spec não diz o fuso;
  a evidência é o Last-Modified do servidor: hg=02:59:31 no JSON e
  Last-Modified=06:00:10 GMT na resposta (br, 1º turno, 05/10/2026). Brasil não
  tem horário de verão desde 2019, então UTC-3 fixo.
- ts_tse usa a GERAÇÃO (dg/hg). A totalização (dt/ht) às vezes é posterior à
  geração (ex.: zz do 1º turno: ht=09:19:47, hg=02:59:27), então não serve para ordenar.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

BRASILIA = timezone(timedelta(hours=-3))


def inteiro(v: str | None) -> int | None:
    if v is None or v == "":
        return None
    return int(v)


def decimal(v: str | None) -> float | None:
    if v is None or v == "":
        return None
    return float(v.replace(",", "."))


def data_hora(d: str | None, h: str | None) -> datetime | None:
    """'05/10/2026' + '02:59:31' (Brasília) -> datetime em UTC."""
    if not d or not h:
        return None
    local = datetime.strptime(f"{d} {h}", "%d/%m/%Y %H:%M:%S").replace(tzinfo=BRASILIA)
    return local.astimezone(timezone.utc)


@dataclass(frozen=True)
class Candidato:
    sq_cand: str
    numero: int
    nome: str            # nome de urna (nmu)
    partido: str         # sigla do partido (par.sg)
    votos: int | None    # vap: votos computados
    pct_tse: float | None  # pvapn: % sobre votos a votáveis concorrentes (como o TSE divulga)
    pct_validos: float | None  # votos / votos válidos * 100, só para destinação "Válido"
    destinacao: str      # dvt
    eleito: bool         # e = 's' (eleito ou foi ao 2º turno)
    situacao: str        # st: só preenchido após totalização final


@dataclass(frozen=True)
class Resultado:
    eleicao: int
    turno: int
    cargo: int
    tpabr: str           # br | uf | mu | zona
    cdabr: str           # br, sigla da UF ou código do município (5 dígitos)
    ts_tse: datetime | None          # geração (dg/hg), UTC
    ts_totalizacao: datetime | None  # dt/ht, UTC (não confiável para ordenar)
    andamento: str       # n | p | f
    totalizacao_final: bool
    divulga: bool        # dv = 'n' -> votos dos candidatos vêm zerados
    secoes_total: int | None
    secoes_totalizadas: int | None
    pct_secoes: float | None         # pstn
    eleitorado: int | None           # te
    # c e a são relativos ao eleitorado das seções instaladas (esi), não a te:
    # esi = c + a. Abstenção % = a / esi.
    eleitorado_instaladas: int | None  # esi
    comparecimento: int | None       # c
    abstencao: int | None            # a
    votos_total: int | None          # tv
    votos_validos: int | None        # vv
    brancos: int | None              # vb
    nulos: int | None                # tvn = nulos + nulos técnicos
    candidatos: tuple[Candidato, ...]


def parse(conteudo: bytes | str | dict) -> Resultado:
    j = conteudo if isinstance(conteudo, dict) else json.loads(conteudo)
    s, e, v = j.get("s", {}), j.get("e", {}), j.get("v", {})
    vv = inteiro(v.get("vv"))

    cargos = j.get("carg") or []
    if len(cargos) != 1:
        raise ValueError(f"esperado 1 cargo no EA20, veio {len(cargos)}")
    carg = cargos[0]

    candidatos = []
    for agr in carg.get("agr", []):
        for par in agr.get("par", []):
            for c in par.get("cand", []):
                votos = inteiro(c.get("vap"))
                dvt = c.get("dvt", "")
                pct_validos = None
                if dvt == "Válido" and votos is not None and vv:
                    pct_validos = votos / vv * 100
                candidatos.append(Candidato(
                    sq_cand=c["sqcand"],
                    numero=int(c["n"]),
                    nome=c.get("nmu") or c.get("nm", ""),
                    partido=par.get("sg", ""),
                    votos=votos,
                    pct_tse=decimal(c.get("pvapn")),
                    pct_validos=pct_validos,
                    destinacao=dvt,
                    eleito=c.get("e") == "s",
                    situacao=c.get("st", ""),
                ))

    return Resultado(
        eleicao=int(j["ele"]),
        turno=int(j["t"]),
        cargo=int(carg["cd"]),
        tpabr=j["tpabr"],
        cdabr=j["cdabr"],
        ts_tse=data_hora(j.get("dg"), j.get("hg")),
        ts_totalizacao=data_hora(j.get("dt"), j.get("ht")),
        andamento=j.get("and", ""),
        totalizacao_final=j.get("tf") == "s",
        divulga=j.get("dv") == "s",
        secoes_total=inteiro(s.get("ts")),
        secoes_totalizadas=inteiro(s.get("st")),
        pct_secoes=decimal(s.get("pstn")),
        eleitorado=inteiro(e.get("te")),
        eleitorado_instaladas=inteiro(e.get("esi")),
        comparecimento=inteiro(e.get("c")),
        abstencao=inteiro(e.get("a")),
        votos_total=inteiro(v.get("tv")),
        votos_validos=vv,
        brancos=inteiro(v.get("vb")),
        nulos=inteiro(v.get("tvn")),
        candidatos=tuple(candidatos),
    )
