# Spec: bloqueio integral por licença e reinício global

- ID: `2026-08-27-bloqueio-licenca-reinicio-global`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-08-27`
- Última atualização: `2026-08-27`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Quando a licença estiver ausente, revogada, expirada ou indisponível, o EPG
Stream deve parar todos os emissores, recusar operações de XMLTV/portadoras no
backend e bloquear os respectivos controles do painel. Usuários e instalação
de licença permanecem acessíveis. Após uma licença válida, os emissores que
deveriam estar ativos retomam automaticamente. O painel também terá uma ação
administrativa para reiniciar todos os emissores de uma vez.

## 2. Contexto e comportamento atual

- O supervisor consulta a licença e tenta parar processos em estado inválido.
- O painel desabilita somente a criação de portadora.
- As APIs de fontes, publicações, grade e gestão continuam acessíveis.
- Não existe ação de reinício global.
- Impacto: uma licença inválida não produz o bloqueio operacional e visual
  completo esperado pelo produto.

## 3. Objetivos

- [ ] Bloquear processos, APIs e controles de gestão quando a licença for inválida.
- [ ] Retomar automaticamente processos previamente elegíveis após revalidação.
- [ ] Permitir reiniciar todos os emissores por uma única ação confirmada.

## 4. Fora de escopo

- Alterar o formato das chaves ou o servidor de licenças.
- Alterar PIDs, TSID, ONID, XMLTV ou o transporte multicast.
- Remover a janela configurável de cache da licença.

## 5. Requisitos funcionais

| ID | Requisito | Prioridade |
|---|---|---|
| RF-01 | Licença inválida para todos os emissores e impede reinício automático. | obrigatória |
| RF-02 | Backend recusa gestão de fontes, publicações, grade e portadoras com HTTP 402. | obrigatória |
| RF-03 | Painel mantém somente Usuários e Licença operáveis e exibe alerta vermelho. | obrigatória |
| RF-04 | Licença válida faz o supervisor retomar emissores elegíveis. | obrigatória |
| RF-05 | Administrador pode reiniciar todos os emissores em uma ação. | obrigatória |

## 6. Requisitos não funcionais

- O backend é a autoridade; o bloqueio não depende apenas de JavaScript.
- Chaves e credenciais não aparecem em resposta ou log.
- Nenhuma alteração em PSI/SI ou mídia.

## 7. Critérios de aceite

- [x] Licença inválida encerra todos os processos até o próximo ciclo de validação.
- [x] APIs bloqueadas retornam HTTP 402 e mensagem de licença inválida.
- [x] O painel exibe `Licença inválida, entre em contato com o suporte` em vermelho.
- [x] Apenas Usuários e Licença permanecem habilitados durante o bloqueio.
- [x] Uma licença novamente válida retoma automaticamente os fluxos elegíveis.
- [x] Reiniciar todos confirma a ação e reinicia os fluxos elegíveis sem alterar dados.

## 8. Contratos afetados

- Novo endpoint: `POST /api/carriers/restart-all`, somente administrador e licença válida.
- Rotas de gestão/consulta operacional passam a exigir licença válida.
- Persistência: não se aplica.
- Mídia: parâmetros e transporte permanecem inalterados.

## 9. Desenho técnico

```text
LicenseManager -> Supervisor -> stop/resume de emissores
               -> Handler -> HTTP 402 nas rotas bloqueadas
               -> UI -> alerta e disabled nos controles
Administrador -> restart-all -> Supervisor reinicia fluxos elegíveis
```

## 10. Riscos e mitigação

| Risco | Probabilidade | Impacto | Mitigação |
|---|---|---|---|
| Bloquear diagnóstico necessário | média | médio | manter estado, usuários e licença acessíveis |
| Retomar fluxo parado manualmente | baixa | alto | preservar `manual_stop` durante bloqueio |
| Reinício parcial | média | médio | retornar contagem e erros por portadora |

## 11. Plano de implementação

- [x] Centralizar autorização de licença no handler.
- [x] Implementar `restart_all` no supervisor e endpoint administrativo.
- [x] Bloquear controles e exibir alerta na interface.
- [x] Cobrir bloqueio, retomada e UI com testes.
- [x] Atualizar documentação e versão.

## 12. Matriz de validação

| ID | Cenário | Ambiente | Resultado esperado | Estado |
|---|---|---|---|---|
| T-01 | unittest Python | local | suíte aprovada | passou |
| T-02 | sintaxe JS embutido | local | sem erro | passou |
| T-03 | licença inválida/válida | container isolado | stop e retomada | passou |
| T-04 | restart-all | container isolado | processos reiniciados | passou |
| T-05 | produção | servidor | health, UI e emissores saudáveis | passou |

## 13. Implantação e rollback

- Gerar imagem imutável posterior à v1.11.0 e candidato isolado sem multicast.
- Preservar container, imagem e dados atuais antes do corte.
- Rollback: parar a nova imagem e reativar o container v1.11.0 preservado.

## 14. Registro de execução

| Data/hora | Ação/decisão | Resultado/evidência |
|---|---|---|
| 2026-08-27 | diagnóstico inicial | supervisor fail-closed existente; API/UI incompletas |
| 2026-08-27 | testes locais | 44 testes EPG aprovados, 5 skips Bash; 3 testes da autoridade aprovados |
| 2026-08-27 | API inválida | estado/usuários/licença HTTP 200; fontes/publicações/grade/restart-all HTTP 402 |
| 2026-08-27 | painel inválido | alerta e cinco grupos de gestão desabilitados em desktop e 390 px |
| 2026-08-27 08:16 | candidato | licença válida e inválida testadas em loopback; zero multicast |
| 2026-08-27 08:16 | produção | v1.12.0, 27/27 emissores, licença 63/80, zero reinícios |

## 15. Resultado final

- Estado final: `concluída`
- Critérios de aceite: `6/6 concluídos`
- Testes: 44 EPG aprovados, 5 skips Bash; 3 autoridade; JS e API real aprovados.
- Produção: `epgserver:v1.12.0-20260827`, image ID
  `sha256:32c5800fb944308b0cdb0c0cfd890482aae4fe3e70e67f818935b8f368ffc247`.
- Rollback: `epg-stream-pre-v1.12.0-20260827-081608` e backup
  `/srv/epg-stream-backup-pre-v1.12.0-20260827-081608`.
- Commit de implementação: `e7896bc`.
- Tag: `epg-v1.12.0`.
