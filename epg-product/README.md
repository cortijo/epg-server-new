# EPG Stream

Produto independente para geração e distribuição de EPG ISDB-TB via UDP
multicast. Ele não recebe, transcodifica nem retransmite vídeo ou áudio. Cada
instância administra fontes XMLTV e portadoras auxiliares que o modulador
combina aos canais já existentes.

## Recursos

- fontes XMLTV HTTP/HTTPS, inclusive `.gz`;
- upload, normalização e publicação versionada de XMLTV em URL permanente;
- BrazilTVEPG criado como fonte padrão;
- uma saída multicast por portadora, com 1 a 64 serviços;
- fonte XMLTV própria por serviço, com herança da fonte padrão da portadora;
- SID/Program Number, TSID, ONID, PID de PMT, bitrate, TTL e interface;
- PAT, PMT, SDT, EIT p/f, EIT schedule, TDT e TOT;
- logo ISDB-TB por canal com descriptor SDT e CDT no PID `0x0029`;
- perfil brasileiro ISDB-TB / ABNT NBR 15603;
- início automático, reinício após falha, controle manual e logs;
- painel autenticado com programa atual, progresso, próximo programa e grade;
- grade horizontal de três horas com filtro por portadora e navegação temporal;
- usuários administradores e operadores gerenciados pelo próprio painel;
- senhas persistidas somente como PBKDF2-HMAC-SHA256 com salt individual;
- armazenamento independente em `/data`.

## Construção

Execute na raiz do repositório:

```bash
docker build -f epg-product/Dockerfile \
  -t epgserver:v1.7.0-20260826 .
```

A imagem compila somente o emissor `TVStreamEpgOnly`. O runtime não contém
TVStreamer, GStreamer, FFmpeg ou ferramentas de transcodificação.

## Primeira execução

O método recomendado é o instalador interativo, executado na raiz do
repositório:

```bash
chmod +x scripts/install.sh
sudo ./scripts/install.sh
```

Ele faz o build, cria ou atualiza o container, valida o health check e preserva
backup e container anterior em atualizações. Credenciais de bootstrap são
solicitadas sem eco, usadas somente no primeiro start e removidas do ambiente
do container definitivo. O script informa a porta necessária, mas não modifica
o firewall.

Instalação manual:

```bash
sudo install -d -o 10001 -g 10001 -m 0750 /srv/epg-stream
cp epg-product/.env.example epg-product/.env
# Edite .env e defina uma senha exclusiva de pelo menos 10 caracteres.
docker compose --env-file epg-product/.env \
  -f epg-product/docker-compose.yml up -d
```

Abra `http://IP_DO_SERVIDOR:9100/`. O navegador solicitará o usuário e a senha
definidos no `.env`.

As variáveis do `.env` criam apenas o primeiro administrador quando o volume
está vazio. Depois da primeira inicialização, use **Usuários** no painel para
criar contas, editar nomes e perfis, trocar senhas ou desativar acessos. É
possível remover as variáveis de credencial do container após confirmar a
migração, pois nenhuma senha legível é mantida no JSON.

Perfis:

- **Administrador:** operação completa e gerenciamento de usuários;
- **Operador:** fontes, portadoras, programação e logs, sem acesso aos usuários.

O backend impede remover, desativar ou rebaixar o último administrador ativo.

O `network_mode: host` é intencional: ele permite selecionar a interface de
saída multicast do host. O container não recebe IP próprio de bridge e a porta
HTTP 9100 escuta diretamente nos endereços do servidor. Não use `ports:` ou
`-p` junto com esse modo.

## Instalação implantada em 25/08/2026

| Item | Valor |
|---|---|
| Imagem | `epgserver:v1.7.0-20260826` |
| Container | `epg-stream` |
| Rede Docker | `host` |
| HTTP | `9100`, diretamente no host |
| Reinício | `unless-stopped` |
| Usuário do processo | `10001:10001` |
| Volume | `/srv/epg-stream:/data` |
| URL pública XMLTV | `http://181.233.106.46:9100/xmltv/<token>.xml` |
| Container de rollback | `epg-stream-pre-v1.6.0-20260825-214509` (imagem v1.5.0) |
| Backup de rollback | `/srv/epg-stream-backup-pre-v1.6.0-20260825-214509` |

