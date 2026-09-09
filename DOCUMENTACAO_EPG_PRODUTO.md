# EPG Stream — documentação autoritativa do produto independente

> Versão documentada para a entrega: **1.18.0**. Este documento é o ponto inicial obrigatório para manutenção do
> EPG Stream. As regras gerais do repositório continuam em `AGENTS.md` e o
> procedimento operacional compartilhado em `GUIA_OPERACIONAL_AGENTES.md`.

## 1. Finalidade e limite

EPG Stream é um produto separado do TVStream. Sua única responsabilidade é
transformar programação XMLTV em tabelas de serviço MPEG-TS compatíveis com o
perfil brasileiro ISDB-TB e entregá-las em UDP multicast para um multiplexador
ou modulador.

O produto **não** recebe o TS dos canais, não transcodifica mídia, não modifica
vídeo/áudio e não substitui o multiplexador. Ele emite um TS auxiliar leve, com
uma portadora lógica e vários serviços, para que o Dexing faça o passthrough dos
PIDs de EPG ao multiplex final.

```text
fonte XMLTV ─> cache/grade ─> gerador SI ─> UDP multicast auxiliar
                    │                         │
                    └─> painel ao vivo        └─> Dexing ─> multiplex/RF
canal original UDP ──────────────────────────────> Dexing ─> multiplex/RF
```

O TVStream existente permanece no executável, imagem, container, porta e volume
originais. O novo produto usa imagem `tvstream-epg`, container `epg-stream`,
porta HTTP 9100 e volume `/srv/epg-stream`.

### 1.1 Histórico funcional

| Versão | Entrega principal |
|---|---|
| 1.0.x | produto EPG independente, painel, XMLTV e multicast auxiliar |
| 1.1.x | usuários administradores/operadores e gestão de credenciais |
| 1.2.x | tabela compacta, grade sob demanda, clonagem e melhorias de UX |
| 1.3.0 | fonte XMLTV individual por canal e logo experimental |
| 1.3.1 | visualização autenticada do logo no painel |
| 1.4.0 | logo estrito ISDB-TB/ARIB: seis formatos, SDT `0xCF` e CDT `0xC8` |
| 1.5.0 | grade horizontal por portadora, navegação temporal e detalhes |
| 1.6.0 | publicações XMLTV normalizadas, versionadas e com URL permanente |
| 1.7.0 | BIT `0xC4` anuncia CDT de logo pelo descritor SI `0xD7` |
| 1.8.0 | categoria XMLTV/fallback por canal no descritor EIT `0x54` |
| 1.9.0 | fuso e correção de relógio configuráveis por portadora |
| 1.10.0 | licenciamento online por canais e interface de logo temporariamente oculta |
| 1.11.0 | gestão visual: Ver/Copiar chave v2 e instalação atômica no painel EPG |
| 1.12.x | bloqueio integral sem licença, reinício global e correção da sinopse EIT |
| 1.13.0 | simulador de receptor ISDB-TB com captura não intrusiva do TS gerado |
| 1.13.1 | `0x4D` leva somente o título e `0x4E` leva toda a sinopse no perfil ISDB-TB |
| 1.18.0 | Grade geral reúne todos os canais com dias, filtros, busca, navegação e zoom |
| 1.17.3 | PAT/PMT/SDT/BIT/EIT recebem versão persistente para invalidar cache após restart |

Tags são imutáveis. Uma correção posterior deve gerar nova versão; nunca mova
uma tag existente nem publique outra imagem com a mesma tag.

## 2. Componentes

| Componente | Arquivo | Responsabilidade |
|---|---|---|
| Painel/API | `epg-product/app.py` | autenticação, HTTP, CRUD e visão ao vivo |
| Store | `epg-product/app.py` | JSON atômico e schema do produto |
| GuideCache | `epg-product/app.py` | download, gzip, parsing e cache XMLTV |
| Publicações XMLTV | `epg-product/app.py` | upload, normalização, vigência e URL estável |
| Supervisor | `epg-product/app.py` | processos, auto-start, restart e logs |
| Cliente de licença | `epg-product/license_client.py` | validação online fail-closed e limite de canais |
| Servidor de licença | `license-server/` | geração, vínculo, limite e revogação das chaves |
| Emissor | `src/EpgOnlyMain.cpp` | sinalização, shaping CBR e socket multicast |
| Gerador EPG | `src/EpgInjector.cpp` | XMLTV, descritores, EIT, TDT e TOT |
| Imagem | `epg-product/Dockerfile` | build mínimo e runtime sem transcode |

O código C++ é compartilhado em fonte com o TVStream para evitar duas
implementações divergentes do formato ISDB-TB. Na imagem final, somente o
binário emissor é copiado. Não há binário do TVStream no produto EPG.

### 2.1 Mapa do repositório

| Caminho | Conteúdo |
|---|---|
| `epg-product/` | produto web, Docker, testes e arquivos operacionais |
| `src/EpgOnlyMain.cpp` | multiplex auxiliar, PAT/PMT/SDT/CDT e saída UDP |
| `src/EpgInjector.cpp` | XMLTV, EIT, TDT/TOT, descritores e carrosséis |
| `src/EpgInjector.h` | contrato compartilhado do gerador |
| `scripts/verify_isdbtb_ts.py` | auditor estrutural do TS ISDB-TB |
| `scripts/verify_epg_clock.py` | auditor de horário EIT/TDT/TOT |
| `epg-product/tests/` | testes do painel, API, dados e segurança |
| `license-server/` | autoridade de licenças, imagem e testes independentes |
| `specs/` | especificações e evidências de cada mudança |
| `ARQUITETURA_EPG_MULTICAST_ISDBTB.md` | detalhes binários das tabelas SI |
| `GUIA_OPERACIONAL_AGENTES.md` | método obrigatório de análise/release/deploy |

### 2.2 Inicialização e supervisão

`epg-product/app.py` cria o store, migra dados e logos legados, inicia o
supervisor e só então atende HTTP. Cada portadora ativa possui um processo
`TVStreamEpgOnly`. O supervisor registra PID, estado, erro, reinícios e cauda de
log. Uma saída inesperada com `auto_start=true` provoca nova tentativa após
três segundos; uma parada manual suspende essa recuperação até nova ação.

## 3. Modelo de dados

