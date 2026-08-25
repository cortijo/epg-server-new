# Regras de desenvolvimento do EPG Server

Estas regras valem para todo o repositório.

## Leitura obrigatória

Antes de alterar o projeto, leia integralmente:

1. `AGENTS.md`;
2. `GUIA_OPERACIONAL_AGENTES.md`;
3. `DOCUMENTACAO_EPG_PRODUTO.md`;
4. `specs/README.md` e `specs/TEMPLATE.md`;
5. a especificação e o código relacionados à mudança.

## Fluxo obrigatório

1. Preserve alterações existentes e nunca descarte trabalho do operador.
2. Crie ou atualize uma spec para toda mudança não trivial.
3. Registre objetivo, fora de escopo, riscos, testes e rollback antes do código.
4. Não adicione recepção, transcodificação ou retransmissão de áudio/vídeo.
5. Valide Python, JavaScript e C++ proporcionalmente ao impacto.
6. Mudanças PSI/SI exigem captura e auditoria do TS real em ambiente isolado.
7. Mudanças visuais exigem teste desktop e móvel no navegador.
8. Nunca publique senhas, tokens, cookies, URLs privadas, dados ou backups.
9. Só crie commit quando todos os testes previstos estiverem aprovados.
10. Use commits descritivos e envie para `origin/main` em
    `https://github.com/cortijo/epgserver.git`.
11. Depois de implantar, preserve imagem/container anterior e backup dos dados.
12. Atualize a spec com commit, tag, imagem, health, evidências e rollback.

Não use `latest` para produção e não sobrescreva tags de imagem ou Git.
