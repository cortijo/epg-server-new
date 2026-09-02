# Spec: pacote nativo para Ubuntu 24.04+

- ID: `2026-09-02-pacote-nativo-ubuntu`
- Estado: `concluída`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`
- Criada em: `2026-09-02`
- Última atualização: `2026-09-02`
- Issue/commit relacionado: `a preencher`

## 1. Resumo

Distribuir o EPG Stream 1.13.1 como pacote Debian nativo, executado por systemd
sem Docker, para Ubuntu 24.04 ou superior. O Docker existente permanece
inalterado e suportado.

## 2. Contexto e comportamento atual

Hoje o painel Python e o emissor C++ são empacotados somente na imagem Docker.
O Ubuntu já possui todas as bibliotecas de runtime usadas pela imagem.

## 3. Objetivos

- [x] Gerar `.deb` reproduzível para a arquitetura nativa (`amd64`/`arm64`).
- [x] Instalar serviço, usuário restrito, configuração e persistência padrão.
- [x] Oferecer configuração interativa sem guardar a senha inicial em texto.
- [x] Validar instalação e health em Ubuntu 24.04 limpo.

## 4. Fora de escopo

- Não alterar PSI/SI, XMLTV, licenciamento ou painel.
- Não remover nem substituir a distribuição Docker.
- Não alterar firewall automaticamente.
- Não criar repositório APT assinado nesta entrega.

## 5. Requisitos

| ID | Requisito |
|---|---|
| RF-01 | Arquivos da aplicação em `/usr/lib/epg-stream`. |
| RF-02 | Dados em `/var/lib/epg-stream` e configuração em `/etc/epg-stream`. |
| RF-03 | Serviço `epg-stream.service`, usuário/grupo `epgstream` e porta padrão 9100. |
| RF-04 | Configurador solicita licença, instalação e administrador inicial. |
| RF-05 | Senha inicial é removida do arquivo de ambiente após bootstrap saudável. |
| RNF-01 | Upgrade preserva dados, configuração e chave. |
| RNF-02 | Remoção não apaga dados operacionais. |
| RNF-03 | Serviço usa hardening systemd e reinício automático. |

## 6. Critérios de aceite

- [x] CA-01 — O build em Ubuntu 24.04 gera `.deb` instalável.
- [x] CA-02 — Um ambiente limpo contém binário, painel, auditor e unit systemd.
- [x] CA-03 — O painel responde `/health` executado como usuário `epgstream`.
- [x] CA-04 — Testes existentes e testes de empacotamento passam.
- [x] CA-05 — Nenhuma credencial ou chave é incluída no pacote/Git.

## 7. Desenho

```text
dpkg -> /usr/lib/epg-stream/{app.py,license_client.py,TVStreamEpgOnly,auditor}
     -> /etc/epg-stream/epg-stream.env + license.key
     -> /var/lib/epg-stream
     -> systemd epg-stream.service (User=epgstream)
```

## 8. Riscos e mitigação

| Risco | Mitigação |
|---|---|
| ABI varia por distribuição | suporte declarado somente Ubuntu 24.04+ e build no 24.04 |
| segredo ficar no EnvironmentFile | configurador remove senha após primeiro health |
| upgrade apagar dados | scripts nunca removem `/var/lib/epg-stream` |
| firewall bloquear 9100/multicast | documentar; pacote não altera firewall |

## 9. Validação e rollback

- suíte Python/JS e `git diff --check`;
- build em container Ubuntu 24.04;
- inspeção do pacote com `dpkg-deb`;
- instalação e smoke test em container Ubuntu 24.04 limpo;
- rollback: `apt install ./epg-stream_VERSAO_ANTERIOR.deb` ou uso da imagem Docker.

## 10. Registro

| Data/hora | Ação | Resultado |
|---|---|---|
| 2026-09-02 | início | contrato criado antes do código |
| 2026-09-02 | testes locais | 52 testes aprovados; 5 testes Bash ignorados no Windows |
| 2026-09-02 | build Linux | Ubuntu 24.04/amd64; pacote `1.13.1-1` gerado com sucesso |
| 2026-09-02 | instalação limpa | `dpkg` instalado; arquivos, permissões e usuário validados |
| 2026-09-02 | smoke nativo | `/health` respondeu 503 degradado esperado sem licença; processo executado como `epgstream` |
| 2026-09-02 | artefato | 189812 bytes; SHA-256 `afb22bfc3c887e57cd9932d4e4ed5a2c3a4808706bdd75a3cad8a0c4a7ad04f9` |
