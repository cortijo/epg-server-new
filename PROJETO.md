# EPG Server — documento raiz para continuidade por agentes e LLMs

> Documento canônico de entrada do projeto. Leia este arquivo inteiro antes de
> diagnosticar, editar, implantar ou responder sobre o sistema.
>
> Última consolidação: **02/09/2026**.

## 1. Resumo executivo

O EPG Server é um produto Docker independente que transforma fontes XMLTV em
um transporte MPEG-TS auxiliar com sinalização e EPG compatíveis com o perfil
brasileiro ISDB-TB. Esse transporte é enviado por UDP multicast ao
multiplexador Dexing, que o combina aos canais de áudio e vídeo já existentes.

O produto deliberadamente **não recebe, não transcodifica e não retransmite
vídeo ou áudio**. Seu escopo é administração de grade, PSI/SI, EPG, relógio,
categorias e estrutura experimental de logos.

```text
XMLTV HTTP/HTTPS ou upload
        │
        ▼
Painel/API Python ──> cache e normalização ──> configuração persistida
        │                                           │
        │                                           ▼
        └──────────────────────────────> TVStreamEpgOnly (C++)
                                                    │
                                                    ▼
                                        MPEG-TS EPG-only multicast
                                                    │
canal original com áudio/vídeo ─────────────────────┤
                                                    ▼
                                             Dexing / mux
                                                    │
                                                    ▼
                                               ISDB-TB / RF
```

O repositório oficial é `https://github.com/cortijo/epgserver.git`. A branch de
trabalho e publicação é `main`. Tags Git e tags Docker são imutáveis; nunca use
`latest` em produção.

## 2. Estado real em 02/09/2026

Esta seção deve ser atualizada em todo deploy. Não presuma que o código local,
o candidato e a produção estejam na mesma versão.

### 2.1 Git e código local

- implementação Parse-XML: `b4001f5`;
- tag prevista para o estado final documentado: `epg-v1.17.2`;
- `PRODUCT_VERSION`: `1.18.0`;
- servidor de licenças e produção: `1.1.0`;
- o bloqueio integral sem licença e o reinício global foram validados e
  promovidos em 27/08/2026 às 08:16;
- as specs mais recentes são `specs/2026-09-02-sobre-atualizacao-segura.md`
  e `specs/2026-09-02-fonte-parse-xml.md`.

Nunca descarte o working tree. Antes de qualquer ação execute:

```bash
git status --short
git diff --name-only
git log -8 --oneline --decorate
git remote -v
```

### 2.2 Produção ativa

Em 08/09/2026 o host principal `181.233.106.46` foi atualizado para
`epgserver:v1.17.3-20260908`. Além das correções anteriores de EIT, simulador e
atualização segura, essa versão oferece fontes **Parse-XML**: normaliza fontes
sem `<channel>`, completa timezone, elimina eventos inválidos e mantém uma URL
interna estável com a última cópia válida. A v1.15.0 acrescenta o catálogo de
canais e programação por fonte, sincronização visual e relatório dos ajustes
Parse-XML. A v1.15.1 registra os programas descartados pelo Parse-XML, permite
consultar o motivo do descarte e adiciona busca por nome ou ID no catálogo de
canais. A v1.15.3 mostra diretamente em cada item de Fontes XMLTV os canais e
programas sincronizados, a última atualização válida e o próximo instante de
renovação do cache, persistindo o resumo após reinícios. Na v1.15.4, um worker
sincroniza em background todas as fontes vencidas a cada
60 minutos, somente com licença válida e mantendo a última cópia quando a
origem falha. O deploy preservou 27 emissores, licença válida e zero reinícios.
O cliente `187.19.16.59` foi promovido para a v1.17.3 em 08/09/2026,
preservando o container v1.17.2 e uma cópia integral dos dados para rollback.

Host operacional conhecido: `181.233.106.46`.

