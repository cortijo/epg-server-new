# Spec: Logotipo ISDB-TB estrito no emissor EPG

- ID: `2026-08-25-logo-isdbtb-estrito`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-25`
- Última atualização: `2026-08-25`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Substituir o primeiro teste experimental de logo por sinalização compatível com
ARIB/ISDB-TB: seis formatos de logo em CDT, PNG indexado sem PLTE/tRNS usando a
CLUT fixa de 128 cores do receptor, descritor 0xCF coerente e versão de SDT
incrementada. Preservar EIT, relógio, usuários e portadoras existentes.

## 2. Contexto e comportamento atual

- O emissor publica SDT/0x0011 e CDT/0x0029 e o Dexing faz PID passthrough.
- O logo atual contém `PLTE`, publica apenas `logo_type=0x05` e usa
  `section_number=last_section_number=5`.
- A captura do SPORTV mostrou CRC e IDs válidos, mas o receptor não exibiu a
  imagem.
- O padrão ARIB usa CLUT fixa no receptor e seis formatos 0x00..0x05.

## 3. Objetivos

- [x] Gerar os seis formatos ARIB com dimensões normativas.
- [x] Gerar PNG com somente IHDR, IDAT e IEND, sem paleta transmitida.
- [x] Publicar CDT completo 0x00..0x05 e descritor SDT coerente.
- [x] Incrementar versão da sinalização quando um logo mudar ou for removido.
- [x] Migrar logos existentes sem exigir novo upload.

## 4. Fora de escopo

- Alterar EIT, XMLTV, vídeo/áudio ou configuração do Dexing.
- Prometer exibição em receptores que não implementem download de logo ARIB.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Logo salvo produz variantes 48x24, 36x24, 48x27, 72x36, 54x36 e 64x36. | obrigatória |
| RF-02 | Cada variante usa índices da CLUT fixa e não contém PLTE/tRNS. | obrigatória |
| RF-03 | CDT usa seção/tipo 0..5 e last_section_number 5. | obrigatória |
| RF-04 | A miniatura web continua visível em PNG convencional. | obrigatória |
| RF-05 | Logo legado é migrado automaticamente e reinicia apenas sua portadora. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Não alterar destinos multicast nem canais não relacionados. |
| RNF-02 | Manter caminhos de arquivos sob `/data/logos`. |
| RNF-03 | Preservar rollback integral da imagem e dados atuais. |

## 7. Critérios de aceite

- [x] CA-01 — Captura isolada contém CDT 0xC8 para os tipos 0..5 com CRC válido.
- [x] CA-02 — As seis imagens têm dimensões corretas e chunks IHDR/IDAT/IEND.
- [x] CA-03 — SDT contém descriptor 0xCF e versão de sinalização atualizada.
- [x] CA-04 — EIT 0x12, TDT/TOT 0x14, SDT 0x11 e CDT 0x29 permanecem válidos.
- [x] CA-05 — Produção fica saudável, sem restart de container e com SPORTV emitindo.

## 8. Contratos afetados

### API

Endpoints permanecem iguais. A resposta de upload informa seis variantes.

### Configuração e persistência

`logo.variants` passa a mapear tipos 0..5 para arquivos transmitidos;
`logo.path` continua sendo a miniatura. `carrier.signalling_version` é
server-owned e avança módulo 32. Configurações antigas são migradas.

### Mídia e rede

Saída e PIDs permanecem: SDT 0x0011, EIT 0x0012, relógio 0x0014 e CDT 0x0029.
Somente o conteúdo/versão da SDT e CDT muda.

## 9. Desenho técnico

### Antes

```text
PNG -> quantização Pillow/PLTE -> CDT tipo 5 -> PID 0x0029
```

### Depois

```text
PNG -> RGBA -> CLUT ARIB fixa -> 6 PNG sem PLTE -> 6 CDT -> PID 0x0029
                              -> preview PNG convencional -> painel
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `epg-product/app.py` | CLUT, variantes, migração e versão. |
| `src/EpgOnlyMain.cpp` | múltiplos CDT e versão da SDT. |
| `scripts/verify_isdbtb_ts.py` | auditoria estrutural estrita. |
| `tests/test_epg_product.py` | contratos automatizados. |
| `epg-product/README.md` | operação e compatibilidade. |
| `DOCUMENTACAO_EPG_PRODUTO.md` | arquitetura persistida. |

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Receptor ainda não suportar logo ARIB | média | médio | validar captura e documentar limite do receptor. |
| Migração interromper emissor | baixa | médio | teste em cópia, backup e container anterior preservado. |
| Versão em cache | média | médio | avançar SDT e logo_version. |

