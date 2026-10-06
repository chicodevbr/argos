# Roteiro da noite da eleição — 2º turno, 25/10/2026

Urnas fecham às **17h (Brasília)**. A coleta roda em **dois lugares ao mesmo tempo**:
- **GitHub Actions:** envia ao **R2** e guarda o artefato do run.
- **Este Mac:** cópia local em `data/raw`, espelhada numa pasta (`SYNC_PASTA`, de preferência
  num disco externo), e que alimenta o painel. **A cópia local não envia ao R2**: assim o R2
  recebe uma cópia só (~400 mil operações de escrita na noite), longe do limite de 1 milhão/mês
  do plano gratuito.

Se um cair, o outro continua.

| Eleição | Código | Cargo | Abrangência |
|---|---|---|---|
| `2026-t2-federal` | 6258 | presidente (Flávio Bolsonaro × Lula) | BR, exterior, 27 UFs, todos os municípios |
| `2026-t2-estadual` | 6260 | governador | só AC, AM, DF, ES, RJ, RN, TO |

---

## Entre 19 e 24/10 — ensaio na imagem nova do Actions

O GitHub troca `ubuntu-latest` para **Ubuntu 26 a partir de 19/10**. O workflow usa
`ubuntu-latest` (fixar `ubuntu-24.04` foi tentado em 05/10 e o job ficou sem runner).
Depois do dia 19, rodar o ensaio de novo: **Actions → coleta → Run workflow** com
`ambiente: oficial`, `eleicoes: ensaio`, `duracao: 900`, `municipios` marcado. Tem de
terminar verde, com o artefato e com arquivos novos no R2. Se falhar, há tempo de corrigir.

## Na véspera (ou na manhã de 25/10)

1. **Arquivos do 2º turno já existem?** (só 2 requisições)
   ```
   curl -s -o /dev/null -w '%{http_code}\n' https://resultados.tse.jus.br/oficial/ele2026/6258/dados/br/br-c0001-e006258-u.json
   curl -s -o /dev/null -w '%{http_code}\n' https://resultados.tse.jus.br/oficial/ele2026/6260/config/mun-e006260-cm.json
   ```
   - `200`: marcar `confirmado = true` nas duas eleições de 2º turno em `config/eleicoes.toml`.
   - `404`: normal até perto da eleição. O coletor aguenta (backoff em 404 e lista de
     municípios de reserva). Não é bloqueante.
2. **UFs do governador.** Quando o `ele-c.json` trouxer a eleição 6260, conferir se as UFs
   em `abr` são as mesmas do campo `ufs` de `2026-t2-estadual`:
   ```
   curl -s https://resultados.tse.jus.br/oficial/comum/config/ele-c.json \
     | python3 -c "import json,sys; d=json.load(sys.stdin); print([[a['cd'] for a in e['abr']] for p in d['pl'] for e in p['e'] if e['cd']=='6260'])"
   ```
   Se diferir, corrigir `ufs` no toml, commitar e dar push (o Actions usa o código da `main`).
3. **Testes:** `source .venv/bin/activate && python -m pytest -q` → tudo verde.
4. **Disco:** `df -h .` → pelo menos **3 GB livres**. No ensaio geral, cada snapshot ocupou ~8 KB
   em disco (dado + metadado, cada um no mínimo um bloco); se o TSE regerar os arquivos a cada
   ciclo, a noite inteira pode chegar a ~200 mil snapshots e ~1,7 GB. A pasta `SYNC_PASTA` ocupa
   o mesmo tanto no disco em que estiver.
5. **Base da projeção presente** (1º turno por município, carregado em 05/10):
   ```
   ls data/parquet/snapshot_totais/ data/parquet/municipios.parquet
   ```
   Se faltar (máquina nova), recarregar — ~10 min, ~11 mil requisições a 20/s:
   ```
   source .venv/bin/activate
   python -m apuracao.coletor --ambiente oficial --eleicao 2026-t1-federal --eleicao 2026-t1-estadual --municipios --uma-vez
   python -m apuracao.modelo municipios --cm "$(ls data/raw/6257/config/mun-e006257-cm/*.json.gz | tail -1)"
   python -m apuracao.modelo construir
   ```
   Opcional: curva de 2022 para comparar no "Caminho da apuração" (66 MB, ~1 min; não é rede do
   TSE de resultados, é o CDN de dados abertos):
   ```
   ls data/parquet/hist_boletim/2022_t2.parquet || python -m apuracao.carga --boletim 2022:2
   ```
