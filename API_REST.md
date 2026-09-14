# API REST do OMNIEPG

Versão da API: `v1`  
Versão inicial do produto com a API: `1.24.0`

A API usa as mesmas validações, licença e supervisão do painel. Uma portadora
criada ou alterada pela API é persistida em `/data`, respeita o limite de
canais licenciado e reinicia seu emissor quando necessário.

## Acesso e autenticação

Base: `http://SERVIDOR:9100/api/v1`

Todas as rotas exigem HTTP Basic com um usuário ativo do OMNIEPG. Gestão de
usuários, licença, configurações gerais e reinício global exigem perfil
`admin`. Use HTTPS em produção; HTTP Basic não cifra a senha.

```bash
export OMNI_URL=http://127.0.0.1:9100/api/v1
export OMNI_USER=epgadmin
read -rsp 'Senha: ' OMNI_PASSWORD; export OMNI_PASSWORD
curl --fail-with-body -u "$OMNI_USER:$OMNI_PASSWORD" "$OMNI_URL/system"
```

Contrato OpenAPI: `GET /api/v1/openapi.json`  
Documentação embarcada: `GET /api/v1/docs`

## Respostas e erros

As respostas são JSON UTF-8. Cadastros retornam `201`; consultas e mudanças
retornam `200`. Erros usam `{ "error": "mensagem" }`.

| Código | Significado |
|---:|---|
| 400 | campo ou operação inválida |
| 401 | credencial ausente ou incorreta |
| 402 | licença inválida ou limite excedido |
| 403 | usuário sem perfil administrativo |
| 404 | recurso inexistente |
| 500 | falha interna inesperada |

## Sistema, configuração, licença e usuários

```text
GET  /system
GET  /settings                         admin
PUT  /settings                         admin
GET  /license                          admin
PUT  /license                          admin
GET  /users                            admin
POST /users                            admin
PUT  /users/{user_id}                  admin
DELETE /users/{user_id}                admin
```

```bash
curl -u "$OMNI_USER:$OMNI_PASSWORD" -X PUT "$OMNI_URL/settings" \
  -H 'Content-Type: application/json' -d '{
    "xmltv_sync_mode":"interval", "xmltv_sync_minutes":60,
    "xmltv_daily_time":"04:15", "emitter_refresh_minutes":180,
    "emitter_retry_minutes":5, "detect_cache_updates":true
  }'

curl -u "$OMNI_USER:$OMNI_PASSWORD" -X POST "$OMNI_URL/users" \
  -H 'Content-Type: application/json' -d '{
    "username":"operador", "display_name":"Operador NOC",
    "role":"operator", "enabled":true, "password":"senha-forte-aqui"
  }'
```

Ao editar usuário, envie `username`, `display_name`, `role` e `enabled`;
`password` é opcional. Não é permitido excluir o usuário autenticado nem o
último administrador ativo.

## Fontes XMLTV

```text
GET    /sources
POST   /sources
GET    /sources/{source_id}
PUT    /sources/{source_id}
DELETE /sources/{source_id}?replacement_id={outra_fonte}
GET    /sources/{source_id}/catalog?force=0
POST   /sources/{source_id}/sync
```

```bash
curl -u "$OMNI_USER:$OMNI_PASSWORD" -X POST "$OMNI_URL/sources" \
  -H 'Content-Type: application/json' -d '{
    "name":"Fornecedor principal", "url":"https://epg.exemplo/guide.xml",
    "source_type":"xmltv", "is_default":false
  }'
```

`source_type` aceita `xmltv` ou `parse_xml`. O token interno de uma fonte
Parse-XML nunca é exposto. Excluir uma fonte em uso exige `replacement_id`.

## Portadoras e canais

```text
GET    /carriers
POST   /carriers
GET    /carriers/{carrier_id}
PUT    /carriers/{carrier_id}
DELETE /carriers/{carrier_id}
GET    /carriers/{carrier_id}/channels
POST   /carriers/{carrier_id}/channels
GET    /carriers/{carrier_id}/channels/{channel_id}
PUT    /carriers/{carrier_id}/channels/{channel_id}
DELETE /carriers/{carrier_id}/channels/{channel_id}
GET    /channels
```

