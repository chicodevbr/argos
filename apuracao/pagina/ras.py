"""Página "quem venceu em cada RA": o 2º turno por região administrativa, ao lado da contagem por zona eleitoral
(que é como o resultado costuma ser divulgado e que junta RAs). Por enquanto, Brasília.

uv run python -m apuracao.pagina --recorte brasilia --ras --site site

Dados: os mesmos de pagina/zonas.py. A RA de cada seção é a que contém as coordenadas do seu local de votação.
Para conferir essa atribuição, a página testa mover para a RA vizinha cada local perto de um limite ou cujo
bairro, no cadastro do TSE, cita outra RA, e mostra se algum vencedor mudaria (`checar`).
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

from apuracao.carga import regioes as malhas
from apuracao.config import Config
from apuracao.pagina import votos, zonas
from apuracao.pagina.regioes import Recorte, _br
from apuracao.pagina.urnas import A, B, _nome_local

MODELO = Path(__file__).with_name("modelo_ras.html")
TITULOS = {"brasilia": "Quem venceu em cada RA de Brasília", "rio": "Quem venceu em cada RA do Rio"}
# fonte dos limites das RAs e exemplos de bairro com o nome antigo no cadastro do TSE, por recorte
LIMITES = {"brasilia": "das 35 RAs (SISDIA/GDF, 2022)", "rio": "das 33 RAs da prefeitura (Instituto Pereira Passos)"}
BAIRRO_ANTIGO = {"brasilia": " (Sol Nascente aparece como \"Ceilândia Norte\", Sobradinho II como \"Sobradinho\")"}
PERTO_M = 100        # locais a menos de 100 m do limite da RA entram no teste
ATENCAO_M = 300      # e, nas RAs apertadas, os locais até 300 m do limite aparecem se decidirem a RA
APERTADA = 1_000     # RAs decididas por menos de 1.000 votos


def _norm(s: str) -> str:
    return unicodedata.normalize("NFD", str(s or "")).encode("ascii", "ignore").decode().upper()


def _segmentos(g: dict) -> np.ndarray:
    pol = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
    return np.vstack([np.c_[np.asarray(a)[:-1], np.asarray(a)[1:]] for p in pol for a in p])


def distancias(lon: float, lat: float, segs: dict[str, np.ndarray]) -> dict[str, float]:
    """Distância (m, plano local) do ponto ao contorno de cada RA."""
    kx, ky = 111_320 * np.cos(np.radians(lat)), 110_540
    out = {}
    for nome, s in segs.items():
        x1, y1, x2, y2 = (s[:, 0] - lon) * kx, (s[:, 1] - lat) * ky, (s[:, 2] - lon) * kx, (s[:, 3] - lat) * ky
        dx, dy = x2 - x1, y2 - y1
        t = np.clip(-(x1 * dx + y1 * dy) / np.maximum(dx * dx + dy * dy, 1e-9), 0, 1)
        out[nome] = float(np.min(np.hypot(x1 + t * dx, y1 + t * dy)))
    return out


def checar(r: Recorte, sec: pd.DataFrame, loc: pd.DataFrame, regs: list[dict]) -> dict:
    """Move, um por vez, cada local duvidoso para a RA alternativa e vê se o vencedor de alguma RA muda.
    Duvidoso: a menos de PERTO_M do limite (alternativa = RA mais próxima) ou com bairro do TSE que cita outra RA
    (o nome de RA mais longo contido no bairro: "SOBRADINHO II" é Sobradinho II, não Sobradinho). Trocas que
    deixariam uma RA sem nenhum voto não contam (o local é a RA inteira; o bairro do TSE é o nome antigo)."""
    nome = lambda u: r.nomes.get(u, u)  # noqa: E731
    por_local = sec.groupby(["zona", "local"], as_index=False)[["a", "b"]].sum()
    lc = loc.merge(por_local, on=["zona", "local"])
    segs = {x["nome"]: _segmentos(x["geometria"]) for x in regs}
    ras_norm = {_norm(nome(u)): u for u in segs}
    # RAs sem nenhum local (no Rio, o Jacarezinho) entram zeradas; ganhar um vencedor não conta como virada
    tot = sec.groupby("unidade")[["a", "b"]].sum().reindex(list(segs), fill_value=0)
    base = np.sign(tot["a"] - tot["b"])
    com_votos = (tot["a"] + tot["b"]) > 0
    apertadas = set(tot.index[(tot["a"] - tot["b"]).abs() < APERTADA])
    trocas, viradas, atencao, duvidosos = 0, [], [], set()
    for x in lc.itertuples():
        alts = {}
        d = distancias(x.lon, x.lat, segs)
        if x.unidade in apertadas and PERTO_M <= d[x.unidade] < ATENCAO_M:
            viz = min((v, k) for k, v in d.items() if k != x.unidade)[1]
            t = tot.copy()
            t.loc[x.unidade] -= [x.a, x.b]
            t.loc[viz] += [x.a, x.b]
            if (t.loc[x.unidade] != 0).any() and np.sign(t.loc[x.unidade, "a"] - t.loc[x.unidade, "b"]) != base[x.unidade]:
                atencao.append({"local": _nome_local(x.nome), "bairro": _nome_local(x.bairro), "de": nome(x.unidade),
                                "para": nome(viz), "m": round(d[x.unidade]),
                                "antes": int(tot.loc[x.unidade, "a"] - tot.loc[x.unidade, "b"]),
                                "depois": int(t.loc[x.unidade, "a"] - t.loc[x.unidade, "b"])})
        if d[x.unidade] < PERTO_M:
            viz = min((v, k) for k, v in d.items() if k != x.unidade)[1]
            alts[viz] = f"a {_br(d[x.unidade], 0)} m do limite com {nome(viz)}"
        cit = [k for k in ras_norm if k in _norm(x.bairro)]
        if cit:
            u = ras_norm[max(cit, key=len)]
            if u != x.unidade:
                alts.setdefault(u, f"bairro no TSE: {_nome_local(x.bairro)}")
        for u, motivo in alts.items():
            duvidosos.add((x.zona, x.local))
            t = tot.copy()
            t.loc[x.unidade] -= [x.a, x.b]
            t.loc[u] += [x.a, x.b]
            if (t.loc[x.unidade] == 0).all():
                continue
            trocas += 1
            for ra in t.index[(np.sign(t["a"] - t["b"]) != base) & com_votos]:
                viradas.append({"local": _nome_local(x.nome), "de": nome(x.unidade), "para": nome(u), "motivo": motivo,
                                "ra": nome(ra), "antes": int(tot.loc[ra, "a"] - tot.loc[ra, "b"]),
                                "depois": int(t.loc[ra, "a"] - t.loc[ra, "b"])})
    return {"locais": len(lc), "duvidosos": len(duvidosos), "trocas": trocas, "viradas": viradas, "perto_m": PERTO_M,
            "atencao": sorted(atencao, key=lambda x: x["m"]), "atencao_m": ATENCAO_M}


def montar_dados(r: Recorte, sec: pd.DataFrame, loc: pd.DataFrame, nomes: dict[int, str], checagem: dict) -> dict:
    nome = lambda u: r.nomes.get(u, u)  # noqa: E731
    ras = []
    for u, g in sec.groupby("unidade"):
        s = zonas._soma(g)
        por_zona = g.groupby("zona")["aptos"].sum().sort_values(ascending=False)
        ras.append({"chave": u, "ra": nome(u), **s, "dif": s["a"] - s["b"], "locais": int(g["local"].nunique()),
                    "zonas": [{"z": int(z), "pct": round(float(100 * ap / g["aptos"].sum()), 1)} for z, ap in por_zona.items()]})
    dz = zonas.montar_dados(r, sec, loc, nomes)
    tot = zonas._soma(sec)
    tot.update({"ras": len(ras), "ra_a": sum(x["dif"] > 0 for x in ras), "ra_b": sum(x["dif"] < 0 for x in ras),
                "zonas": dz["total"]["zonas"], "za": dz["total"]["za"], "zb": dz["total"]["zb"],
                "ap_ra_a": sum(x["ap"] for x in ras if x["dif"] > 0)})
    d = {"ras": sorted(ras, key=lambda x: -x["m"]), "zonas": dz["zonas"], "pontos": dz["pontos"], "total": tot,
         "checagem": checagem, "nomes": {"a": nomes[A], "b": nomes[B]},
         "meta": {"titulo": TITULOS.get(r.id, f"Quem venceu em cada {r.unidade}: {r.nome_total}"), "rotulo": r.rotulo,
                  "nome_total": r.nome_total, "sigla": r.sigla, "abrev": r.abrev, "apertada": APERTADA,
                  "metodo": metodo(r)}}
    d["textos"] = _textos(r, d)
    return d


def metodo(r: Recorte) -> list[str]:
    return [
        "Votos de cada seção eleitoral no 2º turno para presidente (Portal de Dados Abertos do TSE, \"votação por "
        f"seção\"), somados pela região administrativa do local de votação. A soma das RAs confere com o resultado "
        f"oficial {r.do_lugar}.",
        f"A RA de cada local é a que contém as coordenadas do local no cadastro do TSE, nos limites oficiais "
        f"{LIMITES.get(r.id, 'da malha do recorte')}; local sem coordenada no 2º turno herda a do 1º turno ou de 2022. "
        "O eleitor vota onde está inscrito, que em geral é perto de onde mora, mas não sempre: o resultado de uma RA "
        "é o das urnas instaladas nela.",
        "Zona eleitoral é a divisão do TRE para organizar o eleitorado e não segue as RAs. O mapa das zonas é "
        "aproximado: o TSE não publica o contorno, e cada área fica com a zona do local de votação mais próximo.",
        f"Conferência: cada local a menos de {PERTO_M} m do limite da sua RA, ou cujo bairro no cadastro do TSE cita "
        "outra RA, foi movido para a RA alternativa, um de cada vez, para ver se algum vencedor mudaria. O bairro do "
        f"TSE muitas vezes usa o nome antigo{BAIRRO_ANTIGO.get(r.id, '')}, por isso não substitui as coordenadas.",
        "Margem = % de Lula − % do 22 nos votos válidos, em pontos; diferença = votos de Lula − votos do 22.",
    ]


def _textos(r: Recorte, d: dict) -> dict:
    t, na, nb = d["total"], d["nomes"]["a"], d["nomes"]["b"]
    lista = lambda xs: xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " e " + xs[-1]  # noqa: E731
    # quem venceu menos RAs ("minoria"): suas RAs listadas e o que acontece com elas na contagem por zona
    a_menos = t["ra_a"] < t["ra_b"]
    maior, menor = (nb, na) if a_menos else (na, nb)
    sinal = 1 if a_menos else -1
    ras_m = [x["ra"] for x in sorted(d["ras"], key=lambda x: -sinal * x["m"]) if sinal * x["dif"] > 0]
    zonas_m = [z["nome"] for z in d["zonas"] if z["m"] is not None and sinal * z["m"] > 0]
    lede = (f"{r.no_lugar}, {maior} venceu em {max(t['ra_a'], t['ra_b'])} das {t['ras']} regiões administrativas, e "
            f"{menor} em {len(ras_m)}" + (f": {lista(ras_m)}" if 0 < len(ras_m) <= 8 else "") + ". ")
    zv = sorted([(t["zb"], nb), (t["za"], na)], reverse=True)   # quem venceu mais zonas
    venc_votos = nb if t["b"] > t["a"] else na
    if zv[0][1] != venc_votos and zv[0][0] != zv[1][0]:
        lede += (f"Por zona eleitoral, como o resultado costuma ser divulgado, o quadro se inverte: {zv[0][1]} vence em "
                 f"{zv[0][0]} das {t['zonas']} zonas, embora {venc_votos} tenha tido mais votos no total. As vitórias "
                 f"de {venc_votos} se concentram em menos zonas, com margens maiores.")
    elif len(zonas_m) / t["zonas"] < len(ras_m) / t["ras"]:
        lede += (f"Por zona eleitoral, como o resultado costuma ser divulgado, {menor} aparece vencendo em só "
                 f"{len(zonas_m)} das {t['zonas']}" + (f" ({lista(zonas_m)})" if 0 < len(zonas_m) <= 4 else "")
                 + ": as zonas juntam RAs, e as vitórias nas menores somem dentro de zonas maiores.")
    else:
        lede += f"Por zona eleitoral, {menor} vence em {len(zonas_m)} das {t['zonas']}."
    apertadas = [x for x in d["ras"] if abs(x["dif"]) < APERTADA]
    c = d["checagem"]
    firm = (f"{len(apertadas)} RAs foram decididas por menos de {_br(APERTADA, 0)} votos"
            + (": " + lista([f"{x['ra']} ({na.split()[0] if x['dif'] > 0 else nb.split()[0]} por {_br(abs(x['dif']), 0)} "
                              f"{'voto' if abs(x['dif']) == 1 else 'votos'})" for x in sorted(apertadas, key=lambda x: abs(x['dif']))])
               if apertadas else "") + ". ")
    if c["viradas"]:
        v = c["viradas"]
        firm += (f"Movendo para a RA vizinha os {c['duvidosos']} locais perto de um limite ou com bairro de outra RA no "
                 f"cadastro do TSE, um de cada vez, {len(v)} {'troca muda' if len(v) == 1 else 'trocas mudam'} um vencedor: "
                 + "; ".join(f"{x['local']} ({x['motivo']}), de {x['de']} para {x['para']}, mudaria {x['ra']}" for x in v) + ".")
    else:
        firm += (f"Movendo para a RA vizinha, um de cada vez, os {c['duvidosos']} locais a menos de {c['perto_m']} m de um "
                 "limite ou com bairro de outra RA no cadastro do TSE, nenhum vencedor muda.")
    if c["atencao"]:
        firm += (f" Mais longe do limite, entre {c['perto_m']} e {c['atencao_m']} m, "
                 + ("um local decidiria uma RA apertada se fosse contado" if len(c["atencao"]) == 1 else
                    f"{len(c['atencao'])} locais decidiriam uma RA apertada se fossem contados")
                 + " na RA vizinha: "
                 + "; ".join(f"{x['local']} (bairro no TSE: {x['bairro']}), a {x['m']} m do limite com {x['para']}"
                             for x in c["atencao"])
                 + ". Pelas coordenadas e pelos limites oficiais, ficam onde estão.")
    return {"lede": lede, "firmeza": firm}


def gerar(r: Recorte, cfg: Config, dir_parquet: Path, saida: Path, ano: int = 2026) -> Path:
    sec, loc = zonas.carregar(r, cfg, ano)
    checagem = checar(r, sec, loc, malhas.ler(r.malha, r.campo))
    dados = montar_dados(r, sec, loc, votos.candidatos_validos(dir_parquet, cfg)[ano], checagem)
    return zonas.escrever(r, dados, MODELO, saida)
