# Spec: tela Sobre e atualização segura

- ID: `2026-09-02-sobre-atualizacao-segura`
- Estado: `concluído`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-09-02`
- Última atualização: `2026-09-02`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Adicionar ao EPG Stream uma tela Sobre com produto, versão e desenvolvedor e
permitir que administradores consultem e solicitem uma atualização publicada no
repositório oficial. A instalação automática será exclusiva do pacote nativo;
o modo Docker continuará sendo atualizado pelo host.

## 2. Objetivos

- [x] Exibir versão atual e `Developed by Julio Cortijo`.
- [x] Consultar releases oficiais sem expor credenciais.
- [x] Atualizar o pacote nativo mediante confirmação e validação SHA-256.
- [x] Não conceder privilégios de root ao processo web.

## 3. Fora de escopo

- Autoatualizar ou controlar o Docker a partir do contêiner.
- Atualização automática sem ação do administrador.
- Alterar PSI/SI, multicast, XMLTV ou licenciamento.

## 4. Requisitos e aceite

| ID | Requisito/aceite |
|---|---|
| RF-01 | Botão Sobre disponível para usuários autenticados. |
| RF-02 | Somente administrador consulta/solicita atualização. |
| RF-03 | Release, arquitetura, nome do pacote, versão e digest são validados novamente pelo atualizador root. |
| RF-04 | Solicitação usa arquivo atômico em `/var/lib/epg-stream`; serviço web não executa `apt` ou `sudo`. |
| RF-05 | Em Docker, o painel informa o procedimento externo e não tenta substituir o contêiner. |
| CA-01 | Testes Python e JavaScript passam. |
| CA-02 | Pacote Ubuntu contém path/service/atualizador e instala sem regressão. |
| CA-03 | Falha de rede ou release inválida não altera a instalação corrente. |

## 5. Desenho

```text
administrador -> /api/update/check -> GitHub Releases (somente leitura)
administrador -> /api/update/apply -> update-request.json (usuário epgstream)
systemd.path -> epg-stream-updater.service (root)
             -> refaz consulta oficial -> SHA-256 -> dpkg metadata -> apt install
```

## 6. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| supply chain | repositório fixo, HTTPS, digest obrigatório e conferência do `.deb` |
| escalada pelo painel | instalador privilegiado ignora URL/digest enviados pelo painel e consulta novamente a release oficial |
| interrupção | pacote anterior permanece disponível para rollback; instalação somente após download/validação completos |
| Docker parar | modo Docker não executa atualização interna |

## 7. Validação e rollback

- testes unitários do parser de releases, autorização e UI;
- sintaxe Python/JavaScript e `git diff --check`;
- build e instalação em Ubuntu 24.04 isolado;
- rollback com instalação do `.deb` anterior ou retorno à imagem anterior.

## 8. Registro

| Data/hora | Ação | Resultado |
|---|---|---|
| 2026-09-02 | início | contrato criado antes do código |
| 2026-09-02 | validação | testes unitários, sintaxe JS/Python, pacote Ubuntu 24.04 e interface desktop/mobile aprovados |