O arquivo `/data/epg-product.json` possui `schema_version`, `users`, `sources`,
`carriers` e `xmltv_publications`. A gravação ocorre em arquivo temporário, com `fsync`, seguida de
troca atômica. O arquivo final recebe permissão `0600`.

Exemplo sanitizado:

```json
{
  "schema_version": 3,
  "users": [{
    "id": "user-EXEMPLO",
    "username": "administrador",
    "role": "admin",
    "enabled": true,
    "password_hash": "HASH_PBKDF2_EM_BASE64",
    "password_salt": "SALT_ALEATORIO_EM_BASE64",
    "password_iterations": 310000
  }],
  "sources": [{
    "id": "braziltvepg",
    "name": "BrazilTVEPG (padrão)",
    "url": "https://EXEMPLO/grade.xml",
    "is_default": true
  }],
  "carriers": [{
    "id": "carrier-EXEMPLO",
    "name": "Portadora 72",
    "source_id": "braziltvepg",
    "auto_start": true,
    "transport_stream_id": 72,
    "original_network_id": 72,
    "destination": "239.192.1.201",
    "port": 5012,
    "interface_address": "10.0.0.10",
    "pmt_pid": 4096,
    "bitrate": 1000000,
    "ttl": 32,
    "clock_mode": "standard",
    "clock_utc_offset_minutes": -180,
    "clock_correction_minutes": 0,
    "signalling_version": 1,
    "services": [{
      "id": "service-2304",
      "name": "Canal Exemplo",
      "source_id": "fonte-especifica-ou-vazio-para-herdar",
      "epg_channel_id": "canal.exemplo.br",
      "service_id": 2304,
      "logo": {
        "enabled": true,
        "path": "/data/logos/carrier-EXEMPLO/service-2304-preview.png",
        "variants": {"0": "...-type-00.png", "5": "...-type-05.png"}
      }
    }]
  }]
}
```

Regras:

- o par multicast/porta não se repete entre portadoras;
- `service_id` é único dentro da portadora e deve ser igual ao Program Number
  do canal no Dexing;
- `services[].source_id` pode apontar para qualquer fonte cadastrada; vazio
  herda `carriers[].source_id`, que continua sendo a fonte padrão;
- TSID e ONID representam o multiplex final;
- o PID base de PMT ocupa uma sequência: base, base + 1 etc.;
- uma fonte usada não pode ser excluída;
- `signalling_version` é controlado pelo servidor e avança módulo 32 antes de
  cada início/reinício da portadora e quando o logo muda, para invalidar a
  sinalização em cache no mux e no receptor;
- IDs persistentes não são reciclados durante clonagem;
- `/api/state` omite URLs XMLTV; `/api/sources` é autenticado e permite edição.

Toda gravação passa por normalização e validação no servidor. Campos internos,
como versão de sinalização, caminhos de logo e hashes, não devem ser aceitos
como autoridade quando vierem do navegador.

Cada item de `xmltv_publications` mantém `id`, nome, token aleatório e uma lista
de versões. A versão contém vigência epoch UTC, hash SHA-256, contagens,
estatísticas de correção e caminho interno. Os XML normalizados ficam em
`/data/xmltv-publications/<publication-id>/<version-id>.xml`. Caminho e token
nunca são aceitos do navegador.

## 4. XMLTV e programação em tempo real

Desde a v1.14.1, uma fonte pode usar o tipo **Parse-XML**, destinado a
operadoras que entregam raiz `<tv>` e elementos `<programme>`, porém omitem
`<channel>`, timezone ou incluem eventos sem duração. O processo baixa a
origem, sintetiza canais válidos, aplica `-0300` quando não existe offset,
remove eventos inválidos, valida a grade integral e só então substitui o cache.

O emissor recebe uma URL interna estável e tokenizada e mantém seu ciclo normal
de atualização a cada três horas. Se a operadora falhar, a última cópia válida
permanece ativa. A URL original não aparece em `/api/state` e o token interno
não aparece em `/api/state` nem em `/api/sources`.

O cache aceita HTTP/HTTPS e detecta gzip pela extensão ou magic bytes. O limite
é 96 MiB após descompressão, o timeout é 30 segundos e o cache vale 300
segundos. O XML usa parsing
incremental; só são retidos programas de ontem até oito dias à frente.

Datas com offset são convertidas para UTC. Datas sem offset assumem UTC-3, em
coerência com o perfil atual. A API entrega epoch UTC e informa o fuso de
apresentação.

O supervisor também agrupa os canais por fonte: uma única portadora pode
consultar XMLTVs diferentes sem duplicar downloads dentro da janela do cache.
Para cada serviço, `/api/guide` retorna:

- `current`: programa cujo início <= agora < fim e percentual de progresso;
- `next`: primeiro programa que começa depois de agora;
- `schedule`: programas que cruzam o dia civil corrente;
- `fetched_at`: idade da fonte em cache.

O painel atualiza estado e guia a cada 15 segundos. Isso é tempo real em relação
à grade XMLTV; não há análise de conteúdo para detectar atraso da emissora.

Ao editar um canal, a lista do ID XMLTV deve ser obtida da fonte escolhida
naquele próprio canal. Alterar a fonte padrão não deve substituir escolhas
individuais já salvas.

### Catálogo de canais da fonte

Desde a v1.15.0, em **Fontes XMLTV**, use **Ver canais e programação** para
sincronizar e listar todos os canais encontrados. A tabela apresenta o nome,
ID XMLTV usado na associação, quantidade de programas na janela operacional,
programa no ar e até 24 entradas atuais/futuras expansíveis por canal.

Durante o download aparece **Sincronizando canais…**. O botão **Sincronizar
novamente** força uma nova consulta; a abertura comum pode reutilizar por cinco
minutos o cache já validado.

Para fontes Parse-XML, o painel também detalha o que foi necessário para tornar
o link aceitável: canais criados, timezone UTC−03:00 incluído, referências de
IDs ajustadas, duplicidades removidas e programas inválidos descartados. Esse
relatório é apenas diagnóstico e não altera PIDs ou a emissão multicast.

Na v1.15.1, **Consultar programas inválidos** mostra canal, título, datas
originais e motivo do descarte. O relatório fica persistido ao lado do cache
normalizado em `TOKEN.diagnostics.json`, modo `0600`, e permanece consultável
após reinício. Por segurança, são detalhados até 2.000 eventos e o excedente é
informado separadamente. O catálogo também possui busca instantânea por nome ou
ID XMLTV; a filtragem ocorre no navegador e não renova a fonte.

