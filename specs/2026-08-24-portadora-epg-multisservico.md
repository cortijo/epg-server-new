# Portadora EPG multissserviço

- ID: `2026-08-24-portadora-epg-multisservico`
- Estado: concluído e implantado
- Versão alvo: `v127`

## Objetivo

Permitir que um único multicast EPG auxiliar represente uma portadora do
Dexing e transporte a sinalização de três, quatro ou mais canais que tenham o
mesmo TSID e ONID. Cada serviço mantém seu próprio Program Number final e seu
ID exato no XMLTV.

## Regras funcionais

| ID | Regra |
|---|---|
| RF-01 | Um cadastro do módulo representa uma portadora e possui destino, porta, interface, TSID e ONID comuns. |
| RF-02 | A portadora contém de 1 a 64 serviços. |
| RF-03 | Cada serviço possui nome, ID XMLTV e Program Number final únicos dentro da portadora. |
| RF-04 | A PAT anuncia todos os serviços e uma PMT vazia individual para cada serviço. |
| RF-05 | A SDT anuncia todos os serviços da portadora. |
| RF-06 | As EIT de todos os serviços são intercaladas no PID `0x0012`. |
| RF-07 | TDT/TOT são enviados uma única vez no PID `0x0014`. |
| RF-08 | Os contadores de continuidade de `0x0012` e `0x0014` são únicos após a agregação. |
| RF-09 | O painel permite adicionar e remover serviços sem duplicar o multicast. |
| RF-10 | Configurações v1 com um único `epg_channel_id`/`service_id` são migradas automaticamente para uma lista com um serviço. |

## Validações

- TSID, ONID e Program Number: 1 a 65535.
- Program Numbers não podem se repetir na mesma portadora.
- O ID XMLTV é obrigatório em cada serviço.
- O PID base das PMTs deve estar entre `0x0020` e `0x1FFE`, não pode ser
  reservado e o intervalo consecutivo deve caber no espaço de PIDs.
- Destino deve ser multicast IPv4 e não pode colidir com outra portadora.
- Bitrate, porta, TTL, fonte XMLTV e interface seguem as validações existentes.

## Contrato HTTP

`POST /api/epg-only/save` recebe os campos comuns e:

```json
{
  "services": [
    {"id":"svc-1","name":"Canal 1","epg_channel_id":"canal1.br","service_id":2301},
    {"id":"svc-2","name":"Canal 2","epg_channel_id":"canal2.br","service_id":2302}
  ]
}
```

`GET /api/epg-only/state` devolve a mesma lista. Os campos legados são aceitos
somente na leitura/migração.

## Arquitetura de transporte

```text
XMLTV -> EpgInjector serviço A --\
XMLTV -> EpgInjector serviço B ----> agregador 0x0012/0x0014 -> UDP multicast
XMLTV -> EpgInjector serviço C --/            ^
PAT multi + PMTs + SDT multi -----------------|
```

- PMTs usam PIDs consecutivos a partir de `pmt_pid`.
- O agregador usa round-robin entre os serviços.
- Pacotes `0x0014` dos injetores posteriores são descartados; só o primeiro
  fornece TDT/TOT.
- Antes do envio, o agregador reescreve o continuity counter por PID.

## Compatibilidade e rollback

- O arquivo persistido passa para versão 2.
- Um registro v1 torna-se uma portadora de um serviço sem alterar destino,
  TSID, ONID, Program Number ou XMLTV; assim o `teste2` continua no ar.
- Rollback: manter o contêiner `v126` parado com `restart=no` e preservar cópia
  do diretório `/srv/tvstreamer5` anterior ao deploy.

## Critérios de aceite

1. Build Docker e testes CTest aprovados.
2. JavaScript embarcado passa na verificação sintática.
3. Portadora de teste com três serviços apresenta os três Program Numbers na
   PAT e SDT, três PMTs e EIT para os três SIDs.
4. Não há descontinuidade repetida nos PIDs `0x0012` e `0x0014`.
5. Transporte contém apenas PAT, SDT, EIT, TDT/TOT, PMTs configuradas e NULL.
6. Cadastro legado de um serviço é carregado e emitido sem alteração.
7. Reinício do processo preserva cadastro e autostart.
8. Produção mantém canais existentes ativos e o emissor `teste2` operacional.

## Evidências

| Data | Validação | Resultado |
|---|---|---|
| 2026-08-24 | especificação criada antes do código | concluído |
| 2026-08-24 | build Docker e CTest | compilação aprovada; 2/2 testes passaram |
| 2026-08-24 | transporte com SIDs 2301, 2302 e 2303 | PAT/3 PMTs/SDT/EIT válidas, CRC 0 e continuidade 0 |
| 2026-08-24 | compatibilidade v1 | `teste2` carregado automaticamente como portadora de um serviço |
| 2026-08-24 | validação de API | Program Number duplicado corretamente rejeitado |
| 2026-08-24 | interface web | JavaScript válido e formulário dinâmico sincronizado com a API |
| 2026-08-24 | implantação | `tvstreamer5:v127-20260824`, saúde OK, quatro canais ativos e rollback v126 preservado |
| 2026-08-24 | captura da saída física `teste2` | 1.200 datagramas; CRC, IDs e continuidade sem erros; PAT/PMT/SDT/EIT/TDT/TOT presentes |