| Item | Estado confirmado |
|---|---|
| EPG | `epg-stream`, imagem `epgserver:v1.17.3-20260908` |
| Licenças | `epg-license-server`, imagem `epg-license-server:v1.1.0-20260826` |
| HTTP EPG | TCP `9100`, rede Docker `host` |
| HTTP licenças | TCP `9200`, acesso limitado pelo firewall às redes autorizadas |
| Dados EPG | `/srv/epg-stream:/data` |
| Dados da autoridade | volume `epg-license-data` |
| Chave do cliente | volume `epg-license-client-secret` |
| Segredo mestre v2 | volume `epg-license-master-secret`, modo `0600`, UID/GID 10002 |
| Emissores | 27 processos `TVStreamEpgOnly` |
| Canais licenciados | 52 de 100 na validação de 08/09/2026 |
| Reinícios dos containers ativos | zero |
| Rollback EPG imediato | `epg-stream-pre-v1.17.3-20260908-163731` (imagem v1.17.2, parado) |
| Rollback licenças imediato | `epg-license-server-pre-v1.1.0-20260827-073708` |
| Backup EPG | `/srv/epg-stream-backup-pre-v1.17.3-20260908-163731` |
| Backup autoridade | `epg-license-data-backup-pre-v1.1.0-20260827-073708` |
| Backup chave cliente | `epg-license-client-backup-pre-v1.11.0-20260827-073708` |

A produção recebeu o bloqueio integral da v1.12: licença inválida encerra os
emissores, deixa somente Usuários e Licença operáveis e faz as APIs de gestão
retornarem HTTP 402. Após revalidação, os fluxos elegíveis retomam. O painel
também possui **Reiniciar todos os fluxos**. A autoridade permanece na v1.1 e a
licença ativa continua em `key_version=2`.

Uma instalação cliente adicional em `187.19.16.59` executa
`epgserver:v1.17.3-20260908`. Ela valida automaticamente a licença a cada 43200
segundos, opera com 17 portadoras/50 canais e preserva rollback em
`epg-stream-pre-v1.17.3-20260908-163449`, além do backup integral
`/srv/epg-stream-backup-pre-v1.17.3-20260908-163449`. O multicast foi confirmado
na interface `ens19`, de `10.10.10.20` para `239.192.15.1:5012`, em datagramas
de 1316 bytes e sem descarte pelo kernel na amostra pós-deploy.

### 2.3 Candidato validado e isolado

| Item | Estado confirmado |
|---|---|
| EPG candidato | `epg-v112-candidate`, `v1.12.0-20260827-candidate`, parado após validação |
| Licenças candidato | autoridade de produção usada somente para validar a cópia da chave |
| HTTP | somente loopback, porta `19112` |
| Portadoras carregadas | 27 |
| Emissores | zero, pois `auto_start` foi desligado somente no clone |
| Reinícios | zero |
| Dados | volumes candidatos separados da produção |

Validações aprovadas no candidato v1.12:

- licença válida, 63/80 canais, zero emissores no clone isolado;
- `restart-all` válido retornou zero porque todas as portadoras do clone foram
  marcadas para início manual;
- candidato sem chave manteve estado/usuários/licença acessíveis e respondeu
  HTTP 402 para fontes, publicações, grade e reinício global;
- o HTML bloqueado contém o alerta e os controles novos;
- a cópia não iniciou multicast nem interferiu nos 27 emissores reais.

O script `scripts/_deploy_v112_once.sh` registra o cutover executado da v1.12,
com candidato inválido, backup e rollback. Ele não é um instalador genérico.

## 3. Regras invioláveis do produto

1. Não incorporar transcode, playout, recepção ou retransmissão de mídia.
2. Não alterar SID, TSID, ONID, PIDs ou destinos persistidos automaticamente.
3. Não declarar sucesso apenas porque o processo está `online`; capture o TS.
4. Não iniciar candidato com os mesmos multicasts de produção.
5. Não expor URL XMLTV privada, senha, chave, hash, token ou cookie em Git,
   logs, API pública, `docker inspect` ou documentação.
