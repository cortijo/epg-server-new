# Spec: Handoff completo do EPG Stream

- ID: `2026-08-25-handoff-completo-epg-stream`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-25`
- Última atualização: `2026-08-25`
- Issue/commit relacionado: `20e43cc`

## 1. Resumo

Consolidar em um documento autoritativo tudo o que foi desenvolvido no produto
EPG Stream até a versão 1.4.0, incluindo arquitetura, persistência, API,
interface, transporte ISDB-TB, logotipos ARIB, integração Dexing, testes,
diagnóstico, segurança, versionamento, imagem Docker, deploy e rollback. O
objetivo é permitir que outro agente continue o desenvolvimento sem depender do
histórico desta conversa.

## 2. Contexto e comportamento atual

- A documentação existente descreve boa parte do produto, mas contém trechos
  históricos da v1.2.2 e não consolida todos os contratos da v1.4.0.
- O produto está implantado como `tvstream-epg:v1.4.0-20260825`.
- O runbook geral continua em `GUIA_OPERACIONAL_AGENTES.md`.

## 3. Objetivos

- [x] Registrar todos os componentes e fluxos da versão 1.4.0.
- [x] Documentar contratos que não podem ser quebrados.
- [x] Dar a outro agente um roteiro reproduzível de análise, teste e deploy.
- [x] Remover informações operacionais obsoletas do documento autoritativo.

## 4. Fora de escopo

- Alterar código, configuração persistida, container ou multicast.
- Expor senhas, URLs privadas, hashes reais ou configuração de produção.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Cobrir painel, API, dados, supervisão e emissão. | obrigatória |
| RF-02 | Cobrir XMLTV por canal, clonagem, usuários e logos. | obrigatória |
| RF-03 | Incluir validação TS, integração Dexing e troubleshooting. | obrigatória |
| RF-04 | Incluir workflow Git/Docker/deploy/rollback para agentes. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Não conter segredos ou configuração integral de produção. |
| RNF-02 | Ser coerente com o código e a versão implantada. |
| RNF-03 | Usar exemplos sanitizados e destinos de laboratório. |

## 7. Critérios de aceite

- [x] CA-01 — Todos os endpoints atuais estão documentados.
- [x] CA-02 — O modelo de dados inclui fonte por canal, logo e versão de sinalização.
- [x] CA-03 — PIDs/tabelas/carrosséis e logo ARIB estão explicados.
- [x] CA-04 — Outro agente possui checklist de mudança e matriz de testes.
- [x] CA-05 — `git diff --check`, revisão de links e auditoria de segredos passam.

## 8. Contratos afetados

Somente documentação. Nenhum contrato de API, persistência ou mídia muda.

## 9. Desenho técnico

Atualizar `DOCUMENTACAO_EPG_PRODUTO.md` como fonte de verdade detalhada e
referenciá-la no README do produto.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Comando destrutivo ser copiado sem contexto | baixa | alto | separar leitura, teste, deploy e rollback com alertas. |
| Exemplo vazar dado real | baixa | alto | placeholders e valores de laboratório. |
| Documento divergir do código | média | médio | conferir rotas, constantes e histórico atual. |

## 11. Plano de implementação

- [x] Ler documentação, specs, rotas, constantes e histórico.
- [x] Reescrever documento autoritativo.
- [x] Atualizar índice do README.
- [x] Validar conteúdo, links e segredos.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Formatação | local | `git diff --check` | sem erro | passou |
| T-02 | Cobertura | local | comparar rotas/constantes/histórico | 21/21 rotas e v1.4.0 coerentes | passou |
| T-03 | Segurança | local | busca de credenciais e IPs reais | nenhum valor real | passou |
| T-04 | Links | local | conferir caminhos referenciados | 14/14 existentes | passou |

## 13. Plano de implantação

Não se aplica: alteração documental. Após validação, commit e push em
`origin/main` conforme política do repositório.

## 14. Plano de rollback

Reverter apenas o commit documental se houver informação incorreta. Não tocar
no container `epg-stream` nem no volume `/srv/epg-stream`.

## 15. Observabilidade

Não se aplica à execução do produto. O documento deve registrar os comandos de
observação existentes.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-25 | inventário | rotas, constantes, Docker, tags e specs v1.0.0–v1.4.0 conferidos |
| 2026-08-25 | consolidação | documento autoritativo ampliado para arquitetura, dados, API, XMLTV por canal, logo ARIB, painel, Dexing, testes, release, deploy e rollback |
| 2026-08-25 | validação | 21/21 rotas, 14/14 caminhos, versão 1.4.0, diff e revisão de segredos aprovados |

## 17. Resultado final

- Estado final: `concluído`
- Critérios de aceite: `5/5 concluídos`
- Testes executados: `T-01 a T-04 aprovados`
- Resultado da produção: `não alterada`
- Imagem implantada: `não se aplica`
- Rollback preservado: `não se aplica`
- Commit: `20e43cc` (conteúdo) e commit de fechamento desta spec
- Tag: `não se aplica`
- Pull request/URL: `origin/main`
- Pendências: nenhuma para esta alteração documental.