6. **Secrets do R2** no GitHub (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_BUCKET`). Só o Actions usa; o Mac não precisa das variáveis `R2_*`.
7. **Pasta da cópia local (`SYNC_PASTA`).** De preferência num **disco externo** (protege contra
   falha do disco do Mac). Conectar e conferir que o caminho existe, por exemplo:
   ```
   ls /Volumes/NOME_DO_DISCO && mkdir -p /Volumes/NOME_DO_DISCO/eleicao-copia
   ```
   Sem disco externo, usar uma pasta fora do repositório (ex.: `~/eleicao-copia`): protege contra
   apagar `data/raw` por engano, mas não contra falha do disco, e dobra o espaço usado no Mac.
8. **Mac na tomada**, rede estável. O `coleta.sh` já impede o repouso (`caffeinate`);
   o Mac dorme após 1 minuto ocioso se o script não estiver rodando.

---

## 16h30 — disparar a coleta

### 1. GitHub Actions

Aba **Actions → coleta → Run workflow**:

| Campo | Valor |
|---|---|
| ambiente | `oficial` |
| eleicoes | **`segundo-turno`** ⚠️ o padrão do formulário é `ensaio` |
| duracao | `20400` (5h40, o máximo que cabe nas 6h do Actions) |
| municipios | marcado |

Dois crons de reserva disparam sozinhos com `segundo-turno`: às **16h45** e às **21h45**
(este cobre a apuração depois das ~22h10, quando o primeiro run atinge o limite de 5h40).
Se rodarem junto com o disparo manual, não tem problema: snapshots têm nome por horário de
coleta e nunca se sobrescrevem. Se o cron das 21h45 não aparecer na aba Actions até ~21h50
(o cron do GitHub pode atrasar), disparar um run manualmente.

### 2. Cópia local (3 terminais)

```
# terminal 1 — coleta + cópia para SYNC_PASTA (sem R2)
cd ~/repos/eleicao && source .venv/bin/activate
R2_BUCKET= SYNC_PASTA=/Volumes/NOME_DO_DISCO/eleicao-copia \
  AMBIENTE=oficial ELEICOES="2026-t2-federal 2026-t2-estadual" MUNICIPIOS=1 DURACAO=28800 scripts/coleta.sh

# terminal 2 — tabelas para o painel + verificação de anomalias (também grava em logs/modelo.jsonl)
cd ~/repos/eleicao && source .venv/bin/activate
mkdir -p logs && python -m apuracao.modelo construir --loop 15 2>&1 | tee -a logs/modelo.jsonl

# terminal 3 — painel (abre em http://localhost:8501)
cd ~/repos/eleicao && source .venv/bin/activate
APURACAO_ELEICAO=2026-t2-federal streamlit run apuracao/app/main.py
```

`R2_BUCKET=` (vazio) é de propósito: se `R2_BUCKET` estiver definida, o `coleta.sh` envia ao R2
e **ignora** `SYNC_PASTA`. Vazia, a cópia vai só para a pasta, mesmo que as variáveis `R2_*`
tenham ficado exportadas no terminal. Ao iniciar, o log não pode mostrar
`aviso: sem R2_BUCKET nem SYNC_PASTA`.

---

## O que é normal ver

- **Selo no topo do painel ("Apuração ao vivo"):** verde = coleta local ativa; amarelo = de 2 a 5 min
  sem atividade; vermelho = parada (ver "Coleta local parou" abaixo). Ele lê `logs/coletor.jsonl`.

- **Antes das 17h:** os 36 arquivos de BR/exterior/UF dão `404` (evento `"ausente"` no log)
  e o intervalo entre tentativas cresce até 5 min. **Nenhum município é consultado** até o
  arquivo da UF dele existir (porteiro): isso evita milhares de 404, que podem bloquear o IP.
- **Quando o TSE publica:** evento `"publicado"`, depois `"snapshot"` a cada mudança; os
  municípios daquela UF entram em seguida, em ciclos de 10 min (~10 req/s no total).
- **A cada minuto,** uma linha `"resumo"`:
  ```
  grep '"resumo"' logs/coletor.jsonl | tail -1
  ```
  `requisicoes`, `snapshots`, `nao_mod` (304), `ausente` (404), `erros`. Erros esporádicos
  de rede/timeout são normais (backoff com nova tentativa).
- **Anomalias nos dados do TSE:** o normal é **nenhuma**. O terminal 2 verifica a cada passada
  e registra `"anomalia"` em `logs/modelo.jsonl`; o painel mostra um aviso amarelo com a lista.
  ```
  grep '"anomalia"' logs/modelo.jsonl | tail -5
  ```
- **Painel:** a projeção aparece em *presidente → BR* e em *governador → cada UF*. Em presidente,
  o gráfico "ao longo da noite" mostra a evolução da projeção; o terminal 2 grava cada cálculo em
  `data/projecao/historico.jsonl` (base para avaliar a projeção depois da eleição).
  Antes de haver municípios contados, a faixa é larga e a projeção ≈ 1º turno — esperado.
- **Caminho da apuração** (presidente → BR): quanto falta contar por região, quanto o candidato que
  está atrás precisa do que falta e a chance de virada, além da curva de 2022 (boletim de urna) para comparar.
  O horário da virada só aparece com ritmo medido em todas as UFs e mais de 10% das seções; ele
  tende a sair 20-30 min cedo se a virada ocorrer no fim. Confie mais no "quanto falta" que no horário.

---

## Se algo der errado

| Sintoma | O que fazer |
|---|---|
| Coleta local parou (selo vermelho no painel; erro, terminal fechado, Mac reiniciou) | Rodar o mesmo comando de novo. Retoma ETag e hash do disco; não duplica nem sobrescreve nada. |
| Run do Actions falhou ou foi cancelado | Disparar novo run (`segundo-turno`). Verificar a anotação de erro no resumo do run. A cópia local segue enquanto isso. |
| Muitos erros seguidos, `429`/`403`, ou nenhum snapshot com o site do TSE no ar | Possível bloqueio de IP (o TSE bloqueia **10 min** e reinicia o prazo a cada tentativa). Não reiniciar em sequência. Baixar `max_req_s` no toml (ex.: 10) e reiniciar a coleta **uma vez** depois de 10 min. O Actions usa outro IP. |
| `"config_municipios_reserva"` no log | O `-cm.json` do 2º turno ainda não existia: usou a lista do repositório. Normal. |
| `"config_municipios_indisponivel"` no log | Sem lista de municípios: coleta só BR/UF e **a projeção fica sem dados novos**. Verificar se `config/municipios-reserva-2026.json` existe e reiniciar a coleta. |
| Cópia local com erros (`sync_erro` em `logs/sync.jsonl`), ex.: disco externo desconectou | A coleta continua normalmente em `data/raw` (a cópia roda em outro processo). Reconectar o disco: o sincronizador tenta de novo a cada 15 s e copia o que faltou. |
| R2 sem arquivos novos | É o Actions que envia: ver se o run está rodando e sem anotação de erro. Os snapshots também ficam no artefato do run. |
| Painel diz "Sem dados" | O terminal 2 (`modelo construir --loop 15`) está rodando? Há arquivos em `data/parquet/snapshot_totais/`? |
| Painel sem a seção de projeção | Selecionar eleição de 2º turno; presidente só aparece em *BR*, governador só por UF. Conferir a base do 1º turno (véspera, item 5). |
| Painel com erro depois de mexer no código | Reiniciar o Streamlit (Ctrl+C e rodar de novo): o recarregamento automático não relê módulos importados. |
| Governador em UF fora das 7 configuradas | O TSE mudou a lista: corrigir `ufs` em `2026-t2-estadual`, reiniciar a coleta local, commitar e disparar novo run no Actions. |
| Painel avisa **anomalia nos dados do TSE** | É do lado do TSE; a coleta continua e o snapshot citado em `arquivo_raw` é a prova. Anotar o horário. `versao_antiga` isolada = servidor do CDN desatualizado; inofensiva (o painel usa sempre a versão mais nova). `votos_diminuem` / `secoes_diminuem` = o TSE corrigiu ou errou algo: os números do painel e a projeção podem oscilar naquele trecho. `validos_nao_batem` / `comparecimento_excede` / `soma_ufs_nao_bate` = arquivo incoerente: desconfiar daquele arquivo até a próxima versão. Não reiniciar nada por causa disso. |
| Disco quase cheio | Liberar espaço fora do repositório. **Não** apagar `data/raw` (é a fonte da verdade). |

---

## Como ler a projeção

- **Presidente:** modelo por município (1º turno + deslocamento observado nos já apurados da
  mesma UF/região). Backtest em 2022: erro médio 0,09 ponto, máximo 0,51; faixa de 90% conteve
  o resultado em todos os cenários. Ler o % parcial nacional, no mesmo backtest, errou até
  13,7 pontos (Nordeste chegando por último).
- **Governador:** bem menos previsível. Backtest em 2022: com 25% contado, erro médio 3,2 pontos
  e faixa de ~9; a faixa conteve o resultado em ~87% dos casos. **Use a faixa, não só a mediana.**
- O modelo supõe que, num município parcial, as seções já apuradas representam o município.

---

## Depois da noite

1. Deixar a coleta rodar até a **totalização final** (`totalização final: sim` no painel) ou
   até o fim da duração.
2. Baixar o artefato `snapshots-<run_id>` de cada run do Actions (fica 90 dias).
3. Conferir no R2 que há arquivos em `raw/6258/` e `raw/6260/`.
4. **Só se o Actions tiver falhado** (R2 incompleto): enviar a cópia local ao R2, com as
   variáveis `R2_*` exportadas, `python -m apuracao.coletor.sync`. Custa ~2 operações de escrita
   por snapshot e, na primeira vez, envia também o 1º turno que já está em `data/raw`
   (~46 mil operações).
5. Nada de `data/` vai para o git.
