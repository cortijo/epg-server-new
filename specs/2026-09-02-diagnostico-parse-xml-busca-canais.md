# Spec: diagnóstico Parse-XML e busca de canais

- ID: `2026-09-02-diagnostico-parse-xml-busca-canais`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`

## Objetivo

Permitir consultar quais programas foram descartados pelo Parse-XML e o motivo,
além de localizar rapidamente canais pelo nome ou ID XMLTV.

## Requisitos

- [x] Registrar canal, título, início, fim e motivo de cada programa inválido.
- [x] Persistir o diagnóstico junto ao cache normalizado, em arquivo protegido.
- [x] Exibir e pesquisar os erros no teste e no catálogo da fonte.
- [x] Buscar canais por nome ou ID sem nova requisição.
- [x] Preservar o XML normalizado e todos os emissores.

## Fora de escopo

- Reintroduzir eventos inválidos no EPG.
- Alterar regras de PIDs, EIT, multicast ou associações existentes.

## Segurança e limites

O arquivo `TOKEN.diagnostics.json` fica ao lado do cache Parse-XML com modo
`0600`. Até 2.000 erros são detalhados por sincronização; o excedente é contado
como omitido para impedir respostas e arquivos sem limite.

## Riscos e rollback

- Diagnóstico grande: limite de 2.000 entradas e sidecar máximo de 4 MiB.
- Dados antigos sem sidecar: catálogo continua funcionando e uma nova
  sincronização gera o relatório.
- Rollback: retornar à imagem v1.15.0 usando o mesmo volume; o sidecar adicional
  é ignorado pela versão anterior.

## Validação executada

- 64 testes aprovados e 5 testes de shell ignorados por indisponibilidade do
  Bash no ambiente Windows;
- compilação Python, sintaxe do JavaScript embarcado e `git diff --check`
  aprovados;
- imagem candidata `epgserver:v1.15.1-candidate` construída antes da promoção;
- produção promovida para `epgserver:v1.15.1-20260902`, com 27 emissores,
  zero reinícios e nenhum erro crítico nos cinco minutos posteriores;
- licença permaneceu válida, com 63 de 100 canais no momento do deploy;
- backup preservado em
  `/srv/backups/epg-stream-before-v1151-20260902.tar.gz` e rollback anterior
  mantido parado.

Depois da atualização, fontes Parse-XML que ainda possuem cache antigo devem
ser sincronizadas novamente para gerar o primeiro arquivo de diagnóstico.
