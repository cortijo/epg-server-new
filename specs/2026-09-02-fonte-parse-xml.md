# Spec: fonte Parse-XML normalizada

- ID: `2026-09-02-fonte-parse-xml`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-09-02`
- Última atualização: `2026-09-02`
- Commit de implementação: `b4001f5`

## 1. Resumo

Adicionar um tipo explícito de fonte `Parse-XML` para provedores que entregam
programas sem declarações de canais, timezone ou com eventos inválidos. O EPG
Stream baixa, normaliza, valida e fornece ao emissor uma URL interna estável,
sem alterar fontes XMLTV padrão existentes.

## 2. Contexto e comportamento atual

- A fonte NEXCABO analisada possui 47 IDs referenciados e zero `<channel>`.
- Há 196 eventos sem duração e datas sem timezone.
- O parser direto mostra zero canais; a validação normalizada compara uma grade
  integral com a janela operacional e rejeita o resultado.

## 3. Objetivos

- [x] Permitir escolher `XMLTV padrão` ou `Parse-XML` por fonte.
- [x] Sintetizar canais, aplicar `-0300` e remover eventos inválidos.
- [x] Entregar ao emissor uma URL interna estável e normalizada.
- [x] Manter a última cópia válida quando a origem falhar.

## 4. Fora de escopo

- Alterar PIDs, EIT, TSID, ONID, multicast ou C++.
- Converter formatos que não tenham raiz XMLTV `<tv>`.
- Modificar automaticamente fontes existentes.

## 5. Requisitos e critérios de aceite

| ID | Requisito/aceite |
|---|---|
| RF-01 | Fontes antigas migram implicitamente como `xmltv`. |
| RF-02 | `parse_xml` só publica conteúdo depois da normalização completa. |
| RF-03 | O token da URL intermediária não aparece em `/api/state` nem `/api/sources`. |
| CA-01 | A amostra NEXCABO resulta em 47 canais e programas válidos. |
| CA-02 | O emissor recebe URL local estável; fontes normais preservam a URL original. |
| CA-03 | Testes Python/JS, Docker e interface desktop/móvel passam. |

## 6. Desenho técnico

```text
URL do provedor -> download limitado -> normalizador Parse-XML -> cache válido
                                                        |
                                                        v
                     /parsed-xml/TOKEN.xml <- emissor C++ a cada 3 horas
```

O cache mantém o último documento válido. Uma falha na renovação não substitui
o conteúdo anterior. O endpoint usa token aleatório e não aparece nas APIs.

## 7. Riscos e rollback

| Risco | Mitigação |
|---|---|
| origem inválida interromper EPG | só substituir cache após validação |
| exposição da URL original/token | remover ambos das APIs de estado/listagem |
| regressão de fonte normal | tipo padrão `xmltv` e testes separados |

Rollback: voltar à imagem v1.14.0 preservando o volume; campos novos são
ignorados pela versão anterior.

## 8. Validação

- suíte unitária e sintaxe Python/JavaScript;
- fixture sem `<channel>`, timezone e com evento inválido;
- URL interna servida em candidato Docker isolado;
- teste visual desktop e móvel;
- health e emissores preservados após implantação.

## 9. Registro

| Data/hora | Ação | Resultado |
|---|---|---|
| 2026-09-02 | diagnóstico | fonte real: 0 canais declarados, 47 referenciados, 196 eventos inválidos |
| 2026-09-02 | testes | 62 testes aprovados; sintaxe Python/JS aprovada |
| 2026-09-02 | fonte real isolada | HTTP 200; 47 canais, 40.909 programas, cache persistido |
| 2026-09-02 | interface | formulário Parse-XML aprovado em desktop e 390x844 |
| 2026-09-02 | implantação | `epgserver:v1.14.1-20260902` saudável; licença válida; 27 emissores; zero reinícios |
| 2026-09-02 | rollback | v1.14.0 preservada parada como `epg-stream-v1140-rollback-20260902` |
