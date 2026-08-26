# Acesso público controlado ao servidor de licenças

- ID: `2026-08-26-acesso-publico-servidor-licencas`
- Estado: `concluído`
- Responsável: Codex
- Solicitante: Julio Cortijo
- Criada em: 2026-08-26

## Objetivo

Permitir administrar o EPG License Server pelo IP público do host sem abrir a
porta para toda a Internet e sem alterar o EPG ou os emissores multicast.

## Requisitos

- [x] Alterar o bind HTTP de `127.0.0.1:9200` para `0.0.0.0:9200`.
- [x] Liberar TCP/9200 somente para os sets `trusted_ipv4` e `trusted_ipv6` já
  mantidos pela tabela `inet tvstream_firewall`.
- [x] Persistir a porta em `/etc/nftables.d/tvstream-firewall.nft`.
- [x] Preservar volume, licenças, chave do cliente, imagem e credenciais.
- [x] Confirmar health, autenticação administrativa, licença 63/100, 27
  emissores e zero reinícios após a troca.

## Segurança

O serviço usa HTTP Basic e não termina TLS. Portanto a porta não pode ser
liberada globalmente. Acesso por IP público fica restrito às redes já aprovadas:
`45.224.164.0/22`, `181.233.106.0/24`, redes privadas existentes e
`2804:44f0::/32`. Para acesso fora dessas redes, usar túnel SSH ou implantar
proxy HTTPS antes de ampliar o firewall.

## Rollback

- recriar `epg-license-server` com `LICENSE_HTTP_HOST=127.0.0.1`;
- restaurar o backup explícito de `tvstream-firewall.nft` criado na alteração;
- recarregar atomicamente a tabela `inet tvstream_firewall`.

## Validação

| Cenário | Resultado esperado | Estado |
|---|---|---|
| escuta TCP | `0.0.0.0:9200` | aprovado |
| firewall persistente | 9200 apenas nos sets confiáveis | aprovado |
| health público permitido | HTTP 200 | aprovado em `181.233.106.46:9200` |
| licença/EPG | 63/100 e 27 emissores | aprovado |
| containers | zero reinícios | aprovado |

## Resultado operacional

- URL: `http://181.233.106.46:9200`;
- backup do firewall:
  `/etc/nftables.d/tvstream-firewall.nft.pre-license-9200-20260826-210354`;
- imagem e volume do servidor de licenças preservados;
- aceite explícito do solicitante registrado para HTTP sem TLS, limitado às
  redes confiáveis.
