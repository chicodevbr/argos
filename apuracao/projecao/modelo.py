"""Projeção do resultado final de um 2º turno (dois candidatos, A e B) durante a apuração.

Abordagem por município (CLAUDE.md -> Projeção):
- Apurado (100% das seções): resultado real.
- Parcial: extrapola o próprio município pelo percentual de seções.
- Não iniciado: parte da disputa direta do 1º turno no município, p1 = A1/(A1+B1),
  e aplica o deslocamento logit(p2) - logit(p1) estimado para o seu grupo (ex.: UF)
  a partir dos municípios já contados. O total de votos válidos segue log(vv2/vv1),
  estimado do mesmo jeito.
- Total = soma dos votos projetados (cada município pesa pelo seu tamanho).

Estimativa por grupo (hierárquica: Brasil -> niveis[0] -> ... -> niveis[-1]):
cada grupo combina a média dos seus contados com a média do grupo de cima, pesando
pela precisão de cada um. A distância típica de um grupo à média de cima (sd_entre)
é estimada pela dispersão entre os grupos já contados, com piso em `sd_entre_min`
(medido em dados históricos). Assim, um grupo sem nenhum contado (ex.: Nordeste
inteiro por chegar) herda a média de cima com incerteza sd_entre, e não a variação
interna dos grupos que já chegaram.

Incerteza por simulação (n_sim sorteios), somando:
- erro sistemático nacional ~ N(0, tau): os contados podem diferir dos que faltam
  de um jeito que os grupos não captam. Calibrado no backtest;
- erro da média em cada nível da hierarquia (comum a todos os municípios do grupo);
- variação de cada município não iniciado em torno da média do grupo;
- municípios parciais: variação ~ N(0, sd_dentro * fator_parcial * (1 - frac)).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

EPS = 1e-4
SD_DENTRO_MIN = 0.05       # piso da variação entre municípios (logit), evita confiança infinita

# Porte do município pelos votos válidos do 1º turno. No governador de 2022, cidades
# grandes e pequenas da mesma UF se deslocaram de forma diferente (sd entre portes
# 0,111 em logit; em AL, BA, MS, PE e SE em sentidos opostos), o que engana a
# projeção quando a capital chega primeiro. No presidente a diferença foi 0,011.
# Limitação: só são corrigidas diferenças que coincidem com estas faixas.
PORTES = [0, 10_000, 50_000, 200_000, np.inf]
ROTULOS_PORTE = ["<10mil", "10-50mil", "50-200mil", ">200mil"]


def porte(vv1: pd.Series) -> pd.Series:
    return pd.cut(vv1, PORTES, labels=ROTULOS_PORTE, right=False).astype(str)


@dataclass
class Parametros:
    niveis: list[str] = field(default_factory=lambda: ["regiao", "uf"])  # do mais amplo ao mais fino
    tau: float = 0.02                  # sd (logit) do erro sistemático nacional; calibrar no backtest
    # Piso de sd_entre por nível (deslocamento e log(vv2/vv1)). Medido em 2022
    # (presidente, 1º->2º turno): entre regiões 0,012 e entre UFs da região 0,019
    # (deslocamento); 0,011 e 0,011 (razão). Usamos o dobro: 2022 é um caso só.
    sd_entre_min: dict[str, float] = field(default_factory=lambda: {"regiao": 0.03, "uf": 0.04})
    sd_entre_min_razao: dict[str, float] = field(default_factory=lambda: {"regiao": 0.02, "uf": 0.02})
    sd_prior_nacional: float = 0.25    # incerteza do deslocamento nacional sem nada contado
    sd_prior_nacional_razao: float = 0.15
    fator_parcial: float = 0.5
    n_sim: int = 1000
    q_inf: float = 0.05
    q_sup: float = 0.95
    seed: int | None = 0


@dataclass
class Projecao:
    pct_a: float               # mediana da fatia de A nos votos válidos (0-100)
    pct_a_inf: float
    pct_a_sup: float
    prob_a_vence: float
    votos_a: float             # medianas
    votos_b: float
    pct_contado: float         # % dos votos válidos do 1º turno em municípios já com dados
    por_grupo: pd.DataFrame    # projeção por grupo do nível mais fino
    # votos finais simulados por grupo (n_sim x grupos, na ordem de por_grupo); usados
    # pelo caminho da apuração (caminho.py)
    sim_a_grupo: np.ndarray | None = None
    sim_b_grupo: np.ndarray | None = None


@dataclass
class _Nivel:
    codigos: np.ndarray        # grupo de cada município neste nível
    media: np.ndarray          # média posterior por grupo
    se: np.ndarray             # sd da média posterior por grupo (sem a incerteza do nível de cima)
    sd_dentro: np.ndarray      # variação entre municípios do grupo


def _logit(p):
    p = np.clip(p, EPS, 1 - EPS)
    return np.log(p / (1 - p))


def _expit(x):
    return 1 / (1 + np.exp(-x))


def _rotulo(df: pd.DataFrame, cols: list[str]) -> pd.Series:
    """'regiao|uf' por linha, vetorizado (agg("|".join, axis=1) levava ~1s por chamada)."""
    r = df[cols[0]].astype(str)
    for c in cols[1:]:
        r = r + "|" + df[c].astype(str)
    return r


def _ponderado(x: np.ndarray, w: np.ndarray) -> tuple[float, float, float]:
    """(média, sd, n_efetivo) ponderados; n_efetivo = (Σw)²/Σw²."""
    sw = w.sum()
    if sw <= 0:
        return 0.0, 0.0, 0.0
    m = float((x * w).sum() / sw)
    sd = float(np.sqrt(((x - m) ** 2 * w).sum() / sw))
    return m, sd, float(sw ** 2 / (w ** 2).sum())


def _hierarquia(df: pd.DataFrame, contado: np.ndarray, valor: np.ndarray, peso: np.ndarray,
                niveis: list[str], sd_entre_min: dict[str, float], sd_prior: float) -> list[_Nivel]:
    """Estimativa hierárquica de `valor` (definido onde `contado`). Um _Nivel por nível,
    começando pelo nacional."""
    n = len(df)
    m, sd, ne = _ponderado(valor[contado], peso[contado])
    sd_w = max(sd, SD_DENTRO_MIN)
    se = sd_w / np.sqrt(ne) if ne > 0 else sd_prior
    niveis_out = [_Nivel(np.zeros(n, dtype=int), np.array([m]), np.array([se]), np.array([sd_w]))]

    for i, nome in enumerate(niveis):
        rotulo = _rotulo(df, niveis[: i + 1])
        cod, uniq = pd.factorize(rotulo)
        pai = niveis_out[-1]
        pai_de = np.zeros(len(uniq), dtype=int)
        pai_de[cod] = pai.codigos
        stats = []
        for g in range(len(uniq)):
            sel = (cod == g) & contado
            stats.append(_ponderado(valor[sel], peso[sel]))
        gm = np.array([s[0] for s in stats])
        gsd = np.array([s[1] for s in stats])
        gn = np.array([s[2] for s in stats])

        # sd_entre: dispersão das médias dos grupos com dados em torno dos pais
        com_dados = gn >= 3
        if com_dados.sum() >= 3:
            desvio = gm[com_dados] - pai.media[pai_de[com_dados]]
            sd_entre = max(float(np.sqrt(np.average(desvio ** 2, weights=gn[com_dados]))),
                           sd_entre_min.get(nome, 0.1))
        else:
            sd_entre = sd_entre_min.get(nome, 0.1)

        sd_dentro = np.where(com_dados, np.maximum(gsd, SD_DENTRO_MIN), pai.sd_dentro[pai_de])
        prec = 1 / sd_entre ** 2 + gn / sd_dentro ** 2
        media = (pai.media[pai_de] / sd_entre ** 2 + gm * gn / sd_dentro ** 2) / prec
        niveis_out.append(_Nivel(cod, media, 1 / np.sqrt(prec), sd_dentro))
    return niveis_out


def projetar(base: pd.DataFrame, atual: pd.DataFrame, params: Parametros | None = None) -> Projecao:
    """
    base: um município por linha, colunas `chave`, niveis..., a1, b1, vv1 (1º turno).
    atual: colunas `chave`, frac (0-1, seções totalizadas), a2, b2 (votos observados no 2º turno).
    `chave` identifica o município (ex.: "sp71072").
    """
    p = params or Parametros()
    rng = np.random.default_rng(p.seed)
    df = base.merge(atual[["chave", "frac", "a2", "b2"]], on="chave", how="left")
    if "porte" in p.niveis and "porte" not in df:
        df["porte"] = porte(df["vv1"])
    df[["frac", "a2", "b2"]] = df[["frac", "a2", "b2"]].fillna(0.0)
    frac = df["frac"].clip(0, 1).to_numpy()
    a2, b2 = df["a2"].to_numpy(float), df["b2"].to_numpy(float)
    a1, b1, vv1 = df["a1"].to_numpy(float), df["b1"].to_numpy(float), df["vv1"].to_numpy(float)
    contado = ((a2 + b2) > 0) & (frac > 0)
    parcial = contado & (frac < 0.9999)
    nao_ini = ~contado

    # Extrapolação do próprio município (apurado: frac = 1, nada muda)
    div = np.where(contado, frac, 1.0)
    a_proj, b_proj = np.where(contado, a2 / div, 0.0), np.where(contado, b2 / div, 0.0)

    l1 = _logit(np.divide(a1, a1 + b1, out=np.full_like(a1, 0.5), where=(a1 + b1) > 0))
    util = contado & (a1 + b1 > 0) & (vv1 > 0)
    swing = np.zeros(len(df))
    razao = np.zeros(len(df))
    swing[util] = _logit(a_proj[util] / (a_proj[util] + b_proj[util])) - l1[util]
    razao[util] = np.log((a_proj[util] + b_proj[util]) / vv1[util])
    peso = a2 + b2

    hs = _hierarquia(df, util, swing, peso, p.niveis, p.sd_entre_min, p.sd_prior_nacional)
    hr = _hierarquia(df, util, razao, peso, p.niveis, p.sd_entre_min_razao, p.sd_prior_nacional_razao)

    n = p.n_sim
    nacional = rng.normal(0, p.tau, size=(n, 1))
    a = np.tile(a_proj, (n, 1))
    b = np.tile(b_proj, (n, 1))

    def sorteio(h: list[_Nivel], idx: np.ndarray) -> np.ndarray:
        """Média do nível mais fino + erro de cada nível + variação do município."""
        fino = h[-1]
        x = np.tile(fino.media[fino.codigos[idx]], (n, 1))
        for nv in h:
            efeito = rng.normal(0, 1, size=(n, len(nv.se))) * nv.se
            x += efeito[:, nv.codigos[idx]]
        return x + rng.normal(0, 1, size=(n, len(idx))) * fino.sd_dentro[fino.codigos[idx]]

    if nao_ini.any():
        idx = np.flatnonzero(nao_ini)
        p2 = _expit(l1[idx] + sorteio(hs, idx) + nacional)
        vv2 = vv1[idx] * np.exp(sorteio(hr, idx))
        a[:, idx], b[:, idx] = vv2 * p2, vv2 * (1 - p2)

    if parcial.any():  # o que falta apurar pode diferir do que já veio
        idx = np.flatnonzero(parcial)
        tot = a_proj[idx] + b_proj[idx]
        sd = hs[-1].sd_dentro[hs[-1].codigos[idx]] * p.fator_parcial * (1 - frac[idx])
        p2 = _expit(_logit(a_proj[idx] / tot) + nacional + rng.normal(0, 1, size=(n, len(idx))) * sd)
        a[:, idx], b[:, idx] = tot * p2, tot * (1 - p2)

    va, vb = a.sum(axis=1), b.sum(axis=1)
    pct = 100 * va / (va + vb)

    grupos = _rotulo(df, p.niveis) if p.niveis else pd.Series("BR", index=df.index)
    cod, uniq = pd.factorize(grupos)
    por_grupo = []
    sim_a_grupo = np.zeros((n, len(uniq)))
    sim_b_grupo = np.zeros((n, len(uniq)))
    for k, nome in enumerate(uniq):
        m = cod == k
        ga, gb = a[:, m].sum(axis=1), b[:, m].sum(axis=1)
        sim_a_grupo[:, k], sim_b_grupo[:, k] = ga, gb
        gp = 100 * ga / np.where(ga + gb > 0, ga + gb, 1)
        por_grupo.append({
            "grupo": nome, "pct_a": float(np.median(gp)),
            "pct_a_inf": float(np.quantile(gp, p.q_inf)), "pct_a_sup": float(np.quantile(gp, p.q_sup)),
            "pct_contado": float(100 * vv1[m & contado].sum() / max(vv1[m].sum(), 1)),
        })

    return Projecao(
        pct_a=float(np.median(pct)),
        pct_a_inf=float(np.quantile(pct, p.q_inf)),
        pct_a_sup=float(np.quantile(pct, p.q_sup)),
        prob_a_vence=float((va > vb).mean()),
        votos_a=float(np.median(va)),
        votos_b=float(np.median(vb)),
        pct_contado=float(100 * vv1[contado].sum() / max(vv1.sum(), 1)),
        por_grupo=pd.DataFrame(por_grupo),
        sim_a_grupo=sim_a_grupo,
        sim_b_grupo=sim_b_grupo,
    )


# Parâmetros por cargo. Presidente: validados no backtest de 2022 (erro máx. 0,51 pp,
# cobertura 100%). Governador: medidos no 2º turno de 2022 (12 UFs): sd do deslocamento
# entre UFs 0,39 (sem nada contado) e entre portes dentro da UF 0,111; usamos um pouco acima.
# Os argumentos passados substituem os padrões do cargo.
def parametros_presidente(**kw) -> Parametros:
    return Parametros(**{"niveis": ["regiao", "uf"], **kw})


def parametros_governador(**kw) -> Parametros:
    return Parametros(**{
        "niveis": ["porte"],
        "sd_entre_min": {"porte": 0.15},
        "sd_entre_min_razao": {"porte": 0.05},
        "sd_prior_nacional": 0.5,
        **kw,
    })
