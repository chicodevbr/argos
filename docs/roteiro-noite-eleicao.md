# Roteiro da noite da eleição — 2º turno, 25/10/2026

Urnas fecham às **17h (Brasília)**. A coleta roda em **dois lugares ao mesmo tempo**:
o GitHub Actions (sincroniza com o R2 e guarda artefato) e este Mac (cópia local, que
também alimenta o painel). Se um cair, o outro continua.

| Eleição | Código | Cargo | Abrangência |
|---|---|---|---|
| `2026-t2-federal` | 6258 | presidente (Flávio Bolsonaro × Lula) | BR, exterior, 27 UFs, todos os municípios |
| `2026-t2-estadual` | 6260 | governador | só AC, AM, DF, ES, RJ, RN, TO |

---

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
   em disco (dado + metadado); se o TSE regerar os arquivos a cada ciclo, a noite inteira pode
   chegar a ~200 mil snapshots e ~1,7 GB, mais o mesmo tanto em `SYNC_PASTA`, se usada.
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
6. **Secrets do R2** no GitHub (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
   `R2_BUCKET`). Para a cópia local também subir ao R2, deixar as mesmas variáveis exportadas
   no terminal (opcional).
7. **Mac na tomada**, rede estável. O `coleta.sh` já impede o repouso (`caffeinate`);
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

O cron de reserva dispara sozinho às **16h45** com `segundo-turno`. Se os dois rodarem,
não tem problema: snapshots têm nome por horário de coleta e nunca se sobrescrevem.

5h40 a partir de 16h30 cobre até ~22h10. Se a apuração atrasar, disparar **um novo run**
por volta das 21h30 (os dois se sobrepõem um pouco; não há perda).

### 2. Cópia local (3 terminais)

```
# terminal 1 — coleta (+ R2 se as variáveis R2_* estiverem exportadas)
cd ~/repos/eleicao && source .venv/bin/activate
AMBIENTE=oficial ELEICOES="2026-t2-federal 2026-t2-estadual" MUNICIPIOS=1 DURACAO=28800 scripts/coleta.sh

# terminal 2 — tabelas para o painel
cd ~/repos/eleicao && source .venv/bin/activate
python -m apuracao.modelo construir --loop 15

# terminal 3 — painel (abre em http://localhost:8501)
cd ~/repos/eleicao && source .venv/bin/activate
APURACAO_ELEICAO=2026-t2-federal streamlit run apuracao/app/main.py
```

---

## O que é normal ver

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
- **Painel:** a projeção aparece em *presidente → BR* e em *governador → cada UF*.
  Antes de haver municípios contados, a faixa é larga e a projeção ≈ 1º turno — esperado.

---

## Se algo der errado

| Sintoma | O que fazer |
|---|---|
| Coleta local parou (erro, terminal fechado, Mac reiniciou) | Rodar o mesmo comando de novo. Retoma ETag e hash do disco; não duplica nem sobrescreve nada. |
| Run do Actions falhou ou foi cancelado | Disparar novo run (`segundo-turno`). Verificar a anotação de erro no resumo do run. A cópia local segue enquanto isso. |
| Muitos erros seguidos, `429`/`403`, ou nenhum snapshot com o site do TSE no ar | Possível bloqueio de IP (o TSE bloqueia **10 min** e reinicia o prazo a cada tentativa). Não reiniciar em sequência. Baixar `max_req_s` no toml (ex.: 10) e reiniciar a coleta **uma vez** depois de 10 min. O Actions usa outro IP. |
| `"config_municipios_reserva"` no log | O `-cm.json` do 2º turno ainda não existia: usou a lista do repositório. Normal. |
| `"config_municipios_indisponivel"` no log | Sem lista de municípios: coleta só BR/UF e **a projeção fica sem dados novos**. Verificar se `config/municipios-reserva-2026.json` existe e reiniciar a coleta. |
| R2 com erros (`sync_erro` em `logs/sync.jsonl`) | Os snapshots continuam em `data/raw`. O sincronizador tenta de novo a cada 15 s; depois da noite, rodar `python -m apuracao.coletor.sync` para enviar o que faltou. |
| Painel diz "Sem dados" | O terminal 2 (`modelo construir --loop 15`) está rodando? Há arquivos em `data/parquet/snapshot_totais/`? |
| Painel sem a seção de projeção | Selecionar eleição de 2º turno; presidente só aparece em *BR*, governador só por UF. Conferir a base do 1º turno (véspera, item 5). |
| Painel com erro depois de mexer no código | Reiniciar o Streamlit (Ctrl+C e rodar de novo): o recarregamento automático não relê módulos importados. |
| Governador em UF fora das 7 configuradas | O TSE mudou a lista: corrigir `ufs` em `2026-t2-estadual`, reiniciar a coleta local, commitar e disparar novo run no Actions. |
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
4. Enviar ao R2 o que a cópia local tiver a mais: `python -m apuracao.coletor.sync`.
5. Nada de `data/` vai para o git.
