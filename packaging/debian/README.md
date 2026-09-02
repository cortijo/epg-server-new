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

O artefato é criado em `dist/epg-stream_1.13.1-1_ARCH.deb`. Compile uma vez em
`amd64` e outra em `arm64` para publicar as duas arquiteturas.

## Instalação no cliente

```bash
sudo apt install ./epg-stream_1.13.1-1_amd64.deb
sudo epg-stream-configure
```

O configurador solicita porta, URL pública opcional, servidor de licenças,
identificador estável, arquivo da chave e, apenas em volume vazio, o primeiro
administrador. Após o primeiro `/health`, a senha de bootstrap é removida de
`/etc/epg-stream/epg-stream.env`.

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

## Atualização e remoção

```bash
sudo apt install ./epg-stream_NOVA-VERSAO_ARCH.deb
sudo apt remove epg-stream
```

Atualizações preservam configuração e dados. A remoção também preserva
`/var/lib/epg-stream`; exclua esse diretório somente com backup e autorização
explícita. Para rollback, instale novamente o `.deb` anterior.