### 4.1 Publicações XMLTV versionadas

O módulo **Publicações XMLTV** recebe XML, XMLTV ou GZIP autenticado com limite
de 96 MiB. O conversor lê o documento completo, exige raiz `<tv>` e executa:

1. consolidação de declarações repetidas de canal;
2. inclusão de `-0300` em timestamps sem offset;
3. preservação de timestamps com `Z` ou `±HHMM`;
4. reconciliação de `programme@channel` pelo prefixo numérico quando existe um
   único `<channel id>` candidato;
5. criação de declaração mínima para referência ainda não declarada;
6. descarte de programa sem canal, data válida ou duração positiva;
7. nova validação pelo parser do painel;
8. gravação atômica e cálculo de SHA-256.

A vigência corresponde ao menor início e maior término dos eventos válidos. A
URL `/xmltv/<token>.xml` não exige Basic Auth porque é consumida pelo emissor,
mas utiliza token de 128 bits. A cada GET, o servidor seleciona a versão que
abrange o instante atual; em sobreposição vence a versão com início mais novo.
Sem versão vigente, entrega a próxima e, sem futura, a expirada mais recente.
Assim a URL não muda e a rotação não depende de cron. O cache de fontes pode
postergar a percepção da troca por até 300 segundos.

`EPG_PUBLIC_BASE_URL` é opcional e deve conter somente esquema e autoridade,
por exemplo `http://181.233.106.46:9100`. Quando definido, o painel usa essa
base no botão **Copiar URL**; sem ela, usa a origem atual do navegador.

## 5. Transporte ISDB-TB

Cada portadora gera um MPEG-TS CBR auxiliar com datagramas UDP de 1316 bytes
(7 × 188). O espaço não usado é preenchido pelo PID nulo `0x1FFF`.

| PID/tabela | Uso |
|---|---|
| `0x0000` PAT | relaciona todos os SIDs aos PIDs de PMT |
| PMT base + N | PMT vazia do serviço auxiliar, sem PCR |
| `0x0011` SDT | nomes, fornecedor, TSID e ONID |
| `0x0012` EIT | present/following e schedule de todos os serviços |
| `0x0014` TDT/TOT | relógio e offset brasileiro |
| `0x0024` BIT | anuncia CDT `0xC8` quando há logo habilitado |
| `0x0029` CDT | seis formatos de logo ARIB, quando habilitado |
| `0x1FFF` | preenchimento CBR |

O agregador nunca intercala pacotes de duas seções EIT. A continuidade é
reescrita globalmente por PID após combinar serviços. Detalhes de descritores,
codificação, segmentação, CRC e horários estão em
[`ARQUITETURA_EPG_MULTICAST_ISDBTB.md`](ARQUITETURA_EPG_MULTICAST_ISDBTB.md).

PAT, PMT, SDT, BIT e a versão inicial da EIT compartilham a versão persistente
da portadora. O emissor ainda avança a versão da EIT quando o fingerprint da
programação muda em execução. Cadastrar o input no Dexing pode exigir o
primeiro **Parse Program**, mas reinícios posteriores não devem depender dessa
operação para que uma EIT nova substitua a tabela em cache.

Cada evento pode transportar o descritor de conteúdo `0x54`. O emissor usa a
primeira categoria XMLTV reconhecida e, na ausência dela, o campo
`default_category` configurado no canal. O fallback não substitui uma categoria
válida da programadora e não exige PID adicional além da EIT `0x0012`.

Cada portadora também possui um modo de relógio. `standard` preserva o
comportamento histórico UTC-03:00 sem correção. `custom` permite escolher o
fuso entre UTC-12:00 e UTC+14:00, em passos de 15 minutos, e aplicar uma
correção de -1440 a +1440 minutos. A correção é relativa ao relógio do host e,
portanto, continua avançando; ela não congela uma data/hora. O mesmo
referencial civil é aplicado à EIT `0x0012` e a TDT/TOT `0x0014`, enquanto o
descritor `0x58` da TOT anuncia somente o fuso escolhido.

Cadências atuais do emissor:

| Conteúdo | Intervalo |
|---|---:|
| PAT e PMTs | 100 ms |
| SDT | 500 ms |
| BIT de anúncio da CDT | 1 s |
| CDT de logo | 1 s |
| slot de emissão do gerador EPG | 20 ms |
| reinício do carrossel EIT | 2 s |
| ciclo present/following | 2 s |
| atualização TDT/TOT | 5 s |

Uma seção MPEG-TS não pode exceder 4093 bytes. A SDT é dividida para suportar
até 64 serviços e a EIT schedule respeita segmentação e CRC próprios.

### 5.1 Logotipos ISDB-TB

Cada serviço pode manter `logo.path`, usado apenas como miniatura autenticada
no painel, e `logo.variants`, um mapa dos tipos ARIB `0` a `5`. As variantes
possuem dimensões `48x24`, `36x24`, `48x27`, `72x36`, `54x36` e `64x36`, usam
a CLUT comum de 128 cores e omitem `PLTE`/`tRNS`; o receptor fornece a paleta.
O emissor publica o descritor `0xCF` na SDT/PID `0x0011`, uma BIT `0xC4` no
PID `0x0024` cujo segundo loop anuncia `0xC8` em um descritor SI `0xD7`, e seis
CDT `0xC8` no PID `0x0029`. `signalling_version` pertence ao servidor e avança módulo 32 a
cada inclusão, troca, remoção ou migração de logo, evitando cache de uma SDT
anterior. Registros v1.3 com arquivo único são convertidos no início, antes de
qualquer processo emissor ser iniciado.

O PNG enviado pelo usuário aceita no máximo 2 MiB. O backend normaliza a
imagem, gera as seis variantes e valida que cada uma cabe na seção CDT antes de
persistir. A versão 1.3 transmitia PNG comum e por isso podia aparecer no painel
sem ser aceita por televisores; a v1.4 corrige esse contrato. A confirmação
exige captura do PID `0x0029` e auditoria do descritor `0xCF`, não apenas a
miniatura visual.

## 6. Ciclo de um emissor

1. O supervisor lê a portadora e resolve sua fonte.
2. Inicia `/app/TVStreamEpgOnly` com configuração no ambiente do processo.
3. O emissor baixa a fonte, monta tabelas e abre o socket multicast.
4. stderr/stdout é anexado a `/data/logs/<id>.log`.
5. Se sair com `auto_start`, o supervisor registra, aguarda três segundos e
   tenta novamente.
