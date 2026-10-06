# Argos — apuração das eleições de 2026

Coleta ao vivo os resultados divulgados pelo TSE, projeta o resultado final durante a apuração
e analisa os dados por UF e município, comparando 2026 com 2014, 2018 e 2022.

- **Coleta ao vivo:** consulta os arquivos JSON de divulgação do TSE e guarda cada versão nova como
  um snapshot imutável (nada é sobrescrito). Roda no GitHub Actions e numa cópia local.
- **Painel:** Streamlit com a apuração ao vivo (números, evolução, projeção com faixa de incerteza,
  aviso de anomalias nos dados do TSE) e uma página de análise do 1º turno com mapas.
- **Projeção:** modelo por município (1º turno + deslocamento observado nos já apurados), validado
  em backtest com 2022.
- **Página compartilhável:** abstenção no 1º turno de 2026, gerada a partir das tabelas.

O 2º turno é em **25/10/2026**. O passo a passo da noite está em
[docs/roteiro-noite-eleicao.md](docs/roteiro-noite-eleicao.md).

## Fontes de dados

| Fonte | Uso | Onde fica |
|---|---|---|
| Arquivos JSON de divulgação do TSE (`resultados.tse.jus.br`) | coleta ao vivo e 1º turno de 2026 por município | `data/raw/{eleição}/...` |
| Portal de Dados Abertos do TSE (CSV) | histórico 2014, 2018, 2022 | `data/raw/historico/{ano}/` |
| API de malhas do IBGE | mapas por município | `data/raw/ibge/` |

As especificações oficiais dos arquivos do TSE estão em [docs/tse/](docs/tse/). Códigos de eleição,
cargos e URLs ficam em [config/eleicoes.toml](config/eleicoes.toml), nunca no código.

## Instalação

