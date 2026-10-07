"""Página "votos por região": percentual de cada candidato a presidente por unidade (RA do DF etc.),
no 1º turno de 2026 comparado ao de 2022.

uv run python -m apuracao.pagina --recorte brasilia --votos

Votos por seção e candidato: votacao_secao_{ano}_BR.zip (carga/secao.py, `votos`); a unidade de cada
seção vem de regioes.secoes_por_unidade (local de votação dentro da malha). Votos válidos = votos nos
candidatos que o resultado oficial lista como válidos (2026: arquivo do TSE do Brasil; 2022: Portal de
Dados Abertos). Em 2026, o nº 28 recebeu votos nas seções mas não está entre os válidos oficiais (o
total de válidos do DF, 1.772.808, é a soma dos outros 12); esses votos ficam fora, como no TSE.

Cores: 13 vermelho, 22 azul (polos do divergente validado). Comparação por NÚMERO: 13 é Lula nos dois anos; 22 é Flávio Bolsonaro em 2026 e Jair Bolsonaro em
2022 (ambos pelo PL). Os demais candidatos são outros nos dois anos.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from apuracao.carga import secao
from apuracao.config import Config
from apuracao.modelo import consultas
from apuracao.pagina import regioes
from apuracao.pagina.recortes import nome_proprio
from apuracao.pagina.regioes import Recorte, _br, em

MODELO = Path(__file__).with_name("modelo_votos.html")
ANOS = (2026, 2022)
A, B = 13, 22  # comparação por número
# nomes de urna publicados sem acento ou com sinal trocado
ACENTOS = {"Flavio Bolsonaro": "Flávio Bolsonaro", "Felipe D Avila": "Felipe d'Avila", "Clariana Barao": "Clariana Barão"}


def nome_urna(n: str) -> str:
    x = nome_proprio(n)
    return ACENTOS.get(x, x)


def candidatos_validos(dir_parquet: Path, cfg: Config) -> dict[int, dict[int, str]]:
    """Por ano: número -> nome de urna dos candidatos a presidente com votos válidos (resultado oficial)."""
    con = consultas.conectar(dir_parquet)
    t1 = next(e.codigo for e in cfg.eleicao if e.turno == 1 and "presidente" in e.cargos and e.ciclo.endswith("2026"))
    c26 = con.execute(
        """SELECT c.numero, c.nome FROM snapshot_candidatos c JOIN (
             SELECT arquivo_raw FROM snapshot_totais WHERE eleicao = ? AND abrangencia = 'br'
             ORDER BY ts_tse DESC LIMIT 1) USING (arquivo_raw) WHERE c.destinacao = 'Válido'""", [t1]).fetchall()
    c22 = con.execute(
        """SELECT numero, any_value(nome) FROM hist_votacao WHERE ano = 2022 AND turno = 1 AND cargo = 1
           AND destinacao = 'Válido' GROUP BY numero""").fetchall()
    # 2014: o CSV não tem a destinação do voto; a carga conferiu que os votos nominais somam o total de
    # válidos de cada UF (carga/tse_csv.py), então todos os candidatos com votos nominais são válidos.
    c14 = con.execute(
        """SELECT numero, any_value(nome) FROM hist_votacao WHERE ano = 2014 AND turno = 1 AND cargo = 1
           GROUP BY numero HAVING sum(votos_validos) > 0""").fetchall()
    return {2026: {int(n): nome_urna(s) for n, s in c26}, 2022: {int(n): nome_urna(s) for n, s in c22},
            2014: {int(n): nome_urna(s) for n, s in c14}}


def montar_dados(r: Recorte, secoes_por_ano: dict[int, pd.DataFrame], votos_por_ano: dict[int, pd.DataFrame],
                 candidatos: dict[int, dict[int, str]]) -> dict:
    anos, por_unidade = {}, {}
    for a in ANOS:
        v = votos_por_ano[a]
        validos = v[v["numero"].isin(candidatos[a].keys())]
        fora = v[~v["numero"].isin(candidatos[a].keys()) & ~v["numero"].isin([95, 96])]
        un = secoes_por_ano[a][["cod_mun", "zona", "secao", "unidade"]]
        m = validos.merge(un, on=["cod_mun", "zona", "secao"], how="left")
        if m["unidade"].isna().any():
            raise ValueError(f"{a}: {int(m['unidade'].isna().sum())} linhas de voto sem unidade")
        tot = validos.groupby("numero")["votos"].sum().sort_values(ascending=False)
        anos[str(a)] = {
            "candidatos": [{"numero": int(n), "nome": candidatos[a][n], "votos": int(x),
                            "pct": round(100 * x / tot.sum(), 3)} for n, x in tot.items()],
            "validos": int(tot.sum()),
            "brancos": int(v.loc[v["numero"] == 95, "votos"].sum()),
            "nulos": int(v.loc[v["numero"] == 96, "votos"].sum()),
            "fora_dos_validos": [{"numero": int(n), "nome": nome_urna(g["nome"].iloc[0]), "votos": int(g["votos"].sum())}
                                 for n, g in fora.groupby("numero")],
        }
        p = m.pivot_table(index="unidade", columns="numero", values="votos", aggfunc="sum", fill_value=0)
        por_unidade[a] = p
    ordem = {a: [c["numero"] for c in anos[str(a)]["candidatos"]] for a in ANOS}
    linhas = []
    for k in por_unidade[2026].index:
        nome = r.nomes.get(k, str(k))
        linha = {"ra": nome, "chave": k, "extra": r.extras.get(nome, "")}
        for a in ANOS:
            if k not in por_unidade[a].index:
                linha[str(a)] = None
                continue
            row = por_unidade[a].loc[k]
            val = int(row.sum())
            pct = {int(n): round(100 * row.get(n, 0) / val, 3) for n in ordem[a]}
            linha[str(a)] = {"validos": val, "pct": pct, "margem": round(pct[A] - pct[B], 3),
                             "demais": round(100 - pct[A] - pct[B], 3)}
        linhas.append(linha)
    linhas.sort(key=lambda x: x["2026"]["margem"])  # da maior vantagem do 22 à maior do 13
    d = {"anos": anos, "ras": linhas, "A": A, "B": B,
         "meta": {"pequenas": [u["ra"] for u in sorted(linhas, key=lambda u: u["2026"]["validos"])
                               if u["2026"]["validos"] < 10_000],
                  "titulo": r.titulo_votos, "rotulo": r.rotulo, "sigla": r.sigla, "unidade": r.unidade,
                  "unidades": r.unidades, "abrev": r.abrev, "nome_total": r.nome_total,
                  "metodo": r.metodo_votos, "sem_secoes": []}}
    d["textos"] = _textos(r, d)
    return d


def _textos(r: Recorte, d: dict) -> dict:
    c26, c22 = d["anos"]["2026"]["candidatos"], d["anos"]["2022"]["candidatos"]
    pc = lambda cs, n: next(c for c in cs if c["numero"] == n)  # noqa: E731
    a26, b26, a22, b22 = pc(c26, A), pc(c26, B), pc(c22, A), pc(c22, B)
    us = d["ras"]
    mais_b, mais_a = us[0], us[-1]
    com_22 = [u for u in us if u["2022"]]
    cresceu_a = sum(1 for u in com_22 if u["2026"]["pct"][A] > u["2022"]["pct"][A])
    demais26 = 100 - a26["pct"] - b26["pct"]
    demais22 = 100 - a22["pct"] - b22["pct"]
    terceiros22 = [c["nome"] for c in c22 if c["numero"] not in (A, B)][:2]
    art = "as" if r.todas == "todas" else "os"
    de = "das" if r.todas == "todas" else "dos"
    ganhou_a = [u["ra"] for u in us if u["2026"]["margem"] > 0]
    ganhou_b = [u["ra"] for u in us if u["2026"]["margem"] < 0]

    def vit(nome: str, ganhou: list[str]) -> str:
        if not ganhou:
            return f"{nome} não venceu em nenhuma"
        if len(ganhou) == 1:
            return f"{nome} venceu só {em(r, ganhou[0])}"
        return f"{nome} venceu em {len(ganhou)} {de} {len(us)} {r.abrev}"
    vitorias = f"{vit(b26['nome'], ganhou_b)}; {vit(a26['nome'], ganhou_a)}"
    return {
        "lede": (f"{r.no_lugar}, {b26['nome']} teve {_br(b26['pct'])}% dos votos válidos no 1º turno de 2026, e "
                 f"{a26['nome']}, {_br(a26['pct'])}%. Em 2022, {b22['nome']} teve {_br(b22['pct'])}% e "
                 f"{a22['nome']}, {_br(a22['pct'])}%. {vitorias[0].upper() + vitorias[1:]}."),
        "titulo_mapa": (f"Maior vantagem de {b26['nome']}: {mais_b['ra']} ({_br(-mais_b['2026']['margem'], 1)} pontos); "
                        + (f"maior vantagem de {a26['nome']}: {mais_a['ra']} ({_br(mais_a['2026']['margem'], 1)} pontos)"
                           if mais_a["2026"]["margem"] > 0 else
                           f"menor: {mais_a['ra']} ({_br(-mais_a['2026']['margem'], 1)} pontos)")),
        "nota_ras": (f"{a26['nome']} teve percentual maior que em 2022 em {cresceu_a} "
                     f"{'das' if r.todas == 'todas' else 'dos'} {len(com_22)} {r.abrev}."),
        "demais": (f"Os outros {len(c26) - 2} candidatos somaram {_br(demais26)}% em 2026, contra {_br(demais22)}% "
                   f"dos outros {len(c22) - 2} em 2022 ({' e '.join(terceiros22)} à frente)."),
        "comparacao": (f"Comparação por número: {A} é {a26['nome']} nos dois anos; {B} é {b26['nome']} em 2026 e "
                       f"{b22['nome']} em 2022."),
        "art": art,
    }


def gerar(r: Recorte, cfg: Config, dir_parquet: Path, saida: Path) -> Path:
    dir_h = Path(cfg.historico.dir)
    secoes_por_ano = regioes.secoes_por_unidade(r, dir_h)
    votos_por_ano = {a: secao.votos(dir_h / str(a) / f"votacao_secao_{a}_BR.zip", r.uf) for a in ANOS}
    if r.cod_mun:
        votos_por_ano = {a: v[v["cod_mun"] == r.cod_mun] for a, v in votos_por_ano.items()}
    dados = montar_dados(r, secoes_por_ano, votos_por_ano, candidatos_validos(dir_parquet, cfg))
    malha = regioes.malha_pagina(r, cfg.ibge.arquivo if cfg.ibge else None)
    com = {u["chave"] for u in dados["ras"]}
    dados["meta"]["sem_secoes"] = sorted(r.nomes.get(f["properties"]["chave"], f["properties"]["chave"])
                                         for f in malha["features"] if f["properties"]["chave"] not in com)
    html = MODELO.read_text().replace("__TITULO__", r.titulo_votos)
    html = html.replace("const D = __DADOS__;", "const D = " + json.dumps(dados, ensure_ascii=False, separators=(",", ":")) + ";")
    html = html.replace("const MALHA = __MALHA__;", "const MALHA = " + json.dumps(malha, separators=(",", ":")) + ";")
    if "__DADOS__" in html or "__MALHA__" in html or "__TITULO__" in html:
        raise RuntimeError("modelo sem os marcadores esperados")
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    saida.write_text(html)
    return saida