6. Não remover imagens, containers e backups anteriores antes da homologação.
7. Não modificar todas as portadoras ao editar ou reiniciar apenas uma.
8. Não permitir exclusão de fonte em uso ou do último administrador ativo.
9. Não usar rede Docker bridge para o emissor: multicast exige rede `host` e
   interface explícita do host.
10. Mudança em PSI/SI só é aceita após captura, auditoria e teste no mux/RF.

## 4. Ordem obrigatória de leitura

Um agente novo deve seguir esta ordem:

1. `PROJETO.md` — visão integral e estado real;
2. `AGENTS.md` — regras mandatórias do repositório;
3. `GUIA_OPERACIONAL_AGENTES.md` — procedimento operacional;
4. `DOCUMENTACAO_EPG_PRODUTO.md` — detalhes de domínio e operação;
5. `specs/README.md` e `specs/TEMPLATE.md` — processo spec-driven;
6. spec relacionada à solicitação;
7. código e testes envolvidos.

Quando documentação e código divergirem, confirme nesta ordem:

```text
container/imagem em execução -> PRODUCT_VERSION -> tag/commit -> código
-> dados persistidos -> documentação histórica
```

Corrija a documentação no mesmo commit que corrige a divergência.

## 5. Mapa do repositório

| Caminho | Responsabilidade |
|---|---|
| `epg-product/app.py` | painel, Basic Auth, usuários, CRUD, API, XMLTV, supervisor |
| `epg-product/license_client.py` | cliente online fail-closed e instalação atômica da chave |
| `epg-product/Dockerfile` | build C++ e runtime mínimo do produto |
| `epg-product/docker-compose.yml` | referência de implantação do EPG |
| `epg-product/tests/` | testes de domínio, API, UI, auditor e instalador |
| `license-server/app.py` | autoridade, administração e validação de licenças |
| `license-server/Dockerfile` | imagem isolada da autoridade |
| `license-server/tests/` | testes de hash, limite, vínculo, revogação e rotação |
| `src/EpgOnlyMain.cpp` | PAT/PMT/SDT/BIT/CDT, carrossel, CBR e multicast |
| `src/EpgInjector.cpp` | XMLTV, EIT, categorias, TDT/TOT e descritores |
| `src/ConfigManager.*` | configuração compartilhada do núcleo C++ |
| `scripts/install.sh` | instalação/upgrade interativo do EPG |
| `packaging/debian/` | pacote nativo, serviço systemd e atualizador validado Ubuntu 24.04+ |
| `scripts/firewall-manager.sh` | firewall nftables declarativo e separado |
| `scripts/verify_isdbtb_ts.py` | auditoria offline do MPEG-TS ISDB-TB |
| `scripts/verify_epg_clock.py` | auditoria de EIT/TDT/TOT e fuso |
| `specs/` | contratos, decisões, testes, deploy e rollback por mudança |
| `DOCUMENTACAO_EPG_PRODUTO.md` | referência técnica extensa |
| `GUIA_OPERACIONAL_AGENTES.md` | método de desenvolvimento e entrega |

## 6. Arquitetura de execução

### 6.1 Painel e store

`epg-product/app.py` usa somente a biblioteca padrão do Python e Pillow para
logos. Na inicialização ele:

1. abre `/data/epg-product.json`;
2. cria o primeiro administrador apenas em volume vazio;
3. normaliza/migra campos compatíveis;
4. inicializa cache XMLTV, publicações e cliente de licença;
5. cria o supervisor;
6. inicia somente portadoras `auto_start=true` quando a licença é válida;
7. serve HTTP com autenticação Basic.

A persistência usa arquivo temporário, `fsync`, troca atômica e permissão
`0600`. Não edite o JSON de produção à mão enquanto o processo estiver ativo.

### 6.2 Supervisor

Cada portadora ativa corresponde a um processo `TVStreamEpgOnly`. O supervisor
mantém PID, estado, último erro, número de reinícios e arquivo de log. Uma saída
inesperada com `auto_start=true` agenda nova tentativa. Stop manual impede a
recuperação automática até nova ação do usuário.

### 6.3 Emissor C++

