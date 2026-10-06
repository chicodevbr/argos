"""Os lugares das páginas "abstenção por região": Brasília (RAs do DF), cidade do Rio (RAs da
prefeitura) e estado do Rio (municípios). Ver pagina/regioes.py."""

from __future__ import annotations

import json
import re
from pathlib import Path

from apuracao.config import Config
from apuracao.pagina.regioes import Recorte

METODO_SECAO = ("Abstenção = eleitores que não votaram ÷ eleitores aptos das seções, a mesma conta do TSE. "
                "Dados do 1º turno para presidente, seção a seção (Portal de Dados Abertos do TSE, \"detalhe da "
                "votação por seção\"), incluindo os eleitores em trânsito. A soma das seções confere com o total "
                "oficial nos dois anos.")

# --- Brasília ------------------------------------------------------------------------------
# Nomes como publicados na malha do GDF (caixa alta, "AGUA QUENTE" sem acento) -> forma de leitura.
NOMES_DF = {
    "PLANO PILOTO": "Plano Piloto", "GAMA": "Gama", "TAGUATINGA": "Taguatinga", "BRAZLÂNDIA": "Brazlândia",
    "SOBRADINHO": "Sobradinho", "PLANALTINA": "Planaltina", "PARANOÁ": "Paranoá",
    "NÚCLEO BANDEIRANTE": "Núcleo Bandeirante", "CEILÂNDIA": "Ceilândia", "GUARÁ": "Guará",
    "CRUZEIRO": "Cruzeiro", "SAMAMBAIA": "Samambaia", "SANTA MARIA": "Santa Maria",
    "SÃO SEBASTIÃO": "São Sebastião", "RECANTO DAS EMAS": "Recanto das Emas", "LAGO SUL": "Lago Sul",
    "RIACHO FUNDO": "Riacho Fundo", "LAGO NORTE": "Lago Norte", "CANDANGOLÂNDIA": "Candangolândia",
    "ÁGUAS CLARAS": "Águas Claras", "RIACHO FUNDO II": "Riacho Fundo II",
    "SUDOESTE/OCTOGONAL": "Sudoeste/Octogonal", "VARJÃO": "Varjão", "PARK WAY": "Park Way",
    "SCIA": "SCIA (Estrutural)", "SOBRADINHO II": "Sobradinho II", "JARDIM BOTÂNICO": "Jardim Botânico",
    "ITAPOÃ": "Itapoã", "SIA": "SIA", "VICENTE PIRES": "Vicente Pires", "FERCAL": "Fercal",
    "SOL NASCENTE E POR DO SOL": "Sol Nascente/Pôr do Sol", "ARNIQUEIRA": "Arniqueira",
    "ARAPOANGA": "Arapoanga", "AGUA QUENTE": "Água Quente",
}
# RAs que pedem artigo ("no Itapoã"); as demais levam "em" ("em Ceilândia").
NO_DF = {"Plano Piloto", "Gama", "Paranoá", "Núcleo Bandeirante", "Guará", "Cruzeiro", "Recanto das Emas",
         "Lago Sul", "Riacho Fundo", "Lago Norte", "Riacho Fundo II", "Sudoeste/Octogonal", "Varjão",
         "Park Way", "SCIA (Estrutural)", "Jardim Botânico", "Itapoã", "SIA", "Sol Nascente/Pôr do Sol",
         "Arapoanga"}


def brasilia(cfg: Config) -> Recorte:
    return Recorte(
        id="brasilia", titulo="Abstenção em Brasília", rotulo="Distrito Federal", uf="df", cod_mun=None,
        sigla="DF", no_lugar="No Distrito Federal", do_lugar="do DF", sujeito="o DF", nome_total="Distrito Federal",
        unidade="região administrativa", unidades="regiões administrativas", abrev="RAs", todas="todas",
        agrupar="coordenadas", malha=Path(cfg.df.arquivo), campo="ra_nome",
        nomes=NOMES_DF, preposicao={n: "no" for n in NO_DF},
        metodo=[METODO_SECAO,
                "O TSE trata o DF como um único município. Cada seção foi atribuída à região administrativa onde "
                "fica o seu local de votação, pelas coordenadas do local (cadastro de locais de votação do TSE) "
                "dentro dos limites oficiais das 35 RAs (SISDIA/GDF, camada de 2022). É a RA do local de votação, "
                "que em geral coincide com a de moradia, mas não sempre."],
        cortes=[16, 17.5, 18.5, 19.5, 21],  # as da página publicada em 06/10
        titulo_votos="Votos em Brasília",
        metodo_votos=[
            "Percentual de cada candidato a presidente sobre os votos válidos, no 1º turno de 2026 e de 2022, seção a "
            "seção (Portal de Dados Abertos do TSE, \"votação por seção\"). Votos válidos são os dos candidatos que o "
            "resultado oficial lista como válidos; a soma confere com o total oficial do DF, candidato a candidato.",
            "O TSE trata o DF como um único município. Cada seção foi atribuída à região administrativa onde fica o seu "
            "local de votação, pelas coordenadas do local dentro dos limites oficiais das 35 RAs (SISDIA/GDF, 2022). É "
            "a RA do local de votação, que em geral coincide com a de moradia, mas não sempre."],
        subpasta="", saida="abstencao-brasilia-2026.html",
    )


