# Integração OMNIEPG com DeXin NDS3306I

## Objetivo

O OMNIEPG 1.25.1 cadastra um NDS3306I e vincula uma portadora EPG a um `Output TS`. A sincronização consulta o estado atual antes de alterar e não remove inputs, programas ou PIDs alheios à operação.

Desde a versão 1.25.1, a integração usa adaptadores por fabricante. O NDS3306I é o primeiro adaptador. Equipamentos de outros fabricantes podem ser cadastrados como **Outro / configuração manual**: o OMNIEPG continua emitindo EPG normalmente e não tenta alterar o modulador. Novos adaptadores devem implementar as mesmas operações de teste, inventário e sincronização, sem modificar o emissor multicast.

## Cadastro

1. Entre como administrador e abra **Moduladores Dexing**.
2. Cadastre nome, IP, protocolo, usuário e senha.
3. Use **Testar**. O teste autentica e lê o Output TS 1 sem gravar.
4. Edite uma portadora e selecione equipamento, `Output TS` (1–48), interface `Data1`–`Data4` e modo automático ou manual.
5. Salve. No modo manual, use **Sincronizar agora**.

`Output TS 1` na tela corresponde ao índice `0` da API. A conversão é automática.

## Fluxo aplicado

1. Faz login em `/login_process.php` e mantém o cookie apenas durante a operação.
2. Lê inputs e programas com `refreshPrg`, `op_code=0`.
3. Procura o par exato `multicast:porta` em `IP<N>_Data<X>_<ip>:<porta>`.
4. Reutiliza o input existente ou o cadastra com `mux_edit_input_ch`, `op_code=5`, validando o inventário novamente.
5. Executa Parse Program com `refreshPrg`, `op_code=3`.
6. Lê toda a tabela de PID passthrough com `pidpass`, `op_code=3`.
7. Acrescenta somente as linhas ausentes `0x0012 → 0x0012` (EIT) e `0x0014 → 0x0014` (TDT/TOT).
8. Grava a tabela completa com `pidpass`, `op_code=1`, preservando as linhas anteriores.
9. Cruza `tsout.prg_info[].program_number` com os SIDs cadastrados na portadora.

## Segurança

- Somente administradores licenciados acessam cadastro, teste e sincronização.
- A API nunca devolve a senha e os logs não registram senha nem cookie.
- O arquivo local tem permissão `0600`; backups completos contêm credenciais e devem ser protegidos.
- Certificado interno HTTPS é aceito por padrão. Habilite validação TLS quando houver certificado confiável.
- O sistema não move programas para o Output TS e não apaga configurações existentes.

## API

- `GET /api/modulators` — lista equipamentos sem senhas;
- `POST /api/modulators` — cria/edita;
- `POST /api/modulators/test` — testa login e inventário;
- `POST /api/modulators/delete` — exclui equipamento sem vínculo;
- `POST /api/carriers/dexing-sync` — sincroniza a portadora.

```bash
curl -u epgadmin:SENHA -H 'Content-Type: application/json' \
  -d '{"id":"carrier-exemplo"}' \
  http://127.0.0.1:9100/api/carriers/dexing-sync
```

O retorno informa input criado/reutilizado, `IP<N>`, PIDs incluídos, programas lidos e associação dos SIDs.

## Diagnóstico

- Login recusado: confira credenciais e HTTP/HTTPS.
- Input solicitado mas ausente: o firmware pode exigir um campo adicional; capture o Add manual nessa versão.
- SID não localizado: o `Program Number` do Output TS precisa ser igual ao SID OMNIEPG.
- PID já existe: é normal; não haverá duplicação.
- Timeout: confira rota e firewall entre OMNIEPG e a gerência do Dexing.

Os testes em `epg-product/tests/test_dexing.py` cobrem índice do Output TS, reutilização de input, merge dos PIDs, idempotência e associação por Program Number.
