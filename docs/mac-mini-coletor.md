# Mac mini como coletor reserva

Terceira cópia da coleta, numa máquina separada, para o caso de o Mac principal travar,
reiniciar ou perder a rede no meio da noite. O Mac mini **só coleta**: não roda painel,
construção de tabelas nem projeção. Ele tem macOS Catalina (10.15, Intel), e o `pyarrow`
(Parquet) exige macOS 12 ou mais novo.

Conferido em 06/10/2026 neste Mac, num ambiente Python vazio só com `requirements-coletor.txt`:
45 testes do coletor, da sincronização e da configuração passaram, e o `coleta.sh` rodou de
ponta a ponta (27 requisições, 27 snapshots, cópia para pasta). O bash do Catalina (3.2) é o
mesmo do Mac principal, onde o ensaio geral rodou. **Ainda não foi testado no próprio Mac
mini**: é o passo 4 abaixo.

## Instalação (uma vez, antes de 19/10)

1. **Hora certa.** Ajustes do Sistema → Data e Hora → "Definir data e hora automaticamente"
   ligado. O nome de cada snapshot é o horário da coleta.
2. **Python 3.12.** O Homebrew não suporta mais o Catalina; use o instalador oficial:
   em https://www.python.org/downloads/macos/ baixe o "macOS 64-bit universal2 installer" da
   versão 3.12 mais recente que tenha instalador (versões só de segurança saem sem instalador).
   Depois de instalar, rode "Install Certificates.command" na pasta /Applications/Python 3.12.
   Confira: `python3.12 --version`.
3. **Código.** Com git (pede as Command Line Tools na primeira vez: `xcode-select --install`):
   ```
   mkdir -p ~/repos && cd ~/repos
   git clone https://github.com/chicodevbr/argos.git eleicao && cd eleicao
   python3.12 -m venv .venv
   .venv/bin/pip install -r requirements-coletor.txt
   ```
   Sem git: baixe o zip do repositório no GitHub (Code → Download ZIP) e descompacte em
   `~/repos/eleicao`. Para atualizar depois, baixe de novo.
4. **Teste.**
   ```
   .venv/bin/python -m pytest -q tests/test_coletor.py tests/test_sync.py tests/test_config.py
   ```
   Tudo verde. Depois, uma coleta curta do 1º turno (encerrado; ~30 requisições):
   ```
   AMBIENTE=oficial ELEICOES="2026-t1-federal" DURACAO=25 SYNC_PASTA=/tmp/teste-copia scripts/coleta.sh
   tail -2 logs/coletor.jsonl     # "resumo" com snapshots > 0 e erros 0, depois "fim"
   rm -rf data/raw logs /tmp/teste-copia
   ```
5. **Energia.** Ajustes → Economia de Energia: "Impedir que o computador entre em repouso
   automaticamente" ligado; atualizações automáticas do macOS desligadas até 26/10 (evita
   reinício no meio da noite). O `coleta.sh` também segura o repouso enquanto roda.
6. **Rede.** De preferência no cabo. Se der, numa conexão diferente da do Mac principal (ex.:
   roteador do celular): aí a reserva cobre também a queda da internet de casa. Na mesma rede,
   as duas máquinas juntas fazem até 40 requisições/s pelo mesmo IP, abaixo do limite de 100/s
   do TSE.

**Rede entre as máquinas.** Em 06/10 o Mac mini e o Mac principal não se enxergavam na rede de casa
(isso não afeta a coleta, que sai pela internet). Para diagnosticar, rode em cada máquina, passando o
IP da outra: `scripts/diagnostico-rede.sh 192.168.0.78` (no Mac mini). Só lê, não muda nada.

## Na noite (25/10)

Às **16h30**, junto com o Mac principal:
```
cd ~/repos/eleicao && git pull      # mesma versão do código e do config (código 6258 confirmado)
AMBIENTE=oficial ELEICOES="2026-t2-federal 2026-t2-estadual" MUNICIPIOS=1 DURACAO=28800 \
  SYNC_PASTA=/Volumes/NOME_DO_DISCO/eleicao-copia-macmini scripts/coleta.sh
```
- Sem disco externo, `SYNC_PASTA=~/eleicao-copia`.
- Para aliviar o TSE (mesma rede do Mac principal e preferir margem), tire `MUNICIPIOS=1`: o
  Mac mini coleta só Brasil e UFs, que é o que o painel mostra; os municípios vêm do Mac
  principal e do Actions.
- Para conferir de vez em quando: `tail -1 logs/coletor.jsonl` deve ter horário de menos de
  1 min atrás (o coletor grava um "resumo" a cada 60 s).

## Depois da noite: juntar com o Mac principal

Os snapshots nunca colidem (cada nome é o horário da coleta). No Mac principal, com o disco do
Mac mini (ou a pasta copiada) conectado:
```
rsync -a --ignore-existing /Volumes/NOME_DO_DISCO/eleicao-copia-macmini/raw/ data/raw/
source .venv/bin/activate && python -m apuracao.modelo construir
```
A cópia guarda tudo sob `raw/` (por isso o `/raw/` no fim da origem: os arquivos precisam
cair em `data/raw/{eleicao}/{uf}/...`, não em `data/raw/raw/...`). Para conferir antes, troque
`-a` por `-an` (só lista). A construção reconhece cada snapshot pelo caminho e só processa os novos. Uma mesma versão de
arquivo coletada pelas duas máquinas entra duas vezes, com horários de coleta diferentes; as
consultas usam sempre a versão mais recente do TSE.

**Durante a noite**, se o Mac principal cair de vez: o painel pode passar a ler os dados do Mac
mini. Copie a pasta do Mac mini para o Mac principal (ou para outra máquina com o projeto
completo), rode o `rsync` acima e o `construir --loop 15`.
