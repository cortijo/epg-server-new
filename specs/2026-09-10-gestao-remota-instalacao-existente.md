# Spec: Gestão remota de instalação OMNIEPG existente

- ID: `2026-09-10-gestao-remota-instalacao-existente`
- Estado: `concluída`
- Responsável: Codex
- Solicitante: Julio Cortijo
- Criada em: 2026-09-10
- Última atualização: 2026-09-10
- Issue/commit relacionado: a preencher

## 1. Resumo

Separar o automatizador em visão de nova instalação e visão de servidor já
instalado. A segunda deve inventariar e administrar o OMNIEPG por SSH sem
armazenar credenciais, oferecendo saúde, latência, versão, canais, fontes,
erros, backup/restore, licença e troca controlada de imagem.

## 2. Contexto e comportamento atual

O v1.0.1 instala máquinas novas, mas não oferece manutenção posterior. A gestão
exige acesso manual ao Docker e ao painel do cliente.

## 3. Objetivos

- [x] Alternar claramente entre instalação nova e gestão existente.
- [x] Consultar saúde, latência, imagem, container, canais, fontes e erros.
- [x] Criar/listar/restaurar backups remotos.
- [x] Reiniciar, atualizar ou fazer downgrade preservando rollback.
- [x] Alterar servidor e chave de licença com validação posterior.

## 4. Fora de escopo

- Alterar configuração de portadoras, PIDs ou XMLTV pelo automatizador.
- Capturar ou retransmitir multicast.
- Monitoramento contínuo externo ou alertas push nesta versão.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Inspeção não deve modificar o host. | obrigatória |
| RF-02 | Dados exibidos devem omitir URLs e segredos de fontes/licença. | obrigatória |
| RF-03 | Backup e restore devem usar nomes validados e cópia pré-restore. | obrigatória |
| RF-04 | Upgrade/downgrade deve exigir ref/tag imutável e manter rollback. | obrigatória |
| RF-05 | Mudança de licença deve usar a API local do OMNIEPG e atualizar URL por recriação segura. | obrigatória |

## 6. Requisitos não funcionais

- Credenciais e chaves somente em memória e stdin.
- Nenhum Docker socket local montado.
- Toda ação destrutiva exige confirmação visual.
- Saídas são redigidas antes de entrar no job.

## 7. Critérios de aceite

- [x] Visões nova/existente alternam sem misturar formulários.
- [x] Inspeção retorna latência, health, imagem e diagnóstico sanitizado.
- [x] Scripts de backup, restore e troca de imagem preservam rollback.
- [x] Valores maliciosos de container, backup, ref e imagem são rejeitados.
- [x] Suíte e smoke test autenticado passam.

## 8. Contratos afetados

- `POST /api/manage/inspect`
- `POST /api/manage/action`
- reutilização de `GET /api/jobs/<id>`
- nenhuma mudança na API do OMNIEPG.

## 9. Desenho técnico

```text
painel autenticado -> SSH fingerprint fixada -> docker/health/API loopback
                    -> inventário sanitizado
                    -> job privilegiado -> backup/restart/recreate/rollback
```

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Restore incorreto | baixa | alto | whitelist do arquivo e backup automático antes do restore |
| Imagem incompatível | média | alto | health e rollback automático |
| Exposição de fonte | baixa | médio | somente nome/tipo/uso; URL omitida |
| Senha em argv | baixa | alto | scripts e payloads enviados por stdin |

## 11. Plano de implementação

- [x] Backend de inventário sanitizado.
- [x] Jobs de backup, restore, restart, licença e versão.
- [x] Interface das duas visões.
- [x] Testes e documentação.
- [x] Imagem imutável e smoke test isolado.

## 12. Matriz de validação

| ID | Cenário | Resultado esperado | Estado |
|---|---|---|---|
| T-01 | validação de entradas | injeções recusadas | passou |
| T-02 | scripts operacionais | rollback e backup presentes | passou |
| T-03 | testes Python | suíte aprovada | passou |
| T-04 | imagem Docker | health/auth aprovados | passou |

## 13. Implantação e rollback

Gerar nova tag imutável do automatizador, preservar o container v1.0.1 e
substituí-lo apenas após smoke test. Rollback: parar o novo e iniciar o anterior.

## 14. Registro de execução

| Data/hora | Ação | Evidência |
|---|---|---|
| 2026-09-10 | início | escopo e contratos definidos |
| 2026-09-10 | inventário real | Ubuntu 22.04, imagem 1.22.0, licença válida, 27/51, quatro fontes e cinco erros |
| 2026-09-10 | backup real | arquivo íntegro de 84 MB; OMNIEPG permaneceu saudável |
| 2026-09-10 | promoção | instalador 1.1.2 saudável, auth 401/200 e 27 emissores preservados |
| 2026-09-10 | hotfix de resposta SSH | 1.1.3 validada contra 187.19.16.59; ruído externo ignorado e inventário retornado |

## 15. Resultado final

- Estado: concluída
- Imagem: `omniepg-installer:v1.1.3`
- Image ID: `sha256:0e6785fc4ab49d959c6a7e91fb7af630676db74a93724091c04907a76e78e794`
- Produção: `181.233.106.46`, loopback `127.0.0.1:9300`
- Rollback: `omniepg-installer-pre-v1.1.2-20260910`, imagem 1.0.1
- Commit: a preencher