Requer Python 3.12+ e [uv](https://docs.astral.sh/uv/).

```
uv sync
uv run pytest
```

## Comandos

### Coleta

```
# coletor + sincronização (R2 se R2_BUCKET estiver definida, senão a pasta SYNC_PASTA)
AMBIENTE=oficial ELEICOES="2026-t2-federal 2026-t2-estadual" MUNICIPIOS=1 scripts/coleta.sh

# só o coletor
uv run python -m apuracao.coletor --ambiente oficial --eleicao 2026-t2-federal --municipios

# carga única de uma eleição já apurada (busca cada arquivo uma vez e encerra)
uv run python -m apuracao.coletor --ambiente oficial --eleicao 2026-t1-federal --eleicao 2026-t1-estadual --municipios --uma-vez

# só a sincronização (envia ao R2 o que estiver em data/raw; --destino-pasta copia para uma pasta)
uv run python -m apuracao.coletor.sync --dir-raw data/raw
```

| Variável do `coleta.sh` | Significado |
|---|---|
| `AMBIENTE` | `oficial` ou `simulado` (obrigatória) |
| `ELEICOES` | ids de `config/eleicoes.toml`, separados por espaço (obrigatória) |
| `MUNICIPIOS` | `1` inclui os arquivos de cada município (base da projeção) |
| `DURACAO` | segundos; padrão `coletor.duracao_max_s` (5h45, cabe nas 6h do Actions) |
| `DIR_RAW`, `CONFIG` | pasta dos snapshots e arquivo de configuração |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | envio ao Cloudflare R2 |
| `SYNC_PASTA` | cópia para uma pasta (usada quando `R2_BUCKET` está vazia) |

Máquina que só coleta (ex.: Mac antigo, sem `pyarrow`): `python3.12 -m venv .venv &&
.venv/bin/pip install -r requirements-coletor.txt`. Ver [docs/mac-mini-coletor.md](docs/mac-mini-coletor.md).

No GitHub Actions, o workflow **coleta** dispara pela aba Actions (perfis `ensaio` e `segundo-turno`)
e por dois crons de reserva em 25/10: 16h45 e 21h45 (Brasília), cada run com até 5h40.

### Tabelas (data/raw → data/parquet)

```
uv run python -m apuracao.modelo construir                # processa os snapshots novos
uv run python -m apuracao.modelo construir --loop 15      # repete a cada 15 s (noite da eleição)
uv run python -m apuracao.modelo municipios --cm "$(ls data/raw/6257/config/mun-e006257-cm/*.json.gz | tail -1)"
```

A construção também verifica anomalias nos dados do TSE (versão velha servida pelo CDN, votos ou
seções diminuindo, totais incoerentes) e registra cada uma como evento `anomalia`. Com dados novos de
2º turno, grava a projeção de presidente em `data/projecao/historico.jsonl` (`--historico-projecao`). Apagar
`data/parquet/` e rodar de novo reconstrói tudo a partir do bruto.

### Histórico e malha

```
uv run python -m apuracao.carga --ano 2022 --ano 2018 --ano 2014   # baixa e carrega os CSVs
uv run python -m apuracao.carga --ano 2022 --sem-download          # usa os zips já baixados
uv run python -m apuracao.carga --malha                            # malha municipal do IBGE
```

### Boletim de urna (curva de noites passadas)

```
uv run python -m apuracao.carga --boletim 2022:2                     # 66 MB de zips
uv run python -m apuracao.carga --boletim 2026:1 --sem-guardar-zip   # ~1,4 GB; apaga cada zip depois de ler
```

O boletim de urna (Portal de Dados Abertos) traz os votos de cada seção e o horário em que o
boletim chegou ao TSE. Com ele, `apuracao/projecao/noite.py` reconstrói a curva da apuração minuto a
minuto e o ritmo de cada região. Em 2022 os totais batem com os oficiais e a curva bate com a
publicada pelo g1 na noite (1 min de defasagem). O TSE publica o boletim alguns dias depois do
turno; enquanto não sai, o comando avisa `boletim_indisponivel`. Saída em `data/parquet/hist_boletim/`.

### Painel

```
uv run streamlit run apuracao/app/main.py
```

Abre em http://localhost:8501 com três páginas: **Apuração ao vivo**, **Análise do 1º turno** e
**A noite da apuração** (curva e ritmo por região de noites passadas, a partir do boletim de urna).
No topo da página ao vivo, um selo mostra se a coleta local está ativa (lê `logs/coletor.jsonl`).
Variáveis opcionais: `APURACAO_DIR_PARQUET`, `APURACAO_CONFIG`, `APURACAO_MALHA`,
`APURACAO_LOG_COLETOR`, `APURACAO_HISTORICO_PROJECAO` e `APURACAO_ELEICAO` (id da eleição aberta
ao iniciar, ex.: `2026-t2-federal`).

### Projeção e backtest

```
uv run python -m apuracao.projecao.backtest --ano 2022                         # presidente
uv run python -m apuracao.projecao.backtest --ano 2022 --cargo governador
uv run python -m apuracao.projecao.backtest --ano 2022 --cargo governador --uma-fora
```

No 2º turno de presidente, o painel mostra também o **caminho da apuração**
(`apuracao/projecao/caminho.py`): quanto falta contar por região, quanto o candidato que está atrás
precisa do que falta, a chance de virada e o horário provável, comparados com a curva minuto a minuto
do 2º turno de 2022, reconstruída do boletim de urna (exige `--boletim 2022:2` carregado; ver abaixo). O horário supõe que cada UF segue no ritmo dos últimos 30 min. Na noite real de 2022
(virada às 18h44), a partir de 20% das seções previu entre 5 e 11 min cedo.

Em 2022, a projeção de presidente errou em média 0,09 ponto (máximo 0,51) e a faixa de 90% conteve o
resultado em todos os cenários de ordem de chegada testados; ler o percentual parcial nacional errou
até 13,7 pontos. Governador é bem menos previsível (cobertura de ~87%, deixando uma UF de fora).

### Página compartilhável

```
uv run python -m apuracao.pagina    # gera data/pagina/abstencao-1o-turno-2026.html
uv run python -m apuracao.carga --secao 2026 --secao 2022 --regioes-df --regioes-rio   # dados das páginas por região
uv run python -m apuracao.pagina --recorte brasilia --site site    # Brasília -> site/index.html
uv run python -m apuracao.pagina --recorte rio --site site         # cidade do Rio -> site/rio-de-janeiro/
uv run python -m apuracao.pagina --recorte estado-rj --site site   # estado do Rio -> site/estado-do-rio/
```

**Netlify:** o repositório publica a pasta `site/` (ver `netlify.toml`): o HTML é gerado aqui e
versionado, sem build no Netlify. Para atualizar, gere com `--site site`, faça commit e push.

**Abstenção por região** (`apuracao/pagina/regioes.py`, lugares em `recortes.py`): detalhe por seção
(presidente, com os eleitores em trânsito; a soma confere com os totais oficiais). Brasília e a cidade
do Rio são um município só no TSE: cada seção vai para a região administrativa do seu local de
votação, pelas coordenadas do local dentro da malha oficial (35 RAs do GDF; 33 RAs do IPP/Prefeitura
do Rio, com os bairros de cada uma). No estado do Rio, cada seção vai para o seu município.

O arquivo gerado é autocontido (dados e malha embutidos) e é publicado como página. As frases com
afirmações sobre os dados são calculadas a partir deles.

## Estrutura

```
apuracao/
  coletor/    polling dos JSONs do TSE, snapshots, sincronização (R2 ou pasta)
  carga/      CSVs do Portal de Dados Abertos (inclusive boletim de urna) e malha do IBGE
  modelo/     parsers (EA20, EA12), tabelas Parquet, anomalias
  projecao/   modelo de projeção, dados de entrada, backtest
  app/        painel Streamlit (páginas ao vivo e análise, mapas)
  pagina/     gerador da página compartilhável
config/       eleicoes.toml e lista de municípios de reserva
docs/         roteiro da noite, coletor reserva (Mac mini) e especificações do TSE
scripts/      coleta.sh (coletor + sincronização)
tests/        testes e fixtures (JSONs e CSVs reais do TSE)
data/         dados coletados e derivados (não versionado)
```

Os testes nunca acessam a rede: o coletor é testado contra um servidor falso e os parsers contra
fixtures reais.
