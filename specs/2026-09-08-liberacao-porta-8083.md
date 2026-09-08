# Liberação da porta TCP 8083

- Estado: concluído e implantado
- Data: 08/09/2026

## Objetivo

Liberar TCP/8083 no servidor `181.233.106.46` somente para os conjuntos IPv4 e
IPv6 confiáveis já cadastrados, sem alterar as exceções individuais existentes.

## Implementação

- tabela efetiva: `inet tvstream_firewall`;
- configuração persistente:
  `/etc/nftables.d/tvstream-firewall.nft`;
- TCP/8083 incluída nas regras de `trusted_ipv4` e `trusted_ipv6`;
- sintaxe validada com `nft -c` antes da aplicação;
- regras ativas inseridas sem recarregar globalmente o firewall;
- Docker, NAT, multicast e a exceção individual de `187.19.16.59/32` para
  9100/9200 não foram modificados.

## Validação e rollback

- conexão TCP/8083 originada em `187.19.16.59`: aberta;
- EPG Server permaneceu saudável durante a alteração;
- backup preservado:
  `/etc/nftables.d/tvstream-firewall.nft.pre-port8083-20260908-114756`.

Para rollback, restaurar o backup, validar com `nft -c` e reaplicar a unit
`tvstream-firewall.service` com acesso de console disponível.
