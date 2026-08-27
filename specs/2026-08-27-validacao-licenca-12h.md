# Spec: Validação automática da licença a cada 12 horas

- ID: `2026-08-27-validacao-licenca-12h`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-27`
- Última atualização: `2026-08-27`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Alterar o intervalo automático padrão de validação da licença de 60 segundos
para 12 horas. A versão 1.12.0 aceitava `43200` no ambiente, mas limitava
internamente qualquer valor a 3600 segundos.

## 2. Objetivos e escopo

- [x] Tornar 43200 segundos o padrão do cliente e da aplicação.
- [x] Permitir intervalos configuráveis de até sete dias.
- [x] Expor o padrão no Compose e no instalador da versão 1.12.1.
- [x] Preservar validação forçada ao instalar/consultar explicitamente a chave.
- [x] Garantir que a primeira consulta nunca seja atendida pelo cache vazio.
- Não adicionar tolerância offline nem alterar o comportamento fail-closed.

## 3. Critérios de aceite

- [x] Sem variável de ambiente, o intervalo efetivo é 43200 segundos.
- [x] `EPG_LICENSE_CHECK_SECONDS=86400` não é reduzido para uma hora.
- [x] Valores acima de sete dias são limitados a 604800 segundos.
- [x] Health, emissores e multicast permanecem saudáveis após o deploy.

## 4. Riscos e rollback

O principal risco é aumentar o tempo para detectar revogação. A consulta
manual/forçada permanece disponível. Rollback: reativar o container preservado
com a imagem v1.12.0 e intervalo anterior.

## 5. Matriz de validação

| ID | Cenário | Resultado esperado | Estado |
|---|---|---|---|
| T-01 | teste unitário de padrão/limites | 43200/86400/604800 | passou |
| T-02 | suíte EPG/licenças | aprovação | passou |
| T-03 | candidato Docker | health válido e intervalo 43200 | passou |
| T-04 | produção cliente | emissores e multicast ativos | passou |

## 6. Resultado final

- Estado final: concluída.
- Testes: 46 testes EPG, 3 de licenças, candidato isolado e captura multicast.
- Produção: `epgserver:v1.12.1-20260827`, cinco emissores, dez canais,
  intervalo 43200 e zero reinícios.
- Rollback: `epg-stream-pre-v1.12.1-20260827` e
  `/srv/epg-stream-backup-pre-v1.12.1-20260827`.
- Commit: commit desta entrega.
- Tag: `epg-v1.12.1`.
