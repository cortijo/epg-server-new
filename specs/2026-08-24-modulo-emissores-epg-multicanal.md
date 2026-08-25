# Spec: Cadastro e supervisão multicanal de emissores EPG-only

- ID: `2026-08-24-modulo-emissores-epg-multicanal`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-24`
- Última atualização: `2026-08-24`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Transformar o emissor EPG-only validado no Dexing em um módulo persistente do
painel TVStream. O operador poderá cadastrar, duplicar, editar, iniciar, parar e
reiniciar um emissor auxiliar por canal, informando o Program Number final do
Output TS e os demais identificadores ISDB-TB, sem retransmitir vídeo ou áudio.

## 2. Contexto e comportamento atual

- Onde existe: `TVStreamEpgOnly` aceita um canal por processo e somente por
  variáveis de ambiente.
- Como reproduzir: cada novo canal exige criar manualmente outro container ou
  processo e repetir todas as variáveis.
- Evidência: o piloto `teste2` passou no Dexing somente depois de usar o Program
  Number final `2304` como SID do EIT.
- Impacto: a replicação manual favorece colisão de multicast, erro de SID e
  falta de recuperação/persistência centralizadas.

## 3. Objetivos

- [ ] Cadastrar e persistir vários emissores EPG-only pelo painel.
- [ ] Supervisionar um processo leve por cadastro com recuperação automática.
- [ ] Permitir duplicar um cadastro para acelerar a inclusão dos demais canais.
- [ ] Exibir estado operacional e parâmetros de associação ao Dexing.
- [ ] Migrar o piloto `teste2` sem emissão duplicada e sem alterar mídia.

## 4. Fora de escopo

- Automatizar a configuração do Dexing.
- Transcodificar, receber ou retransmitir áudio/vídeo.
- Alterar a estrutura MPEG-TS dos canais tradicionais.
- Desativar automaticamente o EPG embutido dos canais existentes.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | CRUD persistente de emissores com nome, fonte/ID XMLTV, SID, TSID, ONID, multicast, porta, interface, PMT PID, bitrate, TTL e autostart. | obrigatória |
| RF-02 | Ações iniciar, parar, reiniciar e duplicar pelo painel. | obrigatória |
| RF-03 | Resolver a URL pela fonte EPG cadastrada sem expô-la na API de estado. | obrigatória |
| RF-04 | Rejeitar endereço não multicast, campos fora de faixa, PID reservado e destino/porta duplicados. | obrigatória |
| RF-05 | Reiniciar automaticamente processo que encerre inesperadamente enquanto o cadastro estiver habilitado. | obrigatória |
| RF-06 | Registrar logs de ciclo de vida e mostrar PID, status, reinícios e último erro no painel. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | O módulo não inicializa GStreamer para os emissores auxiliares. |
| RNF-02 | Configuração é gravada atomicamente em `/data/tvstreamer5-epg-only.json`. |
| RNF-03 | Nenhuma URL com credencial é retornada ao navegador ou registrada no Git. |
| RNF-04 | Fluxos, playout e configuração tradicional permanecem compatíveis. |
| RNF-05 | Cada filho é iniciado sem shell, evitando interpretação de parâmetros. |

## 7. Critérios de aceite

- [x] CA-01 — Dado um cadastro válido, quando iniciado, então aparece ativo e
  existe exatamente um processo `TVStreamEpgOnly` com o destino configurado.
- [x] CA-02 — Dados dois cadastros válidos, quando ativos, então os dois destinos
  recebem TS de 1316 bytes com PIDs EPG-only e IDs próprios.
- [x] CA-03 — Dado destino/porta repetido, quando salvar, então a API rejeita a
  operação e não interrompe o emissor existente.
- [x] CA-04 — Dado um processo encerrado externamente, quando o supervisor o
  detecta, então registra a falha e tenta reiniciar automaticamente.
- [x] CA-05 — Dado um cadastro persistido com autostart, quando o container
  reinicia, então o emissor retorna sem cadastro manual.
- [x] CA-06 — O painel permite duplicar e obriga o operador a definir um destino
  livre antes de salvar a cópia.
- [x] CA-07 — Canais tradicionais e 24×7 permanecem funcionais.

## 8. Contratos afetados

### API

- `GET /api/epg-only/state`: lista configurações seguras e runtime.
- `POST /api/epg-only/save`: cria/atualiza um cadastro.
- `POST /api/epg-only/start|stop|restart|delete`: ação por `id`.
- Respostas: `{ "result":"ok" }` ou `{ "error":"mensagem" }`.
- Todas as rotas seguem a autenticação já aplicada a `/api/`.

### Configuração e persistência

- Arquivo: `/data/tvstreamer5-epg-only.json`, versão 1.
- A URL resolvida da fonte é persistida para o processo, mas omitida do estado
  público; o painel trabalha com `epg_source_id`.
- Arquivo ausente cria lista vazia, preservando instalações existentes.

### Mídia e rede

- Entrada: XMLTV HTTP/HTTPS.
- Saída: MPEG-TS UDP multicast CBR, 7 × 188 bytes por datagrama.
- PIDs: PAT `0x0000`, SDT `0x0011`, EIT `0x0012`, TDT/TOT `0x0014`, PMT
  configurável e NULL `0x1FFF`.
- SID: deve ser o Program Number final mostrado no Output TS do Dexing.

## 9. Desenho técnico

### Antes

```text
Docker/container manual -> variáveis -> TVStreamEpgOnly -> um multicast
```

### Depois

```text
painel/API -> EpgOnlyManager -> JSON persistente
                         \-> processo filho 1 -> multicast EPG canal 1
                         \-> processo filho N -> multicast EPG canal N
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `src/EpgOnlyManager.*` | persistência, validação e supervisão de processos |
| `src/main.cpp` | ciclo de vida do novo manager |
| `src/HttpServer.*` | API e painel de cadastro/operação |
| `CMakeLists.txt` | incluir manager no servidor principal |
| `DOCUMENTACAO_CODEBASE.md` | arquitetura, API e operação |
| `README.md` | uso do módulo |

