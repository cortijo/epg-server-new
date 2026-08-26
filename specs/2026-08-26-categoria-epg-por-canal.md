# Categoria de conteúdo no EPG com fallback por canal

- ID: `2026-08-26-categoria-epg-por-canal`
- Estado: concluído
- Versão-alvo: `1.8.0`

## Objetivo

Transmitir a categoria de cada evento na EIT ISDB-TB pelo descritor de conteúdo
`0x54`. Categorias existentes no XMLTV têm prioridade. Quando o evento não
possuir categoria reconhecida, o operador pode definir uma categoria padrão em
cada canal.

## Regras

- O fallback é opcional e persistido como `service.default_category`.
- Categorias suportadas: Filmes, Notícias, Entretenimento, Esportes, Infantil,
  Música, Cultura, Sociedade, Educação e Lazer.
- O emissor procura a primeira categoria XMLTV reconhecida; somente se não
  encontrar usa o fallback do canal.
- O descritor `0x54` permanece dentro da EIT no PID `0x0012`; nenhum PID novo é
  necessário no Dexing.
- Canais existentes sem o novo campo continuam compatíveis.

## Critérios de aceite

- [x] O formulário permite escolher categoria padrão por canal ou deixar em
  “Usar categoria do XMLTV”.
- [x] A API valida e persiste somente categorias suportadas.
- [x] Evento com categoria XMLTV reconhecida não é sobrescrito pelo fallback.
- [x] Evento sem categoria recebe o descritor `0x54` correspondente ao canal.
- [x] A alteração da categoria participa da versão/fingerprint da EIT.
- [x] O auditor identifica o descritor `0x54` numa captura multicast isolada.

## Rollback

Recriar o container com `epgserver:v1.7.0-20260826`. O campo adicional é
ignorado pela versão anterior, portanto não há migração destrutiva.

## Evidências

Em 26/08/2026, a suíte local executou 33 testes com sucesso; cinco testes de
firewall dependentes de Bash foram corretamente ignorados no Windows. A imagem
candidata foi compilada no servidor Linux e emitida em laboratório em
`239.255.250.54:55054`, TSID/ONID 81 e SID 2401.

A captura final apresentou simultaneamente `0x10` para o evento classificado
como Filmes no XMLTV e `0x40` para o evento sem categoria que recebeu o
fallback Esportes. O auditor terminou com `ok=true`, zero CRC, zero
descontinuidade e nenhum erro estrutural.

A versão foi publicada como imagem `epgserver:v1.8.0-20260826`, tag Git
`epg-v1.8.0` e commit funcional `0af3125`. Após o deploy, o health informou
`1.8.0`, o login administrativo respondeu HTTP 200 e as 27 portadoras ficaram
em execução, sem erros ou reinícios. O rollback preservado é o container
`epg-stream-pre-v1.8.0-20260826-095523` e os dados em
`/srv/epg-stream-backup-pre-v1.8.0-20260826-095523`.
