# Spec: Importação inicial do EPG Server

- ID: `2026-08-25-importacao-repositorio-epgserver`
- Estado: `publicada/concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-25`
- Última atualização: `2026-08-25`
- Issue/commit relacionado: `26e975d5e398fdb1f95b41a10c93ccbdd75fc777`

## 1. Resumo

Publicar a aplicação independente EPG Stream no repositório vazio
`cortijo/epgserver`, preservando somente código, testes, Docker, auditorias,
documentação e specs relacionados ao produto EPG.

## 2. Objetivos

- [x] Isolar o painel Python e o emissor C++ EPG-only.
- [x] Manter o build Docker reproduzível a partir da raiz.
- [x] Incluir testes e auditorias PSI/SI.
- [x] Incluir documentação para continuidade por outro agente.
- [x] Excluir módulos de streaming, dados e credenciais de produção.

## 3. Fora de escopo

- Alterar a imagem ou o container atualmente implantados.
- Implementar BIT/PID `0x0024` nesta importação.
- Modificar emissão, XMLTV, autenticação ou interface.

## 4. Critérios de aceite

- [x] Testes Python aprovados no novo checkout.
- [x] JavaScript incorporado sem erro de sintaxe.
- [x] Imagem Docker construída usando somente o novo repositório.
- [x] Varredura sem segredos e arquivos operacionais.
- [x] Commit enviado ao branch `main` do novo repositório.

## 5. Riscos e mitigação

- Dependência ausente: build Docker comprova o conjunto mínimo de fontes.
- Vazamento de produção: importar somente arquivos rastreados e executar busca
  por padrões sensíveis antes do commit.
- Documentação divergente: registrar explicitamente a limitação atual da BIT.

## 6. Testes planejados

| ID | Teste | Resultado esperado | Estado |
|---|---|---|---|
| T-01 | `py_compile` | sem erro | passou |
| T-02 | unittest | 12/12 aprovados | passou |
| T-03 | JavaScript `node --check` | sem erro | passou |
| T-04 | Docker build isolado | imagem construída | passou |
| T-05 | segredos e `git diff --check` | sem achados | passou |

## 7. Rollback

Como o remoto está vazio, um eventual problema deve ser corrigido por novo
commit. Não reescrever o histórico depois de publicado.

## 8. Registro de execução

| Data | Ação | Evidência |
|---|---|---|
| 2026-08-25 | repositório remoto verificado | remoto acessível e sem referências |
| 2026-08-25 | importação seletiva | somente aplicação EPG e documentação associada |
| 2026-08-25 | testes locais | py_compile, 12 unittests, JavaScript e diff aprovados |
| 2026-08-25 | build isolado | imagem `epgserver:repo-import-test-20260825` construída somente com este checkout |
| 2026-08-25 | smoke Docker | health 1.5.0, painel autenticado, zero reinícios e produção preservada |
| 2026-08-25 | publicação | commit inicial `26e975d` enviado para `origin/main` |

## 9. Resultado final

- Estado: `publicada e concluída`
- Commit: `26e975d5e398fdb1f95b41a10c93ccbdd75fc777`
- Tag: não aplicável nesta importação
- Pendências: nenhuma.
