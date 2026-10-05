"""Carga dos CSVs de dados abertos, contra amostras reais de 2022. Sem rede."""

import io
import shutil
import zipfile
from pathlib import Path

import httpx
import pytest

from apuracao.carga import tse_csv
from apuracao.carga.baixar import baixar_ano
from apuracao.coletor.log import LogJson
from apuracao.config import carregar
from apuracao.modelo import consultas

FIX = Path(__file__).parent / "fixtures" / "historico"
CONFIG = Path(__file__).parent.parent / "config" / "eleicoes.toml"
VOT = FIX / "votacao_candidato_munzona_2022.zip"
DET = FIX / "detalhe_votacao_munzona_2022.zip"
iv = {c: i for i, (c, _) in enumerate(tse_csv.HIST_VOTACAO)}
ic = {c: i for i, (c, _) in enumerate(tse_csv.HIST_COMPARECIMENTO)}


def test_numero_trata_nulo_e_ne():
    assert tse_csv.numero("10") == 10
    assert tse_csv.numero("#NULO") is None and tse_csv.numero("-1") is None
    assert tse_csv.numero("#NE") is None and tse_csv.numero("-3") is None


def test_votacao_so_presidente_do_br_e_governador_das_ufs():
    linhas = list(tse_csv.linhas_votacao(VOT))
    por_cargo = {}
    for l in linhas:
        por_cargo.setdefault(l[iv["cargo"]], []).append(l)
    assert set(por_cargo) == {1, 3}  # deputados e senador descartados
    assert len(por_cargo[1]) == 51 and len(por_cargo[3]) == 21
    assert {l[iv["turno"]] for l in por_cargo[1]} == {1, 2}
    assert {l[iv["uf"]] for l in por_cargo[1]} == {"ac", "zz"}
    assert all(len(l[iv["cod_mun_tse"]]) == 5 and len(l[iv["zona"]]) == 4 for l in linhas)
    assert all(isinstance(l[iv["votos"]], int) for l in linhas)


def test_comparecimento_presidente_e_governador():
    linhas = list(tse_csv.linhas_comparecimento(DET))
    assert sorted({(l[ic["cargo"]], l[ic["turno"]]) for l in linhas}) == [(1, 1), (1, 2), (3, 1)]
    for l in linhas:
        assert l[ic["comparecimento"]] + l[ic["abstencao"]] == l[ic["aptos"]]


def test_carregar_ano_gera_parquet(tmp_path):
    (tmp_path / "hist" / "2022").mkdir(parents=True)
    for z in (VOT, DET):
        shutil.copy(z, tmp_path / "hist" / "2022" / z.name)
    n = tse_csv.carregar_ano(2022, tmp_path / "hist", tmp_path / "pq", LogJson(io.StringIO()))
    assert n == {"hist_votacao": 72, "hist_comparecimento": 9}
    con = consultas.conectar(tmp_path / "pq")
    total = con.execute(
        f"SELECT sum(votos) FROM read_parquet('{tmp_path}/pq/hist_votacao/*.parquet') WHERE cargo = 1"
    ).fetchone()[0]
    assert total > 0


def test_carregar_ano_csv_vazio_falha_com_mensagem(tmp_path):
    """Como o CSV de 2026 em 05/10: só cabeçalho."""
    d = tmp_path / "hist" / "2026"
    d.mkdir(parents=True)
    for z in (VOT, DET):
        nome = z.name.replace("2022", "2026")
        with zipfile.ZipFile(z) as zin, zipfile.ZipFile(d / nome, "w") as zout:
            for m in zin.namelist():
                cab = zin.read(m).split(b"\n", 1)[0] + b"\n"
                zout.writestr(m.replace("2022", "2026"), cab)
    with pytest.raises(ValueError, match="sem linhas"):
        tse_csv.carregar_ano(2026, tmp_path / "hist", tmp_path / "pq", LogJson(io.StringIO()))


def test_baixar_ano_pula_arquivo_ja_baixado(tmp_path):
    cfg = carregar(CONFIG)
    cfg = cfg.model_copy(update={"historico": cfg.historico.model_copy(update={"dir": tmp_path})})
    conteudo = b"zipfalso"
    pedidos = []

    def h(req):
        pedidos.append(req.method)
        return httpx.Response(200, content=b"" if req.method == "HEAD" else conteudo,
                              headers={"Content-Length": str(len(conteudo))})

    t = httpx.MockTransport(h)
    arquivos = baixar_ano(cfg, 2022, LogJson(io.StringIO()), transport=t)
    assert [p.read_bytes() for p in arquivos] == [conteudo, conteudo]
    assert pedidos.count("GET") == 2
    baixar_ano(cfg, 2022, LogJson(io.StringIO()), transport=t)
    assert pedidos.count("GET") == 2  # segunda vez: só HEAD