6. Parada manual impede restart até Start/Restart.
7. Editar reinicia somente aquela portadora.

Variáveis internas:

```text
EPG_STREAM_ID, EPG_STREAM_NAME, EPG_SOURCE_URL, EPG_SERVICES_JSON,
EPG_TSID, EPG_ONID, EPG_DESTINATION, EPG_PORT, EPG_INTERFACE,
EPG_PMT_PID, EPG_BITRATE, EPG_TTL, EPG_SIGNAL_VERSION
EPG_CLOCK_UTC_OFFSET_MINUTES, EPG_CLOCK_CORRECTION_SECONDS
```

Não registre essas variáveis: fontes comerciais podem conter credenciais.

## 7. API

Todos os endpoints, exceto `/health` e a URL pública `/xmltv/TOKEN.xml`, usam
HTTP Basic.

| Método | Endpoint | Função |
|---|---|---|
| GET | `/health` | saúde e versão |
| GET | `/xmltv/TOKEN.xml` | XMLTV normalizado selecionado, sem autenticação |
| GET | `/` | painel web autenticado |
| GET | `/api/session` | usuário autenticado e perfil |
| GET | `/api/users` | listar usuários (somente administrador) |
| POST | `/api/users` | criar/editar usuário (somente administrador) |
| POST | `/api/users/delete` | excluir usuário (somente administrador) |
| GET | `/api/state` | portadoras e runtime, sem URLs |
| GET | `/api/license` | forçar checagem e retornar estado público da licença |
| GET | `/api/sources` | fontes para administração |
| GET | `/api/publications` | publicações, versões, vigências e situação |
| POST | `/api/publications` | criar ou renomear publicação |
| POST | `/api/publications/upload?id=ID&filename=NOME` | upload XMLTV bruto, até 96 MiB |
| POST | `/api/publications/version/delete` | excluir uma versão e recalcular seleção |
| POST | `/api/publications/delete` | excluir arquivos e invalidar a URL |
| GET | `/api/logo?carrier_id=ID&service_id=ID` | miniatura PNG autenticada |
| POST | `/api/sources` | criar/editar fonte |
| POST | `/api/sources/test` | baixar e contar fonte |
| POST | `/api/sources/delete` | excluir fonte sem uso |
| GET | `/api/catalog?source_id=ID` | canais do XMLTV |
| GET | `/api/guide?carrier_id=ID` | agora/próximo/grade |
| POST | `/api/carriers` | criar/editar portadora |
| POST | `/api/carriers/logo` | enviar PNG e gerar seis variantes ARIB |
| POST | `/api/carriers/logo/delete` | remover variantes e atualizar sinalização |
| POST | `/api/carriers/start` | iniciar emissor |
| POST | `/api/carriers/stop` | parar sem auto-restart |
| POST | `/api/carriers/restart` | reiniciar emissor |
| POST | `/api/carriers/restart-all` | reiniciar todos os emissores elegíveis (administrador) |
| POST | `/api/carriers/delete` | parar e excluir |
| GET | `/api/logs?carrier_id=ID` | últimas linhas |

Mutações recebem JSON e retornam `{"result":"ok"}` ou status 4xx/5xx com
`{"error":"mensagem"}`. Corpo vazio ou acima de 3 MiB e Origin divergente são
rejeitados. A API de logo recebe PNG em data URL/base64 dentro desse envelope.
O endpoint de upload XMLTV é a única mutação que recebe corpo binário bruto e
usa o limite específico de 96 MiB.

As ações dinâmicas aceitas em `/api/carriers/<ação>` são somente `start`,
`stop` e `restart`. Qualquer outra rota deve retornar 404.

### 7.1 Licenciamento por canais

A versão 1.10.0 conta todos os elementos `services` persistidos. O cliente lê
a chave de um arquivo montado somente leitura e envia chave, identificador da
instalação e total de canais para `POST /api/validate` do servidor independente.
O painel e `/api/state` recebem somente o estado público, nunca a chave.

O comportamento é fail-closed: configuração ausente, resposta inválida,
indisponibilidade, revogação, expiração, vínculo divergente ou excesso de
canais interrompem os emissores. O painel continua disponível, mas somente
Usuários e Licença permanecem operáveis; fontes, publicações, grade, logs e
gestão de portadoras retornam HTTP 402. `/health` retorna 503. `start`,
`restart` e `restart-all` também revalidam antes de executar. Quando a licença
volta a ser válida, o supervisor retoma os emissores que não estavam parados
manualmente.

Variáveis obrigatórias:

```text
EPG_LICENSE_SERVER_URL=http://127.0.0.1:9200
EPG_LICENSE_KEY_FILE=/run/secrets/epg_license_key
EPG_LICENSE_INSTALLATION_ID=identificador-estavel
EPG_LICENSE_CHECK_SECONDS=43200
```

O servidor, sua API, bootstrap, backup e limites de segurança estão documentados
em `license-server/README.md`. Em hosts diferentes, a URL deve usar HTTPS.

## 8. Segurança

- `EPG_ADMIN_USER` e `EPG_ADMIN_PASSWORD` são usados somente no primeiro
  bootstrap ou na migração do schema 1;
- depois da migração, as credenciais são persistidas e o container pode ser
  executado sem essas variáveis;
- senhas exigem no mínimo dez caracteres e nunca são gravadas em texto puro;
- o hash usa PBKDF2-HMAC-SHA256, salt aleatório e 310.000 iterações;
- comparação do hash usa tempo constante;
- o perfil `admin` gerencia usuários e todo o produto; o perfil `operator`
  opera fontes, portadoras, guia e logs, mas não acessa usuários;
- o último administrador ativo não pode ser desativado, rebaixado ou excluído;
- imagem roda como UID/GID 10001, sem capabilities e novos privilégios;
- filesystem é somente leitura; `/data` e `/tmp` são exceções;
- CSP, anti-frame e anti-MIME-sniff estão ativos;
- use proxy HTTPS, VPN ou rede administrativa; o produto não termina TLS.
- a chave de licença fica em arquivo externo ao volume da aplicação e não
  aparece no estado, logs ou `docker inspect`;