O emissor baixa e interpreta XMLTV, constrói seções PSI/SI, empacota blocos de
188 bytes e envia datagramas UDP de 1316 bytes. O mux é CBR, preenchido com PID
nulo quando necessário. Não existe PCR de mídia; a PMT usa `0x1FFF` como PID
sem PCR.

### 6.4 Servidor de licenças

É uma imagem e um processo separados, UID/GID `10002:10002`. Não possui acesso
ao XMLTV nem aos emissores. O EPG é UID/GID `10001:10001`.

Na v1.0 a autoridade guarda somente SHA-256 e a chave é irrecuperável após a
criação. Na v1.1 as chaves v2 são derivadas por HMAC-SHA256 de um segredo
mestre externo e do ID imutável da licença. O JSON ainda guarda somente hash,
prefixo e versão. Sem o arquivo mestre não é possível usar **Ver chave**.

## 7. Modelo de dados persistido

O arquivo principal contém:

- `schema_version`;
- `users`;
- `sources`;
- `carriers`;
- `xmltv_publications`.

Uma portadora possui, entre outros:

- `id`, `name` e `auto_start`;
- `source_id` padrão;
- `transport_stream_id` e `original_network_id`;
- `destination`, `port` e `interface_address`;
- `pmt_pid`, `bitrate`, `ttl` e `signalling_version`;
- modo, fuso e correção do relógio;
- lista `services`.

`signalling_version` avança módulo 32 e é persistido antes de cada nova
geração do emissor. PAT, PMT, SDT, BIT e a versão inicial da EIT usam esse
mesmo valor. Assim, reinícios e alterações de canais invalidam o cache PSI/SI
do mux/receptor; durante a execução, mudanças reais da grade continuam
incrementando a EIT independentemente.

Cada serviço/canal possui:

- ID interno não reciclável;
- nome;
- `service_id`, que deve ser o Program Number real no Dexing;
- `epg_channel_id`, igual ao `<channel id>` do XMLTV;
- `source_id` opcional para substituir a fonte da portadora;
- categoria padrão opcional;
- metadados de logo preservados, embora a UI esteja oculta na v1.10.

Restrições importantes:

- até 64 serviços por portadora;
- destino multicast/porta não se repete;
- SID é único dentro da portadora;
- TSID/ONID representam o multiplex final, não um número arbitrário;
- fonte do canal vazia herda a fonte da portadora;
- fonte em uso não pode ser excluída;
- o licenciamento conta serviços, não portadoras.

## 8. XMLTV e publicações

### 8.1 Fontes

O sistema aceita XMLTV HTTP/HTTPS e `.gz`. BrazilTVEPG é criado como fonte
padrão, mas outras fontes podem ser cadastradas. Cada canal pode escolher sua
própria fonte.

O tipo `parse_xml`, introduzido na v1.14.1, normaliza feeds de operadora que
omitem canais/timezone ou contêm eventos inválidos e os entrega ao emissor por
uma URL interna tokenizada. Fontes anteriores continuam implicitamente como
`xmltv`, sem transformação.

O ID do evento precisa coincidir com `epg_channel_id`. Nome visual semelhante
não realiza associação.

### 8.2 Publicações versionadas

O módulo **Publicações XMLTV** recebe uploads periódicos da programadora e
mantém uma URL permanente com token. Novos arquivos não mudam a URL.

Normalizações atuais:

- adiciona `-0300` a datas sem timezone;
- preserva `Z` e offsets explícitos;
- reconcilia IDs pelo código numérico quando a correspondência é inequívoca;
- remove duplicidades e eventos inválidos ou sem duração;
- seleciona arquivo vigente, próximo ou último expirado;
- aceita XML/XMLTV/GZIP até 96 MiB;
- nunca usa o nome do upload como caminho confiável.

Arquivos ficam em `/data/xmltv-publications`. O token público deve ser tratado
como segredo operacional e nunca deve ir ao Git.

## 9. Transporte ISDB-TB e PIDs

