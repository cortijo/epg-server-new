# Spec: impedir fechamento acidental do cadastro de usuário

- ID: `2026-08-24-modal-cadastro-epg`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-24`
- Última atualização: `2026-08-24`
- Issue/commit relacionado: `90d63b6`

## 1. Resumo

O formulário de cadastro/edição de usuário fecha ao clicar no fundo escuro.
O painel deve preservar os dados digitados e fechar esse formulário somente por
uma ação explícita de salvar ou cancelar.

## 2. Contexto e comportamento atual

- O helper JavaScript `modal()` fecha qualquer modal quando o alvo do clique é
  o backdrop.
- Um clique acidental fora do formulário perde os valores ainda não salvos.
- O formulário de usuário oferece “Voltar”, em vez de cancelamento explícito.

## 3. Objetivos

- [x] Ignorar cliques no backdrop de todos os modais do painel EPG.
- [x] Fechar o formulário de usuário após salvar com sucesso ou cancelar.
- [x] Preservar validações e demais funções do painel.

## 4. Fora de escopo

- Alterar autenticação, usuários persistidos, XMLTV ou transporte multicast.
- Alterar o container `tvstreamer5`.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Clique fora do modal não deve fechá-lo. | obrigatória |
| RF-02 | Cancelar fecha sem salvar. | obrigatória |
| RF-03 | Salvar fecha somente após resposta bem-sucedida. | obrigatória |
| RF-04 | Erro de validação mantém o formulário aberto. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Não alterar API ou persistência. |
| RNF-02 | Manter compatibilidade móvel e desktop. |
| RNF-03 | Implantação com imagem imutável e rollback. |

## 7. Critérios de aceite

- [x] CA-01 — Ao clicar no backdrop, o formulário permanece aberto e preenchido.
- [x] CA-02 — Ao cancelar, o formulário fecha sem request de gravação.
- [x] CA-03 — Ao salvar com sucesso, o formulário fecha e o painel atualiza.
- [x] CA-04 — Ao ocorrer erro, o formulário permanece aberto e mostra o erro.

## 8. Contratos afetados

### API

- Nenhuma alteração nos endpoints ou formatos JSON.

### Configuração e persistência

- Não se aplica.

### Mídia e rede

- Não se aplica; nenhuma alteração MPEG-TS ou multicast.

## 9. Desenho técnico

### Antes

```text
clique no backdrop -> closeModal() -> valores digitados perdidos
```

### Depois

```text
clique no backdrop -> nenhuma ação
Salvar bem-sucedido ou Cancelar -> closeModal()
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `epg-product/app.py` | comportamento do modal e formulário de usuário |
| `epg-product/tests/test_app.py` | regressão estática do HTML/JavaScript |

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| modal sem forma de fechar | baixa | médio | manter botões explícitos em todas as telas |
| formulário fechar após erro | baixa | médio | chamar `closeModal()` somente após await bem-sucedido |

## 11. Plano de implementação

- [x] Mapear o helper e os botões atuais.
- [x] Remover fechamento pelo backdrop.
- [x] Ajustar salvar/cancelar do usuário.
- [x] Adicionar teste de regressão.
- [x] Validar imagem isolada e implantar.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | suíte Python | local | unittest + py_compile | sem falhas | passou |
| T-02 | backdrop | HTML servido | clicar fora com campos preenchidos | permanece aberto | passou |
| T-03 | salvar/cancelar | HTML servido | executar ambas as ações | fechamento explícito | passou |
| T-04 | produção | servidor | health, versão e logs | saudável | passou |

## 13. Plano de implantação

- Imagem/tag prevista: `tvstream-epg:v1.1.1-20260824` / `epg-v1.1.1`.
- Container isolado em porta alternativa com cópia do volume.
- Preservar imagem/container atual antes da troca.

## 14. Plano de rollback

- Container atual: `epg-stream` com imagem `tvstream-epg:v1.1-20260824`.
- Se painel, autenticação ou health falhar, retornar o container preservado.
- Confirmar `/health` e login após restauração.

## 15. Observabilidade

- `/health` deve informar 1.1.1.
- Restart count deve permanecer zero.
- Logs HTTP sem erros 5xx recorrentes.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-24 | análise | fechamento localizado no `onclick` do backdrop |
| 2026-08-24 | validação local | py_compile e 8 testes unitários aprovados |
| 2026-08-24 | imagem isolada | health 1.1.1 e quatro asserções do modal aprovadas na porta 19101 |
| 2026-08-24 | produção | health interno/externo 200, API protegida 401, logs funcionais e restart count 0 |

## 17. Resultado final

- Estado final: `concluído`
- Critérios de aceite: `4/4 concluídos`
- Testes executados: `8 unitários + imagem isolada`
- Resultado da produção: `EPG Stream 1.1.1 saudável em rede host`
- Imagem implantada: `tvstream-epg:v1.1.1-20260824` (`sha256:f34d74448cc533f8f24ade4a159efb488920f3519f14fd748ce35f402ded690c`)
- Rollback preservado: `epg-stream-pre-v1.1.1-20260824` e backup correspondente
- Commit: `90d63b6`
- Tag: `epg-v1.1.1`
- Pendências: `teste de aceitação pelo operador`
