# Spec: Emissor multicast somente EPG para remux no modulador

- ID: `2026-08-24-emissor-epg-only-multicast`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-24`
- Última atualização: `2026-08-24`
- Issue/commit relacionado: `12f6646`

## 1. Resumo

Adicionar um executável independente que gere somente os PIDs MPEG-TS de EPG e relógio para o Dexing incorporar via `PID PASSTHRU`. O piloto usa o guia do `teste2` em um multicast livre, sem receber, decodificar, transcodificar ou retransmitir o vídeo/áudio existente.

## 2. Contexto e comportamento atual

- Onde o comportamento existe hoje: `EpgInjector` insere EIT/TDT/TOT dentro da saída completa de cada canal processado pelo `StableUdpOutput`.
- Como reproduzir: habilitar EPG em um canal convencional exige publicar outro transporte contendo também vídeo e áudio.
- Evidência observada: o Dexing encaminha PIDs adicionais por `Input Channel`, e o manual identifica EPG como caso de uso do `PID PASSTHRU`.
- Impacto operacional: replicar transportes completos aumenta banda de rede, buffers e processamento sem necessidade quando os canais já chegam ao modulador.

## 3. Objetivos

- [x] Emitir EIT no PID `0x0012` e TDT/TOT no PID `0x0014` em multicast próprio.
- [x] Manter bitrate CBR configurável usando somente pacotes EPG/relógio e NULL `0x1FFF`.
- [x] Reutilizar o gerador ISDB-TB/XMLTV já validado.
- [x] Executar o piloto em container separado sem reiniciar ou substituir o `tvstreamer5` atual.

## 4. Fora de escopo

- Alterar o canal `teste2` ou seu destino `239.192.1.191:5001`.
- Alterar o container de produção `tvstreamer5:v123-20260821`.
- Automatizar a configuração do Dexing.
- Suportar vários serviços no mesmo emissor nesta primeira prova; isso será evolução posterior após validação do hardware.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Ler origem XMLTV, ID XMLTV, SID, TSID, ONID, destino, porta, interface e bitrate por variáveis de ambiente. | obrigatória |
| RF-02 | Gerar perfil explícito `isdbtb` com EIT p/f, schedule, TDT e TOT. | obrigatória |
| RF-03 | Enviar datagramas UDP com 7 pacotes TS de 188 bytes e pacing CBR. | obrigatória |
| RF-04 | Preencher slots livres exclusivamente com PID NULL `0x1FFF`. | obrigatória |
| RF-05 | Encerrar corretamente em SIGTERM para operação via Docker. | obrigatória |
| RF-06 | Expor nos logs estado do guia e contadores de emissão sem revelar conteúdo sensível. | desejável |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Nenhum fluxo, configuração ou container existente será modificado durante o piloto. |
| RNF-02 | O executável não deve inicializar GStreamer nem processar vídeo/áudio. |
| RNF-03 | O destino deve ser multicast IPv4 e os parâmetros numéricos devem ser validados. |
| RNF-04 | A imagem e o commit devem ser reproduzíveis e não conter configuração real persistida. |

## 7. Critérios de aceite

- [x] CA-01 — Dado o piloto ativo, quando uma captura é analisada, então somente PAT `0x0000`, SDT `0x0011`, EIT `0x0012`, TDT/TOT `0x0014`, PMT configurada e NULL `0x1FFF` estão presentes.
- [x] CA-02 — Dado o XMLTV do piloto, quando o guia carrega, então a auditoria registra programas e emissão de EIT e relógio.
- [x] CA-03 — Dado o destino de laboratório, quando o emissor funciona, então datagramas de 1316 bytes são enviados pela interface `10.10.10.20` sem tocar no multicast do `teste2`.
- [x] CA-04 — O container `tvstreamer5` permanece na imagem `v123`, saudável e sem reinício provocado pelo piloto.
- [x] CA-05 — O Dexing consegue bloquear a nova entrada e encaminhar `0x0012`/`0x0014` por PID PASSTHRU no Output TS de teste.
- [x] CA-06 — O Dexing apresenta a entrada auxiliar como `prog: 1/1`, permitindo referenciar o Input Channel 9 sem selecionar o serviço auxiliar no Stream Select.

## 8. Contratos afetados

### API

- Endpoint/método: não se aplica; o piloto é um processo independente.
- Request antes/depois: não se aplica.
- Response antes/depois: não se aplica.
- Códigos de erro: processo retorna código diferente de zero em configuração inválida ou falha de socket.

### Configuração e persistência

- Arquivo/campo: variáveis `EPG_SOURCE_URL`, `EPG_CHANNEL_ID`, `EPG_SERVICE_ID`, `EPG_TSID`, `EPG_ONID`, `EPG_DESTINATION`, `EPG_PORT`, `EPG_INTERFACE`, `EPG_BITRATE` e `EPG_TTL`.
- Valor padrão: perfil ISDB-TB, bitrate 1.000.000 bit/s e TTL 32; campos de identidade/destino são explícitos no deploy.
- Migração e compatibilidade: nenhuma; não lê nem grava `tvstreamer5-config.json`.

### Mídia e rede

- Entrada: XMLTV HTTP/HTTPS.
- Saída: MPEG-TS sobre UDP multicast, 7 × 188 bytes por datagrama.
- Codec/container: sem áudio/vídeo; serviço auxiliar com PAT/PMT/SDT, EPG/relógio e pacotes NULL.
- SID/PIDs/PCR/bitrate: SID configurável; PAT `0x0000`; SDT `0x0011`; EIT `0x0012`; TDT/TOT `0x0014`; PMT padrão `0x1000`; NULL `0x1FFF`; PCR_PID `0x1FFF`; CBR configurável.
- Interface/porta/multicast do piloto: `10.10.10.20` → `239.192.1.192:5012` a 1 Mb/s.

## 9. Desenho técnico

### Antes

```text
XMLTV -> EpgInjector -> StableUdpOutput + vídeo/áudio -> multicast completo -> Dexing
```

### Depois

```text
multicast original com vídeo/áudio --------------------------> Dexing Stream Select
XMLTV -> EpgInjector + PAT/PMT/SDT -> pacing CBR -> multicast -> Dexing PID PASSTHRU
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `src/EpgOnlyMain.cpp` | executável independente e emissor UDP CBR |
| `CMakeLists.txt` | novo alvo `TVStreamEpgOnly` |
| `Dockerfile` | copiar o novo binário para a imagem |
| `README.md` | documentar operação e integração Dexing |
| `DOCUMENTACAO_CODEBASE.md` | registrar a nova topologia independente |