### Decisões e alternativas

- Decisão: processo filho por cadastro usando o binário já validado.
- Motivo: mantém isolamento, baixo consumo e identidade TS independente.
- Alternativa rejeitada: incorporar múltiplos `EpgInjector` no servidor HTTP.
- Motivo: aumentaria o acoplamento e o risco sobre o processo principal.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Emissão duplicada na migração | média | alto | parar piloto separado imediatamente antes de ativar cadastro gerenciado |
| Colisão de multicast | média | alto | validação de unicidade e confirmação em ambiente isolado |
| SID incorreto | média | alto | ajuda explícita no formulário e SID obrigatório |
| Filho em ciclo rápido | baixa | médio | retentativa supervisionada com intervalo e contador |
| Regressão do painel | baixa | médio | build, API real e renderização em container isolado |

## 11. Plano de implementação

- [x] Mapear arquitetura, API, persistência e painel existentes.
- [x] Implementar modelo, persistência e supervisor.
- [x] Integrar ciclo de vida e APIs.
- [x] Criar seção, modal, duplicação e ações no painel.
- [x] Atualizar documentação e versão.
- [x] Validar dois emissores em ambiente isolado.
- [x] Implantar com backup e rollback.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Build/regressão | Docker isolado | CMake + CTest | compila e testes passam | passou: imagem e 2/2 CTest |
| T-02 | API/validação | container isolado | salvar válido/inválido/duplicado | respostas e persistência corretas | passou: unicast e destino repetido rejeitados |
| T-03 | Dois emissores | multicast de laboratório | capturar dois destinos | TS/IDs próprios e válidos | passou: dois processos; 1316 bytes; amostra A com 14.000 pacotes válida |
| T-04 | Recuperação | isolado | encerrar um filho | reinício automático | passou: PID 14 encerrado, novo PID 49 e restart_count=1 |
| T-05 | Persistência | isolado | reiniciar container | autostart retorna | passou: dois cadastros retornaram ativos |
| T-06 | Painel | navegador isolado | cadastrar/duplicar/ações | interface funcional | passou: JavaScript válido e HTML/API servidos; inspeção visual final na implantação |
| T-07 | Produção | servidor | health, API, processos e canais | saudável, sem duplicação | passou: v126, health, 4/4 ativos, EPG-only ativo, restart 0 |

