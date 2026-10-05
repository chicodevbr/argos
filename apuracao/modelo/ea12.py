"""Parser do EA12 (configuração de municípios, `mun-e<eleicao>-cm.json`).

Referência: docs/tse/tse-ea12-arquivo-de-configuracao-de-municipios.pdf.
Fonte da tabela `municipios` (de-para código TSE x código IBGE).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

# Região por UF (divisão do IBGE). zz = exterior.
REGIAO = {
    **dict.fromkeys(["ac", "am", "ap", "pa", "ro", "rr", "to"], "Norte"),
    **dict.fromkeys(["al", "ba", "ce", "ma", "pb", "pe", "pi", "rn", "se"], "Nordeste"),
    **dict.fromkeys(["df", "go", "ms", "mt"], "Centro-Oeste"),
    **dict.fromkeys(["es", "mg", "rj", "sp"], "Sudeste"),
    **dict.fromkeys(["pr", "rs", "sc"], "Sul"),
    "zz": "Exterior",
}


@dataclass(frozen=True)
class Municipio:
    cod_tse: str         # 5 dígitos com zeros à esquerda
    # A spec diz 5 dígitos, mas o arquivo real traz o código IBGE completo de 7
    # (ex.: 3500105, Adamantina). Vazio para localidades do exterior.
    cod_ibge: str | None
    nome: str
    uf: str
    regiao: str
    capital: bool
    zonas: tuple[str, ...]


def parse(conteudo: bytes | str | dict) -> list[Municipio]:
    j = conteudo if isinstance(conteudo, dict) else json.loads(conteudo)
    saida = []
    for abr in j["abr"]:
        uf = abr["cd"].lower()
        for mu in abr.get("mu", []):
            saida.append(Municipio(
                cod_tse=mu["cd"].zfill(5),
                cod_ibge=mu.get("cdi") or None,
                nome=mu["nm"],
                uf=uf,
                regiao=REGIAO[uf],
                capital=mu.get("c") == "s",
                zonas=tuple(mu.get("z", [])),
            ))
    return saida
