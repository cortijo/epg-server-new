# Spec: Gerenciador declarativo de firewall do servidor

- ID: `2026-08-25-gerenciador-firewall-declarativo`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-25`
- Última atualização: `2026-08-25`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Criar um utilitário operacional separado do instalador do EPG Server para
cadastrar redes e portas autorizadas e reaplicar, de forma idempotente, somente
as regras administradas pelo próprio utilitário. A configuração deve ser
legível, validada antes da troca e persistente após reinicialização.

## 2. Contexto e comportamento atual

- O instalador do produto deliberadamente não altera firewall.
- A configuração atual do servidor é operacional e externa ao repositório.
- Alterações manuais repetidas aumentam o risco de divergência e bloqueio SSH.
- O Docker mantém regras próprias que não podem ser apagadas pelo utilitário.

## 3. Objetivos

- [x] Cadastrar/remover redes IPv4/IPv6 e portas TCP/UDP.
- [x] Gerar e aplicar uma tabela nftables exclusiva e idempotente.
- [x] Validar a configuração e proteger a sessão SSH antes da aplicação.
- [x] Instalar persistência no boot sem editar regras pertencentes ao Docker.

## 4. Fora de escopo

- Alterar o instalador `scripts/install.sh`.
- Gerenciar NAT, encaminhamento, multicast de saída ou chains do Docker.
- Aplicar regras no servidor durante esta mudança de código.
- Suportar simultaneamente UFW, firewalld ou backends legados de iptables.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Manter configuração em linhas `NETWORK=`, `TCP_PORT=` e `UDP_PORT=`. | obrigatória |
| RF-02 | Oferecer comandos list/add/remove/check/render/apply/install. | obrigatória |
| RF-03 | Aceitar porta única ou intervalo e CIDR IPv4/IPv6 válido. | obrigatória |
| RF-04 | Aplicar somente a tabela `inet epg_managed`, sem flush global. | obrigatória |
| RF-05 | Recusar apply remoto que não autorize a porta e o IP da sessão SSH. | obrigatória |
| RF-06 | Manter regra de loopback, conexões estabelecidas e ICMP/ICMPv6. | obrigatória |
| RF-07 | Instalar unit systemd para reaplicar a configuração no boot. | obrigatória |

## 6. Requisitos não funcionais

| ID | Requisito |
|---|---|
| RNF-01 | Não interromper emissores, containers ou multicast de saída. |
| RNF-02 | Não imprimir nem persistir credenciais. |
| RNF-03 | Validar com `nft -c` antes da transação real. |
| RNF-04 | Gravar configuração e regras geradas por troca atômica. |

## 7. Critérios de aceite

- [x] CA-01 — Dada uma configuração válida, `render` produz conjuntos IPv4,
  IPv6, TCP e UDP e uma chain INPUT com política explícita.
- [x] CA-02 — Dado CIDR, protocolo ou porta inválidos, o comando termina sem
  alterar a configuração ativa.
- [x] CA-03 — Dada uma sessão SSH, `apply` é recusado se o IP remoto ou a porta
  local da sessão não estiverem autorizados.
- [x] CA-04 — Aplicações repetidas geram a mesma tabela e não executam flush do
  ruleset nem alteram tabelas/chains do Docker.
- [x] CA-05 — O modo de simulação permite validar sem privilégios e sem nftables.

## 8. Contratos afetados

### API

Não se aplica.

### Configuração e persistência

- Arquivo padrão: `/etc/epg-firewall.conf`.
- Regras renderizadas: `/etc/epg-firewall.nft`.
- Executável instalado: `/usr/local/sbin/epg-firewall`.
- Unit: `/etc/systemd/system/epg-firewall.service`.

### Mídia e rede

- Entrada multicast, saída UDP, codecs, SIDs e PIDs: não alterados.
- Escopo: tráfego novo TCP/UDP destinado ao próprio host.

## 9. Desenho técnico

### Antes

```text
alterações manuais -> regras sem fonte declarativa única
```

### Depois

```text
configuração -> validação -> render temporário -> nft -c -> transação nft
                                    └-> tabela inet epg_managed somente
