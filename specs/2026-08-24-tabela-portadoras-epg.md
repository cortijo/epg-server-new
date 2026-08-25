# Spec: tabela expansível de portadoras EPG

- ID: `2026-08-24-tabela-portadoras-epg`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-24`
- Última atualização: `2026-08-24`
- Issue/commit relacionado: `feaa326`

## 1. Resumo

Substituir os cartões de portadoras por uma tabela compacta. Os botões de ação
devem ficar na coluna direita e a programação deve ser carregada e exibida
somente quando o operador expandir a portadora.

## 2. Contexto e comportamento atual

- Cada portadora ocupa um cartão grande.
- As ações ficam abaixo do conteúdo do cartão.
- `render()` chama `loadNow()`, que consulta `/api/guide` para todas as
  portadoras a cada atualização de 15 segundos.
- A programação permanece sempre visível.

## 3. Objetivos

- [x] Exibir uma linha compacta por portadora.
- [x] Manter ações na coluna lateral direita.
- [x] Consultar e mostrar programação somente ao expandir uma linha.
- [x] Manter acesso à grade completa de cada canal.

## 4. Fora de escopo

- Alterar API, XMLTV, persistência ou emissores multicast.
- Alterar o container TVStream principal.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Tabela mostra portadora, IDs, destino, canais, estado e ações. | obrigatória |
| RF-02 | Botão “Ver programação” expande/recolhe a portadora. | obrigatória |
| RF-03 | `/api/guide` só é solicitado na primeira expansão. | obrigatória |
| RF-04 | Canal expandido permite abrir sua grade detalhada. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Tabela deve continuar utilizável em telas pequenas com rolagem horizontal. |
| RNF-02 | Atualização periódica não deve gerar polling de programação recolhida. |
| RNF-03 | Nenhuma alteração de transporte MPEG-TS. |

## 7. Critérios de aceite

- [x] CA-01 — Portadoras aparecem em tabela e ações ficam na última coluna.
- [x] CA-02 — Programação inicia recolhida e não há request de guia no refresh.
- [x] CA-03 — Clique expande a programação e clique seguinte recolhe.
- [x] CA-04 — Clique em um canal abre a grade completa já existente.

## 8. Contratos afetados

### API

- Não muda; apenas o momento de `GET /api/guide?carrier_id=ID`.

### Configuração e persistência

- Não se aplica.

### Mídia e rede

- Não se aplica.

## 9. Desenho técnico

```text
refresh -> state/sources/session -> tabela sem guide
clique Ver programação -> GET /api/guide -> cache da página -> detalhe expandido
clique no canal -> modal de grade completa
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `epg-product/app.py` | tabela, estilos e expansão sob demanda |
| `epg-product/tests/test_app.py` | regressão do HTML/JavaScript |

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| tabela larga no celular | média | baixo | wrapper com rolagem horizontal |
| refresh fechar detalhes | média | baixo | preservar IDs expandidos e cache no JavaScript |
| guia obsoleto em expansão longa | baixa | baixo | limpar cache ao recolher para recarregar na próxima abertura |

## 11. Plano de implementação

- [x] Mapear renderização e polling atuais.
- [x] Criar tabela e coluna lateral de ações.
- [x] Criar expansão e carregamento lazy.
- [x] Validar HTML, interação e API em instância isolada.
- [x] Implantar imagem imutável com rollback.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | testes Python | local | py_compile + unittest | sem falhas | passou |
| T-02 | tabela | navegador isolado | inspecionar desktop | linhas e ações corretas | passou |
| T-03 | expansão | navegador isolado | clicar duas vezes | abre e recolhe | passou |
| T-04 | lazy load | requests/API | observar antes/depois | guide apenas após clique | passou |
| T-05 | produção | servidor | health/logs/restart | saudável | passou |

## 13. Plano de implantação

- Imagem/tag prevista: `tvstream-epg:v1.2.0-20260824` / `epg-v1.2.0`.
- Testar em porta HTTP alternativa com cópia do volume.
- Preservar container e dados da versão 1.1.1.

## 14. Plano de rollback

- Voltar ao container/imagem `tvstream-epg:v1.1.1-20260824`.
- Restaurar dados somente se necessário; esta mudança não migra schema.
- Confirmar health, login e programação.

## 15. Observabilidade

- Health 1.2.0, restart count zero e ausência de HTTP 5xx.
- Requests `/api/guide` somente após expansão.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-24 | análise | cards e polling automático localizados em `render/loadNow` |
| 2026-08-24 | testes locais | py_compile, nove testes e sintaxe JavaScript aprovados |
| 2026-08-24 | navegador isolado | tabela e ações verificadas; expansão/recolhimento e modal da grade aprovados |
| 2026-08-24 | lazy load | zero guides no refresh e um GET após o primeiro clique |
| 2026-08-24 | produção | health 1.2.0 interno/externo, rede host e restart count 0 |

## 17. Resultado final

- Estado final: `concluído`
- Critérios de aceite: `4/4`
- Testes executados: `9 unitários + sintaxe JS + navegador e requests isolados`
- Resultado da produção: `EPG Stream 1.2.0 saudável`
- Imagem implantada: `tvstream-epg:v1.2.0-20260824` (`sha256:7d5b84b53eef25f1b94cf42997402a876b31f980bbb2edc11b31abb878b8f2e3`)
- Rollback preservado: `epg-stream-pre-v1.2.0-20260824` e backup correspondente
- Commit/tag: `feaa326` / `epg-v1.2.0`
- Pendências: `teste de aceitação pelo operador`
