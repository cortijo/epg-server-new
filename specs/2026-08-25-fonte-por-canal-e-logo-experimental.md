# Fonte XMLTV por canal e logo ISDB-TB experimental

## Estado

Concluído e implantado em 25/08/2026 na versão `1.3.0`.

## Objetivo

Permitir que cada serviço de uma portadora selecione sua própria fonte XMLTV e iniciar o teste controlado de logotipos de canal no transporte ISDB-TB, sem alterar os PIDs de EPG já validados.

## Escopo

- manter `source_id` da portadora como fonte padrão e compatibilidade com cadastros existentes;
- adicionar `source_id` opcional por serviço, com herança da fonte padrão;
- resolver e entregar ao emissor a URL correta de cada serviço;
- mostrar grade e catálogo usando a fonte individual do serviço;
- impedir exclusão de fonte usada como padrão ou por qualquer serviço;
- permitir upload e remoção de PNG por serviço já salvo;
- persistir o arquivo em `/data/logos/<portadora>/<serviço>.png`, nunca dentro do JSON;
- limitar o upload a 2 MiB, validar assinatura, IHDR, dimensões e integridade básica do PNG;
- gerar CDT no PID `0x0029` e descriptor de transmissão de logo na SDT do PID `0x0011`;
- manter PAT, PMT, EIT (`0x0012`) e TDT/TOT (`0x0014`) sem mudança;
- apresentar logo como recurso **experimental**, destinado primeiro a uma saída de laboratório.

## Modelo de dados

Cada serviço passa a aceitar:

- `source_id`: fonte XMLTV própria; vazio significa herdar `carrier.source_id`;
- `logo`: objeto persistido contendo `enabled`, caminho, `logo_id`, `logo_version` e `download_data_id`.

Cadastros antigos são normalizados em leitura/validação e continuam usando a fonte da portadora.

## Sinalização experimental de logo

- PID CDT: `0x0029`;
- `table_id`: `0xC8`;
- `data_type`: `0x01`;
- descriptor de transmissão de logo na SDT: tag `0xCF`, tipo `0x01`;
- o arquivo PNG enviado é transportado como logo HD grande (`logo_type 0x05`);
- atualização do arquivo incrementa `logo_version` modulo 4096;
- o primeiro teste não promete conversão para a paleta fixa ARIB nem todos os seis tamanhos; isso depende de validação no receptor/Dexing e será evolução posterior.

## Compatibilidade e segurança

- nenhuma portadora sem logo muda o conjunto de tabelas emitido;
- nenhuma configuração do Dexing é alterada automaticamente;
- o teste de logo exige, em uma saída de laboratório, passthrough dos PIDs `0x0011` e `0x0029` e ausência de uma SDT conflitante gerada pelo modulador;
- dados existentes serão preservados e o deploy terá backup e rollback.

## Critérios de aceite

1. Dois serviços da mesma portadora podem carregar programação de duas fontes diferentes.
2. A fonte padrão continua atendendo serviços sem seleção individual.
3. A tela mostra a fonte e catálogo corretos em cada linha de canal.
4. Upload inválido é recusado e PNG válido fica persistido fora do JSON.
5. Um canal com logo emite CDT `0xC8` no PID `0x0029` e SDT com descriptor `0xCF` correspondente.
6. Canais sem logo continuam emitindo o mesmo perfil anterior.
7. Testes automatizados, imagem isolada e rollback são validados antes da troca de produção.

## Rollback

Restaurar o backup do diretório `/srv/epg-stream` e recriar `epg-stream` com a imagem anterior imutável. A versão anterior ignora os campos adicionais de serviço e os arquivos de logo permanecem inertes.

## Evidências de validação

- quatro testes de contrato Python aprovados;
- sintaxe Python e JavaScript aprovada;
- compilação C++ e imagem Docker aprovadas;
- ambiente isolado saudável, sem emissores herdados ativos;
- captura multicast de laboratório com 2.702 pacotes TS;
- PAT, PMT, SDT, EIT p/f, EIT schedule, TDT/TOT e CDT com CRC válido;
- descriptor `0xCF` do SID 1921 associado ao `download_data_id` 1921 da CDT;
- produção saudável, zero reinícios e processos emissores retomados.
