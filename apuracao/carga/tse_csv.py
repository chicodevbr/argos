"""Carga dos CSVs históricos do Portal de Dados Abertos do TSE -> Parquet.

Fontes (leiame.pdf dentro de cada zip):
- votacao_candidato_munzona_<ano>.zip: votos por candidato, município e zona.
  Presidente vem do membro _BR; governador dos membros por UF (filtrando o cargo,
  porque os membros de UF também trazem presidente e os demais cargos).
- detalhe_votacao_munzona_<ano>.zip: aptos, comparecimento, abstenção, brancos e
  nulos por cargo, município e zona (membro _BRASIL).

Os zips são lidos em streaming, sem descompactar (o de votação tem 8,6 GB
descompactado em 2022).

Convenções do TSE: latin-1, ';', tudo entre aspas; #NULO = -1 e #NE = -3 nos campos
numéricos (viram NULL aqui). O CSV omite zeros à esquerda (município "5231", zona
"64"); normalizamos para 5 e 4 dígitos, como no JSON de divulgação.

Anos antigos: o CSV de 2014 não tem QT_VOTOS_NOMINAIS_VALIDOS nem
NM_TIPO_DESTINACAO_VOTOS. Nesse caso usamos QT_VOTOS_NOMINAIS como válidos e
deixamos a destinação vazia, e `carregar_ano` CONFERE que a soma por (turno, UF,
cargo) bate com QT_TOTAL_VOTOS_VALIDOS do detalhe; se não bater (havia votos
anulados), a carga falha em vez de gravar números errados. Em 2014 bate em todas as
56 (presidente) e 42 (governador) combinações.
"""

from __future__ import annotations

import csv
import io
from collections import Counter
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

from apuracao.coletor.log import LogJson
from apuracao.modelo.construir import _gravar_parquet

CARGOS = {1: "presidente", 3: "governador"}

HIST_VOTACAO = [
    ("ano", "INTEGER"), ("turno", "INTEGER"), ("cargo", "INTEGER"), ("cd_eleicao", "INTEGER"),
    ("uf", "VARCHAR"), ("cod_mun_tse", "VARCHAR"), ("zona", "VARCHAR"),
    ("numero", "INTEGER"), ("nome", "VARCHAR"),
    ("votos", "BIGINT"), ("votos_validos", "BIGINT"), ("destinacao", "VARCHAR"), ("transito", "BOOLEAN"),
]
HIST_COMPARECIMENTO = [
    ("ano", "INTEGER"), ("turno", "INTEGER"), ("cargo", "INTEGER"), ("cd_eleicao", "INTEGER"),
    ("uf", "VARCHAR"), ("cod_mun_tse", "VARCHAR"), ("zona", "VARCHAR"),
    ("aptos", "BIGINT"), ("comparecimento", "BIGINT"), ("abstencao", "BIGINT"),
    ("votos_validos", "BIGINT"), ("brancos", "BIGINT"), ("nulos", "BIGINT"), ("transito", "BOOLEAN"),
]


def numero(v: str) -> int | None:
    """Inteiro do CSV; #NULO (-1), #NE (-3) e vazio viram None."""
    if v in ("", "#NULO", "#NE"):
        return None
    n = int(v)
    return None if n < 0 else n


# Texto de DS_CARGO de cada cargo, usado só como pré-filtro rápido (superconjunto):
# a decisão final é sempre pelo CD_CARGO já parseado. Não depende de aspas: o
# leiame diz que números vêm entre aspas, mas em 2022 vêm sem (`;3;"Governador";`).
PREFILTRO = {1: "Presidente", 3: "Governador"}


def ler_membro(zip_path: Path, membro: str, cargos: set[int]) -> Iterator[dict[str, str]]:
    """Linhas do membro cujo CD_CARGO está em `cargos`.

    Pré-filtra por texto antes do parser de CSV: dos ~11 milhões de linhas dos
    membros de UF de 2022, só uma fração pequena é de presidente/governador, e montar
    um dict por linha levava 12 min para o ano todo.
    """
    marcas = [PREFILTRO[c] for c in cargos]
    with zipfile.ZipFile(zip_path) as z, z.open(membro) as f:
        texto = io.TextIOWrapper(f, encoding="latin-1", newline="")
        cabecalho = next(csv.reader([next(texto)], delimiter=";"))
        candidatas = (l for l in texto if any(m in l for m in marcas))
        for campos in csv.reader(candidatas, delimiter=";"):
            linha = dict(zip(cabecalho, campos))
            if int(linha["CD_CARGO"]) in cargos:
                yield linha


def _membros(zip_path: Path, padrao: str) -> list[str]:
    with zipfile.ZipFile(zip_path) as z:
        return sorted(n for n in z.namelist() if re.search(padrao, n))


