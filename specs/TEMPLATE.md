# Spec: TÍTULO CURTO E OBJETIVO

- ID: `AAAA-MM-DD-slug`
- Estado: `rascunho`
- Responsável: `NOME OU AGENTE`
- Solicitante: `NOME`
- Criada em: `AAAA-MM-DD`
- Última atualização: `AAAA-MM-DD`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Explique em um parágrafo o problema e o resultado esperado para o usuário.

## 2. Contexto e comportamento atual

- Onde o comportamento existe hoje:
- Como reproduzir:
- Evidência observada:
- Impacto operacional:

## 3. Objetivos

- [ ] Objetivo mensurável 1.
- [ ] Objetivo mensurável 2.

## 4. Fora de escopo

- O que esta mudança não fará.
- Componentes que não devem ser alterados.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Descrever comportamento observável. | obrigatória |
| RF-02 | Descrever outro comportamento. | desejável |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Não interromper canais não relacionados. |
| RNF-02 | Não expor credenciais nem dados sensíveis. |
| RNF-03 | Manter compatibilidade com o modulador ISDB-T. |

## 7. Critérios de aceite

Use formato Dado/Quando/Então e resultados verificáveis.

- [ ] CA-01 — Dado ..., quando ..., então ...
- [ ] CA-02 — Dado ..., quando ..., então ...
- [ ] CA-03 — Não ocorre regressão em ...

## 8. Contratos afetados

### API

- Endpoint/método:
- Request antes/depois:
- Response antes/depois:
- Códigos de erro:

### Configuração e persistência

- Arquivo/campo:
- Valor padrão:
- Migração e compatibilidade:

### Mídia e rede

- Entrada:
- Saída:
- Codec/container:
- SID/PIDs/PCR/bitrate:
- Interface/porta/multicast:

Marque `não se aplica` onde necessário.

## 9. Desenho técnico

### Antes

```text
componente -> comportamento atual -> saída atual
```

### Depois

```text
componente -> comportamento novo -> saída esperada
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `src/...` | descrição |

### Decisões e alternativas

- Decisão:
- Motivo:
- Alternativa rejeitada:
- Por que foi rejeitada:

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Descrever risco | baixa/média/alta | baixo/médio/alto | ação concreta |

## 11. Plano de implementação

- [ ] Mapear o caminho atual no código.
- [ ] Implementar a alteração mínima.
- [ ] Atualizar painel/API/configuração, se aplicável.
- [ ] Atualizar documentação.
- [ ] Preparar ambiente isolado.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Build | isolado | build CMake/Docker | compila sem erro | pendente |
| T-02 | Fluxo principal | isolado | descrever | resultado | pendente |
| T-03 | Regressão | isolado | descrever | resultado | pendente |
| T-04 | Produção | produção | smoke test | saudável | pendente |

Estados permitidos: `pendente`, `passou`, `falhou`, `não se aplica`.

## 13. Plano de implantação

- Imagem/tag prevista:
- Container de teste:
- Dados de teste:
- Sequência de publicação:
- Verificações pós-publicação:

## 14. Plano de rollback

- Container/imagem anterior:
- Condição que aciona rollback:
- Comandos ou procedimento:
- Como confirmar a restauração:

## 15. Observabilidade

- Logs esperados:
- Métricas/estado esperados:
- Alertas ou sintomas de regressão:

## 16. Registro de execução

Preencher durante o trabalho, sem reescrever o plano original.

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| AAAA-MM-DD HH:MM | início | contexto confirmado |

## 17. Resultado final

- Estado final: `a preencher`
- Critérios de aceite: `0/N concluídos`
- Testes executados:
- Resultado da produção:
- Imagem implantada:
- Rollback preservado:
- Commit:
- Tag:
- Pull request/URL:
- Pendências:
