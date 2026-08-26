# Publicações XMLTV versionadas

## Estado

`validada`

## Objetivo

Criar um segundo módulo no EPG Server para receber arquivos XMLTV fornecidos
por programadoras, normalizá-los para o formato aceito pelo painel e pelo
emissor ISDB-TB e publicá-los em uma URL permanente. Uma publicação poderá
conter várias versões; a URL selecionará automaticamente o arquivo cuja grade
estiver vigente, sem exigir alteração nas fontes ou portadoras cadastradas.

## Escopo

- cadastro de publicações com nome e token público imutável;
- upload autenticado de XML/XMLTV de até 96 MiB;
- armazenamento persistente das versões normalizadas em `/data`;
- URL pública estável por publicação;
- cálculo automático de início e término da vigência pela programação;
- seleção automática da versão vigente, próxima ou mais recente;
- normalização de datas sem fuso para `-0300`;
- reconciliação de IDs pelo prefixo numérico do canal quando inequívoca;
- remoção de eventos com duração nula/negativa e canais duplicados;
- painel para criar publicação, copiar URL, enviar, consultar e excluir versões;
- testes automatizados, documentação e deploy seguro.

## Fora de escopo

- editar manualmente cada programa;
- converter formatos que não sejam XMLTV;
- buscar arquivos em e-mail, FTP ou API externa;
- alterar a URL pública depois de criada;
- remover ou modificar fontes/portadoras existentes automaticamente;
- alterar firewall.

## Regras de normalização

1. O elemento raiz deve ser `<tv>`.
2. Datas `AAAAMMDDhhmmss` sem sufixo recebem ` -0300`.
3. Datas que já possuem `Z` ou `±HHMM` são preservadas.
4. Se o `programme@channel` não existir, o sistema compara o prefixo numérico
   inicial com os canais declarados. Havendo exatamente um candidato, reescreve
   para o ID declarado.
5. Eventos sem canal, com data inválida ou `stop <= start` são descartados.
6. Declarações repetidas do mesmo canal são consolidadas.
7. A vigência começa no menor `start` e termina no maior `stop` dos eventos
   válidos.
8. O arquivo normalizado deve continuar válido no parser do painel.

## Seleção da versão na URL permanente

1. Entre versões que abrangem o instante atual, vence a de maior `valid_from` e,
   em empate, a enviada mais recentemente.
2. Se nenhuma estiver vigente e houver versão futura, entrega a próxima.
3. Se todas estiverem expiradas, entrega a que terminou mais recentemente.
4. Se não houver versão, a URL responde `404`.
5. A seleção acontece a cada requisição; não depende de cron e não muda a URL.

## Segurança

- rotas administrativas exigem a autenticação existente;
- a URL pública contém token aleatório de 128 bits;
- nomes enviados nunca são usados como caminho de arquivo;
- arquivos são limitados a 96 MiB e gravados de forma atômica;
- caminhos são validados dentro do diretório de publicações;
- resposta pública usa `no-cache` e `X-Content-Type-Options: nosniff`;
- conteúdo persistido não inclui credenciais.

## Critérios de aceite

- o `guide.xml` de referência é convertido com 72 eventos inválidos removidos,
  oito referências de canal reconciliadas e fuso `-0300` acrescentado;
- o resultado é aceito pelos parsers Python e C++ em relação a IDs e datas;
- uma publicação mantém a mesma URL após múltiplos uploads;
- a versão retornada muda automaticamente conforme a vigência;
- a UI mostra vigência, contagens, correções, situação e URL copiável;
- uma versão ou publicação pode ser excluída sem permitir fuga de diretório;
- testes existentes continuam aprovados;
- imagem isolada responde no health check antes do deploy de produção.

## Validação planejada

1. testes unitários da normalização e seleção temporal;
2. conversão integral do `guide.xml` fornecido;
3. compilação Python e C++ e build Docker;
4. teste de API, upload e URL estável em container isolado;
5. revisão de segurança, diff e ausência de segredos;
6. deploy com backup, rollback preservado e health check.

## Rollback

Restaurar o container anterior e o backup do volume preservados no deploy. A
nova chave `xmltv_publications` é aditiva; versões antigas ignoram esse campo.
Os arquivos ficam isolados em `/data/xmltv-publications` e podem permanecer no
volume durante um rollback sem interferir nas fontes existentes.

## Resultado da validação

- 23 testes automatizados aprovados;
- Python, JavaScript do painel e diff aprovados nas verificações sintáticas;
- imagem `v1.6.0` compilada com o emissor C++ atualizado;
- `guide.xml` real de 11,2 MB convertido para 12,1 MB;
- 37.464 programas e 191 canais publicados;
- 74.928 campos de data receberam `-0300`;
- 707 referências de programas foram reconciliadas pelo código do canal;
- cinco declarações ausentes foram sintetizadas, uma duplicada foi removida e
  72 eventos de duração zero foram descartados;
- o XML final ficou com zero datas sem offset e zero programas sem declaração
  de canal correspondente;
- URL pública respondeu sem autenticação, com o mesmo token e metadados da
  versão selecionada;
- o cadastro da URL em **Fontes XMLTV** encontrou 191 canais e 37.464 programas;
- o emissor C++ carregou o canal de teste, 63 programas e 49 seções schedule e
  permaneceu `running` em multicast isolado;
- os artefatos de laboratório foram removidos e a produção v1.5.0 permaneceu
  saudável durante todo o ensaio;
- produção atualizada para `epgserver:v1.6.0-20260825`, schema 3, 28 emissores
  ativos e zero reinícios do container após o corte;
- `EPG_PUBLIC_BASE_URL` configurada como `http://181.233.106.46:9100`, mantendo
  as URLs permanentes utilizáveis fora do servidor;
- rollback preservado no container
  `epg-stream-pre-v1.6.0-20260825-214509` e no backup
  `/srv/epg-stream-backup-pre-v1.6.0-20260825-214509`.