### Decisões e alternativas

- Decisão: reutilizar `EpgInjector` e gerar slots CBR sem GStreamer.
- Motivo: preserva exatamente as tabelas ISDB-TB já homologadas e elimina processamento de mídia.
- Alternativa rejeitada: retransmitir o SPTS completo em outra porta.
- Por que foi rejeitada: mantém a duplicação de banda e processamento que motivou a mudança.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Serviço auxiliar sem elementary streams ser recusado | baixa | médio | anunciar PAT/PMT/SDT coerentes, PCR_PID NULL e validar `prog: 1/1` no hardware |
| PID duplicado na saída do Dexing | média | alto | desabilitar TDT/TOT interno ao passar `0x0014`; garantir uma única origem por PID |
| SID/TSID/ONID divergentes | média | alto | tornar valores explícitos e auditar seções antes de configurar o modulador |
| Colisão de multicast | baixa | alto | conferir configuração e capturar o destino antes do piloto |

## 11. Plano de implementação

- [x] Mapear o gerador EPG e o caminho atual.
- [x] Confirmar destino/interface livres.
- [x] Implementar emissor independente.
- [x] Atualizar build e documentação.
- [x] Validar em container isolado.
- [x] Publicar commit/tag somente após aprovação técnica.
- [x] Subir container de piloto separado.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Build e regressão | Docker isolado | CMake + CTest | todos os alvos compilam e testes passam | aprovado: build completo; 2/2 CTest |
| T-02 | Parâmetros inválidos | isolado | destino unicast/porta inválida | processo falha com mensagem clara | aprovado: unicast rejeitado, exit 2 |
| T-03 | Transporte EPG-only | laboratório | captura UDP + scripts MPEG-TS | somente PIDs esperados, CRC/CC/horário válidos | aprovado novamente na v125: 14.000 pacotes; PAT/PMT/SDT/EIT/TDT/TOT; CRC/CC/IDs válidos |
| T-04 | Consumo | laboratório | medir processo e bitrate | sem GStreamer; uso leve; aproximadamente 1 Mb/s | aprovado: processo único, 0,44% CPU; datagramas a ~1 Mb/s |
| T-05 | Não regressão | produção | inspect/health/restart count | `v123` permanece saudável e não reiniciada | aprovado: running, restart 0, início preservado |
| T-06 | Dexing | hardware | cadastrar nova entrada e PID PASSTHRU | EPG aparece no serviço existente | aprovado pelo operador após SID final 2304 |

## 13. Plano de implantação