```

### Arquivos previstos

| Arquivo | Alteração |
|---|---|
| `scripts/firewall-manager.sh` | CLI, validação, render, apply e instalação |
| `scripts/firewall.conf.example` | configuração segura de referência |
| `epg-product/tests/test_firewall_manager.py` | testes estáticos e funcionais |
| `DOCUMENTACAO_EPG_PRODUTO.md` | operação e rollback |

### Decisões e alternativas

- Decisão: nftables nativo em tabela dedicada.
- Motivo: atualização transacional e convivência com regras Docker.
- Alternativa rejeitada: `iptables -F` ou `nft flush ruleset`.
- Por que: apagariam regras externas e poderiam interromper serviços.

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Perder SSH | média | alto | verificação de `SSH_CONNECTION`, porta 22 configurável e `--force` explícito somente em console |
| Bloquear serviço necessário | média | alto | `check`, `render`, lista declarativa e rollback documentado |
| Interferir no Docker | baixa | alto | tabela própria, hook INPUT e nenhum flush global/FORWARD |
| Erro no boot | baixa | médio | unit `oneshot`, validação antes do apply e arquivo gerado persistido |

## 11. Plano de implementação

- [x] Mapear regras do repositório e limites do instalador.
- [x] Implementar CLI e formato declarativo.
- [x] Implementar proteção SSH, validação e transação nft.
- [x] Adicionar testes e documentação.
- [x] Validar sem aplicar no servidor.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Procedimento | Resultado esperado | Estado |
|---|---|---|---|---|---|
| T-01 | Sintaxe shell | isolado | `bash -n` | sem erro | passou |
| T-02 | Configuração válida | isolado | `check`, `render` e `nft -c` real | conjuntos e sintaxe corretos | passou |
| T-03 | Entradas inválidas | isolado | unittest | rejeição sem mutação | passou |
| T-04 | Idempotência/escopo | isolado | render duplo e inspeção | igual e sem flush global | passou |
| T-05 | Regressão do produto | local | suíte completa | 30 testes aprovados | passou |

## 13. Plano de implantação

Não haverá aplicação automática. O operador deverá copiar/revisar o exemplo,
executar `check`, manter uma sessão de console disponível e só então usar
`install`/`apply` no servidor desejado.

## 14. Plano de rollback

Executar `epg-firewall disable`, que remove somente `table inet epg_managed`,
e desabilitar a unit. Se o acesso remoto estiver indisponível, executar esses
passos pelo console do provedor.

## 15. Observabilidade

- `epg-firewall status` lista a tabela efetivamente carregada.
- `systemctl status epg-firewall` mostra a aplicação no boot.
- Contadores nftables nas regras de drop mostram tráfego bloqueado.

## 16. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-25 | análise inicial | utilitário separado preserva o contrato do instalador e as chains Docker |
| 2026-08-25 | validação funcional | sete testes específicos aprovaram CRUD, entradas inválidas, dry-run, idempotência e proteção SSH |
| 2026-08-25 | validação nftables | regras passaram em `nft -c` no Linux alvo com nome de tabela isolado; nenhuma regra foi aplicada |
| 2026-08-25 | regressão | `bash -n`, Python compile, 30 unittests e `git diff --check` aprovados |

## 17. Resultado final

- Estado final: `concluída`
- Critérios de aceite: `5/5 concluídos`
- Testes executados: `bash -n`, sete testes do gerenciador, `nft -c` real sem apply e 30 testes totais`
- Resultado da produção: `não aplicado por segurança`
- Imagem implantada: `não se aplica`
- Rollback preservado: `remoção da tabela dedicada`
- Commit: `a registrar após a criação do commit validado`
- Tag: `não se aplica`
- Pull request/URL: `a preencher`
- Pendências: `instalação e aplicação dependem de revisão operacional explícita`
