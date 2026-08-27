# Gestão visual da chave de licença

- ID: `2026-08-26-gestao-visual-chave-licenca`
- Estado: `implantada`
- Responsável: Codex
- Solicitante: Julio Cortijo
- Criada em: 2026-08-26

## Problema

O servidor apresenta a chave em um `alert()` somente na criação e persiste
apenas seu hash. O texto do popup não é copiável de forma confiável e não há
como consultar novamente a chave. No painel EPG, a chave é instalada somente
por volume Docker, sem fluxo visual para o administrador.

## Objetivos

- [x] Substituir o popup por modal com chave, botão **Copiar** e aviso claro.
- [x] Permitir **Ver chave** posteriormente sem persistir a chave em texto puro.
- [x] Permitir rotacionar licenças legadas para o novo formato recuperável.
- [x] Adicionar ao painel EPG um módulo administrativo para validar e salvar a
  chave em volume dedicado.
- [x] Migrar a licença atual de forma coordenada, preservando os 27 emissores.

## Desenho de segurança

- O servidor recebe um segredo mestre de 32 bytes por arquivo montado fora do
  volume de dados.
- Chaves v2 são derivadas com HMAC-SHA256 do segredo mestre e do ID imutável da
  licença. O JSON continua armazenando apenas SHA-256, prefixo e versão.
- `POST /api/licenses/key` retorna a chave somente a administrador autenticado
  e com `Cache-Control: no-store`.
- Licenças legadas não podem ser recuperadas. `POST /api/licenses/rotate`
  converte a licença para v2 e devolve a nova chave uma vez.
- O painel EPG valida a chave no servidor antes de substituir o arquivo atual.
  Falha de validação não altera a licença em uso.
- Apenas o perfil `admin` pode salvar a chave; a API e o estado nunca devolvem
  a chave instalada.

## Contratos

### EPG License Server

- `POST /api/licenses/key` com `{ "id": "..." }` — revelar chave v2;
- `POST /api/licenses/rotate` com `{ "id": "..." }` — rotacionar legado/v2;
- `LICENSE_MASTER_KEY_FILE` — arquivo obrigatório com ao menos 32 bytes.

### EPG Stream

- `POST /api/license/key` com `{ "key": "EPG-..." }` — validar e instalar;
- `GET /api/license` — estado público, sem chave;
- `EPG_LICENSE_KEY_FILE` deve apontar para volume gravável pelo UID 10001.

## Critérios de aceite

- [x] Chave criada aparece em modal e é copiável.
- [x] **Ver chave** retorna a mesma chave e nenhum JSON persiste texto puro.
- [x] Rotação invalida a chave antiga e valida a nova.
- [x] Chave inválida no EPG é recusada sem substituir o arquivo atual.
- [x] Chave válida pode ser instalada pelo painel e libera a capacidade correta.
- [x] Operador não visualiza o módulo nem pode chamar a mutação.
- [ ] Desktop e viewport móvel não apresentam overflow ou popup nativo.
- [x] Produção termina com 27 emissores, licença válida e zero reinícios.

## Versões previstas

- EPG Stream: `v1.11.0`;
- EPG License Server: `v1.1.0`;
- tag Git: `epg-v1.11.0`.

## Rollback

- preservar container/imagem v1.10.0 e servidor de licenças v1.0.0;
- preservar volumes `epg-license-data`, `epg-license-client-secret` e backup;
- não remover o segredo mestre após rollback, pois será necessário para voltar
  à versão v2 da chave.

## Registro de execução

- Testes locais: 41 testes EPG aprovados e 3 testes do servidor de licenças
  aprovados; `py_compile`, JavaScript incorporado e `git diff --check` sem
  falhas funcionais.
- Teste visual do servidor: modal **Ver chave**, seleção, fallback HTTP e
  **Copiar chave** conferidos; a área de transferência recebeu a chave exata.
- Candidato no host operacional: EPG em `127.0.0.1:19110` e autoridade em
  `127.0.0.1:19200`, com dados/segredos clonados e `auto_start=false`.
- Rotação legada, instalação válida, rejeição atômica da chave inválida,
  ausência de texto puro, modo `0600` e persistência após restart aprovados.
- Produção permaneceu em v1.10.0/1.0.0, com 27 emissores e zero reinícios.
- Deploy autorizado e executado em 27/08/2026 às 07:37. Produção passou para
  `epgserver:v1.11.0-20260826` e `epg-license-server:v1.1.0-20260826`.
- Resultado: 27 emissores, zero reinícios, licença v2 válida em 63/80, UI EPG e
  **Ver chave** presentes, zero erros fatais/tracebacks nos logs recentes.
- O limite 80 já existia no backup `epg-license-data-backup-pre-v1.1.0-20260827-073708`;
  não foi alterado pela migração.
- Rollback: `epg-stream-pre-v1.11.0-20260827-073708` e
  `epg-license-server-pre-v1.1.0-20260827-073708`.
- Backups: `/srv/epg-stream-backup-pre-v1.11.0-20260827-073708`,
  `epg-license-data-backup-pre-v1.1.0-20260827-073708` e
  `epg-license-client-backup-pre-v1.11.0-20260827-073708`.
- Pendências: teste visual móvel, commit/push e tag `epg-v1.11.0`.
