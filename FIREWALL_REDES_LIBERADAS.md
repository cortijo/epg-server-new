# Firewall do EPG Server por listas em `/opt`

Este perfil restringe portas TCP do host às redes cadastradas, usando a tabela
nftables exclusiva `inet epg_firewall`. Ele não altera NAT, FORWARD, regras do
Docker nem o tráfego multicast de saída.

O projeto também contém `scripts/firewall-manager.sh`, indicado quando for
necessário administrar TCP, UDP, intervalos e operações CRUD. Este guia cobre
o perfil simples implantado no servidor EPG de instalação recente.

## Pré-requisitos e segurança

- Ubuntu/Debian com nftables, Python 3 e systemd;
- acesso root/sudo;
- console do provedor disponível para contingência;
- rede do administrador e porta SSH cadastradas antes da primeira aplicação.

Descubra o cliente e a porta da sessão atual com `echo "$SSH_CONNECTION"`.
Um endereço individual usa `/32` em IPv4 ou `/128` em IPv6. Não use os
endereços reservados dos exemplos em produção.

## Instalação

Na raiz do repositório:

```bash
sudo apt-get update
sudo apt-get install -y nftables python3
sudo install -o root -g root -m 0750 scripts/firewall-redes-liberadas.sh /usr/local/sbin/epg-firewall
sudo install -o root -g root -m 0644 scripts/redes-liberadas.example /opt/redes-liberadas
sudo install -o root -g root -m 0644 scripts/portas-liberadas.example /opt/portas-liberadas
sudo install -o root -g root -m 0644 scripts/firewall-redes-liberadas.service /etc/systemd/system/epg-firewall.service
```

Edite as listas antes de ativar:

```bash
sudo nano /opt/redes-liberadas
sudo nano /opt/portas-liberadas
```

Exemplo de redes e portas:

```text
# /opt/redes-liberadas
198.51.100.25/32
203.0.113.0/24
2001:db8:1234::/48

# /opt/portas-liberadas
22
9100
```

Ative e aplique:

```bash
sudo systemctl daemon-reload
sudo systemctl enable epg-firewall
sudo systemctl start epg-firewall
```

## Liberar ou remover uma rede

```bash
sudo nano /opt/redes-liberadas
sudo systemctl restart epg-firewall
```

Não reinicie Linux, Docker ou `epg-stream`. O script rejeita CIDR/porta
inválidos e, durante uma sessão SSH, recusa regras que não preservem o cliente
e a porta SSH atuais.

## Verificação

```bash
sudo systemctl status epg-firewall --no-pager
sudo nft list table inet epg_firewall
curl -fsS http://127.0.0.1:9100/health
docker ps --filter name=epg-stream
sudo timeout 5 tcpdump -ni INTERFACE -c 10 \
  'udp and dst host GRUPO_MULTICAST and dst port PORTA'
```

## Rollback e recuperação

Pelo host ou console do provedor:

```bash
sudo systemctl disable --now epg-firewall
sudo nft delete table inet epg_firewall
```

Isso remove somente a tabela deste perfil. Nunca execute `nft flush ruleset`,
pois também apagaria regras do Docker e de outros serviços. Após corrigir as
listas, use `sudo systemctl enable --now epg-firewall`.
