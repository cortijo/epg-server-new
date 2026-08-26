# Licenciamento online por quantidade de canais

- ID: `2026-08-26-licenciamento-por-canais`
- Estado: `validado para implantação`
- Responsável: Codex
- Solicitante: Julio Cortijo
- Criada em: 2026-08-26
- Última atualização: 2026-08-26
- Issue/commit relacionado: a preencher

## 1. Resumo

Ocultar do painel os controles e miniaturas de logotipo sem remover a estrutura
de dados ou a sinalização já existente, criar um servidor de licenças em imagem
Docker independente e exigir uma licença online válida com limite de canais para
que o EPG Stream opere seus emissores.

## 2. Contexto e comportamento atual

- O painel expõe upload, remoção e miniaturas de logo; backend e emissor suportam
  a estrutura completa.
- O EPG Stream não possui licenciamento.
- Produção possui 27 portadoras e 63 canais na versão 1.9.0.

## 3. Objetivos

- [x] Ocultar toda a interface visual de logo mantendo API, dados e transporte.
- [x] Administrar chaves aleatórias, revogação e limite de canais em serviço separado.
- [x] Bloquear emissão e mutações acima do limite quando a licença não for válida.
- [x] Preservar diagnóstico autenticado e rollback integral.

## 4. Fora de escopo

- Cobrança, gateway de pagamento, revenda e telemetria de audiência.
- Remover arquivos, endpoints ou PSI/SI de logo já existentes.
- Alterar PIDs, TSID, ONID, SID, XMLTV ou formato do multicast.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | O servidor de licenças gera uma chave exibida somente na criação e persiste apenas seu SHA-256. | obrigatória |
| RF-02 | Cada licença define nome, limite de canais, status, instalação e validade opcional. | obrigatória |
| RF-03 | O EPG consulta o servidor com chave, instalação e total de canais. | obrigatória |
| RF-04 | Chave ausente/inválida/revogada/expirada ou excesso bloqueia todos os emissores. | obrigatória |
| RF-05 | POST de portadora que ultrapasse o limite retorna erro sem alterar dados. | obrigatória |
| RF-06 | O painel informa situação e consumo da licença, sem exibir a chave. | obrigatória |
| RF-07 | Controles, upload e miniaturas de logo deixam de aparecer no painel. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Chaves não aparecem em logs, API de estado ou Docker inspect. |
| RNF-02 | Servidor persiste JSON atomicamente e protege administração com Basic Auth. |
| RNF-03 | Falha de licenciamento não encerra o painel, mas health fica degradado. |
| RNF-04 | A licença é revalidada periodicamente e antes de start/restart/salvar portadora. |
| RNF-05 | Não alterar o transporte MPEG-TS das licenças válidas. |

## 7. Critérios de aceite

- [x] CA-01 — Chave válida para 100 canais libera os 63 canais existentes.
- [x] CA-02 — Sem chave ou com chave revogada, nenhum emissor permanece ativo.
- [x] CA-03 — Uma alteração que excede o limite é recusada antes de persistir.
- [x] CA-04 — A chave completa aparece somente na resposta de criação do servidor.
- [x] CA-05 — O painel desktop/móvel não contém controles ou miniaturas de logo.
- [x] CA-06 — As APIs e estruturas de logo continuam existentes e o TS não é alterado.

## 8. Contratos afetados

### API do servidor de licenças

- `GET /health`
- `GET /api/licenses` autenticado
- `POST /api/licenses` autenticado
- `POST /api/licenses/revoke` autenticado
- `POST /api/validate` com chave, instalação e quantidade de canais

### API do EPG Stream

- `GET /health` passa a refletir a licença.
- `GET /api/license` retorna somente estado público da licença.
- `POST /api/carriers` e start/restart exigem licença válida e capacidade.

### Configuração

- `EPG_LICENSE_SERVER_URL`
- `EPG_LICENSE_KEY_FILE`
- `EPG_LICENSE_INSTALLATION_ID`
- `EPG_LICENSE_CHECK_SECONDS`

### Mídia e rede

- Nenhum PID ou payload MPEG-TS é alterado quando a licença está válida.

## 9. Desenho técnico

```text
license-server (9200, volume próprio)
  -> chave aleatória armazenada como hash
  -> POST /api/validate
EPG Stream (9100)
  -> lê chave de arquivo secreto
  -> valida total de services
  -> supervisor inicia ou bloqueia todos os emissores
```

Arquivos previstos: `license-server/*`, `epg-product/license_client.py`,
`epg-product/app.py`, testes, Docker/compose e documentação.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| indisponibilidade do servidor | média | alto | painel permanece acessível, estado explícito e rollback |
| vazamento da chave | baixa | alto | arquivo montado, hash no servidor, sem logs |
| contagem divergente | baixa | alto | contar exclusivamente `services` persistidos/propostos |
| corte bloquear produção | baixa | alto | servidor/chave primeiro, candidato com cópia dos dados |

## 11. Plano de implementação

- [x] Mapear fluxo atual e quantidade real.
- [x] Criar servidor de licenças e testes.
- [x] Integrar cliente/licença ao supervisor e API.
- [x] Ocultar logo no HTML/JS.
- [x] Atualizar documentação e imagens.
- [x] Validar candidato isolado.
- [ ] Implantar com rollback.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Resultado esperado | Estado |
|---|---|---|---|---|
| T-01 | servidor de licenças | local/container | gerar, validar, limitar e revogar | aprovado |
| T-02 | EPG válido | isolado | emissores operam dentro do limite | aprovado: 27 portadoras/63 canais, autostart desligado na cópia |
| T-03 | chave ausente/revogada | isolado | emissores bloqueados e painel acessível | aprovado: health 503 |
| T-04 | limite | isolado | persistência recusada sem efeito parcial | aprovado: HTTP 402 |
| T-05 | painel | navegador desktop/móvel | logo ausente e licença visível | aprovado: 1440px e 390x844 |
| T-06 | produção | servidor | 63/100 canais, 27 portadoras e zero erros | pendente |

## 13. Implantação e rollback

- EPG previsto: `epgserver:v1.10.0-20260826`.
- Licenças previsto: `epg-license-server:v1.0.0-20260826`.
- Porta do servidor de licenças: 9200 em loopback/rede administrativa.
- Preservar v1.9.0 e `/srv/epg-stream` antes do corte.
- Preservar `/srv/epg-license-server` e o arquivo secreto do cliente.

## 14. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-26 | diagnóstico | produção com 27 portadoras e 63 canais; v1.9.0 saudável |
| 2026-08-26 | desenho | chave aleatória, hash SHA-256, validação online e limite por `services` |
| 2026-08-26 | testes locais | 39 testes EPG e 2 testes de licença aprovados; JavaScript válido |
| 2026-08-26 | candidato Linux | 27 portadoras/63 canais; revogação 503, mutação 402 e persistência após restart |

## 15. Resultado final

- Estado final: validado para implantação
- Critérios de aceite: 6/6
- Testes, imagens, commit e rollback: a preencher