| PID | Conteúdo | table_id/descritores principais |
|---|---|---|
| `0x0000` | PAT | `0x00` |
| PMT base | uma PMT por serviço | `0x02` |
| `0x0011` | SDT | `0x42`, nome/provedor e descritor de logo `0xCF` |
| `0x0012` | EIT | p/f `0x4E`, schedule `0x50/0x51`, categoria `0x54` |
| `0x0014` | TDT/TOT | `0x70`/`0x73`, fuso e correção configuráveis |
| `0x0024` | BIT | `0xC4`, anúncio da CDT por descritor `0xD7` |
| `0x0029` | CDT | `0xC8`, variantes de logo |
| `0x1FFF` | stuffing/no-PCR | pacotes nulos e indicação de PMT sem PCR |

### 9.1 Passthrough no Dexing

EPG sem logo:

```text
0x0012 -> 0x0012
0x0014 -> 0x0014
```

Quando a estrutura de logo estiver habilitada e homologada:

```text
0x0011 -> 0x0011
0x0024 -> 0x0024
0x0029 -> 0x0029
```

No Dexing, **Input Channel** significa o número do Input Data auxiliar, não o
SID. Todos os serviços de uma mesma portadora usam o mesmo Input Channel. A
associação no receptor ocorre por TSID + ONID + SID.

O equipamento pode rejeitar/remapear certos PIDs reservados. Sempre audite a
saída final do Dexing e o RF; auditar apenas o multicast auxiliar é
insuficiente.

### 9.2 Relógio

O PID `0x0014` pode usar o padrão UTC-03:00 ou fuso/correção configurados por
portadora. A mesma referência temporal alimenta a EIT. Horário correto em uma
TV e errado em outra pode ser cache/configuração do receptor ou do mux; capture
TDT/TOT e EIT antes de alterar o emissor.

### 9.3 Categorias

Categorias XMLTV reconhecidas são convertidas para o descritor EIT `0x54`. Se
o evento não possuir categoria reconhecida, o canal pode fornecer fallback.
Não existe PID adicional para categoria.

### 9.4 Logos

A estrutura técnica preservada gera seis PNGs compatíveis com a CLUT fixa
ARIB, SDT `0xCF`, BIT `0xC4` e CDT `0xC8`. Os controles visuais estão ocultos
desde a v1.10 por falta de homologação consistente em televisores. Não reative
sem teste em múltiplos receptores, Dexing e RF.

## 10. Funcionalidades do painel

- usuários administradores e operadores;
- troca de senha, ativação e proteção do último administrador;
- CRUD e teste de fontes XMLTV;
- publicações XMLTV versionadas com URL permanente;
- criação, edição e clonagem de portadoras;
- fonte específica por canal;
- categoria padrão por canal;
- relógio/fuso/correção por portadora;
- start, stop, restart, logs e exclusão;
- reinício conjunto dos fluxos elegíveis;
- tabela compacta com ações laterais;
- programação carregada sob demanda;
- grade horizontal de três horas;
- estado e consumo da licença;
- botão **Licença** para validar e instalar uma chave;
- bloqueio integral de XMLTV, grade e portadoras quando a licença é inválida,
  mantendo apenas Usuários e Licença operáveis.

Formulários não fecham ao clicar no fundo. Fechamento ocorre por Salvar,
Cancelar ou Fechar.

## 11. APIs principais

Todas as rotas administrativas do EPG usam Basic Auth. Esta lista resume os
contratos; confirme os handlers em `epg-product/app.py` antes de integrar.

### 11.1 EPG Stream

| Método | Rota | Uso |
|---|---|---|
| GET | `/health` | saúde geral e licença |
| GET | `/api/session` | usuário e perfil atuais |
| GET | `/api/state` | portadoras, runtime e licença, sem URLs sensíveis |
| GET/POST | `/api/sources` | fontes XMLTV |
| POST | `/api/sources/test` | validação de fonte |
| GET | `/api/guide` | programação de uma portadora |
| GET/POST | `/api/carriers` | leitura e gravação de portadora |
| POST | `/api/carriers/start|stop|restart|delete` | operação do emissor |
| POST | `/api/carriers/restart-all` | reinício global dos fluxos elegíveis, somente admin |
| GET/POST | `/api/users` | administração de usuários |
| GET | `/api/license` | força consulta e retorna apenas estado público |
| POST | `/api/license/key` | v1.11: admin valida e instala chave atomicamente |
| GET/POST | `/api/publications...` | publicações e versões XMLTV |

