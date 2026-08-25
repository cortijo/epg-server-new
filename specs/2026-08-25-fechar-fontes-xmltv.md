# Botão Fechar em Fontes XMLTV

- ID: `2026-08-25-fechar-fontes-xmltv`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-25`
- Última atualização: `2026-08-25`
- Issue/commit relacionado: `afd2e9c`

## 1. Resumo

Adicionar um botão explícito **Fechar** no cabeçalho da janela de Fontes XMLTV para retornar ao painel sem depender de outra ação.

## 2. Contexto e comportamento atual

- Onde: `openSources()` no painel embutido do EPG Stream.
- Como reproduzir: abrir **Fontes XMLTV** no painel.
- Evidência: o cabeçalho possui apenas **+ Nova fonte**.
- Impacto: falta uma saída explícita e intuitiva da janela.

## 3. Objetivos

- [x] Exibir **Fechar** no cabeçalho de Fontes XMLTV.
- [x] Fechar a janela sem salvar, excluir ou testar fontes.

## 4. Fora de escopo

- Alterar API, persistência, fontes cadastradas, multicast ou EPG.
- Modificar os formulários de criação e edição de fonte.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Exibir botão Fechar ao lado de Nova fonte. | obrigatória |
| RF-02 | Acionar `closeModal()` ao clicar. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Não interromper portadoras ou canais. |
| RNF-02 | Não modificar dados persistidos. |

## 7. Critérios de aceite

- [x] CA-01 — Dada a janela de Fontes XMLTV aberta, quando clicar em Fechar, então ela desaparece e o painel permanece disponível.
- [x] CA-02 — O botão Nova fonte e as ações existentes continuam disponíveis.
- [x] CA-03 — Testes e sintaxe JavaScript permanecem aprovados.

## 8. Contratos afetados

- API: não se aplica.
- Configuração e persistência: não se aplica.
- Mídia e rede: não se aplica.

## 9. Desenho técnico

### Antes

```text
Fontes XMLTV -> cabeçalho com Nova fonte
```

### Depois

```text
Fontes XMLTV -> cabeçalho com Nova fonte + Fechar -> closeModal()
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `epg-product/app.py` | botão, versão e HTML embutido |
| `epg-product/tests/test_app.py` | regressão estática do painel |
| documentação EPG | versão e comportamento implantado |

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Quebrar o template JavaScript | baixa | médio | validar sintaxe extraída e HTML servido |

## 11. Plano de implementação

- [x] Mapear `openSources()`.
- [x] Adicionar ação Fechar mínima.
- [x] Atualizar versão, teste e documentação.
- [x] Validar imagem isolada e produção.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Python e testes | local | py_compile + unittest | sem falhas | passou |
| T-02 | JavaScript | local | compilar script extraído | sem erro | passou |
| T-03 | Imagem | isolado | build, health, HTML e navegador | versão e botão corretos | passou |
| T-04 | Produção | produção | health, inspect e logs | saudável | passou |

## 13. Plano de implantação

- Imagem/tag prevista: `tvstream-epg:v1.2.2-20260825`, `epg-v1.2.2`.
- Container de teste: porta HTTP isolada.
- Dados de teste: cópia temporária, sem alterar produção.
- Publicação: promover exatamente a imagem validada.

## 14. Plano de rollback

- Container/imagem anterior: EPG Stream 1.2.1 preservado.
- Condição: falha de health, interface ou inicialização.
- Restauração: parar a 1.2.2 e reativar o container 1.2.1 preservado.

## 15. Observabilidade

- `/health` retorna versão 1.2.2.
- Restart count permanece zero.
- Sem novos erros nos logs.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-25 | início | `openSources()` confirmado sem botão Fechar |
| 2026-08-25 | validação local | 11 testes, py_compile e sintaxe JavaScript aprovados |
| 2026-08-25 | validação isolada | health 1.2.2, HTML autenticado e clique visual aprovados |
| 2026-08-25 | implantação | produção saudável, restart 0 e acesso externo aprovado |

## 17. Resultado final

- Estado final: `concluída`
- Critérios de aceite: `3/3 concluídos`
- Testes executados: 11 testes, py_compile, JavaScript, HTML autenticado e navegador.
- Resultado da produção: saudável, rede host, somente leitura e restart 0.
- Imagem implantada: `tvstream-epg:v1.2.2-20260825` (`sha256:88bb1e537e88ef848acc7c8102620193ca7f9bb4590dc5781ae55155234f30ca`).
- Rollback preservado: container `epg-stream-pre-v1.2.2-20260825` e backup correspondente.
- Commit: `afd2e9c` (implementação).
- Tag: `epg-v1.2.2`.
- Pendências: nenhuma.
