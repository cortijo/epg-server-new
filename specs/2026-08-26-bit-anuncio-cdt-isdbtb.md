# BIT e anúncio de CDT para logotipo ISDB-TB

- ID: `2026-08-26-bit-anuncio-cdt-isdbtb`
- Estado: concluído
- Versão-alvo: `1.7.0`

## Objetivo

Completar a sinalização de logotipo ISDB-TB sem alterar EIT, TDT/TOT ou o
transporte CDT já validado. Quando uma portadora possuir ao menos um canal com
logo, o emissor deve publicar:

- SDT `0x42` no PID `0x0011`, com descritor de logo `0xCF`;
- BIT `0xC4` no PID `0x0024`;
- descritor de parâmetros SI `0xD7`, no segundo loop da BIT, anunciando a CDT
  pelo `table_id 0xC8`;
- CDT `0xC8` no PID `0x0029`, mantendo os seis formatos ARIB existentes.

## Contrato binário

A BIT usa `original_network_id` e versão de sinalização da portadora, seção
única e CRC MPEG-2. O primeiro loop de descritores fica vazio. O segundo loop
contém um `broadcaster_id` local e o descritor `0xD7` com
`parameter_version`, `update_time=0`, uma entrada `table_id=0xC8` e descrição
vazia. A presença do `0xC8` é o anúncio normativo necessário para o receptor
detectar a CDT.

O carrossel repete a BIT a cada segundo, somente quando existe CDT de logo.

## Critérios de aceite

- [x] Captura contém PID `0x0024` e tabela `0xC4` com CRC válido.
- [x] BIT possui ONID e versão esperados.
- [x] Segundo loop possui descritor `0xD7` anunciando `0xC8`.
- [x] PID `0x0029`, CDT e os seis formatos de logo permanecem válidos.
- [x] Auditor `--require-logo` exige `0x0011`, `0x0024` e `0x0029`.
- [x] Portadora sem logo não recebe BIT/CDT nem muda seu perfil atual.
- [x] Captura multicast isolada passa sem erros de continuidade ou identidade.

## Integração Dexing

No canal de entrada correspondente à portadora EPG, configurar passthrough
sem remapeamento:

- `0x0011 -> 0x0011`;
- `0x0024 -> 0x0024`;
- `0x0029 -> 0x0029`.

EIT `0x0012` e TDT/TOT `0x0014` continuam conforme a configuração já
validada. A auditoria definitiva deve ser feita na saída TS do Dexing e depois
no sinal RF; validar apenas a entrada não prova o caminho completo.

## Riscos e rollback

O risco principal é conflito com BIT gerada pelo multiplexador. A saída deve
ter uma única fonte coerente de BIT. Em caso de regressão, recriar o container
com a imagem imutável `epgserver:v1.6.0-20260825`; os dados persistidos são
compatíveis e não exigem migração.

## Evidências de validação

Em 26/08/2026, a imagem candidata foi compilada em Linux e executada numa
saída isolada `239.255.250.24:55024`, usando a portadora de teste TSID/ONID 61
e o logo SPORTV. A captura conteve 4.739 pacotes TS:

- PID `0x0024`: 7 pacotes e 7 seções BIT `0xC4`;
- anúncio BIT: `0xC8`, segundo loop, broadcaster local 0;
- PID `0x0029`: 98 pacotes e 42 seções CDT;
- seis tipos de logo, dimensões e PNG ARIB válidos;
- zero erro de CRC, continuidade ou SID/TSID/ONID.

Os dois testes unitários novos passaram. A suíte geral manteve duas falhas
preexistentes do ambiente Windows: fixture XMLTV vencida pela data corrente e
teste de firewall que requer Bash.
