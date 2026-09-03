# Modelo XMLTV para fornecedores de EPG

## Objetivo

Este documento define o formato que um fornecedor deve entregar para que a
programação seja aceita pelo EPG Stream e convertida em metadados compatíveis
com a transmissão ISDB-TB.

O exemplo abaixo foi extraído do canal `SPORTV`, atualmente associado e
funcionando no sistema.

## Requisitos gerais

- arquivo XML válido e codificado em UTF-8;
- elemento raiz `<tv>`;
- uma declaração `<channel>` para cada canal;
- um ou mais elementos `<programme>` associados ao canal;
- identificador único e estável para cada canal;
- horários com data, hora, segundos e fuso horário explícito;
- horário final posterior ao horário inicial;
- título e sinopse sem HTML, scripts ou marcações externas.

## Exemplo completo de um canal

```xml
<?xml version="1.0" encoding="UTF-8"?>
<tv
  generator-info-name="Nome do provedor"
  generator-info-url="https://provedor.example.com">

  <channel id="SPORTV">
    <display-name lang="pt-BR">SPORTV</display-name>
  </channel>

  <programme
    start="20260828000000 -0300"
    stop="20260828003000 -0300"
    channel="SPORTV">
    <title lang="pt-BR">Giro da Rodada: Brasileirão</title>
    <sub-title lang="pt-BR">24ª Rodada</sub-title>
    <desc lang="pt-BR">Assista agora o Giro da Rodada com os destaques dos Campeonatos de Futebol.</desc>
    <category lang="pt-BR">Esportes</category>
    <episode-num system="xmltv_ns">80.240.</episode-num>
  </programme>

  <programme
    start="20260828003000 -0300"
    stop="20260828013000 -0300"
    channel="SPORTV">
    <title lang="pt-BR">Cruzeiro x Atlético-MG</title>
    <desc lang="pt-BR">Cruzeiro e Atlético-MG jogam a rodada de ida das quartas de final da Copa do Brasil, no Mineirão, em Belo Horizonte.</desc>
    <category lang="pt-BR">Esportes</category>
    <previously-shown />
  </programme>

  <programme
    start="20260828013000 -0300"
    stop="20260828023000 -0300"
    channel="SPORTV">
    <title lang="pt-BR">Palmeiras x Santos</title>
    <desc lang="pt-BR">Palmeiras e Santos jogam a rodada de ida das quartas de final da Copa do Brasil, em São Paulo.</desc>
    <category lang="pt-BR">Esportes</category>
    <previously-shown />
  </programme>

</tv>
```

## Declaração do canal

```xml
<channel id="SPORTV">
  <display-name lang="pt-BR">SPORTV</display-name>
</channel>
```

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `channel@id` | Sim | Identificador único e permanente usado para relacionar a programação. |
| `display-name` | Sim | Nome do canal apresentado na interface. |
| `display-name@lang` | Recomendado | Idioma do nome, preferencialmente `pt-BR`. |

O valor de `programme@channel` deve ser exatamente igual ao valor de
`channel@id`, inclusive em maiúsculas, espaços e acentuação.

## Declaração de programa

```xml
<programme
  start="20260828000000 -0300"
  stop="20260828003000 -0300"
  channel="SPORTV">
  <title lang="pt-BR">Nome do programa</title>
  <desc lang="pt-BR">Sinopse do programa</desc>
  <category lang="pt-BR">Esportes</category>
</programme>
```

| Campo | Obrigatório | Descrição |
| --- | --- | --- |
| `programme@start` | Sim | Data e hora de início com fuso explícito. |
| `programme@stop` | Sim | Data e hora final com fuso explícito. |
| `programme@channel` | Sim | Referência exata ao `channel@id`. |
| `title` | Sim | Título do programa. |
| `desc` | Recomendado | Sinopse completa do programa. |
| `category` | Recomendado | Gênero ou categoria editorial. |
| `sub-title` | Não | Episódio, rodada ou subtítulo. |
| `episode-num` | Não | Identificação de temporada/episódio. |
| `previously-shown` | Não | Informa que o conteúdo é reprise. |

## Formato dos horários

O formato recomendado é:

```text
AAAAMMDDHHMMSS -0300
```

Exemplo:

```text
20260828090000 -0300
```

Isso representa 28 de agosto de 2026, às 09:00:00, no fuso UTC−03:00.

O fornecedor pode usar outro deslocamento UTC válido, mas deve informá-lo em
todos os eventos. O sistema converte o instante corretamente para a transmissão.
Horários sem fuso podem ser corrigidos pelo modo Parse-XML, mas não são o formato
recomendado para integração direta.

O elemento `<length>` não é necessário. A duração é calculada por
`programme@stop - programme@start`, evitando divergências.

## Relação com ISDB-TB

Na geração do MPEG-TS, os principais campos são convertidos desta forma:

| XMLTV | ISDB-TB | Utilização |
| --- | --- | --- |
| `title` | descritor `0x4D` | Nome/título do programa. |
| `desc` | descritor `0x4E` | Sinopse, dividida e concatenada quando necessário. |
| `category` | descritor `0x54` | Gênero/classificação de conteúdo. |
| `start` e `stop` | evento EIT | Início e duração do evento. |
| `channel@id` | associação interna | Liga o XMLTV ao `service_id` configurado no canal. |

O identificador XMLTV não precisa ser igual ao `service_id` do MPEG-TS. Essa
associação é feita no cadastro do canal no EPG Stream.

## Campos opcionais

```xml
<sub-title lang="pt-BR">Nome do episódio</sub-title>
<category lang="pt-BR">Esportes</category>
<episode-num system="xmltv_ns">0.5.0</episode-num>
<previously-shown />
<date>20260828</date>
<rating system="ClassInd">
  <value>10</value>
</rating>
```

Campos opcionais não reconhecidos não devem impedir a leitura do arquivo,
desde que o XML permaneça válido e os campos obrigatórios estejam presentes.

## Validações aplicadas pelo sistema

Um programa pode ser recusado quando:

- não possui canal;
- referencia um canal inexistente;
- não possui início ou fim válido;
- o horário final é igual ou anterior ao inicial;
- o XML está truncado ou malformado;
- existem identificadores de canal duplicados ou inconsistentes.

No modo Parse-XML, o sistema também pode sintetizar declarações de canal,
acrescentar UTC−03:00 a horários sem fuso, remover duplicações e manter relatório
dos programas descartados. Para uma integração nova, o fornecedor deve entregar
o XMLTV já no formato correto para evitar essas correções.

## Checklist para homologação

- [ ] O arquivo começa com declaração XML UTF-8.
- [ ] Todos os canais possuem `id` único e `display-name`.
- [ ] Todo `programme@channel` encontra um `<channel id="...">` correspondente.
- [ ] Todos os programas têm `start`, `stop` e `title`.
- [ ] Todos os horários possuem fuso explícito.
- [ ] `stop` é posterior a `start`.
- [ ] As sinopses não repetem o título nem a própria descrição.
- [ ] O arquivo pode ser analisado por um parser XML padrão.
- [ ] A URL de entrega permanece estável durante as atualizações.
- [ ] O servidor responde com HTTP 200 e conteúdo XML completo.

## Entrega recomendada

O provedor deve disponibilizar uma URL HTTPS estável, por exemplo:

```text
https://epg.provedor.example.com/cliente/guide.xml
```

O conteúdo dessa URL pode ser atualizado periodicamente sem alterar seu
endereço. Recomenda-se disponibilizar pelo menos o dia atual e os próximos sete
dias de programação.
