# Spec: Simulador de TV ISDB-TB na saída do EPG Server

- ID: `2026-08-28-simulador-tv-isdbtb`
- Estado: `concluído`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-28`

## Resumo e objetivo

Adicionar ao painel um simulador que captura uma amostra dos mesmos datagramas
gerados pela portadora ativa, decodifica PSI/SI e apresenta PIDs, tabelas,
identidades e metadados EIT da forma como um receptor ISDB-TB os interpreta.

## Escopo

- capturar o TS no emissor antes do envio UDP, sem interferir no multicast;
- auditar PIDs, tabelas, CRC, continuidade, SID, TSID e ONID;
- mostrar título, textos `0x4D`, `0x4E` e resultado reconstruído;
- disponibilizar a ação por portadora no painel.

Ficam fora de escopo áudio/vídeo, transcode e análise da saída do Dexing/RF.

## Desenho

```text
TVStreamEpgOnly -> datagrama 7x188 -> UDP multicast
                         |
                         +-> amostra TS sob demanda
                                      |
                                      v
                         auditor -> relatório JSON -> Simulador TV
```

O painel cria `<carrier>.request`; o emissor espelha alguns segundos em arquivo
temporário e publica por rename atômico como `<carrier>.ts`.

## Aceite, riscos e rollback

- portadora parada retorna erro claro;
- captura limitada e armazenada em `/data/diagnostics`;
- relatório permanece disponível quando encontra erros;
- emissão multicast não para durante a captura;
- API/UI respeitam autenticação e licença;
- rollback restaura os containers v1.12.2 preservados.

## Validação

- build Docker/C++, testes Python e JavaScript;
- captura sintética e relatório real em portadora ativa;
- health, licença, emissores e logs nos dois hosts.

## Registro

| Data | Ação | Resultado |
|---|---|---|
| 2026-08-28 | início | contrato definido antes do código |
| 2026-08-28 | testes locais | 47 testes aprovados, 5 testes Bash ignorados no Windows; JavaScript válido |
| 2026-08-28 | validação 181 | 5.313 pacotes, 4 eventos, 27 emissores preservados |
| 2026-08-28 | validação 187 | 5.313 pacotes, 2 eventos, 5 emissores preservados |
| 2026-08-28 | produção | `epgserver:v1.13.0-20260828` ativa nos dois hosts |

O primeiro ensaio de quatro segundos iniciou entre duas emissões de TDT/TOT e
foi rejeitado corretamente pelo deploy. A produção voltou automaticamente à
v1.12.2. A janela foi ajustada para oito segundos, cobrindo o ciclo completo,
e as duas implantações posteriores foram aprovadas. Rollbacks preservados:
`epg-stream-pre-v1.13.0-20260828-101644` no host 181 e
`epg-stream-pre-v1.13.0-20260828-102020` no host 187.
