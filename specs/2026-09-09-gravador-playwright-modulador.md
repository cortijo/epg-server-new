# Spec: gravador Playwright para perfis de moduladores

## Objetivo

Disponibilizar temporariamente, somente no host `181.233.106.46`, um Playwright
Codegen visual para registrar o procedimento de cadastro no DeXin NDS3306I.

## Fora de escopo

- executar automaticamente alterações no modulador;
- integrar o gravador ao processo multicast;
- armazenar credenciais no Git ou na imagem;
- implantar no host `187.19.16.59`.

## Segurança e isolamento

- container independente, sem acesso aos volumes do OMNIEPG;
- interface publicada apenas no loopback e acessada por túnel SSH;
- senha VNC temporária obrigatória;
- capabilities removidas, `no-new-privileges` e `shm` próprio;
- gravações persistidas fora do repositório e tratadas como sensíveis.

## Aceite

1. O container abre o alvo HTTPS aceitando o certificado embarcado.
2. O operador acessa noVNC exclusivamente por túnel SSH.
3. Codegen grava o fluxo em arquivo persistente.
4. OMNIEPG permanece saudável e com a mesma quantidade de emissores.
5. O gravador pode ser parado sem afetar o produto.

## Rollback

Parar e remover somente `omniepg-modulator-recorder`. Nenhuma alteração no
container `epg-stream` ou em `/srv/epg-stream` é necessária.

## Implantação inicial

- host exclusivo: `181.233.106.46`;
- imagem: `omniepg-modulator-recorder:v1.0.1`;
- publicação: `127.0.0.1:9323 -> 6080/tcp`;
- alvo inicial: DeXin NDS3306I em HTTPS;
- acesso externo: somente por túnel SSH;
- OMNIEPG permaneceu saudável, licenciado e com 27 emissores ativos.
