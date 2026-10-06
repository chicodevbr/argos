# Apuração TSE — coleta, projeção e análise de eleições

## Objetivo

Aplicação em Python para:

1. **Coletar ao vivo** os resultados do 2º turno de 2026 (25/10/2026), gravando a evolução da apuração em snapshots.
2. **Projetar** o resultado final durante a apuração, com faixa de incerteza.
3. **Dashboard analítico** com dados consolidados (abstenção, brancos/nulos, comparecimento por UF e município).
4. **Comparar** 2026 com eleições anteriores (2014, 2018, 2022).

## Prioridades e prazo

O 2º turno é em **25/10/2026**. As urnas fecham às 17h (horário de Brasília).
A ordem de construção é fixa. Não avance de fase sem a anterior funcionando e testada:

1. **Coletor + snapshots** (crítico). Se falhar na noite da eleição, o dado se perde.
2. Painel ao vivo simples + carga do 1º turno de 2026.
3. Modelo de projeção, com backtest em 2022.
4. Carga histórica, dashboard analítico e comparações (sem prazo).

Ao propor mudanças, prefira o simples e robusto ao elegante. Robustez do coletor vem antes de qualquer outra coisa.

## Stack

- Python 3.12+, gerenciado com `uv`
- `httpx` (assíncrono) para coleta
- DuckDB + Parquet para armazenamento e análise
- `pydantic` para validar os JSONs do TSE
- Streamlit para painel e dashboard
- `pytest` para testes
- Cloudflare R2 (compatível com S3) para guardar snapshots em produção
- Execução na eleição: GitHub Actions (repo público, job de até 6h) + cópia local redundante

Não adicione dependências novas sem justificar no PR/commit.

## Estrutura

```
apuracao/
  coletor/        # polling dos JSONs do TSE, gravação de snapshots
  carga/          # ingestão dos CSVs históricos do portal de dados abertos
  modelo/         # normalização, tabelas DuckDB, de-para de municípios
  projecao/       # modelo de projeção e backtest
  app/            # páginas Streamlit
config/
  eleicoes.toml   # códigos de eleição, cargos, URLs base
data/             # NÃO versionar (.gitignore)
  raw/            # JSONs brutos comprimidos
  parquet/        # dados normalizados
docs/tse/         # especificações JSON oficiais e exemplos
tests/fixtures/   # JSONs do ambiente simulado do TSE
.github/workflows/
```

## Fonte de dados ao vivo (JSON de divulgação)

O TSE não tem API REST. Publica **arquivos JSON estáticos em CDN**, atualizados conforme a totalização.
As especificações oficiais ficam em `docs/tse/`. **Consulte-as antes de escrever qualquer parser**, sem supor nomes de campos.

- Base oficial: `https://resultados.tse.jus.br/oficial`
- Base simulado (testes): `https://resultados-sim.tse.jus.br/simulado`
- Padrão de resultado: `/ele2026/{eleicao}/dados/{uf}/{uf}-c{cargo:04d}-e{eleicao:06d}-u.json`
- Padrão por município: `/ele2026/{eleicao}/dados/{uf}/{uf}{cod_mun_tse}-c{cargo:04d}-e{eleicao:06d}-u.json`
- Acompanhamento: sufixo `-ab.json`
- Configuração de municípios: `/ele2026/{eleicao}/config/mun-e{eleicao:06d}-cm.json`
- UF `br` = Brasil; UF `zz` = exterior
- Cargos: 0001 presidente, 0003 governador

Códigos conhecidos do 1º turno de 2026: 6257 (presidente), 6259 (estaduais). Esses códigos vêm de projetos da comunidade, **não da documentação oficial**.
**O código do 2º turno é diferente e ainda precisa ser confirmado**. Mantenha todos os códigos em `config/eleicoes.toml`, nunca fixos no código.

## Regras do coletor

