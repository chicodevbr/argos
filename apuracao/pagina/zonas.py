"""Página "votos por zona eleitoral": o 2º turno somado por zona e o que cada zona junta de região. Por enquanto,
Brasília. A zona é a divisão do TRE para organizar o eleitorado e não segue as RAs: uma zona pode juntar várias
RAs (a 2ª tem Paranoá, Lago Norte, Itapoã e Varjão) e uma RA pode estar em várias zonas (Taguatinga em três).

uv run python -m apuracao.pagina --recorte brasilia --zonas --site site

Dados: os mesmos de pagina/urnas.py (votação e detalhe por seção, locais com coordenadas, 2º turno). A RA de
cada seção é a do seu local de votação (malha do recorte). O mapa não tem limite oficial de zona: cada ponto do
DF fica com a zona do local de votação mais próximo (Voronoi, desenhado na página).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from apuracao.carga import regioes as malhas, secao
from apuracao.config import Config
from apuracao.pagina import regioes, urnas, votos
from apuracao.pagina.regioes import Recorte, _br
from apuracao.pagina.urnas import A, B

MODELO = Path(__file__).with_name("modelo_zonas.html")
TITULOS = {"brasilia": "Os votos por zona eleitoral em Brasília", "rio": "Os votos por zona eleitoral no Rio"}
LIMITES = {"brasilia": "das 35 RAs, SISDIA/GDF, 2022", "rio": "das 33 RAs da prefeitura, Instituto Pereira Passos"}
PARTE = 0.03   # parte de uma RA dentro da zona: a partir de 3% dos eleitores da zona entra nas frases


def ordinal(z: int) -> str:
    return f"{z}ª"


def _soma(g: pd.DataFrame) -> dict:
    a, b, ap, c = int(g["a"].sum()), int(g["b"].sum()), int(g["aptos"].sum()), int(g["comparecimento"].sum())
    return {"ap": ap, "c": c, "a": a, "b": b, "bn": int((g["brancos"] + g["nulos"]).sum()), "se": len(g),
            "sa": int((g["a"] > g["b"]).sum()), "sb": int((g["b"] > g["a"]).sum()),
            "m": round(100 * (a - b) / (a + b), 2) if a + b else None,
            "abst": round(100 * (ap - c) / ap, 2) if ap else None}


def montar_dados(r: Recorte, sec: pd.DataFrame, loc: pd.DataFrame, nomes: dict[int, str]) -> dict:
    """`sec`: uma linha por seção (pagina/urnas.por_secao) com `unidade`; `loc`: locais com lat/lon."""
    nome = lambda u: r.nomes.get(u, u) if isinstance(u, str) else "sem região"  # noqa: E731
    ra_total = {nome(u): _soma(g)["m"] for u, g in sec.groupby("unidade")}
    zonas = []
    for z, g in sec.groupby("zona"):
        partes = []
        for u, gu in g.groupby("unidade", dropna=False):
            p = _soma(gu)
            partes.append({"ra": nome(u), "ap": p["ap"], "a": p["a"], "b": p["b"], "m": p["m"],
                           "pct": round(100 * p["ap"] / g["aptos"].sum(), 1), "m_ra": ra_total.get(nome(u))})
        partes.sort(key=lambda p: -p["ap"])
        zonas.append({"z": int(z), "nome": ordinal(int(z)), **_soma(g), "locais": int(g["local"].nunique()),
                      "ras": partes})
    # mapa: um ponto por coordenada, com a zona que tem mais eleitores ali (um prédio pode ter duas zonas)
    pl = sec.merge(loc[["cod_mun", "zona", "local", "lat", "lon"]], on=["cod_mun", "zona", "local"], how="left")
    if pl["lat"].isna().any():
        raise ValueError(f"{int(pl['lat'].isna().sum())} seções sem coordenada do local de votação")
    pz = pl.groupby(["lat", "lon", "zona"], as_index=False)["aptos"].sum()
    pz = pz.sort_values("aptos", ascending=False).drop_duplicates(["lat", "lon"])
    pontos = [[round(x.lon, 5), round(x.lat, 5), int(x.zona), int(x.aptos)] for x in pz.itertuples()]
    tot = _soma(sec)
    tot.update({"zonas": len(zonas), "za": sum(z["m"] is not None and z["m"] > 0 for z in zonas),
                "zb": sum(z["m"] is not None and z["m"] < 0 for z in zonas),
                "mistas": sum(len([p for p in z["ras"] if p["pct"] >= 100 * PARTE]) > 1 for z in zonas)})
    d = {"zonas": zonas, "pontos": pontos, "total": tot, "nomes": {"a": nomes[A], "b": nomes[B]},
         "meta": {"titulo": TITULOS.get(r.id, f"Os votos por zona eleitoral: {r.nome_total}"), "rotulo": r.rotulo,
                  "nome_total": r.nome_total, "abrev": r.abrev, "metodo": metodo(r)}}
    d["textos"] = _textos(r, d)
    return d


def metodo(r: Recorte) -> list[str]:
    return [
        "Votos de cada seção eleitoral no 2º turno para presidente (Portal de Dados Abertos do TSE, \"votação por "
        "seção\"), somados pela zona eleitoral da seção. Eleitores e comparecimento vêm do \"detalhe da votação por "
        "seção\". A soma das zonas confere com o resultado oficial.",
        "A zona eleitoral é a divisão do TRE para organizar o eleitorado e os cartórios; não segue os limites das "
        "regiões administrativas. A parte de cada RA numa zona é a das seções cujo local de votação fica dentro da "
        f"RA (limites oficiais {LIMITES.get(r.id, 'da malha do recorte')}).",
        "O TSE não publica o contorno das zonas. No mapa, cada ponto do DF fica com a zona do local de votação mais "
        "próximo (diagrama de Voronoi); as áreas sem eleitor, como parques e áreas rurais, também entram. Os "
        "contornos são aproximados; as linhas finas são os limites das RAs.",
        "Margem = % de Lula − % do 22 nos votos válidos, em pontos.",
    ]


def _textos(r: Recorte, d: dict) -> dict:
    t, na, nb = d["total"], d["nomes"]["a"], d["nomes"]["b"]
    lista = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " e " + xs[-1]  # noqa: E731
    # quem venceu mais zonas primeiro; as zonas de quem venceu menos listadas, se forem poucas
    a_mais = t["za"] > t["zb"] or (t["za"] == t["zb"] and t["a"] > t["b"])
    venc, perd, zv, zp = (na, nb, t["za"], t["zb"]) if a_mais else (nb, na, t["zb"], t["za"])
    perd_z = [z["nome"] for z in d["zonas"] if z["m"] is not None and z["m"] != 0 and (z["m"] > 0) != a_mais]
    venc_votos = nb if t["b"] > t["a"] else na
    lede = (f"{r.no_lugar}, {venc} venceu em {zv} das {t['zonas']} zonas eleitorais"
            + (f" e {perd} em {zp}" + (f" ({lista(perd_z)})" if len(perd_z) <= 6 else "") if zp else f"; {perd} não venceu em nenhuma")
            + (f", embora {venc_votos} tenha tido mais votos no total" if venc != venc_votos and zv != zp else "")
            + f". {t['mistas']} das {t['zonas']} zonas {'junta' if t['mistas'] == 1 else 'juntam'} mais de uma região administrativa.")
    # partes de RA em que o vencedor foi outro que o da zona
    contra = []
    for z in d["zonas"]:
        if z["m"] is None:
            continue
        outras = [p for p in z["ras"] if p["m"] is not None and p["pct"] >= 100 * PARTE and (p["m"] > 0) != (z["m"] > 0)]
        if outras:
            vz, vo = (na, nb) if z["m"] > 0 else (nb, na)
            contra.append(f"na {z['nome']} zona, {vz} venceu por {_br(abs(z['m']) + 1e-9, 1)} {'ponto' if abs(z['m']) < 1.95 else 'pontos'}, mas {vo} ganhou na parte "
                          f"{lista([_de(r, p['ra']) for p in outras])}")
    escondido = ("As zonas escondem vitórias: " + "; ".join(contra) + ".") if contra else \
        "Em todas as zonas, o vencedor da zona também venceu em cada RA que ela junta."
    return {"lede": lede, "escondido": escondido[0].upper() + escondido[1:]}


def _de(r: Recorte, ra: str) -> str:
    p = r.preposicao.get(ra, "em")
    return {"no": "do", "na": "da", "nos": "dos", "nas": "das"}.get(p, "de") + " " + ra


def carregar(r: Recorte, cfg: Config, ano: int = 2026) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Seções do 2º turno (pagina/urnas.por_secao) com a RA (`unidade`) do local, e os locais com lat/lon e RA."""
    dh = Path(cfg.historico.dir) / str(ano)
    s = secao.secoes(dh / f"detalhe_votacao_secao_{ano}.zip", r.uf, turno=2)
    v = secao.votos(dh / f"votacao_secao_{ano}_BR.zip", r.uf, turno=2)
    loc = secao.locais(dh / f"eleitorado_local_votacao_{ano}.zip", r.uf, turno=2)
    if r.cod_mun:
        s, v, loc = (x[x["cod_mun"] == r.cod_mun] for x in (s, v, loc))
    if s.empty or v.empty:
        raise SystemExit(f"sem o 2º turno de {ano} nos arquivos por seção em {dh}")
    # local sem coordenada no cadastro do 2º turno: herda a do mesmo local no 1º turno ou na eleição anterior
    for turno, a in ((1, ano), (2, ano - 4)):
        if not loc["lat"].isna().any():
            break
        arq = Path(cfg.historico.dir) / str(a) / f"eleitorado_local_votacao_{a}.zip"
        if arq.exists():
            outro = secao.locais(arq, r.uf, turno=turno)
            loc = regioes._completar_coordenadas(loc, outro).drop(columns=["herdou"])
    if loc["lat"].isna().any():
        # local novo no 2º turno: usa o local onde as mesmas seções votaram no 1º turno
        s1 = secao.secoes(dh / f"detalhe_votacao_secao_{ano}.zip", r.uf, turno=1)
        l1 = secao.locais(dh / f"eleitorado_local_votacao_{ano}.zip", r.uf, turno=1).dropna(subset=["lat", "lon"])
        par = s.merge(s1[["cod_mun", "zona", "secao", "local"]], on=["cod_mun", "zona", "secao"], suffixes=("", "_1"))
        par = par.merge(l1[["cod_mun", "zona", "local", "lat", "lon"]].rename(columns={"local": "local_1"}),
                        on=["cod_mun", "zona", "local_1"])
        ref = par.groupby(["cod_mun", "zona", "local"])[["lat", "lon"]].agg(lambda c: c.mode().iloc[0]).reset_index()
        m = loc.merge(ref, on=["cod_mun", "zona", "local"], how="left", suffixes=("", "_1"))
        falta = m["lat"].isna()
        m.loc[falta, "lat"], m.loc[falta, "lon"] = m.loc[falta, "lat_1"], m.loc[falta, "lon_1"]
        loc = m.drop(columns=["lat_1", "lon_1"])
    loc = loc.assign(unidade=malhas.regiao_de(loc["lon"], loc["lat"], malhas.ler(r.malha, r.campo)))
    sec = urnas.por_secao(s, v).merge(loc[["cod_mun", "zona", "local", "unidade"]], on=["cod_mun", "zona", "local"],
                                      how="left")
    return sec, loc


def gerar(r: Recorte, cfg: Config, dir_parquet: Path, saida: Path, ano: int = 2026) -> Path:
    sec, loc = carregar(r, cfg, ano)
    dados = montar_dados(r, sec, loc, votos.candidatos_validos(dir_parquet, cfg)[ano])
    return escrever(r, dados, MODELO, saida)


def escrever(r: Recorte, dados: dict, modelo: Path, saida: Path) -> Path:
    """Grava a página: modelo com os dados e a malha das RAs (com o nome de cada uma)."""
    malha = regioes.malha_pagina(r)
    for f in malha["features"]:
        f["properties"]["nome"] = r.nomes.get(f["properties"]["chave"], f["properties"]["chave"])
    html = Path(modelo).read_text().replace("__TITULO__", dados["meta"]["titulo"])
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(malha, ensure_ascii=False, separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
