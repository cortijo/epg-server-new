# Spec: Grade horizontal da programação

- ID: `2026-08-25-grade-horizontal-programacao`
- Estado: `implantada/concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-25`
- Última atualização: `2026-08-25`
- Issue/commit relacionado: `151c1b70b7b9fb99fb4711a43cae438c291fa042`

## 1. Resumo

Adicionar ao painel do EPG Stream uma visão consolidada da programação em
formato de linha do tempo, semelhante a um guia eletrônico: canais nas linhas,
horários nas colunas e programas dimensionados pela duração. O módulo reutiliza
o endpoint de guia atual e não altera a emissão multicast.

## 2. Contexto e comportamento atual

- A tabela principal expande uma portadora e mostra apenas atual/próximo em
  cartões lineares.
- A grade diária completa só aparece canal por canal em um modal.
- Não existe comparação visual simultânea dos canais ao longo do horário.

## 3. Objetivos

- [x] Disponibilizar uma grade horizontal acessível pelo cabeçalho do painel.
- [x] Permitir selecionar portadora e deslocar a janela de horário.
- [x] Mostrar logo, canal, SID, programas e posição do horário atual.
- [x] Manter o painel utilizável em telas menores com rolagem horizontal.

## 4. Fora de escopo

- Alterar XMLTV, API, persistência ou transporte MPEG-TS.
- Alterar PIDs, SID, TSID, ONID, multicast ou processos emissores.
- Implantar anúncios ou conteúdo editorial externo.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Botão “Grade de programação” abre o módulo. | obrigatória |
| RF-02 | Cada canal ocupa uma linha e cada programa um bloco proporcional. | obrigatória |
| RF-03 | Janela usa divisões de 30 minutos e navegação anterior/agora/próxima. | obrigatória |
| RF-04 | Seletor alterna entre portadoras sem recarregar a página. | obrigatória |
| RF-05 | Logo autenticado aparece quando cadastrado. | desejável |
| RF-06 | Clique no programa abre seus detalhes. | desejável |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Não reiniciar nem modificar emissores. |
| RNF-02 | Escapar todo texto vindo do XMLTV. |
| RNF-03 | Fazer no máximo uma consulta de guia por portadora e janela de cache. |
| RNF-04 | Manter compatibilidade com desktop e mobile. |

## 7. Critérios de aceite

- [x] CA-01 — Dadas várias atrações, os blocos respeitam início, fim e duração.
- [x] CA-02 — A navegação desloca a janela em 90 minutos e “Agora” a recentraliza.
- [x] CA-03 — A troca de portadora mostra somente seus serviços.
- [x] CA-04 — Canal sem programação exibe estado vazio sem quebrar a linha.
- [x] CA-05 — O módulo fecha explicitamente e não altera qualquer configuração.

## 8. Contratos afetados

### API

Nenhum contrato novo. Reuso de `GET /api/guide?carrier_id=ID` e
`GET /api/logo?carrier_id=ID&service_id=ID`.

### Configuração e persistência

Não se aplica.

### Mídia e rede

Não se aplica; nenhuma mudança no multicast.

## 9. Desenho técnico

### Antes

```text
tabela da portadora -> expandir -> atual/próximo -> modal diário por canal
```

### Depois

```text
cabeçalho -> Grade de programação -> selecionar portadora
         -> /api/guide -> janela temporal horizontal por todos os canais
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `epg-product/app.py` | CSS, HTML e JavaScript da grade |
| `epg-product/tests/test_app.py` | contratos básicos da interface |
| `epg-product/README.md` | registrar o novo módulo |
| `DOCUMENTACAO_EPG_PRODUTO.md` | atualizar funcionalidades do painel |

### Decisões e alternativas

- Janela inicial: 3 horas, alinhada ao intervalo de 30 minutos atual.
- Navegação: passos de 90 minutos para preservar contexto.
- O módulo é um modal amplo e rolável, sem nova rota ou schema.
- A grade usa o cache atual do frontend e consulta sob demanda.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| grade muito larga | alta | médio | rolagem horizontal e coluna do canal fixa |
| programa cruzar a janela | média | baixo | recortar início/fim no limite visível |
| texto XMLTV quebrar HTML | baixa | alto | usar `esc()` em todo conteúdo |
| muitas consultas | média | médio | cache por portadora e consulta somente selecionada |

## 11. Plano de implementação

- [x] Mapear painel e resposta atual de `/api/guide`.
- [x] Implementar CSS, controles, linha do tempo e detalhes.
- [x] Adicionar testes automatizados do contrato visual.
- [x] Atualizar documentação.
- [x] Validar localmente e no navegador.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | unittest | local | suíte `epg-product/tests` | 12/12 aprovados | passou |
| T-02 | sintaxe | local | `py_compile` e `node --check` | sem erro | passou |
| T-03 | visual | navegador | abrir, navegar, trocar portadora e fechar | funcional | passou |
| T-04 | responsivo | navegador | viewport 390×844 | coluna sticky e scroll 1120/374 px | passou |
| T-05 | regressão | local | `git diff --check` e revisão | sem alteração de API/mídia | passou |

## 13. Plano de implantação

- Imagem implantada: `tvstream-epg:v1.5.0-20260825`.
- A imagem foi validada isoladamente na porta 19104 antes da substituição.
- O container e os dados da v1.4.0 foram preservados para rollback.

## 14. Plano de rollback

- Reativar o container/imagem v1.4.0 preservado.
- O schema não muda, portanto não há restauração de dados.
- Acionar rollback se painel, autenticação ou health regredirem.

## 15. Observabilidade

- `/health` deve continuar 200.
- Nenhum processo emissor deve reiniciar ao abrir a grade.
- Console do navegador e requests não devem apresentar erro.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-25 | diagnóstico | API atual já fornece a programação necessária; mudança limitada ao frontend |
| 2026-08-25 | implementação | janela de 3 h, passo de 90 min, filtro, marcador, detalhes e responsividade adicionados |
| 2026-08-25 | correção preventiva | botão permanece desativado até o estado das portadoras ser carregado |
| 2026-08-25 | validação | unittest, Python, JavaScript, desktop, navegação, detalhes e mobile aprovados |
| 2026-08-25 | release | commit `151c1b7`, tag `epg-v1.5.0` e imagem imutável construídos |
| 2026-08-25 | teste isolado | `/health` retornou 1.5.0, interface contém a grade e o container permaneceu com zero reinícios |
| 2026-08-25 | implantação | container `epg-stream` atualizado; health 200, zero reinícios e 27 emissores ativos na verificação final |

## 17. Resultado final

- Estado final: `implantado e concluído`
- Critérios de aceite: `5/5 concluídos`
- Testes executados: `T-01 a T-05 aprovados`
- Resultado da produção: `health 200; versão 1.5.0; zero reinícios; 27 emissores ativos na verificação final`
- Imagem implantada: `tvstream-epg:v1.5.0-20260825` (`sha256:725f34d2c38cda021706a0354dc359646b3ace316b25746c7da135b8971cb4ac`)
- Rollback preservado: `epg-stream-pre-v1.5.0-20260825` e `/srv/epg-stream-backup-pre-v1.5.0-20260825`
- Commit: `151c1b70b7b9fb99fb4711a43cae438c291fa042`
- Tag: `epg-v1.5.0`
- Pull request/URL: `https://github.com/cortijo/TVstream/commit/151c1b70b7b9fb99fb4711a43cae438c291fa042`
- Pendências: nenhuma.
