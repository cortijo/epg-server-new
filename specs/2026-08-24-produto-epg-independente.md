# Spec: produto EPG multicast independente

- ID: `2026-08-24-produto-epg-independente`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-24`
- Última atualização: `2026-08-24`
- Issue/commit relacionado: `9ae4c2a`

## 1. Resumo

Criar um segundo produto e uma segunda imagem Docker contendo apenas o controle,
geração e emissão de EPG multicast ISDB-TB. O produto será isolado do TVStream,
terá persistência, autenticação, fontes XMLTV, portadoras com múltiplos serviços,
supervisão automática e painel próprio com a programação atual e futura de cada
canal em tempo real.

## 2. Contexto e comportamento atual

- Onde o comportamento existe hoje: `TVStreamEpgOnly`, `EpgInjector` e
  `EpgOnlyManager` dentro da imagem completa do TVStream.
- Como reproduzir: a seção Portadoras EPG auxiliares inicia processos filhos do
  emissor na aplicação principal.
- Evidência observada: o fluxo v127 foi validado no Dexing com PAT, PMT, SDT,
  EIT e TDT/TOT, inclusive com vários serviços por portadora.
- Impacto operacional: o EPG ainda depende da imagem e do painel do produto de
  streaming, impedindo sua comercialização como solução separada.

## 3. Objetivos

- [x] Gerar a imagem independente `tvstream-epg:v1-20260824`.
- [x] Preservar o executável e a imagem atuais do TVStream sem mudar seu runtime.
- [x] Permitir CRUD de fontes XMLTV e portadoras com múltiplos serviços.
- [x] Exibir agora, próximo, progresso e grade diária por canal.
- [x] Supervisionar e reiniciar emissores configurados para início automático.
- [x] Documentar arquitetura, API, Docker, validação, deploy e continuidade por IA.

## 4. Fora de escopo

- Transcodificação, vídeo, áudio, playout 24x7, YouTube, SRT, RTMP ou HLS.
- Alteração do container `tvstreamer5` ou de seus arquivos persistentes.
- Implantação desta primeira versão no servidor de produção sem pedido posterior.
- Sistema de licenciamento e cobrança comercial.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Autenticar o painel e a API com credenciais definidas por ambiente. | obrigatória |
| RF-02 | Cadastrar, editar, testar e excluir fontes XMLTV HTTP/HTTPS. | obrigatória |
| RF-03 | Cadastrar portadora, TSID, ONID, multicast, interface, bitrate, TTL e serviços/SIDs. | obrigatória |
| RF-04 | Iniciar, parar, reiniciar e supervisionar cada emissor. | obrigatória |
| RF-05 | Mostrar por serviço a programação atual, próxima e restante do dia. | obrigatória |
| RF-06 | Atualizar a visão operacional automaticamente sem recarregar a página. | obrigatória |
| RF-07 | Persistir configuração em volume Docker e gravar logs por portadora. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Não alterar o caminho operacional do TVStream existente. |
| RNF-02 | Não conter credenciais reais na imagem, no Git ou nos logs. |
| RNF-03 | Manter a emissão ISDB-TB já validada no modulador. |
| RNF-04 | Usar imagem de runtime sem GStreamer e sem transcodificador. |
| RNF-05 | Fazer gravações de configuração de forma atômica. |

## 7. Critérios de aceite

- [x] CA-01 — Dado um volume vazio e credenciais válidas, quando o container
  iniciar, então `/health` responde e o painel autenticado abre.
- [x] CA-02 — Dada uma fonte XMLTV, quando consultada, então o catálogo e a
  grade retornam canais, programa atual, próximo e horários coerentes.
- [x] CA-03 — Dada uma portadora válida, quando iniciada, então o processo
  `TVStreamEpgOnly` permanece ativo e envia datagramas de 1316 bytes.
- [x] CA-04 — Dado um processo emissor encerrado, quando `auto_start=true`,
  então o supervisor registra a falha e o reinicia automaticamente.
- [x] CA-05 — O código e o container do TVStream atual permanecem inalterados;
  a produção v127 continua saudável e sem reinícios.
- [x] CA-06 — A documentação permite a outro agente construir, testar,
  publicar, implantar e reverter o produto sem contexto desta conversa.

## 8. Contratos afetados

### API

- `GET /health` sem autenticação.
- `GET /api/state`, `/api/sources`, `/api/catalog`, `/api/guide` autenticados.
- `POST /api/sources`, `/api/sources/test`, `/api/sources/delete`.
- `POST /api/carriers`, `/api/carriers/start|stop|restart|delete`.
- Erros usam status HTTP 4xx/5xx e JSON `{"error":"..."}`.

### Configuração e persistência

- `/data/epg-product.json`: fontes e portadoras, versão de schema.
- `/data/logs/<id>.log`: saída rotacionada dos emissores.
- Credenciais somente em `EPG_ADMIN_USER` e `EPG_ADMIN_PASSWORD`.

### Mídia e rede

- Entrada: XMLTV HTTP/HTTPS.
- Saída: MPEG-TS EPG-only UDP multicast, 7 × 188 bytes por datagrama.
- Tabelas: PAT, PMT vazia, SDT, EIT p/f, EIT schedule, TDT e TOT.
- Perfil: ISDB-TB / ABNT NBR 15603 conforme núcleo v127.

## 9. Desenho técnico

### Antes

```text
TVStreamer + painel geral -> EpgOnlyManager -> TVStreamEpgOnly -> multicast EPG
```

### Depois

```text
EpgStream Web/API -> configuração + supervisor -> TVStreamEpgOnly -> multicast EPG
                   -> cache XMLTV -> programação agora/próximo/grade
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `epg-product/app.py` | controle, API, painel, XMLTV e supervisão |
| `epg-product/Dockerfile` | segunda imagem, sem runtime multimídia |
| `epg-product/docker-compose.yml` | implantação independente |
| `epg-product/tests/` | testes da configuração, XMLTV e API |
| `epg-product/README.md` | uso do produto |
| `DOCUMENTACAO_EPG_PRODUTO.md` | handoff técnico e operacional completo |
| `CMakeLists.txt` | nenhum ajuste previsto; alvo existente será usado |

