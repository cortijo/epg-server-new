# Spec: backup e restauração das configurações

- ID: `2026-09-03-backup-restore-configuracoes`
- Estado: `v1.16.1 em validação`
- Responsável: `Codex`
- Solicitante: `Julio Cortijo`

## Objetivo

Permitir que um administrador baixe e restaure as configurações do EPG Stream
pelo painel, com validação integral, cópia local pré-restauração e reinício
controlado do serviço.

## Escopo

- usuários e hashes de senha;
- fontes XMLTV e tokens internos;
- portadoras, canais e parâmetros de emissão;
- metadados das publicações XMLTV.
- arquivos persistentes de `/data`, incluindo XMLTV normalizados, publicações,
  caches, logotipos, diagnósticos e logs.

Não entram no arquivo: chave de licença e segredo da autoridade, armazenados no
volume separado `/license`, nem os próprios backups locais para evitar inclusão
recursiva. O pacote é sensível e deve ser armazenado com acesso restrito.

## Requisitos

- [x] Download somente para administrador autenticado.
- [x] Restaurar apenas formato, versão e estrutura válidos.
- [x] Exigir ao menos um administrador ativo e validar fontes/portadoras.
- [x] Criar backup local da configuração atual antes da troca.
- [x] Reiniciar o container após responder ao navegador.
- [x] Funcionar mesmo com licença inválida.
- [x] Não alterar chave de licença nem arquivos externos.

## Riscos e rollback

- Credenciais restauradas mudam o login: mostrar aviso explícito.
- A restauração substitui todo o conteúdo persistente de `/data`: validar o
  pacote e criar uma cópia de segurança completa antes da troca.
- Rollback pela cópia local em `/data/config-backups` ou imagem v1.15.4.

## Validação

- round-trip de backup/restauração em diretório temporário;
- rejeição de arquivo inválido e sem administrador ativo;
- autenticação administrativa e interface;
- suíte, Python, JavaScript, Docker candidato e smoke de produção.

## Resultado local

- 71 testes executados com sucesso; 5 testes dependentes do ambiente foram ignorados;
- código Python compilado e JavaScript embarcado validado;
- restauração exercitada somente em diretório temporário para não substituir dados reais;
- o round-trip confirma a restauração dos arquivos XMLTV além da configuração;
- entradas absolutas, travessia `..`, links, dispositivos e pacotes acima dos
  limites de segurança são rejeitados antes de alterar `/data`.

## Produção v1.16.0

- servidor: `181.233.106.46`;
- imagem: `epgserver:v1.16.0-20260903`;
- container: `epg-stream`, saudável, licença válida e zero reinícios;
- 27 processos emissores preservados após o corte;
- backup: `/srv/epg-stream-backup-pre-v1.16.0-20260903-074830`;
- rollback: `epg-stream-pre-v1.16.0-20260903-074830`;
- endpoint de backup confirmou autenticação obrigatória sem alterar dados reais.