# --- Cidade do Rio -------------------------------------------------------------------------
# RAs que pedem artigo; as demais levam "em" ("em Copacabana", "em Bangu").
PREPOSICAO_RIO = {
    "Centro": "no", "Complexo da Maré": "no", "Complexo do Alemão": "no", "Jacarezinho": "no", "Méier": "no",
    "Rio Comprido": "no", "Tijuca": "na", "Barra da Tijuca": "na", "Rocinha": "na", "Penha": "na",
    "Pavuna": "na", "Ilha do Governador": "na", "Lagoa": "na", "Cidade de Deus": "na", "Portuária": "na",
}


def _bairros_por_ra(cfg: Config) -> dict[str, str]:
    """RA (nome da malha) -> "Bairros: A, B, C", pela camada de bairros do IPP (campo codra)."""
    ras = {f["properties"]["codra"]: f["properties"]["nomera"]
           for f in json.loads(Path(cfg.rio.arquivo).read_text())["features"]}
    por_ra: dict[str, list[str]] = {}
    for f in json.loads(Path(cfg.rio.arquivo_bairros).read_text())["features"]:
        a = f["attributes"]
        if a["codra"] in ras:
            por_ra.setdefault(ras[a["codra"]], []).append(a["nome"].strip())
    return {ra: "Bairros: " + ", ".join(sorted(b)) for ra, b in por_ra.items()}


def rio(cfg: Config) -> Recorte:
    return Recorte(
        id="rio", titulo="Abstenção no Rio de Janeiro", rotulo="Cidade do Rio de Janeiro", uf="rj",
        cod_mun="60011", sigla="Rio", no_lugar="Na cidade do Rio de Janeiro", do_lugar="da cidade", sujeito="a cidade",
        nome_total="Cidade do Rio de Janeiro", unidade="região administrativa",
        unidades="regiões administrativas", abrev="RAs", todas="todas",
        agrupar="coordenadas", malha=Path(cfg.rio.arquivo), campo="nomera",
        preposicao=PREPOSICAO_RIO, extras=_bairros_por_ra(cfg),
        metodo=[METODO_SECAO,
                "O TSE trata a cidade do Rio como um único município. Cada seção foi atribuída à região "
                "administrativa onde fica o seu local de votação, pelas coordenadas do local (cadastro de locais "
                "de votação do TSE) dentro dos limites oficiais das 33 RAs da prefeitura (Instituto Pereira "
                "Passos). Os bairros de cada RA vêm da mesma fonte. É a RA do local de votação, que em geral "
                "coincide com a de moradia, mas não sempre."],
        subpasta="rio-de-janeiro", saida="abstencao-rio-2026.html",
    )


# --- Estado do Rio -------------------------------------------------------------------------
MINUSCULAS = {"de", "da", "do", "das", "dos", "e"}


def nome_proprio(nome: str) -> str:
    """'SÃO JOÃO DE MERITI' -> 'São João de Meriti' (preposições em minúscula)."""
    partes = re.split(r"(\s+|-)", nome.lower())
    saida = []
    for i, p in enumerate(partes):
        saida.append(p if (p in MINUSCULAS and i > 0) or not p.strip() or p == "-" else p[:1].upper() + p[1:])
    return "".join(saida)


def estado_rj(cfg: Config) -> Recorte:
    return Recorte(
        id="estado-rj", titulo="Abstenção no Estado do Rio", rotulo="Estado do Rio de Janeiro", uf="rj",
        cod_mun=None, sigla="RJ", no_lugar="No estado do Rio de Janeiro", do_lugar="do estado", sujeito="o estado",
        nome_total="Estado do Rio de Janeiro", unidade="município", unidades="municípios", abrev="municípios",
        todas="todos", agrupar="municipio", preposicao={"Rio de Janeiro": "no"},
        metodo=[METODO_SECAO,
                "Cada seção foi somada ao seu município. Mapa: malha municipal do IBGE (qualidade mínima)."],
        subpasta="estado-do-rio", saida="abstencao-estado-rj-2026.html",
    )


RECORTES = {"brasilia": brasilia, "rio": rio, "estado-rj": estado_rj}
