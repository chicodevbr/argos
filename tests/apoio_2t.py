"""Gera JSONs EA20 de 2º turno sintéticos a partir de JSONs reais de 1º turno.

Mantém só os candidatos `numeros`, dá a cada um metade dos votos válidos dos demais e
aplica a fração de seções `frac`. Estrutura do JSON = a do arquivo real.
"""

import copy
import io
import json
from datetime import datetime, timezone
from pathlib import Path

FIX = Path(__file__).parent / "fixtures"
MUNS = [("sp", "sp71072"), ("ac", "ac01120")]


def para_2o_turno(j1: dict, numeros: tuple[int, int], frac: float, eleicao: int,
                  inclinacao: float = 0.0, hg: str = "20:00:00") -> bytes:
    j = copy.deepcopy(j1)
    cands = [c for a in j["carg"][0]["agr"] for p in a["par"] for c in p.get("cand", [])]
    v1 = {int(c["n"]): int(c["vap"]) for c in cands}
    outros = sum(v for n, v in v1.items() if n not in numeros)
    a = (v1[numeros[0]] + outros / 2) * (1 + inclinacao)
    b = (v1[numeros[1]] + outros / 2) * (1 - inclinacao)
    votos = {numeros[0]: int(a * frac), numeros[1]: int(b * frac)}
    for agr in j["carg"][0]["agr"]:
        for par in agr["par"]:
            par["cand"] = [c for c in par.get("cand", []) if int(c["n"]) in numeros]
            for c in par["cand"]:
                c["vap"] = str(votos[int(c["n"])])
                c["pvapn"] = c["pvap"] = ""
        agr["par"] = [p for p in agr["par"] if p["cand"]]
    j["carg"][0]["agr"] = [a for a in j["carg"][0]["agr"] if a["par"]]
    j["ele"], j["t"], j["hg"] = str(eleicao), "2", hg
    j["s"]["pstn"] = f"{100 * frac:.9f}".replace(".", ",")
    j["s"]["st"] = str(int(int(j["s"]["ts"]) * frac))
    j["v"]["vv"] = str(sum(votos.values()))
    return json.dumps(j).encode()


def montar_pq_2t(tmp_path: Path) -> Path:
    """data/parquet de teste: 1º turno real por município (SP capital, Acrelândia) e
    2º turno sintético com SP pela metade, AC sem dados e o arquivo br."""
    from apuracao.coletor import snapshot
    from apuracao.coletor.log import LogJson
    from apuracao.modelo.construir import construir

    raw = tmp_path / "raw"
    t0 = datetime(2026, 10, 25, 21, 0, tzinfo=timezone.utc)

    def gravar(eleicao, uf, nome, conteudo):
        snapshot.gravar(raw, eleicao, uf, nome, conteudo, url="u", momento=t0, status=200,
                        etag=None, last_modified=None)

    for uf, mun in MUNS:
        nome = f"{mun}-c0001-e006257-u"
        gravar(6257, uf, nome, (FIX / f"{nome}.json").read_bytes())
    for uf, prefixo, frac in [("sp", "sp71072", 0.5), ("br", "br", 0.3)]:
        j = json.loads((FIX / f"{prefixo}-c0001-e006257-u.json").read_bytes())
        gravar(6258, uf, f"{prefixo}-c0001-e006258-u", para_2o_turno(j, (13, 22), frac, 6258))
    construir(raw, tmp_path / "pq", LogJson(io.StringIO()))
    return tmp_path / "pq"
