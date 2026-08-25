# Spec: gerenciamento de usuários do EPG Stream

- ID: `2026-08-24-usuarios-epg-stream`
- Estado: `concluído`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-24`
- Última atualização: `2026-08-24`
- Issue/commit relacionado: `07178a0`

## 1. Resumo

Substituir a credencial única definida somente pelo ambiente por usuários
persistentes e administráveis no painel do EPG Stream. Deve ser possível criar,
editar, trocar senha, ativar/desativar e excluir usuários, com hash forte de
senha, perfis administrador/operador e migração automática do login existente.

## 2. Contexto e comportamento atual

- O EPG Stream 1.0 autentica exclusivamente por `EPG_ADMIN_USER` e
  `EPG_ADMIN_PASSWORD`.
- Não existe tela, API ou persistência de usuários.
- A credencial aparece no ambiente do container e não pode ser trocada sem
  recriar o serviço.

## 3. Objetivos

- [x] Persistir somente hash/salt de senha no volume.
- [x] Migrar automaticamente a credencial atual para o primeiro administrador.
- [x] Permitir criar e editar mais usuários pelo painel.
- [x] Permitir perfis administrador e operador.
- [x] Impedir exclusão, bloqueio ou rebaixamento do último administrador ativo.
- [x] Atualizar e implantar imagem `tvstream-epg:v1.1-20260824` com rollback.

## 4. Fora de escopo

- OAuth, LDAP, SAML, recuperação de senha por e-mail ou MFA.
- Alteração do emissor MPEG-TS, XMLTV, SIDs, PIDs ou multicast.
- Alteração do container TVStream v127.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Administrador lista, cria, edita, ativa/desativa e exclui usuários. | obrigatória |
| RF-02 | Senha é obrigatória ao criar e opcional ao editar. | obrigatória |
| RF-03 | Operador usa fontes/portadoras, mas não administra usuários. | obrigatória |
| RF-04 | O sistema identifica o usuário atual e seu perfil. | obrigatória |
| RF-05 | Alterar a própria senha invalida a senha anterior imediatamente. | obrigatória |
| RF-06 | Migração preserva configurações e o acesso administrativo atual. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | PBKDF2-HMAC-SHA256 com salt aleatório e comparação em tempo constante. |
| RNF-02 | Nenhuma senha em texto claro no JSON, API, log ou Git. |
| RNF-03 | Gravação atômica e compatibilidade com schema v1. |
| RNF-04 | Não interromper emissores EPG existentes durante migração. |

## 7. Critérios de aceite

- [x] CA-01 — Volume schema v1 inicia e recebe um administrador migrado.
- [x] CA-02 — Administrador cria segundo usuário e ele autentica.
- [x] CA-03 — Edição sem senha preserva o hash; com senha invalida a anterior.
- [x] CA-04 — Operador recebe 403 ao consultar ou alterar usuários.
- [x] CA-05 — Último administrador ativo não pode ser removido/desativado/rebaixado.
- [x] CA-06 — Reinício preserva usuários e o EPG Stream/TVStream seguem saudáveis.

## 8. Contratos afetados

### API

- `GET /api/session`: usuário autenticado sem dados sensíveis.
- `GET /api/users`: lista sanitizada, somente administrador.
- `POST /api/users`: cria/edita, somente administrador.
- `POST /api/users/delete`: exclui, somente administrador.
- Falta de autenticação: 401; falta de permissão: 403.

### Persistência

- `schema_version` passa de 1 para 2.
- `users[]`: id, username, display_name, role, enabled, salt,
  password_hash, iterations, created_at e updated_at.
- Migração usa ambiente apenas se `users[]` ainda não existir.

### Mídia e rede

- Não se aplica; nenhuma alteração de transporte.

## 9. Desenho técnico

```text
Basic Auth -> busca username -> PBKDF2 -> usuário/perfil -> autorização da rota
painel Usuários -> API admin -> Store atômico schema v2
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `epg-product/app.py` | hash, migração, autenticação, autorização, API e UI |
| `epg-product/tests/test_app.py` | hash, migração, edição e último admin |
| `epg-product/README.md` | operação de usuários |
| `DOCUMENTACAO_EPG_PRODUTO.md` | modelo, segurança, API e deploy |

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| perder acesso na migração | baixa | alto | backup do volume e bootstrap testado em cópia |
| remover último admin | média | alto | validação transacional no backend |
| senha vazar pela API | baixa | alto | respostas sanitizadas e testes |
| navegador manter Basic Auth | média | baixo | aviso para reautenticar após senha própria |

## 11. Plano de implementação

- [x] Mapear autenticação e persistência atuais.
- [x] Implementar hash e migração.
- [x] Implementar autorização e APIs.
- [x] Implementar painel.
- [x] Testar cópia do volume e imagem paralela.
- [x] Publicar, criar tag/imagem e implantar com rollback.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Resultado esperado | Estado |
|---|---|---|---|---|
| T-01 | hash e senha | unittest | correta aceita, errada rejeita | aprovado |
| T-02 | schema v1 | unittest/container | admin migrado e schema v2 | aprovado |
| T-03 | CRUD/perfis | container paralelo | 200/403/401 corretos | aprovado |
| T-04 | último admin | unittest/API | operação rejeitada | aprovado |
| T-05 | persistência | restart paralelo | segundo usuário permanece | aprovado |
| T-06 | produção | pós-deploy | health, login e EPG saudáveis | aprovado |

## 13. Implantação

- Imagem: `tvstream-epg:v1.1-20260824`.
- Backup: `/srv/epg-stream-backup-pre-v1.1-20260824`.
- Container anterior: `epg-stream-pre-v1.1-20260824`, restart desativado.
- Testar primeiro com cópia do volume e porta HTTP alternativa.

## 14. Rollback

- Parar/remover somente o novo `epg-stream`.
- Restaurar JSON pré-v1.1 se necessário.
- Renomear/iniciar container anterior.
- Confirmar health 1.0 e TVStream v127.

## 15. Observabilidade

- Login bem-sucedido/falha não deve registrar senha.
- Estado do container, restart count, health e erros HTTP.
- Arquivo persistente deve conter `password_hash`, nunca `password`.

## 16. Registro de execução

| Data/hora | Ação | Evidência |
|---|---|---|
| 2026-08-24 | início | schema v1 e credencial única confirmados |
| 2026-08-24 | validação local | 7 testes aprovados e compilação Python válida |
| 2026-08-24 | ensaio isolado | migração, CRUD, RBAC e reinício sem ambiente aprovados em cópia do volume |
| 2026-08-24 | produção | v1.1 ativa, login 200, health externo 200, restart count 0 e TVStream v127 preservado |

## 17. Resultado final

- Estado final: `concluído`
- Critérios: `6/6`
- Testes: `7 unitários + validação HTTP/container paralelo aprovados`
- Imagem: `tvstream-epg:v1.1-20260824` (`sha256:b53313a00681e66261bcba608f9e4616fed7e67ec3655098e732ed7bac593163`)
- Commit/tag: `07178a0` / `epg-v1.1.0`
- Rollback: `epg-stream-pre-v1.1-20260824` e `/srv/epg-stream-backup-pre-v1.1-20260824`