- a autoridade persiste apenas SHA-256 da chave e deve ficar em loopback ou
  atrás de HTTPS.

Para comercialização futura: Argon2id, auditoria imutável, recuperação de senha,
segundo fator, assinatura de imagens e serviço de licenças redundante. Não
misture essas funções ao gerador MPEG-TS.

## 9. Build e testes

Na raiz:

```bash
python3 -m unittest discover -s epg-product/tests -v
python3 -m unittest discover -s license-server/tests -v
python3 -m py_compile epg-product/app.py epg-product/license_client.py license-server/app.py
docker build -f epg-product/Dockerfile -t epgserver:v1.10.0-20260826 .
docker build -f license-server/Dockerfile -t epg-license-server:v1.0.0-20260826 .
docker image inspect epgserver:v1.10.0-20260826
```

O runtime instala somente Python, libcurl, JsonCpp e Boost. Adicionar
GStreamer/FFmpeg indicaria violação de escopo.

## 10. Implantação segura

### 10.1 Instalador interativo recomendado

Na raiz de um clone completo do repositório:

```bash
chmod +x scripts/install.sh
sudo ./scripts/install.sh
```

O instalador solicita a porta HTTP, diretório persistente, nome do container,
tag imutável da imagem, fuso, URL pública opcional e, em volume vazio, as credenciais do primeiro
administrador. Ele recusa `latest`, tags locais já existentes, caminhos de dados
relativos, nomes inválidos e portas fora do intervalo permitido.

O fluxo operacional é:

1. validar privilégios e Docker;
2. instalar `docker.io` somente com confirmação e apenas em Debian/Ubuntu,
   quando o Docker estiver ausente;
3. coletar e validar as configurações;
4. construir a imagem antes de interromper uma versão existente;
5. criar backup do volume e preservar o container anterior em atualização;
6. iniciar com rede host, UID/GID `10001`, raiz somente leitura, `/tmp` em
   `tmpfs`, todas as capabilities removidas e `no-new-privileges`;
7. consultar `/health` de dentro do container;
8. em primeira instalação, remover o container de bootstrap e recriá-lo sem as
   variáveis `EPG_ADMIN_USER` e `EPG_ADMIN_PASSWORD`;
9. restaurar automaticamente o container anterior quando o health check falhar.

O script não altera firewall. Ao terminar, ele informa qual porta TCP precisa
ser autorizada para as redes administrativas. Cada atualização precisa receber
uma tag nova; tags existentes não são sobrescritas.

Em atualização bem-sucedida, os caminhos exatos do container anterior e do
backup são impressos e devem permanecer preservados até a homologação. Para
rollback manual:

```bash
docker rm -f epg-stream
docker rename epg-stream-pre-AAAAMMDD-HHMMSS epg-stream
docker update --restart=unless-stopped epg-stream
docker start epg-stream
curl -fsS http://127.0.0.1:9100/health
```

Restaure o backup dos dados somente se houver incompatibilidade comprovada e
depois de preservar uma cópia do estado que falhou.

### 10.2 Instalação manual

```bash
sudo install -d -o 10001 -g 10001 -m 0750 /srv/epg-stream
sudo install -d -o 10001 -g 10001 -m 0750 /srv/epg-stream/logs
cp epg-product/.env.example epg-product/.env
# definir credenciais fortes sem commitar .env
docker compose --env-file epg-product/.env \
  -f epg-product/docker-compose.yml up -d
curl -fsS http://127.0.0.1:9100/health
```

Use `network_mode: host`. Confirme a interface antes de cadastrar:

```bash
ip -br address
ip route get 239.192.1.201
```

Restrinja TCP/9100 à administração. Multicast é tráfego de saída.

### 10.3 Estado implantado em 26/08/2026

```text
imagem:    epgserver:v1.10.0-20260826
container: epg-stream
rede:      host
restart:   unless-stopped
processo:  UID/GID 10001:10001
volume:    /srv/epg-stream -> /data
licença:   epg-license-server:v1.0.0-20260826 em 0.0.0.0:9200
limite:    63/100 canais
HTTP:      0.0.0.0:9100 no namespace de rede do host
XMLTV:     http://181.233.106.46:9100/xmltv/<token>.xml
```

Com `network_mode: host`, o container compartilha as interfaces, rotas e portas
do Linux. Não existe publicação Docker `-p 9100:9100`: a aplicação abre a porta
9100 diretamente no host e envia multicast pela interface escolhida no painel.

Verificação sem expor credenciais:

```bash
docker inspect epg-stream --format \
  'image={{.Config.Image}} network={{.HostConfig.NetworkMode}} restart={{.HostConfig.RestartPolicy.Name}} status={{.State.Status}} restarts={{.RestartCount}}'
docker inspect epg-stream --format \
  '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
curl -fsS http://127.0.0.1:9100/health
```

Rollback preservado nesta implantação:

- container: `epg-stream-pre-v1.6.0-20260825-214509`, com a imagem v1.5.0;
- dados: `/srv/epg-stream-backup-pre-v1.6.0-20260825-214509`.

Rollback imediato da v1.7.0:

- container: `epg-stream-pre-v1.7.0-20260826-085500`, com a imagem v1.6.0;
- dados: `/srv/epg-stream-backup-pre-v1.7.0-20260826-085500`.

Rollback imediato da v1.8.0:

- container: `epg-stream-pre-v1.8.0-20260826-095523`, com a imagem v1.7.0;
- dados: `/srv/epg-stream-backup-pre-v1.8.0-20260826-095523`.

Rollback imediato da v1.9.0:

- container: `epg-stream-pre-v1.9.0-20260826-124740`, com a imagem v1.8.0;
- dados: `/srv/epg-stream-backup-pre-v1.9.0-20260826-123821`.

A implantação concorrente v2.0.0, que retornava erro HTTP 500, foi parada e
preservada em `epg-stream-disabled-v2.0.0-20260826-125556`.

Validação posterior ao deploy da v1.9.0: health da versão `1.9.0`, autenticação
administrativa HTTP 200, 27 portadoras em execução, zero portadoras em erro e
zero reinícios do container.

Rollback imediato da v1.10.0:

- container: `epg-stream-pre-v1.10.0-20260826-174835`, com a imagem v1.9.0;
- dados: `/srv/epg-stream-backup-pre-v1.10.0-20260826-174835`;
- dados da autoridade: volume Docker `epg-license-data`;
- chave do cliente: volume Docker `epg-license-client-secret`.