### 11.2 License Server

| Método | Rota | Autenticação | Uso |
|---|---|---|---|
| GET | `/health` | pública | saúde e versão |
| GET | `/api/licenses` | Basic admin | metadados sem hash/chave |
| POST | `/api/licenses` | Basic admin | criar/editar licença |
| POST | `/api/licenses/revoke` | Basic admin | revogar |
| POST | `/api/validate` | chave no JSON | validar instalação e limite |
| POST | `/api/licenses/key` | v1.1 Basic admin | recuperar chave v2 |
| POST | `/api/licenses/rotate` | v1.1 Basic admin | converter/rotacionar chave |

Respostas com chave usam `Cache-Control: no-store`. A chave nunca deve aparecer
em listagens, estado, logs ou exceções.

## 12. Licenciamento

O cliente opera em modo **fail-closed**:

- sem chave, servidor indisponível, licença revogada/expirada, instalação
  diferente ou excesso de canais interrompem os emissores;
- o painel continua disponível para diagnóstico;
- o estado é armazenado em cache por intervalo configurável;
- mudanças de portadoras exigem capacidade válida;
- a instalação é vinculada pelo `EPG_LICENSE_INSTALLATION_ID` estável.

### 12.1 Segredos e volumes

- nunca coloque a licença ou o segredo mestre em `.env` ou Git;
- o cliente usa arquivo em volume gravável somente pelo UID 10001;
- a v1.11 valida a nova chave antes de gravar arquivo temporário + `fsync` +
  `os.replace`;
- o segredo mestre usa volume/arquivo separado, modo `0600`, UID 10002;
- backup do JSON sem backup do mestre não permite recuperar chaves v2;
- backup do servidor não substitui backup do arquivo do cliente.

Na produção atual, a licença já é v2. A migração foi feita iniciando a
autoridade v1.1 com mestre, iniciando o EPG v1.11 ainda com a chave legada e,
por fim, rotacionando e instalando a chave nova de forma coordenada.

## 13. Docker, rede e firewall

### 13.1 Runtime

EPG:

- rede `host`;
- usuário `10001:10001`;
- dados em `/data`;
- filesystem da imagem somente leitura quando possível;
- `/tmp` como tmpfs;
- `cap_drop: ALL` e `no-new-privileges`;
- restart `unless-stopped`.

Licenças:

- rede `host`;
- usuário `10002:10002`;
- dados em volume separado;
- porta padrão 9200;
- servidor e cliente não compartilham dados além da chamada HTTP.

### 13.2 Firewall

O script `scripts/firewall-manager.sh` administra apenas
`table inet epg_managed`. Na máquina operacional também existe a tabela
`inet tvstream_firewall`, usada para restringir portas públicas às redes
autorizadas.

Nunca:

- limpe o ruleset global;
- altere regras Docker/NAT sem diagnóstico;
- aplique firewall remoto sem garantir SSH e console fora de banda;
- abra 9200 globalmente sem TLS.

Use `check`, `render` e só depois `apply`. Alterações do arquivo não entram em
vigor até a aplicação.

## 14. Instalação e build

Instalação padrão:

```bash
chmod +x scripts/install.sh
sudo ./scripts/install.sh
```

Build manual atual:

```bash
docker build -f epg-product/Dockerfile \
  -t epgserver:v1.11.0-AAAAMMDD .

docker build -f license-server/Dockerfile \
  -t epg-license-server:v1.1.0-AAAAMMDD .
```

O instalador:

- pergunta parâmetros antes de alterar o host;
- constrói antes do corte;
- preserva volume e container anterior;
- cria backup explícito;
- valida health;
- não altera o firewall;
- não deve receber senhas em argumentos;
- não usa `latest`.

