# Spec: versionamento PSI/SI persistente após reinício

- ID: `2026-09-08-versionamento-psi-si-persistente`
- Estado: `validando`
- Responsável: Codex
- Solicitante: Julio Cortijo
- Criada em: 2026-09-08
- Última atualização: 2026-09-08
- Issue/commit relacionado: a preencher

## 1. Resumo

Após reiniciar um emissor, a EIT voltava à versão zero. O Dexing podia manter
a tabela anterior em cache e o operador precisava executar **Parse Program**
para o EPG reaparecer. A versão PSI/SI passará a avançar e persistir em cada
nova geração do processo, sendo compartilhada por PAT, PMT, SDT, BIT e EIT.

## 2. Contexto e comportamento atual

- Evidência: logs de produção mostram novas cargas da EIT novamente em
  `versao=0`, embora os pacotes continuem sendo emitidos.
- Impacto: operação manual no modulador após reinício ou alteração de canal.
- O incremento em memória após mudança do XMLTV já existe, mas não sobrevive
  ao encerramento do processo.

## 3. Objetivos

- [ ] Persistir e avançar a versão módulo 32 em toda inicialização da portadora.
- [ ] Inicializar EIT e PSI/SI com a mesma versão reservada pelo supervisor.
- [ ] Preservar a atualização da EIT quando a programação muda em execução.

## 4. Fora de escopo

- Automatizar ou controlar o Dexing.
- Alterar SID, TSID, ONID, PIDs, multicast, bitrate, áudio ou vídeo.
- Eliminar o primeiro Parse Program necessário ao cadastrar um input novo.

## 5. Critérios de aceite

- [x] CA-01 — Reiniciar uma portadora avança a versão persistida entre 0 e 31.
- [x] CA-02 — Após reiniciar o container, a próxima geração não reutiliza a
  versão anterior.
- [x] CA-03 — PAT, PMT, SDT, BIT e EIT anunciam a versão reservada.
- [x] CA-04 — Captura isolada passa CRC, continuidade, IDs, EIT e relógio.
- [ ] CA-05 — Os dois servidores mantêm health, licença, processos e multicast.

## 6. Desenho técnico

```text
Antes: start -> EIT versão 0 -> Dexing pode conservar cache
Depois: start -> store incrementa versão -> todas as tabelas usam a nova versão
        -> mudança XMLTV durante execução incrementa novamente apenas a EIT
```

Arquivos: `epg-product/app.py`, `src/ConfigManager.h`,
`src/EpgOnlyMain.cpp`, `src/EpgInjector.cpp`, auditor, testes e documentação.

## 7. Riscos e mitigação

- Versão dá a volta após 32 gerações: comportamento previsto pela norma; cada
  mudança ainda difere da versão imediatamente anterior.
- Falha antes do spawn pode consumir uma versão: seguro e preferível a reuso.
- Regressão PSI/SI: build, suíte, captura e auditoria antes de produção.

## 8. Validação e implantação

| ID | Cenário | Resultado esperado | Estado |
|---|---|---|---|
| T-01 | unittest Python | incremento/persistência e regressão aprovados | passou |
| T-02 | build Docker | C++ compila | passou |
| T-03 | captura TS isolada | auditor sem erros e versões coerentes | passou |
| T-04 | restart isolado | versão muda e persiste | passou |
| T-05 | produção nos dois hosts | health/processos/multicast normais | pendente |

Imagem prevista: `epgserver:v1.17.3-20260908`. Cada host receberá backup novo
e manterá o container/imagem anterior parado para rollback.

## 9. Rollback

Parar o novo `epg-stream`, restaurar o nome do container anterior e iniciá-lo.
O campo `signalling_version` é compatível com versões anteriores; os dados só
serão restaurados se uma inconsistência for comprovada.

## 10. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-09-08 | diagnóstico | emissão ativa; EIT reutilizava versão zero após restart |
| 2026-09-08 | testes | 78 testes EPG e 3 de licença passaram; 5 de firewall ignorados no Windows sem Bash |
| 2026-09-08 | captura isolada | versão 7 e depois 8; PAT/PMT/SDT/EIT=8, CRC e continuidade sem erros |

## 11. Resultado final

- Estado final: em andamento
- Commit/tag/imagem/rollback: a preencher após validação e implantação
