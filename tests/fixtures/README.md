# Fixtures

JSONs **reais** do 1º turno de 2026, baixados do ambiente **oficial** em
05/10/2026 (o simulado `resultados-sim.tse.jus.br` estava fora do ar). O 1º
turno já está encerrado, então o conteúdo não muda mais.

Base: `https://resultados.tse.jus.br/oficial/ele2026/`

| Arquivo | Caminho | Conteúdo |
|---|---|---|
| `br-c0001-e006257-u.json` | `6257/dados/br/` | Presidente, Brasil |
| `zz-c0001-e006257-u.json` | `6257/dados/zz/` | Presidente, exterior |
| `sp-c0001-e006257-u.json` | `6257/dados/sp/` | Presidente, SP |
| `sp-c0003-e006259-u.json` | `6259/dados/sp/` | Governador, SP |
| `sp71072-c0001-e006257-u.json` | `6257/dados/sp/` | Presidente, São Paulo capital (TSE 71072) |
| `sp71072-c0003-e006259-u.json` | `6259/dados/sp/` | Governador, São Paulo capital |
| `ac01120-c0001-e006257-u.json` | `6257/dados/ac/` | Presidente, Acrelândia (TSE 01120) |
| `mun-e006257-cm.json` | `6257/config/` | Configuração de municípios, eleição federal |
| `mun-e006259-cm.json` | `6259/config/` | Configuração de municípios, eleição estadual |
| `ele-c.json` | `/oficial/comum/config/` (fora de `ele2026/`) | Configuração de eleições (todos os ciclos); `cdt2` = código do 2º turno |

Observação: `br-c0001-e006257-ab.json` (acompanhamento) respondeu **404**.

Para atualizar, rode o mesmo `curl` com o caminho acima. Não edite os arquivos à mão.
