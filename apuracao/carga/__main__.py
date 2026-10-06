"""uv run python -m apuracao.carga --ano 2022 [--sem-download]
uv run python -m apuracao.carga --malha   (malha municipal do IBGE, para os mapas)
uv run python -m apuracao.carga --boletim 2022:2   (boletim de urna: curva da noite)
uv run python -m apuracao.carga --secao 2026 --secao 2022 --regioes-df   (página de Brasília)"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apuracao.carga import boletim, regioes_df
from apuracao.carga.baixar import baixar_ano, baixar_secoes
from apuracao.carga.ibge import baixar_malha
from apuracao.carga.tse_csv import carregar_ano
from apuracao.coletor.log import LogJson
from apuracao.config import carregar


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="apuracao.carga")
    ap.add_argument("--ano", type=int, action="append", default=[])
    ap.add_argument("--malha", action="store_true", help="baixa a malha municipal do IBGE")
    ap.add_argument("--config", type=Path, default=Path("config/eleicoes.toml"))
    ap.add_argument("--dir-parquet", type=Path, default=Path("data/parquet"))
    ap.add_argument("--sem-download", action="store_true", help="usa só os zips já baixados")
    ap.add_argument("--boletim", action="append", default=[], metavar="ANO:TURNO",
                    help="boletim de urna (votos por seção + horário de chegada), ex.: 2022:2")
    ap.add_argument("--secao", type=int, action="append", default=[], metavar="ANO",
                    help="baixa o detalhe por seção e os locais de votação do ano")
    ap.add_argument("--regioes-df", action="store_true", help="baixa a malha das regiões administrativas do DF")
    ap.add_argument("--sem-guardar-zip", action="store_true",
                    help="boletim: apaga cada zip depois de lido (1º turno tem ~1,4 GB)")
    args = ap.parse_args(argv)
    cfg, log = carregar(args.config), LogJson()
    if not (args.ano or args.malha or args.boletim or args.secao or args.regioes_df):
        ap.error("informe --ano, --malha, --boletim, --secao e/ou --regioes-df")
    for ano in args.secao:
        baixar_secoes(cfg, ano, log)
    if args.regioes_df:
        regioes_df.baixar(cfg, log)
    for item in args.boletim:
        ano, turno = (int(x) for x in item.split(":"))
        boletim.carregar(cfg, ano, turno, args.dir_parquet, log, baixar=not args.sem_download,
                         guardar_zip=not args.sem_guardar_zip)
    if args.malha:
        baixar_malha(cfg, log)
    for ano in args.ano:
        if not args.sem_download:
            baixar_ano(cfg, ano, log)
        carregar_ano(ano, cfg.historico.dir, args.dir_parquet, log)
    return 0


if __name__ == "__main__":
    sys.exit(main())
