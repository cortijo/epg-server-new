# Clonar portadora EPG

## Objetivo

Permitir duplicar rapidamente uma portadora existente, preservando sua configuração e seus serviços, sem alterar nem interromper a transmissão original.

## Requisitos funcionais

- Exibir a ação **Clonar** na coluna lateral de ações de cada portadora.
- Abrir o formulário preenchido com fonte XMLTV, TSID, ONID, porta, interface, PID base da PMT, bitrate, TTL e serviços da original.
- Criar um novo identificador para a portadora e novos identificadores para seus serviços.
- Acrescentar `- Cópia` ao nome sugerido.
- Limpar o destino multicast e exigir que o operador informe outro endereço.
- Definir a inicialização da cópia como manual.
- Não alterar, parar ou reiniciar a portadora original.
- Manter o fechamento do formulário somente pelos botões Salvar ou Cancelar.

## Requisitos de segurança e operação

- A cópia não pode iniciar no mesmo multicast da original por preenchimento automático.
- A validação existente de IPv4 multicast, interface, porta, serviços e colisão de destino continua obrigatória.
- Nenhum dado é persistido antes do operador clicar em Salvar.

## Critérios de aceite

1. O botão Clonar aparece para cada portadora.
2. O formulário mostra o título `Clonar portadora EPG`.
3. O multicast fica vazio e a inicialização fica Manual.
4. Os canais, SIDs e IDs XMLTV são preservados.
5. Cancelar não cria nem modifica portadoras.
6. Salvar com novo multicast cria uma portadora independente e parada.
7. Testes automatizados, sintaxe JavaScript e inspeção visual são aprovados.

## Entrega

- Versão: `1.2.1`
- Imagem: `tvstream-epg:v1.2.1-20260824`
- Tag Git: `epg-v1.2.1`
- Rollback previsto: `tvstream-epg:v1.2.0-20260824`

## Resultado da validação

- `python -m py_compile epg-product/app.py`: aprovado;
- 10 testes automatizados: aprovados;
- sintaxe JavaScript extraída da interface: aprovada;
- imagem isolada em TCP 19103: iniciou como EPG Stream 1.2.1;
- função `cloneCarrier(id)` confirmada no HTML autenticado da imagem isolada;
- imagem promovida sem rebuild, ID
  `sha256:d0190d2218e1c35110005f8491935a53a2f7b4ff74749a644026a2f68dc52458`;
- produção: `epg-stream`, rede host, somente leitura, UID/GID 10001:10001,
  reinícios 0 e `/health` interno/externo aprovado;
- TVStreamer principal permaneceu ativo em `tvstreamer5:v127-20260824`;
- rollback preservado no container `epg-stream-pre-v1.2.1-20260824` e no
  backup `/srv/epg-stream-backup-pre-v1.2.1-20260824`.
