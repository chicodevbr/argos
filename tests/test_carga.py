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


# --- layout antigo (2014): sem QT_VOTOS_NOMINAIS_VALIDOS / NM_TIPO_DESTINACAO_VOTOS ----

# como no CSV real: DS_CARGO existe (o pré-filtro de texto procura "Presidente")
BASE = ["ANO_ELEICAO", "NR_TURNO", "CD_CARGO", "DS_CARGO", "CD_ELEICAO", "SG_UF", "CD_MUNICIPIO", "NR_ZONA",
        "ST_VOTO_EM_TRANSITO"]


def _zip(caminho: Path, membro: str, cab: list[str], linhas: list[list]):
    corpo = ";".join(f'"{c}"' for c in cab) + "\n" + "".join(";".join(str(v) for v in l) + "\n" for l in linhas)
    with zipfile.ZipFile(caminho, "w") as z:
        z.writestr(membro, corpo.encode("latin-1"))


def _ano_antigo(tmp_path, validos_detalhe: int):
    d = tmp_path / "hist" / "2014"
    d.mkdir(parents=True)
    base = [2014, 1, 1, '"Presidente"', 680, '"AC"', 1120, 9]
    _zip(d / "votacao_candidato_munzona_2014.zip", "votacao_candidato_munzona_2014_BR.csv",
         BASE + ["NR_CANDIDATO", "NM_URNA_CANDIDATO", "QT_VOTOS_NOMINAIS"],
         [base + ['"N"', 13, '"DILMA"', 600], base + ['"N"', 45, '"AÉCIO"', 400]])
    _zip(d / "detalhe_votacao_munzona_2014.zip", "detalhe_votacao_munzona_2014_BRASIL.csv",
         BASE + ["QT_APTOS", "QT_COMPARECIMENTO", "QT_ABSTENCOES", "QT_TOTAL_VOTOS_VALIDOS",
                 "QT_VOTOS_BRANCOS", "QT_TOTAL_VOTOS_NULOS"],
         [base + ['"N"', 1300, 1100, 200, validos_detalhe, 40, 60]])
    return tmp_path / "hist"


def test_layout_antigo_usa_nominais_quando_batem_com_validos(tmp_path):
    hist = _ano_antigo(tmp_path, validos_detalhe=1000)
    n = tse_csv.carregar_ano(2014, hist, tmp_path / "pq", LogJson(io.StringIO()))
    assert n == {"hist_votacao": 2, "hist_comparecimento": 1}
    linhas = list(tse_csv.linhas_votacao(hist / "2014" / "votacao_candidato_munzona_2014.zip"))
    assert [(l[iv["votos"]], l[iv["votos_validos"]], l[iv["destinacao"]]) for l in linhas] == [(600, 600, ""), (400, 400, "")]


def test_layout_antigo_falha_se_houve_votos_anulados(tmp_path):
    hist = _ano_antigo(tmp_path, validos_detalhe=900)  # 100 votos nominais não foram válidos
    with pytest.raises(ValueError, match="nominais != válidos"):
        tse_csv.carregar_ano(2014, hist, tmp_path / "pq", LogJson(io.StringIO()))