- Requisições condicionais com `If-None-Match` (ETag) e `If-Modified-Since`. Resposta 304 não gera snapshot.
- **Nunca sobrescreva** um snapshot. Cada resposta nova é um arquivo novo: `data/raw/{eleicao}/{uf}/{arquivo}/{ts_coleta_utc}.json.gz`.
- Grave o timestamp de coleta (UTC) **e** o timestamp de atualização que vem no próprio JSON.
- Frequência: arquivos `br` e de UF a cada ~30s; arquivos de município em ciclo mais espaçado e escalonado. Respeite as instruções de download do TSE em `docs/tse/`.
- Backoff exponencial com jitter em erro 5xx/timeout. Erro em um arquivo nunca derruba o loop.
- Log estruturado (JSON) com contagem de requisições, 304s, erros e snapshots gravados.
- Sincronização com R2 em processo separado do polling, para que lentidão no upload não atrase a coleta.
- Deve encerrar de forma limpa (SIGTERM) antes do limite de 6h do GitHub Actions.

## Modelo de dados (DuckDB)

- `municipios`: `cod_tse`, `cod_ibge`, `nome`, `uf`, `regiao`. **Códigos TSE e IBGE são diferentes**. Todo join com malhas do IBGE passa por esta tabela.
- `snapshot_totais`: `eleicao`, `cargo`, `abrangencia`, `ts_coleta`, `ts_tse`, `secoes_totalizadas`, `pct_secoes`, `eleitorado`, `comparecimento`, `abstencao`, `brancos`, `nulos`
- `snapshot_candidatos`: `eleicao`, `cargo`, `abrangencia`, `ts_coleta`, `sq_cand`, `numero`, `nome`, `votos`, `pct_validos`
- `hist_votacao`: `ano`, `turno`, `cargo`, `uf`, `cod_mun_tse`, `zona`, `numero`, `votos`
- `hist_comparecimento`: `ano`, `turno`, `uf`, `cod_mun_tse`, `zona`, `aptos`, `comparecimento`, `abstencao`, `brancos`, `nulos`

O dado bruto é a fonte da verdade. As tabelas devem poder ser reconstruídas a partir de `data/raw/` e dos CSVs a qualquer momento.

## Projeção

Não extrapole o percentual nacional parcial: a ordem de chegada dos votos varia por região. Abordagem por município:

- Apurado: resultado real.
- Parcial: extrapola o próprio município pelo percentual de seções.
- Não iniciado: base do 1º turno no município, ajustada pelo deslocamento 1º→2º turno observado em municípios comparáveis já apurados (mesma UF ou perfil).
- Ponderar pelo eleitorado; exibir intervalo, não número único.
- Backtest obrigatório com 2022 (1º turno como base, 2º como verdade), simulando ordens de chegada diferentes.

## Testes

- Fixtures em `tests/fixtures/` baixadas do ambiente simulado. **Testes nunca acessam a rede.**
- Todo parser tem teste contra fixture real.
- Coletor testado com servidor falso que simula 200, 304, 5xx e timeout.

## Comandos

Lista completa, agrupada por uso, no [README.md](README.md). Os mais usados:

```
uv run pytest
AMBIENTE=oficial ELEICOES="2026-t2-federal 2026-t2-estadual" MUNICIPIOS=1 scripts/coleta.sh
uv run python -m apuracao.modelo construir --loop 15   # data/raw -> data/parquet (painel lê daqui)
uv run python -m apuracao.carga --ano 2022             # histórico; --malha baixa a malha do IBGE
uv run streamlit run apuracao/app/main.py
uv run python -m apuracao.pagina                       # página compartilhável -> data/pagina/
```

Ao criar ou mudar um comando, atualize o README.

## Como trabalhar neste repo

- Planeje antes de codar mudanças que toquem mais de um módulo.
- Um módulo por vez; feche com testes passando.
- Em dúvida sobre formato de campo do TSE, leia `docs/tse/` ou uma fixture. Não invente.
- Código, nomes de variáveis e comentários em português é aceitável; seja consistente dentro de cada módulo.