## 15. Método obrigatório de trabalho

### 15.1 Diagnosticar

1. Leia todos os documentos obrigatórios.
2. Confirme Git e estado do servidor sem escrever.
3. Reproduza o sintoma.
4. Identifique portadora, SID, TSID, ONID, destino, porta e horário.
5. Consulte health, estado, guia, logs, processos e XMLTV.
6. Capture o multicast na interface física.
7. Formule hipótese apoiada em evidência.

Não implemente uma correção enquanto o pedido for apenas “analisar”.

### 15.2 Especificar

Para mudança não trivial copie `specs/TEMPLATE.md` e registre:

- problema e impacto;
- objetivos e fora de escopo;
- contratos API/dados/rede;
- riscos;
- critérios Dado/Quando/Então;
- matriz de teste;
- deploy e rollback.

### 15.3 Implementar

- faça a menor alteração que satisfaça a spec;
- preserve mudanças existentes do operador;
- use `rg` para localizar o fluxo real;
- use patch controlado para editar;
- atualize testes e documentação no mesmo conjunto;
- nunca reutilize IDs ou tags;
- não misture refatoração ampla com correção urgente.

### 15.4 Validar

Matriz mínima:

```bash
python3 -m py_compile epg-product/app.py epg-product/license_client.py
python3 -m unittest discover -s epg-product/tests -v
python3 -m py_compile license-server/app.py
python3 -m unittest discover -s license-server/tests -v
git diff --check
```

Quando HTML/JS mudar, extraia o `<script>` incorporado e compile com Node.
Teste navegador real em desktop e viewport móvel.

Quando C++/PSI/SI mudar:

```bash
docker build -f epg-product/Dockerfile -t epgserver:CANDIDATO .

python3 scripts/verify_isdbtb_ts.py AMOSTRA.ts \
  --service-id SID --tsid TSID --onid ONID --epg-only --pmt-pid 0x1000

python3 scripts/verify_epg_clock.py GRUPO PORTA --interface INTERFACE
```

Valide:

- CRC;
- continuity counter;
- seções multipacote;
- SIDs/TSID/ONID;
- EIT p/f e schedule;
- TDT/TOT;
- bitrate e datagramas;
- passthrough e RF.

### 15.5 Candidato

O candidato deve usar:

- outra porta HTTP;
- cópia dos dados;
- volumes de licença separados;
- multicasts de laboratório ou `auto_start=false` no clone;
- tag Docker `-candidate` imutável durante o teste.

Reinicie o candidato e confirme persistência, permissões, zero restart e
ausência de efeitos em produção.

### 15.6 Commit e GitHub

Depois dos testes previstos:

1. revise segredos e arquivos inesperados;
2. execute `git diff --check`;
3. faça commit descritivo;
4. envie `main` para `origin`;
5. confirme o SHA remoto;
6. crie tag anotada apenas quando a versão estiver pronta para release;
7. nunca mova uma tag publicada.

### 15.7 Deploy

Antes do corte:

1. obtenha autorização explícita quando houver restart, rotação ou risco;
2. registre imagem, mounts, rede, usuário, restart e quantidade de emissores;
3. faça backup novo do diretório e dos volumes;
4. preserve o container anterior renomeado e sem restart automático;
5. inicie a nova imagem com os mesmos dados;
6. valide health, licença, logs, processos e pacotes;
7. só então marque a spec como implantada.

### 15.8 Rollback

O rollback normal é:

1. parar/remover somente o container novo;
2. renomear e iniciar o container anterior;
3. restaurar dados apenas se migração incompatível for comprovada;
4. confirmar processos e TS;
5. manter evidência da falha.

Nunca use `docker system prune`, `git reset --hard` ou exclusão recursiva ampla
em uma manutenção normal.

## 16. Diagnóstico rápido

