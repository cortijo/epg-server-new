# Pacote nativo Ubuntu 24.04+

O pacote instala o EPG Stream sem Docker. Ele não altera firewall, rede,
interfaces ou dados de uma instalação Docker existente.

## Construção

Em Ubuntu 24.04 ou superior:

```bash
sudo apt update
sudo apt install -y build-essential dpkg-dev pkg-config \
  libboost-system-dev libboost-thread-dev libcurl4-openssl-dev libjsoncpp-dev
./packaging/debian/build-deb.sh
```

O artefato é criado em `dist/epg-stream_1.16.0-1_ARCH.deb`. Compile uma vez em
`amd64` e outra em `arm64` para publicar as duas arquiteturas.

## Instalação no cliente

```bash
sudo apt install ./epg-stream_1.16.0-1_amd64.deb
sudo epg-stream-configure
```

O configurador solicita porta, URL pública opcional, servidor de licenças,
identificador estável, arquivo da chave e, apenas em volume vazio, o primeiro
administrador. Após o primeiro `/health`, a senha de bootstrap é removida de
`/etc/epg-stream/epg-stream.env`.

Se o repositório de releases for privado, informe no configurador um token
GitHub fine-grained somente leitura, limitado ao repositório do produto e à
permissão **Contents: read**. O token fica fora do painel, em
`/etc/epg-stream/update.token`, com modo `0640` e grupo `epgstream`. Em
repositório público, deixe esse campo vazio.

## Operação

```bash
systemctl status epg-stream
journalctl -u epg-stream -f
sudo systemctl restart epg-stream
curl http://127.0.0.1:9100/health
```

Arquivos:

- aplicação: `/usr/lib/epg-stream`;
- configuração e chave: `/etc/epg-stream`;
- dados, logs, publicações e diagnósticos: `/var/lib/epg-stream`;
- serviço: `/lib/systemd/system/epg-stream.service`.

Administradores podem usar **Sobre > Verificar atualização**. O painel grava
somente uma solicitação sem privilégios; `epg-stream-updater.path` aciona um
serviço root separado, que consulta novamente a release oficial, valida
arquitetura, nome, versão e SHA-256 antes de instalar. O resultado fica em
`/var/lib/epg-stream/update-status.json`.

```bash
systemctl status epg-stream-updater.path
journalctl -u epg-stream-updater.service
```

No Docker, o botão é somente informativo: a imagem deve ser atualizada no host.

## Atualização e remoção

```bash
sudo apt install ./epg-stream_NOVA-VERSAO_ARCH.deb
sudo apt remove epg-stream
```

Atualizações preservam configuração e dados. A remoção também preserva
`/var/lib/epg-stream`; exclua esse diretório somente com backup e autorização
explícita. Para rollback, instale novamente o `.deb` anterior.
