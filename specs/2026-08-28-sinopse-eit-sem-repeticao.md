# Spec: Sinopse EIT sem repetição

- ID: `2026-08-28-sinopse-eit-sem-repeticao`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-28`
- Última atualização: `2026-08-28`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Corrigir a duplicação da sinopse em receptores que concatenam o texto do
`short_event_descriptor` (`0x4D`) com o texto do
`extended_event_descriptor` (`0x4E`).

## 2. Contexto e comportamento atual

- O descritor `0x4D` leva os primeiros 110 bytes da descrição.
- Quando a descrição é longa, o primeiro `0x4E` recomeça no byte zero.
- TVs que concatenam os dois descritores exibem o prefixo duas vezes.
- O problema foi reproduzido em dois receptores diferentes.

## 3. Objetivos

- [x] Fazer o primeiro `0x4E` começar após o texto já transportado no `0x4D`.
- [x] Preservar título, categoria, classificação, horários, PIDs, SID, TSID e ONID.
- [x] Implantar a mesma imagem validada nos hosts operacionais autorizados.

## 4. Fora de escopo

- Alterar XMLTV, painel, API, codecs, áudio ou vídeo.
- Alterar associação de canal, destino multicast ou configuração do Dexing.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Descrição de até 110 bytes permanece somente no `0x4D`. | obrigatória |
| RF-02 | Descrição longa usa `0x4D` para o prefixo e `0x4E` somente para a continuação. | obrigatória |
| RF-03 | A concatenação `0x4D + 0x4E` recompõe a descrição uma única vez. | obrigatória |

## 6. Requisitos não funcionais

- Não interromper portadoras até o corte validado.
- Preservar imagem, container e dados anteriores para rollback.
- Não publicar credenciais, chaves ou URLs XMLTV privadas.

## 7. Critérios de aceite

- [x] CA-01 — Descrição curta não gera `0x4E`.
- [x] CA-02 — Descrição longa gera continuação sem prefixo repetido.
- [x] CA-03 — O TS real possui CRC e continuidade válidos e mantém EIT p/f e schedule.
- [x] CA-04 — Os dois servidores retomam a mesma quantidade de emissores após o corte.

## 8. Contratos afetados

- API e persistência: não alteradas.
- Mídia: PID `0x0012`, conteúdo textual dos descritores EIT `0x4D/0x4E`.
- Rede, bitrate, PIDs, SID, TSID e ONID: não alterados.

## 9. Desenho técnico

### Antes

```text
0x4D = descrição[0:110]
0x4E = descrição[0:N]
```

### Depois

```text
0x4D = descrição[0:110]
0x4E = descrição[110:N]
```

Arquivo alterado: `src/EpgInjector.cpp`.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Receptor que exibe apenas `0x4E` mostrar somente a continuação | baixa | médio | manter resumo compatível no `0x4D`; validar em TS e RF |
| Corte interromper multicast | baixa | alto | candidato isolado, backup e container anterior preservado |
| Truncar descrição longa | baixa | baixo | manter até 13 descritores de 249 bytes além do prefixo |

## 11. Plano de implementação

- [x] Confirmar causa no gerador EIT.
- [x] Alterar o deslocamento e cálculo dos chunks estendidos.
- [x] Atualizar versão e documentação.
- [x] Construir imagem imutável e auditar TS candidato.
- [x] Implantar e validar os dois servidores.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Resultado esperado | Estado |
|---|---|---|---|---|
| T-01 | Build C++/Docker | isolado | compilação aprovada | passou |
| T-02 | Descrição curta | TS candidato | somente `0x4D` | passou |
| T-03 | Descrição longa | TS candidato | `0x4D + 0x4E` sem repetição | passou |
| T-04 | Auditoria PSI/SI | TS candidato e real | CRC/continuidade/EIT válidos | passou |
| T-05 | Smoke test | dois hosts | health, licença, emissores e logs saudáveis | passou |

## 13. Plano de implantação

- Imagem prevista: `epgserver:v1.12.2-20260828`.
- Teste em porta HTTP e multicast isolados antes do corte.
- Corte mantendo os volumes de dados e licença atuais.

## 14. Plano de rollback

Parar o novo `epg-stream`, renomear o container preservado para
`epg-stream` e iniciá-lo. Acionar em caso de falha de build, licença, health,
emissores, captura ou auditoria.

## 15. Observabilidade

- `/health`, `/api/license`, `docker top`, `RestartCount` e logs recentes.
- Captura do multicast real e inspeção dos descritores `0x4D/0x4E`.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-28 | Diagnóstico | `0x4D` contém 110 bytes e `0x4E` reinicia no byte zero. |
| 2026-08-28 | Controle negativo | Imagem anterior: 8/8 sinopses longas com prefixo repetido. |
| 2026-08-28 | Candidato isolado | 3.612 pacotes, 12 sinopses verificadas, zero repetição, CRC e continuidade sem erros. |
| 2026-08-28 | Produção principal | 9.310 pacotes reais da portadora ESPN, 338 sinopses verificadas e zero repetição. |
| 2026-08-28 | Deploy 187.19.16.59 | v1.12.2 saudável, licença válida e 5/5 emissores. |
| 2026-08-28 | Deploy 181.233.106.46 | v1.12.2 saudável, licença válida e 27/27 emissores. |

## 17. Resultado final

- Estado final: `concluída`
- Critérios de aceite: `4/4 concluídos`
- Testes: 46 testes Python; builds Docker nos dois hosts; controle negativo;
  auditoria de TS sintético e captura real.
- Imagem implantada: `epgserver:v1.12.2-20260828`.
- Rollback 181: `epg-stream-pre-v1.12.2-20260828-093817`.
- Rollback 187: `epg-stream-pre-v1.12.2-20260828-093757`.
- Tag: `epg-v1.12.2`.
