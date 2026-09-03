# Spec: saúde do EPG por canal e portadora

- Estado: em validação
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
