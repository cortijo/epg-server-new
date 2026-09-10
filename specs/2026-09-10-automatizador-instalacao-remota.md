# Spec: Automatizador remoto de instalação do OMNIEPG

- ID: `2026-09-10-automatizador-instalacao-remota`
- Estado: `concluída`
- Responsável: Codex
- Solicitante: Julio Cortijo
- Criada em: 2026-09-10
- Última atualização: 2026-09-10
- Issue/commit relacionado: a preencher

## 1. Resumo

Criar uma imagem Docker independente com painel autenticado capaz de inspecionar
uma máquina Linux por SSH, confirmar sua identidade, instalar Docker conforme a
distribuição e implantar o OMNIEPG com rede host, persistência, licença e rollback.

## 2. Contexto e comportamento atual

- A instalação remota hoje depende de comandos manuais.
- O repositório já possui um instalador local, mas não um controlador via SSH.
- Credenciais digitadas não podem ser persistidas nem aparecer em logs.

## 3. Objetivos

- [x] Detectar distribuição, versão, arquitetura, Docker e recursos do host.
- [x] Instalar Docker em Ubuntu/Debian ou famílias RHEL/Fedora suportadas.
- [x] Compilar e implantar uma tag imutável do OMNIEPG em rede host.
- [x] Preservar dados e container anterior, com rollback automático por health.
- [x] Proteger o painel por usuário e senha e não persistir segredos remotos.

## 4. Fora de escopo

- Alterar firewall, rede, multicast, XMLTV ou configuração de portadoras.
- Administrar Windows, macOS ou distribuições sem apt/dnf.
- Armazenar senhas SSH, sudo, licença, Git ou administrador remoto.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Exibir fingerprint SSH antes da implantação. | obrigatória |
| RF-02 | Exigir a fingerprint confirmada para toda conexão. | obrigatória |
| RF-03 | Detectar SO e Docker sem modificar o host. | obrigatória |
| RF-04 | Instalar Docker e implantar o OMNIEPG com configuração informada. | obrigatória |
| RF-05 | Exibir progresso e resultado sem segredos. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Credenciais existem apenas em memória durante o job. |
| RNF-02 | Entradas são validadas e comandos são gerados pelo sistema. |
| RNF-03 | O painel deve operar atrás de HTTPS em produção. |
| RNF-04 | A imagem não tem acesso ao Docker do próprio host. |

## 7. Critérios de aceite

- [x] CA-01 — Login inválido recebe 401 e nenhum formulário é exposto.
- [x] CA-02 — Fingerprint divergente encerra a conexão antes de executar comandos.
- [x] CA-03 — Detecção identifica uma fixture Ubuntu e seleciona apt.
- [x] CA-04 — O script gerado usa rede host, volumes persistentes, tag imutável e rollback.
- [x] CA-05 — Senhas e chave não aparecem no estado ou log do job.

## 8. Contratos afetados

- Nova aplicação independente na porta configurável, padrão 9300.
- `GET /health`, `POST /api/probe`, `POST /api/install`, `GET /api/jobs/<id>`.
- Nenhuma alteração no schema, API ou transporte do OMNIEPG.

## 9. Desenho técnico

```text
navegador HTTPS -> instalador autenticado -> SSH com fingerprint fixada
 -> detecção Linux -> apt/dnf -> Docker -> clone/ref -> build imutável
 -> container candidato -> health -> container ativo ou rollback
```

Arquivos previstos: `installer-automation/`, documentação raiz e testes.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Host SSH incorreto | média | alto | confirmação obrigatória da fingerprint |
| Vazamento de senha | baixa | alto | memória apenas, redação de logs e TLS obrigatório operacionalmente |
| Falha após parar produção | baixa | alto | candidato, health e rollback automático |
| Comando injetado | baixa | alto | validação estrita e quoting centralizado |

## 11. Plano de implementação

- [x] Mapear instalador e contratos atuais.
- [x] Implementar painel, SSH, detecção, geração e execução segura.
- [x] Criar Dockerfile, compose, documentação e testes.
- [x] Construir e validar imagem isolada.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Python | local | unittest | suíte aprovada | passou |
| T-02 | Container | isolado | docker build | imagem compilada | passou |
| T-03 | Segurança | local | testes de redação/validação | segredo ausente | passou |
| T-04 | Produção | não se aplica | sem deploy remoto real | hosts intactos | não se aplica |

## 13. Plano de implantação

Imagem final: `omniepg-installer:v1.0.1`. O operador deve fornecer segredo
de acesso e publicar a interface somente por HTTPS ou rede administrativa.

## 14. Plano de rollback

Remover somente o container do automatizador. Nos hosts instalados, o processo
mantém o container anterior e restaura-o quando o candidato falhar no health.

## 15. Observabilidade

Jobs possuem etapas, horário e mensagens redigidas. Senhas, tokens e chaves não
são registrados.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-09-10 | início | escopo isolado e modelo de segurança definido |
| 2026-09-10 | testes | 7 testes Python aprovados; py_compile e diff-check aprovados |
| 2026-09-10 | imagem isolada | health 200, acesso sem login 401, autenticado 200, UID 10003 |
| 2026-09-10 | imagem final | `omniepg-installer:v1.0.1`, ID `sha256:99287a58edd9dfe029f85021514b0166c896daa5bbfb60f506e715062682c2e4` |

## 17. Resultado final

- Estado final: concluída
- Critérios de aceite: 5/5 concluídos
- Testes executados: 7 testes Python, py_compile, build Docker e smoke HTTP/Auth
- Imagem implantada: não se aplica; imagem isolada final `omniepg-installer:v1.0.1`
- Rollback preservado: não se aplica; nenhum host remoto foi provisionado
- Commit: a preencher
