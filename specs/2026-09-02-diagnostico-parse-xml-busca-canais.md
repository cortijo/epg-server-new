# Spec: diagnóstico Parse-XML e busca de canais

- ID: `2026-09-02-diagnostico-parse-xml-busca-canais`
- Estado: `validando`
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

## Validação prevista

- testes dos três motivos de descarte e persistência após reinício;
- suíte Python, sintaxe JavaScript e Docker;
- fonte Parse-XML real em candidato isolado;
- produção com licença válida e 27 emissores preservados.
