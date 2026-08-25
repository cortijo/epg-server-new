# Pré-visualização autenticada do logo por canal

## Estado

Concluído e implantado em 25/08/2026 na versão `1.3.1`.

## Objetivo

Mostrar, no formulário de edição da portadora, uma miniatura do PNG efetivamente persistido e transmitido pelo serviço.

## Requisitos

- endpoint GET autenticado por `carrier_id` e `service_id`;
- o backend resolve o caminho exclusivamente a partir do cadastro persistido;
- impedir leitura fora de `/data/logos`;
- resposta `image/png`, `nosniff` e sem cache;
- miniatura 64×36 ao lado dos botões de enviar/trocar/remover;
- canal sem logo mantém somente a orientação de upload;
- nenhuma mudança na geração multicast, SDT, CDT, EIT ou relógio.

## Critérios de aceite

1. SPORTV apresenta a imagem normalizada no formulário.
2. Canal sem logo não solicita arquivo inexistente.
3. Requisição com IDs inválidos retorna 404.
4. Testes da interface, autenticação e contenção de caminho passam.

## Evidências

- cinco testes de contrato aprovados;
- endpoint isolado respondeu `200 image/png`, `no-store` e `nosniff`;
- imagem retornada: PNG válido, 64×36 e 1.105 bytes;
- captura real da portadora SPORTV: 3.360 pacotes TS, 35 pacotes no PID
  `0x0029`, cinco CDT `0xC8`, descriptor SDT do SID 1952 associado ao
  `download_data_id` 1952 e zero erros de CRC;
- produção saudável, imagem `tvstream-epg:v1.3.1-20260825`, zero reinícios.