def _base(l: dict[str, str]) -> tuple:
    return (
        int(l["ANO_ELEICAO"]), int(l["NR_TURNO"]), int(l["CD_CARGO"]), int(l["CD_ELEICAO"]),
        l["SG_UF"].lower(), l["CD_MUNICIPIO"].zfill(5), l["NR_ZONA"].zfill(4),
    )


def linhas_votacao(zip_path: Path) -> Iterator[tuple]:
    membros_uf = [m for m in _membros(zip_path, r"_[A-Z]{2}\.csv$") if not m.endswith("_BR.csv")]
    fontes = [(m, {1}) for m in _membros(zip_path, r"_BR\.csv$")] + [(m, {3}) for m in membros_uf]
    for membro, cargos in fontes:
        for l in ler_membro(zip_path, membro, cargos):
            nominais = numero(l["QT_VOTOS_NOMINAIS"])
            validos = numero(l["QT_VOTOS_NOMINAIS_VALIDOS"]) if "QT_VOTOS_NOMINAIS_VALIDOS" in l else nominais
            yield _base(l) + (
                int(l["NR_CANDIDATO"]), l["NM_URNA_CANDIDATO"],
                nominais, validos, l.get("NM_TIPO_DESTINACAO_VOTOS", ""), l["ST_VOTO_EM_TRANSITO"] == "S",
            )


def linhas_comparecimento(zip_path: Path) -> Iterator[tuple]:
    for membro in _membros(zip_path, r"_BRASIL\.csv$"):
        for l in ler_membro(zip_path, membro, set(CARGOS)):
            yield _base(l) + (
                numero(l["QT_APTOS"]), numero(l["QT_COMPARECIMENTO"]), numero(l["QT_ABSTENCOES"]),
                numero(l["QT_TOTAL_VOTOS_VALIDOS"]), numero(l["QT_VOTOS_BRANCOS"]),
                numero(l["QT_TOTAL_VOTOS_NULOS"]), l["ST_VOTO_EM_TRANSITO"] == "S",
            )


def conferir_validos(ano: int, votacao: list[tuple], comparecimento: list[tuple]) -> None:
    """Soma dos votos dos candidatos == votos válidos do detalhe, por (turno, cargo, UF)."""
    iv = {c: i for i, (c, _) in enumerate(HIST_VOTACAO)}
    ic = {c: i for i, (c, _) in enumerate(HIST_COMPARECIMENTO)}
    soma_v, soma_c = Counter(), Counter()
    for l in votacao:
        if not l[iv["transito"]]:
            soma_v[(l[iv["turno"]], l[iv["cargo"]], l[iv["uf"]])] += l[iv["votos_validos"]] or 0
    for l in comparecimento:
        if not l[ic["transito"]]:
            soma_c[(l[ic["turno"]], l[ic["cargo"]], l[ic["uf"]])] += l[ic["votos_validos"]] or 0
    diferentes = {k: (soma_v[k], soma_c[k]) for k in set(soma_v) | set(soma_c) if soma_v[k] != soma_c[k]}
    if diferentes:
        raise ValueError(f"{ano}: CSV sem QT_VOTOS_NOMINAIS_VALIDOS e votos nominais != válidos em "
                         f"{len(diferentes)} (turno, cargo, UF), ex.: {sorted(diferentes.items())[:3]}")


def carregar_ano(ano: int, dir_hist: Path, dir_parquet: Path, log: LogJson) -> dict[str, int]:
    d = Path(dir_hist) / str(ano)
    votacao = list(linhas_votacao(d / f"votacao_candidato_munzona_{ano}.zip"))
    comparecimento = list(linhas_comparecimento(d / f"detalhe_votacao_munzona_{ano}.zip"))
    if not votacao or not comparecimento:
        raise ValueError(f"{ano}: CSV sem linhas de presidente/governador (arquivo ainda vazio no TSE?)")
    iv = {c: i for i, (c, _) in enumerate(HIST_VOTACAO)}
    if all(l[iv["destinacao"]] == "" for l in votacao):  # layout antigo: válidos = nominais
        conferir_validos(ano, votacao, comparecimento)
    _gravar_parquet(Path(dir_parquet) / "hist_votacao" / f"ano={ano}.parquet",
                    "hist_votacao", HIST_VOTACAO, votacao)
    _gravar_parquet(Path(dir_parquet) / "hist_comparecimento" / f"ano={ano}.parquet",
                    "hist_comparecimento", HIST_COMPARECIMENTO, comparecimento)
    n = {"hist_votacao": len(votacao), "hist_comparecimento": len(comparecimento)}
    log.evento("carga_ano", ano=ano, **n)
    return n