Validação posterior ao deploy da v1.10.0: health EPG e licença HTTP 200,
versões `1.10.0` e `1.0.0`, 27 portadoras, 63 canais, 27 emissores em execução,
zero erros, zero reinícios e licença válida `63/100`. Após aceite explícito do
operador, o servidor de licenças passou a escutar em `0.0.0.0:9200`, com acesso
externo restrito aos sets `trusted_ipv4`/`trusted_ipv6` do nftables. O container
final não mantém usuário ou senha de bootstrap em suas variáveis de ambiente.

### 10.4 Comportamento da tabela do painel

- uma linha representa uma portadora;
- ações operacionais permanecem na última coluna;
- a programação começa recolhida;
- o refresh de estado não consulta `/api/guide`;
- a primeira expansão consulta somente a portadora clicada;
- ao recolher, o cache daquela portadora é descartado para que a próxima
  expansão obtenha a grade atualizada;
- **Ver grade** abre o detalhamento diário do serviço selecionado.

### 10.5 Clonagem de portadora

- **Clonar** prepara uma nova portadora com a configuração técnica e os
  serviços da original;
- o ID da portadora e os IDs internos dos serviços não são reutilizados;
- o destino multicast é deliberadamente limpo para evitar colisão;
- a inicialização sugerida é manual;
- cancelar não persiste nada; salvar exige um novo multicast válido;
- a portadora original não é parada, reiniciada nem modificada.

### 10.6 Fechamento de Fontes XMLTV

O cabeçalho da janela de Fontes XMLTV contém **Fechar**. O botão chama apenas
`closeModal()`, não aciona endpoints e não altera a configuração persistida.

### 10.7 Funcionalidades atuais do painel

- administração de usuários e troca de senha pelo perfil administrador;
- CRUD e teste de fontes XMLTV;
- criação, edição e clonagem de portadoras;
- relógio padrão ou fuso/correção personalizados por portadora;
- fonte padrão por portadora e sobreposição de fonte por canal;
- upload, troca, remoção e miniatura autenticada do logo;
- Start, Stop, Restart, exclusão e consulta de logs;
- reinício conjunto de todos os fluxos elegíveis pelo administrador;
- bloqueio visual e operacional das funções de XMLTV/grade/portadoras quando a
  licença estiver inválida, com alerta explícito para contato com o suporte;
- tabela compacta com ações laterais;
- programação recolhida, carregada somente ao clicar;
- visão do programa atual, próximo, progresso e grade diária;
- grade consolidada de três horas com filtro por portadora, blocos
  proporcionais, marcador do horário atual e navegação em passos de 90 minutos;
- clique em uma atração da linha do tempo abre horário, descrição e categoria;
- formulários fecham somente por Salvar, Cancelar ou botão Fechar; clique no
  fundo não descarta edição.

### 10.8 Gerenciador declarativo de firewall

O utilitário `scripts/firewall-manager.sh` é separado do instalador do produto.
Ele administra somente `table inet epg_managed`, no tráfego TCP/UDP destinado
ao host. Não limpa o ruleset global, não toca FORWARD/NAT e não altera chains
do Docker. Multicast de saída e emissores em execução permanecem fora do seu
escopo.

Instalação inicial:

```bash
chmod +x scripts/firewall-manager.sh
sudo ./scripts/firewall-manager.sh install
```

Quando executado por SSH, `init` cadastra automaticamente o IP remoto como
`/32` ou `/128` e a porta local daquela sessão. A instalação cria a unit, mas
não a habilita nem carrega regras antes de uma aplicação validada.

Cadastro e aplicação:

```bash
sudo epg-firewall network add 45.224.164.0/22
sudo epg-firewall network add 2804:44f0::/32
sudo epg-firewall port add tcp 22
sudo epg-firewall port add tcp 9100

sudo epg-firewall list
sudo epg-firewall check
sudo epg-firewall render
sudo epg-firewall apply
```

Portas aceitam valor único ou intervalo, como `5000-5010`. Redes são
canonicalizadas e persistidas em `/etc/epg-firewall.conf`. Alterar o arquivo ou
usar `add/remove` não muda o firewall até `apply`. Cada aplicação faz
`nft -c`, troca a tabela em uma única transação e atualiza
`/etc/epg-firewall.nft`. A primeira aplicação bem-sucedida habilita a unit para
reaplicar a configuração no boot.

Proteções operacionais:

- a configuração precisa ter pelo menos uma rede e uma porta;
- em SSH, o IP remoto e a porta do servidor precisam estar autorizados;
- `--force` é recusado dentro de SSH e serve apenas como confirmação no console;
- `--dry-run --force apply` renderiza sem exigir root ou nftables;
- loopback, conexões estabelecidas/relacionadas, ICMP e ICMPv6 são preservados;
- regras anteriores de UFW/firewalld continuam existindo e ainda podem bloquear
  algo aceito por esta tabela; revise conflitos antes da adoção.

Rollback pelo console do provedor:

```bash
sudo epg-firewall disable
sudo systemctl disable epg-firewall.service
```

`disable` remove somente `table inet epg_managed`. Sempre mantenha um console
fora de banda disponível na primeira aplicação.

## 11. Validação do TS

Use multicast/porta de laboratório:

```bash
tcpdump -ni INTERFACE 'host 239.192.1.201 and udp port 5012'

timeout 20 ffmpeg -hide_banner -loglevel error \
  -i 'udp://@239.192.1.201:5012?overrun_nonfatal=1&fifo_size=5000000' \
  -map 0 -c copy -f mpegts /tmp/epg-portadora.ts

python3 scripts/verify_isdbtb_ts.py /tmp/epg-portadora.ts
python3 scripts/verify_epg_clock.py /tmp/epg-portadora.ts
```

Confirme datagramas de 1316 bytes, bitrate, todas as tabelas, CRC, continuidade,
SIDs/TSID/ONID, EIT `0x50/0x51`, relógio e coerência com `/api/guide`. Processo
ativo, PID existente ou VLC abrir não bastam como homologação.

### 11.1 Teste funcional de usuários

1. Entre no painel como administrador e abra **Usuários**.
2. Crie um operador com senha de pelo menos dez caracteres.
3. Em uma sessão anônima, confirme que o operador autentica e não acessa a
   administração de usuários.