```bash
docker ps --format '{{.Names}} {{.Image}} {{.Status}}'
docker inspect epg-stream --format \
  'image={{.Config.Image}} status={{.State.Status}} restarts={{.RestartCount}}'
docker top epg-stream
docker logs --since 10m epg-stream
ss -lntup | grep -E ':(9100|9200)\b'
tail -n 200 /srv/epg-stream/logs/ID_DA_PORTADORA.log
```

| Sintoma | Sequência de verificação |
|---|---|
| processo online, sem multicast | interface, rota, socket, tcpdump e firewall de saída |
| EPG não aparece | SID/TSID/ONID, PID `0x0012`, Input Channel e saída Dexing |
| horário errado | XMLTV, offset, EIT, TDT/TOT, mux e cache da TV |
| apenas um canal funciona | SID duplicado ou programa associado à portadora errada |
| portadora reinicia | log individual, XMLTV, DNS, interface e licença |
| painel bloqueado | `/api/license`, servidor 9200, chave, instalação e limite |
| logo não aparece | SDT/BIT/CDT, passthrough, saída final e suporte do receptor |

## 17. Histórico consolidado

| Versão | Entrega principal |
|---|---|
| 1.0.x | produto EPG separado, painel, XMLTV e multicast auxiliar |
| 1.1.x | usuários administradores/operadores |
| 1.2.x | tabela compacta, UX e clonagem de portadora |
| 1.3.x | fonte por canal e logo experimental/preview |
| 1.4.0 | seis logos ARIB, SDT `0xCF` e CDT `0xC8` |
| 1.5.0 | grade horizontal e visualização ao vivo |
| 1.6.0 | publicações XMLTV versionadas e URL permanente |
| 1.7.0 | BIT `0xC4` e anúncio CDT `0xD7` |
| 1.8.0 | categorias EIT `0x54` e fallback por canal |
| 1.9.0 | fuso e correção de relógio por portadora |
| 1.10.0 | licenciamento online por serviços e UI de logo oculta |
| 1.11.0 | Ver/Copiar chave v2 e instalação visual no EPG; deploy em 27/08/2026 |
| 1.12.0 | bloqueio integral sem licença e reinício global; deploy em 27/08/2026 |
| 1.12.1 | validação automática padrão a cada 12 horas e correção do primeiro check vazio |
| 1.12.2 | elimina repetição entre os descritores EIT `0x4D` e `0x4E` e adiciona auditoria de sobreposição |
| 1.13.0 | simulador de TV/PIDs captura o TS gerado sem interromper o multicast e reconstrói os metadados EIT |
| 1.13.1 | separa título no `0x4D` e sinopse integral no `0x4E` para evitar quebra criada pelo receptor |
| 1.17.3 | versionamento PSI/SI persistente evita EIT antiga em cache após reinício |

As specs em `specs/` contêm o histórico detalhado de decisões e evidências.

## 18. Pendências conhecidas

1. A UI de logo permanece oculta até homologação confiável em receptores.
2. O painel usa Basic Auth/HTTP; outro host deve usar proxy HTTPS.
3. O JSON local não suporta múltiplas réplicas escritoras simultâneas.
4. Health HTTP não prova multicast ou RF.

## 19. Checklist de handoff

Ao encerrar uma mudança, deixe registrado:

- pedido e diagnóstico;
- spec e estado;
- arquivos alterados;
- comandos e resultados de teste;
- commit e SHA remoto;
- tag Git e tags/im IDs Docker;
- container, rede, mounts e versão ativa;
- quantidade de portadoras, canais, emissores e reinícios;
- evidência TS/Dexing/RF quando aplicável;
- backups e containers de rollback;
- pendências e riscos restantes.

Um próximo agente deve conseguir continuar apenas com este documento, a spec e
o repositório, sem depender do histórico da conversa.

## 20. Regra final

O objetivo não é apenas fazer o painel responder ou o processo ficar ativo. O
resultado correto é uma cadeia inteira coerente:

```text
XMLTV correto
-> configuração persistida correta
-> processo saudável
-> TS auxiliar correto
-> passthrough correto
-> multiplex final correto
-> receptor/RF homologado
```

Se qualquer elo não foi verificado, registre o trabalho como parcial, não como
concluído.
