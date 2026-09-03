# Spec: catálogo e sincronização de fontes XMLTV

- ID: `2026-09-02-catalogo-fontes-xmltv`
- Estado: `validada; implantação pendente`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-09-02`

## Objetivo

Permitir consultar os canais e a programação existente em cada fonte XMLTV,
exibir claramente o processamento em andamento e explicar os ajustes feitos
quando a fonte usa o tipo Parse-XML.

## Requisitos

- [x] Exibir todos os canais disponíveis, nome e ID XMLTV.
- [x] Exibir número de programas, atração atual e próximas 24 entradas por canal.
- [x] Mostrar estado visual enquanto o arquivo é baixado e processado.
- [x] Permitir sincronização forçada pelo painel.
- [x] Informar contadores e comportamento de fallback da normalização Parse-XML.
- [x] Não alterar emissão multicast, PIDs ou associação de canais.

## Fora de escopo

- Alterar a grade recebida além das regras existentes do Parse-XML.
- Alterar PSI/SI, PIDs, emissores ou configuração das portadoras.
- Exibir toda a grade histórica sem limite em uma única resposta.

## Contrato

`GET /api/catalog?source_id=ID&force=1` renova a origem e retorna:

- total de canais e programas na janela operacional;
- horário da sincronização;
- canal atual e até 24 programas correntes/futuros por canal;
- estatísticas de normalização, quando o tipo for Parse-XML.

O parâmetro `force=1` força download e validação. Sem ele, o cache válido de
cinco minutos pode ser utilizado. A janela operacional permanece de um dia no
passado até oito dias no futuro.

## Parse-XML

O relatório informa canais sintetizados, timezones adicionados, referências de
canal reescritas, duplicidades removidas e programas inválidos descartados. A
cópia normalizada somente substitui a anterior após validação integral; em caso
de falha, a última cópia persistida continua disponível.

## Riscos e rollback

- Resposta excessiva: limitar a prévia a 24 entradas atuais/futuras por canal.
- Fonte lenta: manter timeout de 30 segundos e apresentar estado de progresso.
- Regressão operacional: o endpoint reutiliza `GuideCache` e não reinicia
  emissores. Rollback pela imagem anterior v1.14.1 com o mesmo volume.

## Validação

- suíte unitária Python;
- sintaxe do JavaScript embarcado;
- candidato Docker isolado;
- inspeção visual desktop e móvel;
- implantação com rollback e preservação dos emissores.

## Registro

| Data | Ação | Resultado |
|---|---|---|
| 2026-09-02 | testes | 64 aprovados, 5 ignorados por ausência de Bash no Windows |
| 2026-09-02 | sintaxe | Python, JavaScript embarcado e `git diff --check` aprovados |
| 2026-09-02 | Docker | imagem `epgserver:v1.15.0-candidate` compilada no host isoladamente |
| 2026-09-02 | implantação | não iniciada: backup protegido exige nova autorização administrativa |