Rollback mais recente: container `epg-stream-pre-v1.7.0-20260826-085500` e
dados `/srv/epg-stream-backup-pre-v1.7.0-20260826-085500`.

Essa tabela registra a implantação atual, mas deve sempre ser confirmada com
`docker inspect` antes de uma nova alteração.

## Modelo de configuração

1. Em **Fontes XMLTV**, teste e salve o provedor da grade.
2. Crie uma portadora com TSID e ONID iguais aos do multiplex no Dexing.
3. Informe multicast/porta exclusivos para o EPG auxiliar.
4. Adicione todos os canais da portadora. Para cada um, informe:
   - fonte XMLTV própria ou **Herdar fonte da portadora**;
   - nome de apresentação;
   - ID exato do `<channel id="...">` no XMLTV;
   - SID/Program Number exato do canal no modulador.
5. No Dexing, selecione o programa EPG auxiliar e faça passthrough dos PIDs
   `0x0012` e `0x0014` para a mesma saída TS dos canais correspondentes.

Uma portadora usa um multicast EPG auxiliar. Ela não precisa de um multicast
por canal.

## XMLTV enviado pela programadora

Abra **Publicações XMLTV**, crie uma publicação e envie o arquivo `.xml`,
`.xmltv` ou `.gz`. Cada publicação possui uma URL aleatória permanente no
formato:

```text
http://IP_DO_SERVIDOR:9100/xmltv/TOKEN.xml
```

Se o painel for aberto por proxy, túnel ou endereço `localhost`, defina
`EPG_PUBLIC_BASE_URL=http://IP_DO_SERVIDOR:9100` no container para que o botão
**Copiar URL** sempre use o endereço alcançável pelo emissor.

Copie essa URL e cadastre-a em **Fontes XMLTV**. Nos uploads seguintes, use a
mesma publicação; não crie outra fonte. A vigência é calculada pelo primeiro
`start` e último `stop` válidos. A cada acesso, a URL entrega a versão vigente;
se ainda não começou, entrega a próxima e, se todas expiraram, mantém a última
disponível até o envio da nova grade.

Durante o upload, o sistema:

- acrescenta `-0300` a datas XMLTV sem fuso;
- preserva datas que já possuem `Z` ou `±HHMM`;
- reconcilia o ID usado em `<programme channel>` com o `<channel id>` quando o
  prefixo numérico identifica um único canal;
- cria uma declaração mínima para IDs válidos ainda não declarados;
- remove canais duplicados e eventos com duração nula, negativa ou data inválida;
- valida novamente o XML resultante antes de publicá-lo.

Os arquivos ficam em `/data/xmltv-publications`. O upload aceita no máximo
96 MiB e exige autenticação; somente a URL longa com token é pública. Excluir a
publicação invalida definitivamente sua URL.

## Logotipo ISDB-TB / ARIB

Depois de salvar a portadora e o canal, abra a edição e use **Enviar PNG**.
O backend limita o arquivo original a 2 MiB, mantém uma miniatura web e gera os
seis formatos normativos: `48×24`, `36×24`, `48×27`, `72×36`, `54×36` e
`64×36`. As imagens transmitidas são PNG indexados de 8 bits contendo somente
`IHDR`, `IDAT` e `IEND`; a paleta não acompanha o arquivo porque o receptor usa
a CLUT fixa de 128 cores definida pelo padrão ARIB. A atualização incrementa a
versão do logo e da SDT e reinicia somente o emissor afetado.

O transporte passa a incluir:

- descriptor de transmissão de logo `0xCF` na SDT/PID `0x0011`;
- BIT `0xC4` no PID `0x0024`, anunciando CDT `0xC8` pelo descritor SI `0xD7`;
- seis seções CDT `table_id 0xC8`, tipos/seções `0x00` a `0x05`, no PID
  `0x0029`.

