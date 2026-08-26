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

## 7. Rollback

Pare e remova somente o container novo, renomeie o anterior para o nome de
produção e inicie-o. Restaure os dados apenas quando uma migração incompatível
for comprovada. Nunca remova imagem, container ou backup anterior antes da
aceitação do operador.

## 8. Handoff

Registre na spec: diagnóstico, arquivos alterados, testes, commit, tag, image
ID, estado de produção, captura PSI/SI e caminhos de rollback. O próximo agente
deve conseguir continuar sem depender do histórico da conversa.
