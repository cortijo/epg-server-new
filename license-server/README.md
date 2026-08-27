# EPG License Server

Autoridade de licenças do EPG Stream. É um serviço Docker separado, sem acesso
ao XMLTV ou ao multicast. Ele gera chaves, define a quantidade máxima de
canais, vincula a licença a uma instalação e atende a validação online.

## Contrato e segurança

- a chave possui prefixo `EPG-` e entropia criptográfica;
- chaves v2 são derivadas por HMAC-SHA256 de segredo mestre externo e ID;
- a chave completa pode ser recuperada somente por administrador autenticado;
- o JSON persistido contém apenas SHA-256, prefixo e versão da chave;
- a administração usa HTTP Basic e senha PBKDF2-SHA256;
- a primeira validação vincula uma licença ainda livre ao `installation_id`;
- licença revogada, expirada, em outra instalação ou acima do limite é negada;
- `GET /health` não exige autenticação; as APIs administrativas exigem;
- `POST /api/validate` autentica pela própria chave.

Basic Auth e a chave trafegam no HTTP. Em uma única máquina, mantenha o serviço
em `127.0.0.1`. Se o servidor de licenças ficar em outro host, publique-o
somente atrás de HTTPS com certificado válido e controle de rede. Nunca exponha
a porta 9200 diretamente à Internet.

Na implantação PortoNET de 26/08/2026, o solicitante aceitou explicitamente
HTTP sem TLS e o bind foi alterado para `0.0.0.0:9200`. A tabela
`inet tvstream_firewall` limita TCP/9200 aos sets administrativos
`trusted_ipv4` e `trusted_ipv6`; origens fora deles continuam bloqueadas. Essa
exceção não autoriza liberar a porta globalmente.

## Build e primeira execução

```bash
docker build -f license-server/Dockerfile \
  -t epg-license-server:v1.1.0 .

docker volume create epg-license-data
docker volume create epg-license-master-secret
docker run --rm --user 0 -v epg-license-data:/data \
  alpine:3.20 chown 10002:10002 /data

# Gere o segredo por um procedimento seguro e mantenha-o fora do Git.
# O arquivo final deve pertencer a 10002:10002 e ter modo 0600.

docker run -d --name epg-license-server --network host \
  --restart unless-stopped --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=16m \
  --cap-drop ALL --security-opt no-new-privileges \
  -e LICENSE_HTTP_HOST=127.0.0.1 \
  -e LICENSE_HTTP_PORT=9200 \
  -e LICENSE_ADMIN_USER=licenseadmin \
  -e LICENSE_ADMIN_PASSWORD='SENHA_INICIAL_FORTE' \
  -e LICENSE_MASTER_KEY_FILE=/run/secrets/master.key \
  -v epg-license-data:/data \
  -v epg-license-master-secret:/run/secrets:ro \
  epg-license-server:v1.1.0
```

As variáveis do administrador são usadas apenas para inicializar um volume
vazio. Depois que `/data/licenses.json` existir, recrie o container sem
`LICENSE_ADMIN_USER` e `LICENSE_ADMIN_PASSWORD`; o hash continuará no volume.

Para administrar remotamente mantendo a porta em loopback:

```bash
ssh -L 9200:127.0.0.1:9200 usuario@servidor
```

Abra `http://127.0.0.1:9200`, informe as credenciais administrativas e clique
em **Gerar chave**. O modal permite selecionar ou usar **Copiar chave**. Em
licenças v2, **Ver chave** recupera a mesma chave. Licenças legadas exibem
**Rotacionar chave**; a rotação invalida a anterior e exige atualização
coordenada do cliente.

## Instalar a chave no EPG Stream

```bash
sudo install -d -o 10001 -g 10001 -m 0750 /srv/epg-license-client
sudo sh -c "umask 077; printf '%s\n' 'EPG-CHAVE_RECEBIDA' > /srv/epg-license-client/license.key"
sudo chown 10001:10001 /srv/epg-license-client/license.key
```

Na v1.11, o administrador também pode abrir **Licença** no painel EPG, colar a
chave e clicar em **Validar e salvar**. A chave só substitui o arquivo atual
depois que o servidor a aceita.

Configure o cliente:

```text
EPG_LICENSE_SERVER_URL=http://127.0.0.1:9200
EPG_LICENSE_KEY_FILE=/license/license.key
EPG_LICENSE_INSTALLATION_ID=cliente-servidor-01
EPG_LICENSE_CHECK_SECONDS=60
```

Monte o diretório da chave em `/license`, gravável somente pelo UID 10001; o
restante da imagem pode continuar somente leitura. O identificador deve possuir entre 8 e 128
caracteres usando letras, números, ponto, sublinhado, dois-pontos ou hífen.

## API

| Método | Rota | Autenticação | Finalidade |
|---|---|---|---|
| GET | `/health` | pública | saúde e versão |
| GET | `/api/licenses` | Basic admin | listar metadados sem chave/hash |
| POST | `/api/licenses` | Basic admin | criar ou editar licença |
| POST | `/api/licenses/revoke` | Basic admin | revogar licença |
| POST | `/api/licenses/key` | Basic admin | recuperar chave v2, sem cache |
| POST | `/api/licenses/rotate` | Basic admin | rotacionar legado/v2 |
| POST | `/api/validate` | chave no JSON | validar instalação e canais |

Exemplo de validação, apenas para laboratório:

```json
{
  "key": "EPG-...",
  "installation_id": "cliente-servidor-01",
  "channel_count": 63
}
```

A resposta pública informa `valid`, motivo, nome, limite, consumo, expiração e
horário da verificação. Nunca registra ou devolve a chave completa.

## Operação, backup e recuperação

O arquivo `/data/licenses.json` contém o administrador, hashes e metadados de
licença. Faça backup consistente do volume, mantenha permissão 0600 e trate-o
como dado sensível. Faça também backup separado do segredo mestre e da chave
do cliente. Sem o mestre não é possível recuperar chaves v2; sem a chave do
cliente um rollback pode exigir nova instalação ou rotação.

O cliente trabalha em modo **fail-closed**. Após a próxima checagem, perder o
servidor de licenças interrompe os emissores. Planeje monitoramento de
`/health`, disponibilidade de rede e backup antes de usar um host de licença
remoto.

Esse mecanismo controla a aplicação oficial e o deploy operacional. Quem
possui acesso de administrador ao host, à imagem e ao código-fonte pode alterar
o software; ele não substitui proteção jurídica, assinatura de imagens ou um
serviço SaaS de licenciamento endurecido.

## Testes

```bash
python3 -m py_compile license-server/app.py
python3 -m unittest discover -s license-server/tests -v
```