Para o primeiro teste no Dexing, use uma saída de laboratório, passe os PIDs
`0x0011`, `0x0024` e `0x0029` e evite uma segunda SDT/BIT conflitante gerada pelo equipamento.
O modulador deve repassar os três PIDs do mesmo input auxiliar. EIT e
relógio continuam nos PIDs `0x0012` e `0x0014`. A estrutura pode ser auditada
com `scripts/verify_isdbtb_ts.py --epg-only --logo-service-id SID`; a exibição
final ainda depende de o receptor implementar download de logo ARIB.

## Visualização das portadoras

O painel apresenta uma tabela compacta. Cada portadora ocupa uma linha e seus
comandos ficam na coluna **Ações**, à direita. A programação permanece recolhida
e `/api/guide` não é consultado durante o refresh periódico. Clique em **Ver
programação** para carregar e expandir somente aquela portadora; **Ver grade**
abre todos os eventos do canal selecionado.

O botão **Grade de programação** abre uma visão consolidada semelhante a um
guia eletrônico. Selecione a portadora e compare todos os seus canais em uma
linha do tempo de três horas. Os controles deslocam a janela em 90 minutos ou
retornam ao horário atual; os blocos são proporcionais à duração, destacam o
programa no ar e abrem detalhes ao serem clicados. Em telas estreitas, a grade
mantém a coluna do canal e oferece rolagem horizontal.

Na coluna **Ações**, **Clonar** abre uma cópia editável da portadora. Fonte,
TSID, ONID, interface, porta, bitrate, TTL e serviços são preservados, mas os
identificadores internos são novos, o multicast fica vazio e a inicialização
fica manual. Nada é gravado até clicar em **Salvar**, e a portadora original
não é alterada.

A janela **Fontes XMLTV** possui **Fechar** no próprio cabeçalho, ao lado de
**+ Nova fonte**. Essa ação apenas fecha a janela e não executa testes nem
modifica as fontes cadastradas.

## Operação

```bash
curl -fsS http://127.0.0.1:9100/health
docker logs --since 10m epg-stream
docker inspect epg-stream --format \
  'image={{.Config.Image}} status={{.State.Status}} restarts={{.RestartCount}}'
```

Dados persistentes:

```text
/srv/epg-stream/epg-product.json
/srv/epg-stream/logs/<ID_DA_PORTADORA>.log
/srv/epg-stream/logos/<ID_DA_PORTADORA>/<ID_DO_CANAL>-preview.png
/srv/epg-stream/logos/<ID_DA_PORTADORA>/<ID_DO_CANAL>-type-00.png ... type-05.png
```

O JSON usa permissão `0600` e guarda somente salt/hash das senhas.

Faça backup do diretório antes de atualizar a imagem. Nunca coloque o `.env`
ou o JSON real de produção no Git.

## Roteiro de teste do administrador

1. Abra `http://IP_DO_SERVIDOR:9100/` e entre com o administrador existente.
2. Confirme que o botão **Usuários** aparece no topo.
3. Crie um usuário de teste com perfil **Operador** e senha de pelo menos dez
   caracteres.
4. Abra uma janela anônima e confirme que o operador entra, mas não vê o botão
   **Usuários**.
5. Volte como administrador, edite o operador e altere sua senha.
6. Confirme que a senha anterior deixa de funcionar e que a nova funciona.
7. Desative o operador e confirme que o acesso passa a ser recusado.
8. Reative ou exclua a conta de teste.

O sistema deve impedir desativar, rebaixar ou excluir o último administrador
ativo. O teste de usuários não altera XMLTV, portadoras ou multicast.

## Desenvolvimento e testes

```bash
python3 -m unittest tests.test_epg_product -v
python3 -m py_compile epg-product/app.py
```

A documentação autoritativa de arquitetura, dados, API, ISDB-TB, Dexing,
testes, release, deploy, rollback e continuidade por outro agente está em
[`DOCUMENTACAO_EPG_PRODUTO.md`](../DOCUMENTACAO_EPG_PRODUTO.md).
