# Spec: indicadores de sincronização XMLTV

- ID: `2026-09-02-indicadores-sincronizacao-xmltv`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`

## Objetivo

Exibir na validação de cada fonte XMLTV os totais sincronizados de canais e
programas, a data da última atualização válida e a data a partir da qual uma
nova consulta renovará o cache.

Os mesmos indicadores também devem aparecer diretamente na listagem
**Fontes XMLTV**, ao lado das ações de cada fonte.

A partir da v1.15.4, todas as fontes vencidas são sincronizadas realmente em
segundo plano a cada 60 minutos, sem depender da abertura do painel.

## Requisitos

- [x] Retornar pela API o total de canais e programas da cópia validada.
- [x] Retornar última atualização e próxima renovação do cache.
- [x] Exibir os quatro indicadores de forma legível no desktop e no celular.
- [x] Atualizar os indicadores após sincronização manual.
- [x] Preservar Parse-XML, diagnósticos, emissores, PIDs e multicast.
- [x] Mostrar o resumo na listagem principal de fontes, sem exigir abrir o
  catálogo.
- [x] Sincronizar fontes vencidas em background, uma vez por hora, somente com
  licença válida e preservando a última cópia em caso de falha.

## Contrato

`GET /api/catalog` passa a retornar `channel_count`, `programme_count`,
`fetched_at`, `next_refresh_at` e `cache_seconds`. `next_refresh_at` representa
o vencimento horário usado pelo worker de background. A ação manual com
`force=1` continua disponível.

## Riscos e rollback

- A data ser confundida com agendamento: a interface explicará que a renovação
  acontece na próxima consulta.
- Rollback pela imagem v1.15.1, sem migração de dados.

## Validação

- teste unitário do contrato e intervalo de cinco minutos;
- teste dos textos e campos na interface;
- sintaxe Python e JavaScript, suíte completa e Docker candidato;
- teste visual desktop/móvel e smoke test de produção antes do commit final.

## Registro de execução

- 67 testes Python aprovados; 5 testes de shell ignorados pela ausência do Bash
  no Windows;
- compilação Python, JavaScript embarcado e `git diff --check` aprovados;
- imagem `epgserver:v1.15.4-candidate` construída com sucesso;
- produção promovida para `epgserver:v1.15.4-20260903`, com 27 emissores,
  zero reinícios e zero erros críticos após o deploy;
- tentativa de validação visual automatizada pelo navegador foi bloqueada pela
  política local para IP/porta e localhost encaminhado; layout responsivo e
  textos foram cobertos por teste automatizado, sem alegar inspeção visual;
- primeira rodada automática gerou estado persistido para 7 fontes, sem erro
  de sincronização;
- rollback `epg-stream-v1153-rollback-20260903` e backup
  `/srv/backups/epg-stream-before-v1154-20260903.tar.gz` preservados.
