# Spec: Firewall por listas em /opt

- ID: `2026-08-27-firewall-listas-opt`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-27`
- Última atualização: `2026-08-27`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Versionar o perfil nftables implantado no novo servidor EPG, baseado em listas
simples de redes e portas em `/opt`, incluindo instalação, proteção SSH,
validação, persistência e rollback.

## 2. Escopo e objetivos

- [x] Versionar script, unit e modelos sem informações sensíveis.
- [x] Documentar inclusão de rede, diagnóstico e recuperação.
- [x] Preservar Docker, conexões estabelecidas e multicast de saída.
- O perfil avançado `scripts/firewall-manager.sh` não será substituído.
- Produto EPG, containers, PIDs e transporte não serão alterados.

## 3. Requisitos e critérios de aceite

| ID | Requisito/critério | Estado |
|---|---|---|
| RF-01 | Um CIDR por linha em `/opt/redes-liberadas`. | concluído |
| RF-02 | Uma porta TCP por linha em `/opt/portas-liberadas`. | concluído |
| RF-03 | Validar entradas e `nft -c` antes da transação. | concluído |
| RF-04 | Preservar cliente e porta da sessão SSH. | concluído |
| RF-05 | Persistir por systemd e oferecer rollback restrito à própria tabela. | concluído |

## 4. Desenho, riscos e rollback

```text
listas /opt -> validação Python/shell -> nft -c -> table inet epg_firewall
```

O risco principal é perda de SSH. A mitigação combina checagem da sessão,
conexões estabelecidas, console de contingência e rollback que remove somente
`inet epg_firewall`. O script não executa `flush ruleset` nem toca NAT/FORWARD.

## 5. Matriz de validação

| ID | Cenário | Resultado esperado | Estado |
|---|---|---|---|
| T-01 | `bash -n` | sintaxe válida | passou |
| T-02 | suíte do firewall | testes aprovados | passou |
| T-03 | `git diff --check` e auditoria de segredos | aprovação | passou |
| T-04 | produção | restart, health e multicast saudáveis | passou |

## 6. Registro de execução

| Data | Ação | Evidência |
|---|---|---|
| 2026-08-27 | aplicação no novo servidor | serviço ativo, health 200 e multicast preservado |

## 7. Resultado final

- Estado final: `concluída`.
- Testes: sintaxe Bash no Linux, sete testes do gerenciador, `nft -c`,
  `git diff --check` e auditoria de segredos.
- Commit: commit desta entrega.
- Pendências: nenhuma.