### Decisões e alternativas

- Decisão: usar controle em Python padrão e o emissor C++ validado.
- Motivo: reduz dependências, mantém o transporte homologado e isola o produto.
- Alternativa rejeitada: duplicar o painel C++ monolítico do TVStream.
- Por que foi rejeitada: aumentaria acoplamento e custo de manutenção.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| XMLTV muito grande | média | médio | cache, limite de download e parsing incremental |
| Processo emissor morrer | média | alto | supervisor com restart e contador |
| Colisão multicast/porta | média | alto | validação de unicidade e IPv4 multicast |
| Vazamento de URL privada | baixa | alto | API mascara URLs no estado e logs não imprimem segredos |
| Regressão do núcleo EPG | baixa | alto | compilar o mesmo alvo e executar auditoria TS isolada |

## 11. Plano de implementação

- [x] Mapear o caminho atual no código.
- [x] Implementar o produto isolado.
- [x] Implementar programação em tempo real.
- [x] Criar documentação e exemplos sem segredos.
- [x] Construir e validar a imagem em ambiente isolado.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Testes Python | local | unittest | 4 testes aprovados | passou |
| T-02 | Build emissor | Docker | build `TVStreamEpgOnly` | compilou na imagem Ubuntu 24.04 | passou |
| T-03 | API/painel | container isolado | health, auth e CRUD | health 200, sem auth 401, CRUD ativo | passou |
| T-04 | XMLTV e grade | container isolado | BrazilTVEPG | 162 canais; agora/próximo e 40 itens no canal de teste | passou |
| T-05 | UDP/TS | container isolado | captura de 300 datagramas e verificador | 1316 bytes; CRC/continuidade/IDs zero erros | passou |
| T-06 | Regressão TVStream | produção somente leitura | inspect do container original | v127 ativo, restart count zero | passou |

## 13. Plano de implantação

- Imagem/tag prevista: `tvstream-epg:v1-20260824`.
- Container de teste: `tvstream-epg-v1-test`.
- Dados de teste: volume temporário e multicast/porta exclusivos.
- Sequência: build, testes, tag, execução paralela; não tocar no TVStream.
- Verificações: health, auth, estado, PID, datagramas, tabelas e logs.

## 14. Plano de rollback

- Container/imagem anterior: não se aplica; é um produto novo e paralelo.
- Condição: health falha, loop de restart, configuração corrompida ou TS inválido.
- Procedimento: parar/remover apenas `tvstream-epg`; preservar volume para análise.
- Confirmação: `tvstreamer5` permanece inalterado e saudável.

## 15. Observabilidade

- Logs: startup, início/parada/restart do emissor, falha XMLTV e erro UDP.
- Estado: processo, PID, uptime, reinícios, último erro e atualização do guia.
- Sintomas: grade desatualizada, processo inativo, reinícios crescentes.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-24 | início | núcleo v127 e documentação existente confirmados |
| 2026-08-24 | arquitetura | controle independente em Python + emissor C++ validado |
| 2026-08-24 | testes locais | py_compile e 4 unittests aprovados |
| 2026-08-24 | imagem preliminar | `tvstream-epg:v1-20260824`, build concluído |
| 2026-08-24 | integração XMLTV | 162 canais; grade atual/próxima confirmada |
| 2026-08-24 | transporte isolado | 300 datagramas/394800 bytes; PAT/PMT/SDT/EIT/TDT/TOT; zero erros |
| 2026-08-24 | isolamento | container de teste removido; `tvstreamer5:v127-20260824` permaneceu saudável |
| 2026-08-24 | publicação | commit `9ae4c2a` e tag `epg-v1.0.0` enviados ao GitHub |
| 2026-08-24 | imagem final | build a partir da tag; image ID `sha256:8837a2e7e47c...` |
| 2026-08-24 | teste da imagem final | EIT 0x4E/0x50/0x51, zero CRC/continuidade/IDs; guia atual/próxima aprovado |
| 2026-08-24 | implantação autorizada | container `epg-stream` ativado na porta 9100 com volume `/srv/epg-stream` |
| 2026-08-24 | segurança | usuário 10001, rootfs somente leitura, capabilities removidas e credenciais exclusivas |
| 2026-08-24 | firewall | TCP/9100 incluída somente nos conjuntos IPv4/IPv6 confiáveis e configuração recarregada |
| 2026-08-24 | pós-deploy | health externo aprovado; restart/persistência aprovados; v127 permaneceu saudável |

## 17. Resultado final

- Estado final: `concluída`
- Critérios de aceite: `6/6 tecnicamente concluídos`
- Testes executados: `unittest, py_compile, Docker build, HTTP/auth, XMLTV, grade, UDP e auditoria TS`
- Resultado da produção: `container epg-stream ativo e saudável em TCP/9100`
- Imagem implantada: `tvstream-epg:v1-20260824`
- Rollback preservado: `TVStream não será alterado`
- Commit: `9ae4c2a`
- Tag: `epg-v1.0.0`
- Pull request/URL: `https://github.com/cortijo/TVstream/commit/9ae4c2a`
- Pendências: cadastrar as fontes e portadoras reais pelo painel.
