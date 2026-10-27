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

# só a sincronização (envia ao R2 o que estiver em data/raw; --destino-pasta copia para uma pasta;
# --workers = envios simultâneos, padrão 32)
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

No GitHub Actions, o workflow **coleta** dispara pela aba Actions (perfis `ensaio` e `segundo-turno`;
o padrão `escolher` falha de propósito para obrigar a escolha), cada run com até 5h40.
Os crons de reserva de 25/10/2026 foram removidos depois da eleição (ver comentário no workflow).

Backup de um pacote local no R2 (as credenciais só existem como secrets do repo): o workflow
**backup** busca o arquivo numa release em rascunho, envia e confere o SHA-256 baixando de volta.

```
gh release create backup-X --draft --title backup-X pacote.tar.gz pacote.tar.gz.sha256
gh workflow run backup.yml -f release=backup-X -f arquivo=pacote.tar.gz   # destino: backup/ no bucket
gh release delete backup-X --yes                                          # depois do run verde
```

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
uv run python -m apuracao.carga --boletim 2026:1 --sem-guardar-zip   # 4,7 GB (2026); apaga cada zip depois de ler
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

No Codespace, `scripts/retomar-codespace.sh` religa o que estiver parado (coletor do 2º turno,
`construir --loop 15` e painel) sem duplicar o que já roda; use depois que o Codespace for retomado.

Abre em http://localhost:8501 com três páginas: **Apuração ao vivo**, **Análise do 1º turno** e
**A noite da apuração** (curva e ritmo por região de noites passadas, a partir do boletim de urna).
No topo da página ao vivo, um selo mostra se a coleta local está ativa (lê `logs/coletor.jsonl`).
Variáveis opcionais: `APURACAO_DIR_PARQUET`, `APURACAO_CONFIG`, `APURACAO_MALHA`,
`APURACAO_LOG_COLETOR`, `APURACAO_HISTORICO_PROJECAO` e `APURACAO_ELEICAO` (id da eleição aberta
ao iniciar, ex.: `2026-t2-federal`).

### Projeção e backtest

```
uv run python -m apuracao.projecao.backtest --ano 2022                         # presidente
uv run python -m apuracao.projecao.backtest --ano 2022 --ordem-real            # presidente, ordem real de chegada (boletim 2022:2)
uv run python -m apuracao.projecao.backtest --ano 2022 --cargo governador
uv run python -m apuracao.projecao.backtest --ano 2022 --cargo governador --uma-fora
```

Replay de uma noite já coletada (presidente): refaz a projeção minuto a minuto a partir dos
snapshots, com o código do painel, e compara com o resultado final e com o histórico gravado.
A base (1º turno por município) vem de `data/parquet`.

```
uv run python -m apuracao.modelo construir --dir-raw NOITE/data/raw --dir-parquet data/noite-2t/replay/parquet \
    --historico-projecao data/noite-2t/replay/descartar.jsonl
uv run python -m apuracao.projecao.replay --dir-parquet data/noite-2t/replay/parquet \
    --historico NOITE/data/projecao/historico.jsonl --verdade 51.785 --taus 0.01,0.02,0.03
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
uv run python -m apuracao.pagina --inicio --site site              # página inicial (resumo) -> site/index.html; gere as outras antes
uv run python -m apuracao.pagina --recorte brasilia --turnos --site site   # 1º x 2º turno, Brasília -> site/brasilia/
uv run python -m apuracao.pagina --recorte rio --turnos --site site        # 1º x 2º turno, cidade do Rio -> site/rio-de-janeiro/
uv run python -m apuracao.pagina --recorte brasilia --urnas --site site   # quem venceu em cada urna -> site/brasilia/urnas/
uv run python -m apuracao.pagina --recorte brasilia --ras --site site     # quem venceu em cada RA x zona -> site/brasilia/ras/
uv run python -m apuracao.pagina --recorte brasilia --zonas --site site   # votos por zona eleitoral -> site/brasilia/zonas/
uv run python -m apuracao.pagina --recorte rio --ras --site site          # o mesmo na cidade do Rio -> site/rio-de-janeiro/ras/ (e --zonas, --urnas)
uv run python -m apuracao.pagina --recorte brasilia --turnos --ano 2022    # o mesmo com 2022 (conferência) -> data/pagina/
uv run python -m apuracao.pagina --recorte brasilia    # abstenção do 1º turno (saiu do site em 26/10) -> data/pagina/
uv run python -m apuracao.pagina --recorte estado-rj --site site   # estado do Rio -> site/estado-do-rio/
uv run python -m apuracao.pagina --recorte brasilia --votos --site site   # votos por candidato -> site/votos/
uv run python -m apuracao.pagina --perdas --site site   # "Onde Lula perdeu votos" -> site/onde-lula-perdeu/
uv run python -m apuracao.pagina --perdas --recorte brasilia --site site   # por RA do DF -> site/onde-lula-perdeu/brasilia/
uv run python -m apuracao.pagina --vitoria   # "Como Lula venceu em 2026" (2º turno x 2022) -> data/pagina/
uv run python -m apuracao.pagina --pesquisas --site site   # pesquisas do 2º turno x resultado -> site/pesquisas/
```

