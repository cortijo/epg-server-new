# Grade geral de EPG

## Objetivo

Disponibilizar uma visão consolidada, com todos os serviços configurados em linhas
e o horário em colunas, sem alterar o pipeline multicast.

## Comportamento

- `Grade de programação` abre a grade geral em tela ampla.
- `GET /api/guides?start=<epoch>&end=<epoch>` reúne todas as portadoras e respeita
  a fonte XMLTV selecionada individualmente por canal.
- Permite escolher hoje ou os seis próximos dias, ir para `Agora`, navegar e usar
  zoom entre 2 e 24 horas.
- Filtros por categoria, canal/portadora e título/sinopse.
- Cada linha identifica canal, portadora, TSID e SID; o evento abre os metadados.
- A tela informa canais, programas e estado de carregamento.

## Restrições

- Exige licença válida e aceita no máximo nove dias por consulta.
- É somente leitura; não modifica nem reinicia a emissão multicast.

## Aceite

- API agrega fontes herdadas e fontes específicas por serviço.
- Grade, filtros, dias, navegação, zoom e detalhe estão presentes.
- JavaScript válido e suíte Python verde.
