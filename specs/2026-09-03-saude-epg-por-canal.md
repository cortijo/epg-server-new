# Spec: saúde do EPG por canal e portadora

- Estado: concluído e implantado
- Versão: 1.17.0

## Objetivo

Exibir no processo de emissão multicast se cada canal possui programação válida,
com agregação visual por portadora e uma central única de erros.

## Estados

- verde: todos os canais possuem programa atual e próximo evento;
- amarelo na portadora: ao menos um canal tem erro ou aviso;
- vermelho na portadora: todos os canais estão com erro;
- vermelho no canal: fonte falhou, emissor parou, ID não existe, programação está
  vazia ou não existe evento no horário atual;
- amarelo no canal: primeira sincronização pendente, fonte usando cache após falha
  ou programa atual existe sem próximo evento.

## Diagnósticos

- emissor multicast parado;
- falha ao baixar/processar a fonte;
- fonte aguardando sincronização;
- ID XMLTV ausente;
- canal sem programas;
- grade expirada ou lacuna no horário atual;
- ausência de próximo evento;
- uso da última cópia válida após falha de atualização.

## Interface

- indicador em cada portadora;
- indicador e motivo em cada canal expandido;
- alerta geral quando houver problemas;
- botão “Erros do EPG” com contagem e relatório consolidado.

## Validação

- 74 testes aprovados e 5 testes dependentes do ambiente ignorados;
- JavaScript embarcado e Python validados;
- imagem `epgserver:v1.17.0-20260903` implantada em `181.233.106.46`;
- rede `host`, 27 emissores, licença válida, zero reinícios e zero erros críticos;
- primeira sincronização após reinício força o carregamento das fontes em memória;
- diagnóstico real: 59 canais sem eventos na janela atual, 2 IDs XMLTV ausentes
  e 1 falha de fonte, consolidados por canal e portadora no painel;
- rollback: `epg-stream-pre-v1.17.0-20260903-102411`;
- backup: `/srv/epg-stream-backup-pre-v1.17.0-20260903-102411`.
