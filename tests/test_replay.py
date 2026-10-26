"""Replay da noite com tabelas sintéticas: 3 municípios, 1º turno de base e duas rodadas de
snapshots do 2º turno (parcial às 20h, completa às 21h). Nenhum teste acessa a rede."""

from pathlib import Path

import pandas as pd
import pytest

from apuracao.projecao import replay
from apuracao.projecao.modelo import Parametros

MUN = {"sp71072": "sp", "ba38490": "ba", "rs88013": "rs"}


def _gravar(dir_pq: Path, totais: list[dict], cands: list[dict]) -> None:
    for nome, linhas in (("snapshot_totais", totais), ("snapshot_candidatos", cands)):
        (dir_pq / nome).mkdir(parents=True, exist_ok=True)
        pd.DataFrame(linhas).to_parquet(dir_pq / nome / "part-0.parquet", index=False)


def _snap(totais, cands, eleicao, abr, ts, pct, votos, validos=None):
    arq = f"{eleicao}/{abr}/{ts}"
    totais.append({"eleicao": eleicao, "cargo": 1, "abrangencia": abr, "tpabr": "br" if abr == "br" else "mu",
                   "uf": MUN.get(abr, abr), "ts_coleta": pd.Timestamp(ts), "ts_tse": pd.Timestamp(ts),
                   "pct_secoes": pct, "votos_validos": validos or sum(votos.values()), "arquivo_raw": arq})
    for n, v in votos.items():
        cands.append({"arquivo_raw": arq, "eleicao": eleicao, "numero": n, "nome": f"C{n}", "votos": v,
                      "destinacao": "Válido"})


@pytest.fixture
def noite(tmp_path):
    base_t, base_c, t2_t, t2_c = [], [], [], []
    final = {"sp71072": (600, 400), "ba38490": (700, 300), "rs88013": (300, 700)}
    for chave, (a, b) in final.items():
        # 1º turno: A um pouco abaixo do 2º turno em todo lugar
        _snap(base_t, base_c, 6257, chave, "2026-10-04 23:00", 100, {13: a - 50, 22: b - 50}, validos=a + b)
        _snap(t2_t, t2_c, 6258, chave, "2026-10-25 21:00", 100, {13: a, 22: b})
    # às 20h só SP, pela metade
    _snap(t2_t, t2_c, 6258, "sp71072", "2026-10-25 20:00", 50, {13: 300, 22: 200})
    _snap(t2_t, t2_c, 6258, "br", "2026-10-25 21:00", 100, {13: 1600, 22: 1400})
    _gravar(tmp_path / "base", base_t, base_c)
    _gravar(tmp_path / "t2", t2_t, t2_c)
    return replay.Noite(tmp_path / "t2", tmp_path / "base", 6257, 6258)


def test_candidatos_base_e_verdade(noite):
    assert (noite.num_a, noite.num_b) == (13, 22) and len(noite.base) == 3
    verdade, pct = noite.verdade()
    assert verdade == pytest.approx(100 * 1600 / 3000) and pct == 100


def test_recorte_por_horario_de_coleta(noite):
    p = Parametros(n_sim=200)
    cedo = noite.projetar_em("2026-10-25 20:30", p)
    assert 0 < cedo.pct_contado < 100          # só SP, parcial
    assert noite.projetar_em("2026-10-25 19:00", p).pct_contado == 0
    tudo = noite.projetar_em("2026-10-25 21:00", p)
    assert tudo.pct_contado == pytest.approx(100) and tudo.pct_a == pytest.approx(100 * 1600 / 3000, abs=0.01)


def test_rodar_folga_e_comparar(noite):
    verdade, _ = noite.verdade()
    momentos = [pd.Timestamp("2026-10-25 20:30"), pd.Timestamp("2026-10-25 21:00")]
    res = replay.rodar(noite, momentos, Parametros(n_sim=200), verdade)
    assert len(res) == 2 and res["cobre"].all() and replay.folga_minima(res) > 0
    hist = pd.DataFrame({"marca": [str(m) for m in momentos], "pct_contado": [0.0, 100.0],
                         "pct_a": res["projecao"], "pct_a_inf": res["inf"], "pct_a_sup": res["sup"]})
    d = replay.comparar(res, hist)
    assert list(d["painel_com_dados"]) == [False, True]
    assert d["dif_projecao"].abs().max() == 0 and d["dif_contado"].iloc[1] == pytest.approx(0)
