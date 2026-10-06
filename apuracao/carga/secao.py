"""Detalhe da votação por seção e locais de votação (Portal de Dados Abertos do TSE).

- detalhe_votacao_secao_{ano}.zip: uma linha por seção e cargo, com aptos, comparecimento,
  abstenção e o local de votação (NR_LOCAL_VOTACAO). Um membro por UF (_DF.csv) só com os
  cargos estaduais, e um membro _BRASIL.csv com todos, inclusive PRESIDENTE. Presidente
  inclui os eleitores em trânsito (aptos só para presidente): com ele, a soma das seções do
  DF no 1º turno de 2026 dá exatamente o total oficial (2.258.320 aptos, 426.924 abstenções).
- eleitorado_local_votacao_{ano}.zip: uma linha por seção, com o local de votação, bairro,
  latitude e longitude. 2026: membro por UF, coordenadas com vírgula decimal; 2022: um CSV
  nacional, coordenadas com ponto.

Leitura em streaming, sem descompactar (o _BRASIL.csv tem ~1 GB): um filtro de texto pela
UF descarta a maioria das linhas antes de interpretar o CSV. latin-1, ';', aspas.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import pandas as pd

PRESIDENTE = "1"


def _membro(z: zipfile.ZipFile, uf: str, preferir_uf: bool) -> str:
    csvs = [n for n in z.namelist() if n.lower().endswith(".csv")]
    da_uf = [n for n in csvs if n.upper().endswith(f"_{uf.upper()}.CSV")]
    brasil = [n for n in csvs if n.upper().endswith("_BRASIL.CSV")]
    if preferir_uf and da_uf:
        return da_uf[0]
    if brasil:
        return brasil[0]
    if len(csvs) == 1:  # arquivo nacional único (locais de 2022)
        return csvs[0]
    raise ValueError(f"{z.filename}: não achei o membro da UF {uf} nem o nacional")


def _linhas(z: zipfile.ZipFile, membro: str, uf: str):
    """Dicionários das linhas da UF (filtro de texto antes do csv: ~1 GB em poucos minutos)."""
    marca = f'"{uf.upper()}"'
    with z.open(membro) as bruto:
        texto = io.TextIOWrapper(bruto, encoding="latin-1", newline="")
        cabecalho = next(csv.reader([texto.readline()], delimiter=";"))
        for linha in texto:
            if marca in linha:
                valores = next(csv.reader([linha], delimiter=";"))
                registro = dict(zip(cabecalho, valores))
                if registro.get("SG_UF") == uf.upper():
                    yield registro


def secoes(caminho: Path, uf: str, turno: int = 1, cargo: str = PRESIDENTE) -> pd.DataFrame:
    """Uma linha por seção: zona, secao, local, aptos, comparecimento, abstencoes."""
    with zipfile.ZipFile(caminho) as z:
        membro = _membro(z, uf, preferir_uf=cargo != PRESIDENTE)
        linhas = [
            (int(r["NR_ZONA"]), int(r["NR_SECAO"]), int(r["NR_LOCAL_VOTACAO"]), int(r["QT_APTOS"]),
             int(r["QT_COMPARECIMENTO"]), int(r["QT_ABSTENCOES"]))
            for r in _linhas(z, membro, uf)
            if r["CD_CARGO"] == cargo and int(r["NR_TURNO"]) == turno
        ]
    df = pd.DataFrame(linhas, columns=["zona", "secao", "local", "aptos", "comparecimento", "abstencoes"])
    if df.duplicated(["zona", "secao"]).any():
        raise ValueError(f"{caminho}: seção repetida para o cargo {cargo}")
    return df


def _coordenada(v: str) -> float | None:
    v = (v or "").strip().replace(",", ".")
    try:
        x = float(v)
    except ValueError:
        return None
    return None if x in (-1.0, 0.0) else x


def locais(caminho: Path, uf: str, turno: int = 1) -> pd.DataFrame:
    """Um por local de votação: zona, local, nome, bairro, lat, lon (None se sem coordenada)."""
    with zipfile.ZipFile(caminho) as z:
        membro = _membro(z, uf, preferir_uf=True)
        vistos, linhas = set(), []
        for r in _linhas(z, membro, uf):
            if int(r["NR_TURNO"]) != turno:
                continue
            chave = (int(r["NR_ZONA"]), int(r["NR_LOCAL_VOTACAO"]))
            if chave in vistos:
                continue
            vistos.add(chave)
            linhas.append((*chave, r["NM_LOCAL_VOTACAO"], r["NM_BAIRRO"],
                           _coordenada(r["NR_LATITUDE"]), _coordenada(r["NR_LONGITUDE"])))
    return pd.DataFrame(linhas, columns=["zona", "local", "nome", "bairro", "lat", "lon"])
