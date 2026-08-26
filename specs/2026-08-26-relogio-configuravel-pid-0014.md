# Relógio e fuso configuráveis no PID 0x0014

- ID: `2026-08-26-relogio-configuravel-pid-0014`
- Estado: `validando`
- Responsável: Codex
- Solicitante: Julio Cortijo
- Criada em: `2026-08-26`
- Última atualização: `2026-08-26`
- Issue/commit relacionado: a preencher

## 1. Resumo

Permitir que cada portadora mantenha o relógio ISDB-TB atual ou configure um
fuso e uma correção de horário próprios. A configuração deve permanecer
avançando com o relógio do sistema e alterar de forma coerente TDT/TOT e os
horários civis da EIT, sem criar PID adicional.

## 2. Contexto e comportamento atual

- `EpgInjector` aplica UTC-03:00 fixo ao perfil ISDB-TB.
- TDT e TOT são publicados no PID `0x0014` a cada cinco segundos.
- O descritor `local_time_offset_descriptor` da TOT anuncia `BRA`, UTC-03:00.
- Não existe configuração por portadora para corrigir televisores com relógio
  incompatível.

## 3. Objetivos

- [x] Manter o modo padrão compatível com UTC-03:00 atual.
- [x] Permitir selecionar o fuso e uma correção de minutos por portadora.
- [x] Manter EIT e TDT/TOT no mesmo referencial civil configurado.
- [x] Exibir a configuração no formulário da portadora.

## 4. Fora de escopo

- Sincronizar o relógio Linux ou usar NTP.
- Alterar vídeo, áudio, multicast, SID, TSID, ONID ou PIDs.
- Criar regras de horário de verão automáticas por banco IANA.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Modo padrão usa UTC-03:00 e correção zero. | obrigatória |
| RF-02 | Modo personalizado aceita fuso de UTC-12:00 a UTC+14:00 em passos de 15 minutos. | obrigatória |
| RF-03 | Correção aceita -1440 a +1440 minutos e acompanha o avanço do relógio do host. | obrigatória |
| RF-04 | TOT anuncia o mesmo fuso usado para codificar o relógio e a EIT. | obrigatória |
| RF-05 | A edição reinicia somente a portadora alterada. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Dados antigos permanecem no modo padrão sem migração destrutiva. |
| RNF-02 | CRC, continuidade e carrosséis permanecem válidos. |
| RNF-03 | Não introduzir PID novo nem alterar o passthrough do Dexing. |

## 7. Critérios de aceite

- [x] CA-01 — Portadora antiga sem campos novos emite UTC-03:00 sem correção.
- [x] CA-02 — Portadora personalizada em UTC-04:00 com +30 minutos anuncia
  `-04:00` na TOT e relógio avançado em 30 minutos.
- [x] CA-03 — EIT e PID `0x0014` usam o mesmo fuso configurado.
- [x] CA-04 — Valores inválidos são recusados pela API.
- [x] CA-05 — Captura isolada apresenta TDT/TOT, CRC e continuidade válidos.

## 8. Contratos afetados

### API e persistência

`POST /api/carriers` e cada item de `carriers` passam a aceitar:

```json
{
  "clock_mode": "standard|custom",
  "clock_utc_offset_minutes": -180,
  "clock_correction_minutes": 0
}
```

Ausência dos campos equivale a `standard`, `-180`, `0`.

### Mídia e rede

- PID: `0x0014`, tabelas TDT `0x70` e TOT `0x73`.
- EIT continua em `0x0012` e recebe o mesmo referencial civil.
- Multicast, bitrate, interface e passthrough permanecem inalterados.

## 9. Desenho técnico

```text
relógio do host + correção -> deslocamento civil -> TDT/TOT 0x0014
                                      `---------> EIT 0x0012
```

| Arquivo | Alteração |
|---|---|
| `epg-product/app.py` | validação, persistência, ambiente e formulário |
| `src/EpgOnlyMain.cpp` | leitura dos parâmetros da portadora |
| `src/ConfigManager.*` | contrato compartilhado do injetor |
| `src/EpgInjector.cpp` | offset/correção em EIT, TDT e TOT |
| `scripts/verify_epg_clock.py` | auditoria de fuso e correção esperados |
| documentação/testes | contrato, regressão e operação |

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| EIT e relógio divergirem | média | alto | aplicar o mesmo offset a ambos e auditar captura |
| horário congelado | baixa | alto | persistir correção, nunca um timestamp absoluto |
| duas fontes do PID 0x0014 | existente | alto | manter uma única origem no Dexing |
| configuração extrema acidental | média | médio | limites, modo padrão explícito e ajuda na UI |

## 11. Plano de implementação

- [x] Mapear geração atual de EIT/TDT/TOT.
- [x] Implementar campos e validação.
- [x] Propagar configuração ao emissor.
- [x] Atualizar auditor e documentação.
- [x] Executar testes, build e captura isolada.
- [ ] Implantar com rollback preservado.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Resultado esperado | Estado |
|---|---|---|---|---|
| T-01 | Python/JS | local | suíte e sintaxe aprovadas | passou |
| T-02 | Build C++/Docker | Linux isolado | imagem candidata compila | passou |
| T-03 | Modo padrão | multicast isolado | comportamento UTC-03:00 atual | passou |
| T-04 | Modo personalizado | multicast isolado | TOT -04:00 e correção +30 min | passou |
| T-05 | Produção | servidor | health, login e 27 portadoras saudáveis | pendente |

## 13. Implantação e rollback

- Versão prevista: `1.9.0`.
- Imagem prevista: `epgserver:v1.9.0-20260826`.
- A candidata usará porta HTTP, volume e multicast exclusivos.
- O container e o volume da v1.8.0 serão preservados para rollback.
- Rollback: remover somente a v1.9.0, renomear e iniciar o container anterior.

## 14. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-26 | diagnóstico | UTC-03:00 fixo localizado em `EpgInjector`; PID 0x0014 vem somente do primeiro injetor da portadora |
| 2026-08-26 | testes locais | 35 testes aprovados, cinco skips de Bash no Windows, Python e JavaScript válidos |
| 2026-08-26 | painel | formulário conferido em desktop e 390x844; padrão desabilita ajustes e personalizado habilita ambos |
| 2026-08-26 | build | imagem candidata `epgserver:v1.9.0-20260826-candidate` compilada no Linux |
| 2026-08-26 | captura padrão | TSID/ONID 91, SID 2901, TOT -03:00, deslocamento observado -180,8 min, CRC/continuidade sem erro |
| 2026-08-26 | captura personalizada | TSID/ONID 92, SID 2901, TOT -04:00, deslocamento observado -210,7 min, CRC/continuidade sem erro |
| 2026-08-26 | candidato web | health 1.9.0, API persistiu fuso/correção e manteve dados após restart |

## 15. Resultado final

- Estado final: em andamento
- Critérios de aceite: 5/5 concluídos
- Commit/tag/imagem/rollback: a preencher
