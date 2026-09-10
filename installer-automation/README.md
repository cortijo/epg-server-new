# OMNIEPG Installer

Imagem independente para instalar ou atualizar o OMNIEPG em máquinas Linux
remotas via SSH. O painel identifica o host, confirma sua fingerprint, instala
Docker, compila uma referência Git imutável, preserva a instalação anterior e
valida o novo container.

## Segurança

- configure usuário e senha próprios para o painel;
- publique a porta somente em rede administrativa ou atrás de HTTPS;
- não exponha a porta 9300 diretamente na internet por HTTP;
- senhas SSH/sudo, chave de licença e senha inicial existem apenas na memória
  do job e são apagadas do formulário assim que a execução começa;
- o histórico armazena apenas etapas e saída redigida;
- a fingerprint exibida deve ser conferida com o administrador do host;
- o container não monta `/var/run/docker.sock`: ele atua somente no host remoto.

## Inicialização

```bash
cd installer-automation
cp .env.example .env
# edite .env e use uma senha longa e exclusiva
docker compose --env-file .env up -d --build
```

Por padrão o compose publica somente `127.0.0.1:9300`. Use um proxy HTTPS
(Nginx, Caddy ou equivalente) ou um túnel SSH:

```bash
ssh -L 9300:127.0.0.1:9300 usuario@HOST_DO_INSTALADOR
```

Abra `http://127.0.0.1:9300` e autentique-se com os valores do `.env`.

## Fluxo de uso

1. Informe host, porta, usuário e senhas SSH/sudo.
2. Clique **Identificar servidor** e confira a fingerprint fora do painel.
3. Informe repositório, branch/tag, tag Docker imutável, licença e primeiro
   administrador.
4. Clique **Instalar OMNIEPG** e acompanhe as etapas.
5. Ao terminar, abra `http://HOST_REMOTO:PORTA` e valide os emissores.

Distribuições reconhecidas: Ubuntu, Debian e Linux Mint via `apt`; RHEL,
Rocky, AlmaLinux, Fedora e CentOS via `dnf`. Outras são recusadas sem alteração.

## Comportamento no host remoto

- instala `docker`, `git` e certificados pelo gerenciador da distribuição;
- habilita o serviço Docker;
- clona somente a referência informada e constrói a imagem no servidor;
- mantém dados em `/srv/...` e a licença em `/srv/epg-license-client`;
- inicia com `--network host` para preservar multicast;
- renomeia o container anterior com sufixo `rollback-AAAAmmdd-HHMMSS`;
- se o health não responder, remove o candidato e restaura o anterior;
- remove as credenciais do administrador inicial do `docker inspect` após o
  primeiro bootstrap e recria o container com a configuração definitiva.

O automatizador não configura firewall, interfaces, rotas multicast, fontes
XMLTV, portadoras, SID, TSID, ONID ou PIDs.

## Testes

```bash
cd installer-automation
python3 -m unittest -v test_app.py
docker build -t omniepg-installer:v1.0.1 -f Dockerfile ..
```
