# Guia operacional para agentes — EPG Server

## Instalador interativo

Para uma implantação padrão em Linux, prefira `scripts/install.sh`. O script
faz o build antes da troca, cria o volume com UID/GID `10001`, inicia com rede
host e proteções de runtime, valida `/health` e preserva o container anterior e
um backup dos dados. Ele nunca deve receber credenciais por argumento nem
alterar o firewall. Antes de usá-lo, revise a spec
`specs/2026-08-25-instalador-interativo-docker.md`.

```bash
chmod +x scripts/install.sh
sudo ./scripts/install.sh
```

Em produção, confirme os valores apresentados antes de responder às perguntas.
Não use a tag `latest` e não remova o container ou backup de rollback até a
homologação funcional e multicast.

## 1. Escopo

O EPG Server administra XMLTV e gera um MPEG-TS auxiliar com PSI/SI e EPG.
Ele não processa vídeo ou áudio. A imagem Docker compila apenas
`TVStreamEpgOnly` e executa o painel Python.

A partir da versão 1.6, o painel também mantém publicações XMLTV em
`/data/xmltv-publications`. Em manutenção desse módulo, preserve o token/URL da
publicação, as versões anteriores e a seleção por vigência. Nunca trate o nome
original do upload como caminho de arquivo e valide o XML normalizado tanto no
painel quanto no emissor C++.

A partir da versão 1.10, nenhum emissor opera sem licença online válida. O
limite é a soma de `services`, não de portadoras. Antes de um deploy, confirme
servidor de licenças, arquivo secreto, identificador estável e capacidade para
os canais atuais. Nunca registre a chave, coloque-a em `.env`, passe-a como
argumento ou exponha o servidor HTTP fora de loopback; em outro host use HTTPS.

Na v1.11/servidor 1.1, preserve e faça backup do segredo mestre separado da
base. **Ver chave** deriva a chave v2 sem persistir texto puro; a instalação no
EPG valida antes de substituir o arquivo. Rotação de licença ativa exige
autorização explícita, cliente v1.11 já em execução e rollback preservado.

O firewall pode ser administrado separadamente por
`scripts/firewall-manager.sh`. Nunca incorpore essa execução ao instalador do
produto. Antes de aplicar, use `check` e `render`, confirme que a sessão SSH
está coberta, preserve acesso por console e consulte a spec
`specs/2026-08-25-gerenciador-firewall-declarativo.md`.

## 2. Diagnóstico antes da alteração

1. Leia a documentação e a spec atual.
2. Verifique `git status`, branch, remoto, último commit e tags.
3. Confirme a versão em `PRODUCT_VERSION`, Docker e produção.
4. Inspecione o problema sem modificar o servidor.
5. Em produção, registre imagem, rede, volume, usuário, reinícios e health sem
   imprimir variáveis de ambiente ou credenciais.

## 3. Desenvolvimento spec-driven

Copie `specs/TEMPLATE.md`, preencha critérios e só então implemente. Mantenha
uma única fonte de verdade para IDs: TSID, ONID e SID precisam corresponder ao
multiplex final. Uma portadora pode ter até 64 serviços.

## 4. Validação mínima

```bash
python3 -m py_compile epg-product/app.py
python3 -m unittest discover -s epg-product/tests -v
python3 -m py_compile license-server/app.py
python3 -m unittest discover -s license-server/tests -v
git diff --check
docker build -f epg-product/Dockerfile -t epgserver:vX.Y.Z-AAAAMMDD .
```

Extraia o JavaScript incorporado ao HTML e valide com `node --check` quando o
painel for alterado. Faça teste visual real em desktop e viewport móvel.

Para PSI/SI, capture o multicast durante vários ciclos e execute:

```bash
python3 scripts/verify_isdbtb_ts.py AMOSTRA.ts \
  --service-id SID --tsid TSID --onid ONID --epg-only --pmt-pid 0x1000
python3 scripts/verify_epg_clock.py GRUPO PORTA --interface IP_DA_INTERFACE
```

Quando houver logo, acrescente `--require-logo --logo-service-id SID` e
confirme SDT `0xCF`, CDT `0xC8`, PID `0x0029`, CRC e continuidade. A versão
1.5.0 ainda não gera BIT/PID `0x0024`; isso é uma limitação conhecida.

## 5. Release e GitHub

1. Confirme que não existem segredos ou dados operacionais no diff.
2. Rode todos os testes da spec.
3. Faça commit descritivo.
4. Envie `main` ao GitHub.
5. Crie uma tag anotada e imutável quando houver nova versão.
6. Construa imagem com tag imutável; nunca reutilize `latest`.

## 6. Implantação segura

1. Inspecione o container atual antes de qualquer parada.
2. Copie `/srv/epg-stream` para um caminho de backup novo e explícito.
3. Execute a imagem candidata em outra porta e outro volume.
4. Valide autenticação, health, painel e emissores no candidato.
5. Preserve o container anterior renomeado e parado.
6. Inicie a nova imagem com rede host, UID/GID `10001:10001`, filesystem
   somente leitura, `/tmp` temporário, capabilities removidas e
   `no-new-privileges`.
7. Confirme health, zero reinícios, logs e processos emissores.
8. Confirme `license.valid=true`, consumo/limite e que a chave não aparece no
   estado nem no `docker inspect`.

## 7. Rollback

Pare e remova somente o container novo, renomeie o anterior para o nome de
produção e inicie-o. Restaure os dados apenas quando uma migração incompatível
for comprovada. Nunca remova imagem, container ou backup anterior antes da
aceitação do operador.

## 8. Handoff

Registre na spec: diagnóstico, arquivos alterados, testes, commit, tag, image
ID, estado de produção, captura PSI/SI e caminhos de rollback. O próximo agente
deve conseguir continuar sem depender do histórico da conversa.