**Do 1º ao 2º turno** (`apuracao/pagina/turnos.py`): votos de Lula e do 22, brancos, nulos e abstenção em cada RA,
no 1º e no 2º turno, seção a seção. Precisa do 2º turno nos arquivos por seção do TSE (detalhe e votação por seção),
que saem dias depois da eleição (em 2026, no dia seguinte): `uv run python -m apuracao.carga --secao 2026` baixa de
novo os três arquivos quando o tamanho no TSE muda. Sem o 2º turno neles, o comando para com uma mensagem.

**Quem venceu em cada urna** (`apuracao/pagina/urnas.py`): o 2º turno seção a seção (cada seção é uma urna),
somado por local de votação e desenhado no mapa pelas coordenadas do local; mapa com zoom e tabela com busca.

**Página inicial** (`--inicio`): o resultado do 2º turno e um cartão por página, com uma frase tirada da própria
página já gerada em `site/` (`FATOS` em `apuracao/pagina/__main__.py`) e a barrinha de onde cada um venceu (RAs,
urnas). Por isso, gere de novo a inicial depois de atualizar qualquer página.

**Quem venceu em cada RA** (`apuracao/pagina/ras.py`) e **votos por zona eleitoral** (`apuracao/pagina/zonas.py`):
o 2º turno por região administrativa (RA do local de votação, pelas coordenadas) e por zona eleitoral, que junta RAs.
O TSE não publica o contorno das zonas; o mapa usa a zona do local de votação mais próximo (Voronoi). Locais sem coordenada no 2º turno herdam a do 1º turno, de 2022 ou do
local onde as mesmas seções votaram no 1º turno. A página das
RAs confere a atribuição: move para a RA vizinha cada local a menos de 100 m do limite ou cujo bairro no TSE cita
outra RA e mostra se algum vencedor mudaria.

**Pesquisas do 2º turno** (`apuracao/pagina/pesquisas.py`): as pesquisas nacionais com campo encerrado de 20 a
24/10/2026 ficam em `apuracao/pagina/pesquisas_2t_2026.csv` (votos totais como divulgados, fonte por linha); o
resultado vem das tabelas do TSE. Para incluir ou corrigir uma pesquisa, edite o CSV e gere de novo.

**Netlify:** o repositório publica a pasta `site/` (ver `netlify.toml`): o HTML é gerado aqui e
versionado, sem build no Netlify. Para atualizar, gere com `--site site`, faça commit e push. Com `--site`, cada página
ganha uma barra de navegação entre as páginas listadas em `SITE` (`apuracao/pagina/__main__.py`);
`--sem-barra` gera sem ela. Os dados de votos por seção vêm de `votacao_secao_{ano}_BR.zip`
(Portal de Dados Abertos, baixado em `data/raw/historico/{ano}/`).

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