## 11. Plano de implementação

- [x] Mapear captura, configuração do Dexing e código atual.
- [x] Implementar encoder e migração.
- [x] Ajustar emissor e auditor.
- [x] Atualizar documentação.
- [x] Validar imagem e multicast isolados.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Python | local | unittest + py_compile | passa | passou |
| T-02 | C++/Docker | servidor isolado | build imagem | passa | passou |
| T-03 | Transporte | multicast laboratório | captura + auditor estrito | 6 CDT e zero erro | passou |
| T-04 | Migração | cópia sanitizada | iniciar imagem | variantes criadas | passou |
| T-05 | Produção | servidor | health/log/captura SPORTV | saudável e válido | passou |

## 13. Plano de implantação

- Imagem/tag prevista: `tvstream-epg:v1.4.0-20260825` / `epg-v1.4.0`.
- Container de teste e multicast exclusivos.
- Backup de `/srv/epg-stream` antes da troca.
- Preservar container v1.3.1 sem restart automático.

## 14. Plano de rollback

- Restaurar container/imagem `tvstream-epg:v1.3.1-20260825` e o backup dos
  dados se health, emissão ou auditoria falhar.
- Confirmar health, restart count e captura após restauração.

## 15. Observabilidade

- Log de início deve mostrar seis CDT por serviço com logo.
- Auditor deve listar tipos, dimensões, chunks e correspondência SDT/CDT.
- Ausência de loop de reinício.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-25 | Diagnóstico da captura SPORTV | CDT/CRC/IDs válidos; PNG continha PLTE e somente tipo 5. |
| 2026-08-25 | Compatibilidade definida | Seis formatos e CLUT fixa conforme ARIB TR-B14/TR-B15. |
| 2026-08-25 | Testes Python | 18 testes passaram; `py_compile` e `git diff --check` sem erro. |
| 2026-08-25 | Build isolado | Imagem `tvstream-epg:v1.4.0-20260825`, ID `sha256:82c8c4...`, compilou. |
| 2026-08-25 | Migração isolada | Logo legado SPORTV virou preview + tipos 0..5; logo/SDT avançaram para versão 1. |
| 2026-08-25 | Captura laboratório | 4.753 pacotes, CRC/IDs/continuidade sem erro, PIDs 0x11/0x12/0x14/0x29 e seis CDT válidos. |
| 2026-08-25 | Implantação | Container v1.4.0 saudável, restart 0; v1.3.1 e backup preservados. |
| 2026-08-25 | Captura de produção | 4.732 pacotes, três SIDs e seis logos SPORTV válidos, SDT v1, zero erro. |

## 17. Resultado final

- Estado final: `concluída`
- Critérios de aceite: `5/5 concluídos`
- Testes executados: `18 unitários, build Docker, migração e captura UDP isolada`
- Resultado da produção: `health ok, restart 0 e transporte auditado sem erro`
- Imagem implantada: `tvstream-epg:v1.4.0-20260825`
- Rollback preservado: `epg-stream-pre-v1.4.0-20260825 e backup correspondente`
- Commit: `bdfdb91dbab3a720a7b724233ae1c1e893c45c8a`
- Tag: `epg-v1.4.0`
- Pull request/URL: `origin/main`
- Pendências: confirmar visualmente a exibição no receptor/Dexing do operador.