## 13. Plano de implantação

- Imagem/tag prevista: `tvstreamer5:v126-20260824`.
- Container de teste: nome e porta HTTP exclusivos.
- Dados de teste: XMLTV público e dois destinos multicast não usados.
- Sequência: build/testes, imagem, container isolado, commit/tag/push, backup,
  migração do piloto e troca controlada da produção.
- Pós-publicação: `/health`, APIs, processos, logs, captura e canais atuais.

## 14. Plano de rollback

- Preservar `tvstreamer5:v123-20260821` e dados antes da troca.
- Se health/API/canais falharem, restaurar o container v123.
- Se apenas EPG auxiliar falhar, parar os cadastros gerenciados e restaurar o
  container piloto v125.
- Confirmar rollback por restart count, health, processos e captura multicast.

## 15. Observabilidade

- Logs: início, parada, PID do filho, saída inesperada e nova tentativa.
- Estado: `running`, `waiting`, `manual-stop`, `error`, PID e reinícios.
- Sintomas: processo ausente, reinícios crescentes, destino sem tráfego ou EPG
  sem eventos no Dexing.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-24 11:10 | análise inicial | piloto validado; manager 24×7 escolhido como padrão de persistência/API/painel |
| 2026-08-24 11:20 | desenho aprovado para implementação | processo por cadastro, sem shell, URL sensível omitida da API e validação de colisão |
| 2026-08-24 12:00 | implementação concluída | manager, API, painel, duplicação, versão v126 e documentação integrados |
| 2026-08-24 12:15 | validação isolada | build Docker; CTest 2/2; dois emissores ativos; colisões recusadas; recuperação e persistência aprovadas |
| 2026-08-24 12:20 | análise MPEG-TS | 14.000 pacotes/2.632.000 bytes; PAT/PMT/SDT/EIT/TDT/TOT; CRC/CC/IDs sem erro; somente PIDs permitidos |
| 2026-08-24 12:30 | publicação | commit `b6f3b42`, tag `v126` e push de `main`/tag concluídos |
| 2026-08-24 12:40 | implantação controlada | backup de `/srv/tvstreamer5`; v123 e piloto v125 preservados parados; produção iniciada em `tvstreamer5:v126-20260824` |
| 2026-08-24 12:45 | smoke test de produção | health saudável; painel/API v126; 4/4 canais tradicionais ativos; emissor `teste2-EPG` ativo com SID 2304 e restart 0; UDP 1316 bytes observado em `enp2s0f0` |

## 17. Resultado final

- Estado final: `concluída e implantada`
- Critérios de aceite: `7/7 concluídos`
- Testes executados: `build, CTest 2/2, JavaScript, APIs, dois emissores, captura TS, recuperação e persistência`
- Resultado da produção: `v126 saudável, restart 0, quatro canais ativos e teste2-EPG gerenciado`
- Imagem implantada: `tvstreamer5:v126-20260824`
- Rollback preservado: `tvstreamer5-v123-pre-v126-20260824`, `tvstream-epgonly-teste2` e backup `/srv/tvstreamer5-backup-pre-v126-20260824`
- Commit: `b6f3b42`
- Tag: `v126`
- Pull request/URL: `origin/main`
- Pendências: cadastrar os demais canais pelo botão `+ Emissor EPG` e configurar no Dexing os respectivos Input Channel/PID PASSTHRU.