4. Troque a senha do operador; a antiga deve retornar 401 e a nova deve entrar.
5. Desative o operador; sua autenticação deve retornar 401.
6. Confirme que o backend recusa remover, desativar ou rebaixar o último
   administrador ativo.

Essas operações não reiniciam emissores e não alteram os parâmetros MPEG-TS.

## 12. Configuração do Dexing

Para cada portadora:

1. adicione o multicast auxiliar como Input Data;
2. faça Parse Program e confirme os serviços;
3. leve os canais originais para o mesmo Output TS;
4. para EPG sem logo, encaminhe `0x0012 -> 0x0012` e
   `0x0014 -> 0x0014`;
5. para EPG com logo, encaminhe também `0x0011 -> 0x0011`,
   `0x0024 -> 0x0024` e `0x0029 -> 0x0029`;
6. mantenha PAT/PMT/NIT do multiplex conforme o Dexing;
7. associe pelo SID: `service_id` deve ser o Program Number do canal;
8. habilite SDT do EPG sem criar duas SDTs concorrentes no mesmo multiplex;
9. aplique a configuração e valide primeiro no TS de saída e depois no RF.

Todos os canais com mesmo TSID/ONID compartilham um multicast EPG. Outro
TSID/ONID usa outra portadora e outro multicast auxiliar.

No Dexing, **Input Channel** no PID PASSTHRU é o número do Input Data auxiliar,
não o SID. Exemplo: se a portadora EPG aparece como input 4, todas as quatro
linhas usam Input Channel `4`. O receptor associa EIT e logo ao canal por
TSID/ONID/SID; nome semelhante não é suficiente.

## 13. Atualização e rollback

Antes de atualizar:

```bash
sudo cp -a /srv/epg-stream /srv/epg-stream-backup-pre-VERSAO-DATA
docker inspect epg-stream --format '{{.Config.Image}}'
docker stop epg-stream
docker rename epg-stream epg-stream-pre-VERSAO-DATA
docker update --restart=no epg-stream-pre-VERSAO-DATA
```

Inicie a nova tag imutável com o mesmo volume. No rollback, pare o novo,
restaure o JSON apenas se houve migração e volte o anterior. Não remova imagem
ou container anterior antes da aceitação.

### 13.1 Release reproduzível

1. confirme `git status --short`, branch e remoto;
2. conclua a spec e execute toda a matriz proporcional ao risco;
3. faça commit e push; obtenha o SHA remoto;
4. crie tag `epg-vMAJOR.MINOR.PATCH` somente no commit validado;
5. construa imagem imutável `tvstream-epg:vMAJOR.MINOR.PATCH-AAAAMMDD`;
6. registre image ID/digest e nunca reutilize a tag;
7. inicie uma cópia isolada com outro HTTP, diretório e multicast;
8. valide API, persistência, TS e, quando aplicável, Dexing;
9. preserve backup e container anterior antes do corte;
10. faça o corte mantendo o mesmo volume e confirme health/restarts/pacotes;
11. atualize a spec com commit, tag, imagem, evidências e rollback.

Não use `latest` em produção. O arquivo compose do repositório pode conter uma
tag histórica; compare-o com a versão aprovada antes de executar.

## 14. Observabilidade e diagnóstico

```bash
docker logs --since 10m epg-stream
docker top epg-stream
ss -lntup | grep 9100
tail -n 200 /srv/epg-stream/logs/ID.log
```

| Sintoma | Verificação |
|---|---|
| portadora reinicia | URL XMLTV, DNS, interface e log |
| sem pacote | interface, rota multicast e captura física |
| sem programa | ID XMLTV exato e horários da fonte |
| EPG não aparece | SID ou passthrough do PID 0x12 |
| horário errado | offset XMLTV, TDT/TOT e fuso do modulador |
| só um canal tem EPG | SIDs duplicados ou portadora errada |

## 15. Continuidade por outro agente de IA

Antes de alterar:

1. leia `AGENTS.md`, `GUIA_OPERACIONAL_AGENTES.md`,
   `DOCUMENTACAO_CODEBASE.md`, este arquivo e a spec ativa;
2. confirme Git limpo, remoto, branch, tags e versão implantada;
3. reproduza em multicast/porta isolados;
4. crie nova spec em `specs/`;
5. preserve o TVStream e não altere `EpgInjector` sem testar os dois produtos.

Para controle/painel: unittest, imagem, autenticação, erros HTTP, persistência,
restart, fonte normal/gzip, catálogo e grade.

Para MPEG-TS: documente PIDs/SIDs/TSID/ONID, valide CRC, continuidade,
segmentação multipacote e relógio; teste 1 e vários serviços; execute a suíte do
TVStream e homologue em Dexing/ISDB-T.

Matriz mínima por tipo de mudança:

| Mudança | Validação obrigatória |
|---|---|
| HTML/CSS/JS | unittest, navegador desktop/mobile, ações e erros HTTP |
| autenticação/usuários | 401/403, admin/operator, último admin e troca de senha |
| schema/store | migração de cópia real sanitizada, gravação atômica e restart |
| XMLTV | XML simples/gzip, mais de uma fonte, timezone e cache |
| logo | seis variantes, preview, migração, SDT `0xCF`, CDT `0xC8` e Dexing |
| EIT/relógio | auditor TS, CRC, continuidade, p/f, schedule, TDT/TOT |
| supervisor | start/stop/restart, auto-restart e preservação dos outros emissores |
| Docker | usuário 10001, read-only, volume, host network, health e rollback |

Fluxo obrigatório:

```text
diagnóstico -> spec -> alteração mínima -> testes -> revisão de segredos
-> commit/push -> tag -> imagem imutável -> teste paralelo -> deploy -> evidência
```

Registre na spec resultados, image ID, commit, tag, health, captura e rollback.
Falha em teste impede release e implantação.

### 15.1 Contratos que não podem ser quebrados

- não incorporar transcode, vídeo ou áudio ao EPG Stream;
- não alterar o TVStream como efeito colateral sem matriz própria;
- não trocar SID, TSID, ONID ou PID automaticamente em dados existentes;
- não emitir duas seções simultaneamente no mesmo PID com continuidade
  independente;
- não transmitir URL XMLTV em `/api/state` nem expor segredos em logs;
- não confiar em caminhos ou versões de logo enviados pelo navegador;
- não reiniciar todas as portadoras ao editar uma única portadora;
- não excluir fonte em uso por portadora ou canal;
- não permitir que o último administrador ativo seja removido;
- não usar rede bridge quando o requisito for saída multicast pela interface do
  host;
