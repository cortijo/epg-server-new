# Liberação do painel EPG para o servidor cliente

- Estado: concluído e implantado
- Data: 04/09/2026

## Objetivo

Permitir que `187.19.16.59/32` acesse o painel EPG em TCP/9100 no servidor
`181.233.106.46`, sem ampliar o acesso às demais origens.

## Implementação

- firewall efetivo: tabela nftables `inet tvstream_firewall`;
- UFW confirmado como inativo;
- regra ativa adicionada somente para TCP/9100 e origem `187.19.16.59/32`;
- regra persistida em `/etc/nftables.d/tvstream-firewall.nft` junto da exceção
  TCP/9200 já existente;
- configuração validada com `nft -c` antes da aplicação;
- não houve recarga global da tabela nem alteração em NAT, Docker ou multicast.

## Validação

- conexão TCP originada em `187.19.16.59`: aberta;
- requisição HTTP ao painel: `401 Unauthorized`, resposta esperada sem
  credenciais e comprovação de que a aplicação foi alcançada;
- backup preservado:
  `/etc/nftables.d/tvstream-firewall.nft.pre-epgbr-9100-20260904-161402`.

## Rollback

Restaurar o backup acima, validar com `nft -c` e reaplicar exclusivamente a
tabela `inet tvstream_firewall` pela unit `tvstream-firewall.service`.
