# Spec: título no 0x4D e sinopse exclusiva no 0x4E

- ID: `2026-08-28-sinopse-exclusiva-0x4e`
- Estado: `concluído`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-28`

## Objetivo

No perfil ISDB-TB, manter o `short_event_descriptor` (`0x4D`) sempre presente
com idioma e nome do evento, porém com `text_length=0`. Transportar a sinopse
completa exclusivamente em um ou mais `extended_event_descriptor` (`0x4E`).

## Contrato de transporte

```text
0x4D = idioma + título + text_length 0
0x4E[0..n] = sinopse completa, em ordem por descriptor_number
texto reconstruído = texto_0x4D (vazio) + 0x4E[0] + ... + 0x4E[n]
```

O perfil genérico mantém o comportamento anterior. Categoria `0x54`, faixa
etária `0x55`, tabelas EIT e PIDs não mudam.

## Aceite

- `0x4D` presente mesmo quando não há sinopse;
- título integral dentro do limite atual do `0x4D`;
- texto do `0x4D` vazio no perfil ISDB-TB;
- sinopse curta ou longa integralmente no `0x4E`;
- descritores longos numerados e concatenáveis sem separador artificial;
- CRC, continuidade e demais tabelas preservados;
- captura real validada antes de deploy.

## Rollback

Restaurar a imagem `epgserver:v1.13.0-20260828` preservada nos servidores.

## Evidências

| Data | Etapa | Resultado |
|---|---|---|
| 2026-08-28 | contrato | definido antes da alteração |
| 2026-08-28 | testes locais | 48 aprovados; 5 testes Bash ignorados no Windows |
| 2026-08-28 | host 181 | 4/4 títulos presentes, textos 0x4D vazios e sinopses 0x4E presentes |
| 2026-08-28 | host 187 | 2/2 títulos presentes, textos 0x4D vazios e sinopses 0x4E presentes |
| 2026-08-28 | transporte | 5.313 pacotes por host, CRC zero e nenhuma repetição |
| 2026-08-28 | produção | `epgserver:v1.13.1-20260828`, 27 e 5 emissores preservados |

Rollbacks: `epg-stream-pre-v1.13.1-20260828-104324` no host 181 e
`epg-stream-pre-v1.13.1-20260828-104421` no host 187.