- não declarar sucesso apenas porque o processo está online: capture e audite o
  TS efetivamente transmitido.

### 15.2 Sequência para diagnosticar antes de editar

1. reproduza o sintoma e anote portadora, SID, TSID/ONID, destino e horário;
2. confira health, imagem ativa, reinícios e log específico;
3. consulte estado/guia e compare com o XMLTV;
4. capture na interface física e confirme PIDs/bitrate;
5. se o TS estiver correto, revise passthrough e associação no Dexing;
6. escreva hipótese, evidência e spec antes de mudar código;
7. teste a correção isoladamente e preserve a versão anterior.

## 16. Melhorias futuras separáveis

- proxy HTTPS no compose;
- segundo fator, recuperação de senha e auditoria imutável;
- métricas Prometheus e alerta de idade;
- rotação de logs;
- refresh XMLTV em background;
- upload local de XMLTV;
- licenciamento e identidade visual própria;
- atualização progressiva por portadora.

Essas melhorias não devem introduzir processamento de vídeo/áudio.

## 17. Simulador de TV / PIDs

Na barra superior, **Simular TV / PIDs** permite escolher uma portadora ativa e
capturar oito segundos dos mesmos datagramas que o emissor envia ao socket
multicast. A captura é uma cópia local: ela não abre outro receptor multicast,
não altera o pacing e não interrompe o fluxo.

Uso:

1. inicie a portadora que deseja validar;
2. clique em **Simular TV / PIDs**;
3. escolha a portadora e clique em **Capturar e analisar**;
4. confira CRC, continuidade, TSID, ONID, SIDs e a presença dos PIDs;
5. em **Como a TV recebe os eventos**, compare o título, o texto curto `0x4D`,
   a continuação `0x4E` e o texto final reconstruído.

O recurso audita a saída do EPG Server, antes do Dexing. Para a saída RF ainda
é necessário capturar o multiplex final. No Dexing, o mínimo para EPG/relógio é
`0x0012 -> 0x0012` e `0x0014 -> 0x0014`; SDT/NIT/BIT/CDT dependem do desenho do
multiplexador e dos recursos habilitados.

Implementação: o painel grava `/data/diagnostics/<id>.request`; o emissor copia
um número limitado de datagramas para `.ts.tmp` e publica `.ts` por rename
atômico. O backend executa `/app/verify_isdbtb_ts.py` e devolve o relatório JSON.
Sem licença, com a portadora parada ou fora da sessão autenticada, a ação é
bloqueada.

## 18. Distribuição nativa Ubuntu 24.04+

O diretório `packaging/debian` gera um pacote `.deb` sem dependência do Docker.
A aplicação continua composta pelo painel Python e pelo emissor C++, executados
pelo unit `epg-stream.service` como usuário sem login `epgstream`.

```bash
sudo apt update
sudo apt install -y build-essential dpkg-dev pkg-config \
  libboost-system-dev libboost-thread-dev libcurl4-openssl-dev libjsoncpp-dev
./packaging/debian/build-deb.sh
sudo apt install ./dist/epg-stream_1.13.1-1_amd64.deb
sudo epg-stream-configure
```

O configurador copia a chave com grupo restrito, cria a configuração, inicia o
serviço e remove usuário/senha de bootstrap do EnvironmentFile após o primeiro
health. Dados ficam em `/var/lib/epg-stream`, configuração em
`/etc/epg-stream` e aplicação em `/usr/lib/epg-stream`. Upgrade e remoção não
apagam dados. O pacote não altera firewall e não substitui uma implantação
Docker existente automaticamente.

Desde a v1.14, o botão **Sobre** exibe produto, versão e `Developed by Julio
Cortijo`. Administradores podem consultar a última release oficial. Em uma
instalação nativa, o botão de atualização cria uma solicitação atômica no
diretório de dados. Um `systemd.path` inicia o atualizador root separado, que
refaz a consulta ao GitHub, exige digest SHA-256, confere pacote, arquitetura e
versão e só então chama o APT. O painel web nunca recebe root e nunca executa
`sudo`. No Docker a tela apenas orienta atualizar a imagem pelo host, mantendo
volumes, backup e rollback sob controle do operador.

Para releases em repositório GitHub privado, `epg-stream-configure` recebe de
forma silenciosa um token fine-grained com `Contents: read`, limitado ao
repositório. Ele é armazenado exclusivamente em
`/etc/epg-stream/update.token` (`root:epgstream`, modo `0640`) e não é exposto
por API, painel ou arquivo de ambiente. Repositórios públicos não exigem token.

## 19. Limitações conhecidas

- o painel usa HTTP Basic e não encerra TLS;
- toda persistência fica em um JSON local, adequado ao appliance atual, mas não
  a múltiplas réplicas gravando ao mesmo tempo;
- a grade ao vivo representa o XMLTV, não prova sincronismo com o vídeo real;
- compatibilidade de logo depende do receptor e da configuração SDT/CDT do
  multiplexador;
- não existe atualização automática da imagem Docker;
- multicast não é homologado por health HTTP: depende de interface, rota,
  switch, IGMP, Dexing e RF;
- o modo genérico do TVStream e o produto EPG compartilham código C++ crítico;
  qualquer mudança nesse núcleo exige regressão nos dois produtos.

## 20. Ordem de leitura e fonte de verdade

Para um novo agente:

1. `AGENTS.md` — regras obrigatórias do repositório;
2. `GUIA_OPERACIONAL_AGENTES.md` — procedimento de trabalho e deploy;
3. este documento — arquitetura e operação do EPG Stream;
4. `ARQUITETURA_EPG_MULTICAST_ISDBTB.md` — implementação binária do padrão;
5. spec da mudança em `specs/`;
6. código e testes — autoridade final quando a documentação divergir.

Em caso de divergência com notas históricas, confira primeiro
`PRODUCT_VERSION`, a tag Git, a imagem ativa e o schema persistido. Corrija a
documentação no mesmo commit da mudança que causou a divergência.
> Na v1.13.1, o descritor EIT `0x4D` mantém idioma e título, com texto curto
> vazio. A sinopse completa é enviada exclusivamente nos descritores `0x4E`,
> numerados em ordem e sem quebra ou separador artificial.
