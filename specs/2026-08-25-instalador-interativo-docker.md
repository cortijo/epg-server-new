# Instalador interativo Docker

## Estado

`validada`

## Objetivo

Entregar um script único que prepare o host Linux, solicite as configurações
necessárias, compile a imagem Docker, inicialize ou atualize o EPG Server e
valide a saúde da aplicação com o mínimo de intervenção manual.

## Escopo

- instalação interativa em Linux com Bash;
- verificação do Docker e instalação opcional em Debian/Ubuntu;
- coleta e validação de porta, diretório persistente, container, imagem, fuso e
  credenciais do primeiro administrador;
- build local com tag imutável, sem uso de `latest`;
- execução em rede `host`, usuário sem privilégios, filesystem somente leitura,
  capabilities removidas e política de reinício;
- credenciais iniciais em arquivo temporário protegido, removido após uso;
- segunda inicialização sem credenciais no ambiente persistente do container;
- health check automático;
- atualização com backup e container anterior preservado para rollback;
- documentação do procedimento.

## Fora de escopo

- alterar firewall, roteamento, interfaces ou regras multicast;
- apagar automaticamente backups ou containers de rollback;
- publicar imagens em registry;
- modificar um container de produção durante os testes deste requisito.

## Requisitos funcionais

1. O operador executa `sudo ./scripts/install.sh` na raiz do repositório.
2. O script oferece valores padrão seguros e recusa entradas inválidas.
3. Em volume vazio, usuário e senha iniciais são obrigatórios; a senha não pode
   aparecer no terminal, no histórico, em arquivo permanente ou no container
   final.
4. Em volume já inicializado, os usuários existentes são preservados.
5. A imagem é construída antes de interromper um container existente.
6. O sucesso depende de resposta válida em `/health` dentro do container.
7. Em atualização malsucedida, o container anterior volta a usar o nome
   original e é reiniciado.
8. O instalador informa a porta TCP que o operador deve autorizar no firewall,
   sem alterar regras automaticamente.

## Critérios de aceite

- `bash -n scripts/install.sh` termina sem erro;
- os testes automatizados do produto continuam aprovados;
- uma instalação limpa isolada cria o container e responde no health check;
- o container final não contém `EPG_ADMIN_PASSWORD` nem
  `EPG_ADMIN_USER` em sua configuração;
- uma segunda execução atualiza a imagem, preserva os dados e mantém backup e
  container anterior;
- nenhuma credencial real é adicionada ao Git;
- documentação descreve instalação, atualização e rollback.

## Riscos e contenções

- **porta ocupada:** detectar antes de iniciar quando não houver container com o
  mesmo nome;
- **diretório perigoso:** aceitar somente caminho absoluto diferente de `/`;
- **falha após parar a versão anterior:** renomear e reiniciar automaticamente o
  container preservado;
- **credencial exposta:** usar `read -s`, `mktemp`, permissão `0600` e remover o
  arquivo por `trap`;
- **build quebrado:** construir antes de qualquer troca;
- **mudança de firewall:** não automatizar e apenas informar a necessidade.

## Validação planejada

1. análise sintática Bash;
2. suíte `unittest` e compilação Python;
3. busca por segredos e revisão do diff;
4. instalação e atualização completas em nomes, porta e volume exclusivos;
5. inspeção das proteções e do ambiente do container;
6. confirmação de que o container de produção permanece saudável.

## Rollback

Reverter o commit para remover o instalador. Em uma atualização feita pelo
script, parar o container novo, renomear o container `*-pre-*` para o nome
original e iniciá-lo. O backup `*-backup-pre-*` permanece disponível caso a
versão anterior exija restauração dos dados.

## Resultado da validação

- 18 testes Python aprovados;
- `bash -n` e `--help` aprovados no Linux de destino;
- instalação limpa aprovada em container, porta, imagem e volume exclusivos;
- atualização aprovada com preservação do container e backup anteriores;
- inspeção confirmou ausência de `EPG_ADMIN_USER` e `EPG_ADMIN_PASSWORD` no
  container definitivo;
- tag local já existente foi recusada antes da criação do volume;
- falha intencional de inicialização restaurou automaticamente a imagem
  anterior e o health check voltou a responder `200`;
- artefatos dos dois ensaios foram removidos após validação;
- o container de produção permaneceu em execução e `/health` respondeu `ok`.