- Imagem/tag da correção prevista: `tvstreamer5:v125-20260824`.
- Container de teste: `tvstream-epgonly-teste2`.
- Dados de teste: BrazilTVEPG, `MUSIC BOX BRAZIL`, SID final 2304, TSID/ONID 72.
- Sequência de publicação: build, testes, captura isolada, commit/tag, container paralelo e configuração manual do Dexing.
- Verificações pós-publicação: logs, processo único, captura, PIDs, bitrate e saúde da produção.

## 14. Plano de rollback

- Container/imagem anterior: produção não será substituída.
- Condição que aciona rollback: colisão, carga inesperada, tabela inválida ou impacto na produção.
- Comandos ou procedimento: parar e remover apenas `tvstream-epgonly-teste2`; remover do Dexing somente as duas regras PID PASSTHRU do novo Input Channel.
- Como confirmar a restauração: ausência de tráfego em `239.192.1.192:5012` e `tvstreamer5` original saudável.

## 15. Observabilidade

- Logs esperados: configuração resumida, guia carregado, quantidade de programas e contadores EIT/relógio.
- Métricas/estado esperados: um processo `TVStreamEpgOnly`, sem processos GStreamer filhos.
- Alertas ou sintomas de regressão: socket falhando, guia vazio, ausência de PID `0x0012`, continuidade repetidamente inválida ou bitrate divergente.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-24 07:45 | descoberta e escolha do laboratório | destinos existentes preservados; `239.192.1.192:5012` sem tráfego e roteado por `enp2s0f0`/`10.10.10.20` |
| 2026-08-24 08:20 | implementação isolada | criado `TVStreamEpgOnly`, sem dependência de GStreamer, com pacing CBR e preenchimento NULL; documentação e validador EPG-only atualizados |
| 2026-08-24 08:55 | primeira captura funcional | 2.000 datagramas de 1316 bytes; somente PIDs `0x0012`, `0x0014`, `0x1FFF`; CRC e continuidade válidos; fonte atual possui schedule em `0x50`, sem eventos suficientes para exigir `0x51` |
| 2026-08-24 09:05 | regressão e isolamento | build completo; CTest 2/2; configuração unicast rejeitada; processo único sem GStreamer; produção v123 running, restart 0 e horário de início preservado |
| 2026-08-24 09:15 | publicação e piloto | commit `86fa64b`, tag `v124`, imagem `tvstreamer5:v124-20260824` e container paralelo `tvstream-epgonly-teste2`; logs carregaram 759 programas e captura final confirmou UDP 1316 bytes; produção preservada |
| 2026-08-24 09:50 | diagnóstico no Dexing | `IP9_Data2_239.192.1.192:5012` recebe 1 Mb/s, mas aparece `prog: 0`; PID PASSTHRU 9/0x12 e 9/0x14 está correto, portanto falta sinalização mínima para o hardware reconhecer o serviço auxiliar |
| 2026-08-24 10:10 | validação da correção v125 | CTest 2/2; captura isolada em `239.192.1.193:5012` com 14.000 pacotes; somente PIDs 0/0x11/0x12/0x14/0x1000/0x1FFF; CRC/CC/IDs válidos; ffprobe reconheceu program 1, PMT 4096, PCR 8191, serviço `teste2-EPG`, sem streams; CPU 0,41% |
| 2026-08-24 10:20 | implantação da correção | commit/tag `12f6646`/`v125`; container paralelo atualizado para `tvstreamer5:v125-20260824` no destino original `239.192.1.192:5012`; guia carregado e UDP 1316 bytes confirmado; produção v123 preservada com restart 0 |
| 2026-08-24 10:35 | associação ao serviço final | captura do Dexing revelou `teste2` remapeado para Program Number 2304; container auxiliar recriado com SID 2304 e mesmos TSID/ONID 72; nova captura de 8.400 pacotes aprovada, sem erros de CRC, continuidade ou identidade |
| 2026-08-24 11:00 | validação no hardware | operador confirmou EPG associado e passando corretamente no Output TS após PID PASSTHRU |

## 17. Resultado final

- Estado final: `concluída e validada no Dexing`
- Critérios de aceite: `6/6 concluídos`
- Testes executados: `build completo, CTest 2/2, configuração inválida, captura de 2.000 datagramas, validação PSI/SI e inspeção de isolamento`
- Resultado da produção: `tvstreamer5:v123-20260821 running, restart 0 e horário de início preservado`
- Imagem implantada: `tvstreamer5:v125-20260824 somente no container paralelo tvstream-epgonly-teste2`
- Rollback preservado: `produção v123 não será substituída`
- Commit: `12f6646`
- Tag: `v125`
- Pull request/URL: `origin/main`
- Pendências: nenhuma para o piloto; a replicação multicanal segue na spec `2026-08-24-modulo-emissores-epg-multicanal`.