Cadastro completo de uma portadora:

```bash
curl -u "$OMNI_USER:$OMNI_PASSWORD" -X POST "$OMNI_URL/carriers" \
  -H 'Content-Type: application/json' -d '{
    "name":"MOD-1 OUT-TS1", "source_id":"braziltvepg", "auto_start":true,
    "transport_stream_id":60, "original_network_id":60,
    "destination":"239.192.1.201", "port":5001,
    "interface_address":"10.0.0.10", "pmt_pid":4096,
    "bitrate":1000000, "ttl":32, "clock_mode":"standard",
    "clock_utc_offset_minutes":-180, "clock_correction_minutes":0,
    "services":[{
      "name":"SPORTV", "source_id":"", "epg_channel_id":"SPORTV.br",
      "service_id":2304, "default_category":"Esportes"
    }]
  }'
```

Para `PUT /carriers/{id}`, envie a representação completa. Para criar ou
editar somente um canal, o corpo contém `name`, `source_id` (vazio herda a
fonte da portadora), `epg_channel_id`, `service_id` e `default_category`.
Uma portadora precisa conservar ao menos um canal.

## Operação, grade e diagnóstico

```text
POST /carriers/{carrier_id}/actions/start
POST /carriers/{carrier_id}/actions/stop
POST /carriers/{carrier_id}/actions/restart
POST /carriers/{carrier_id}/actions/audit       corpo: {"seconds":8}
POST /carriers/actions/restart-all              admin
GET  /carriers/{carrier_id}/guide
GET  /carriers/{carrier_id}/logs
GET  /guides?start={epoch}&end={epoch}
GET  /errors
```

`/system` oferece o inventário operacional, estado dos processos, saúde do
EPG e licença. `/errors` retorna o diagnóstico por portadora e canal. O log é
limitado aos 30.000 caracteres mais recentes.

O histórico de cada fornecedor está em
`GET /sources/{source_id}/history`. O logo, embora oculto na interface atual,
continua disponível em `GET`, `PUT` e `DELETE`
`/carriers/{carrier_id}/channels/{channel_id}/logo`; o `PUT` recebe
`{"data":"data:image/png;base64,..."}`.

## Publicações XMLTV

```text
GET    /publications
POST   /publications                         JSON: {"name":"Minha grade"}
PUT    /publications/{publication_id}        JSON: {"name":"Novo nome"}
DELETE /publications/{publication_id}
POST   /publications/{publication_id}/versions?filename=guide.xml
DELETE /publications/{publication_id}/versions/{version_id}
```

O upload de versão recebe XML/XMLTV bruto, não JSON:

```bash
curl -u "$OMNI_USER:$OMNI_PASSWORD" --fail-with-body \
  -X POST "$OMNI_URL/publications/ID/versions?filename=guide.xml" \
  -H 'Content-Type: application/xml' --data-binary @guide.xml
```

A URL pública retornada pela publicação permanece estável enquanto os
arquivos versionados são substituídos conforme sua validade.

## Backup e atualização

```text
GET  /backup                 admin; devolve application/gzip
POST /restore                admin; recebe application/gzip e reinicia o serviço
GET  /update                 admin
POST /update/apply           admin; JSON: {"tag":"epg-native-v1.24.0-1"}
```

O backup contém configuração e arquivos persistidos de `/data`. O restore
substitui o estado persistente somente após validar integralmente o pacote e
mantém uma cópia local anterior. A atualização é exclusiva da instalação
nativa; ambientes Docker devem ser promovidos pelo procedimento de imagem e
rollback documentado no projeto.

## Segurança e compatibilidade

- Não coloque credenciais em URL, código-fonte ou logs.
- Restrinja a porta 9100 às redes administrativas ou use proxy HTTPS.
- IDs são opacos; use sempre os valores devolvidos pela API.
- A API nunca devolve hash de senha, token Parse-XML ou caminhos internos dos logos.
- Rotas antigas `/api/*` continuam ativas para o painel, mas integrações novas
  devem usar exclusivamente `/api/v1/*`.
- Mudanças incompatíveis futuras usarão outro prefixo (`/api/v2`).
