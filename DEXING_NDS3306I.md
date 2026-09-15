# Integração OMNIEPG com DeXin NDS3306I

## Objetivo

O OMNIEPG 1.26.0 cadastra um NDS3306I e vincula uma portadora EPG a um `Output TS`. A sincronização consulta o estado atual antes de alterar e não remove inputs, programas ou PIDs alheios à operação.

Desde a versão 1.25.1, a integração usa adaptadores por fabricante. O NDS3306I é o primeiro adaptador. Equipamentos de outros fabricantes podem ser cadastrados como **Outro / configuração manual**: o OMNIEPG continua emitindo EPG normalmente e não tenta alterar o modulador. Novos adaptadores devem implementar as mesmas operações de teste, inventário e sincronização, sem modificar o emissor multicast.

## Cadastro

1. Entre como administrador e abra **Moduladores Dexing**.
2. Cadastre nome, IP, protocolo, usuário e senha.
3. Use **Testar**. O teste autentica e lê o Output TS 1 sem gravar.
4. Ao criar ou editar uma portadora, escolha primeiro a integração no topo do formulário.
5. Ao selecionar equipamento e `Output TS` (1–48), o OMNIEPG faz uma consulta somente leitura, preenche TSID/ONID e mostra os canais como `Program Number — nome` no campo SID.
6. Selecione o Program Number correto para cada canal, a interface `Data1`–`Data4` e salve.
7. Use **Sem integração automática** quando o equipamento não tiver adaptador: TSID, ONID e SID continuam com preenchimento manual.
8. No modo automático, o salvamento executa a sincronização do multicast e dos PIDs. No modo “Somente manual”, use **Sincronizar agora**.

Os valores obtidos na consulta aparecem como **Sincronizado com o modulador**. A leitura do formulário não executa Parse Program e não grava no equipamento; essas alterações só acontecem ao salvar/sincronizar.

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
9. Lê a configuração **General** do Output TS e importa `TSID` e `ONID` para a portadora.
10. Cruza o nome normalizado e exclusivo de cada canal com `tsout.prg_info[]` e importa o respectivo `Program Number` como SID.
11. Reinicia somente o emissor da portadora, caso ela estivesse ativa e algum TSID, ONID ou SID tenha sido alterado.

Não há associação por posição: se um nome estiver ausente ou repetido no Output TS, o SID atual é preservado e a divergência aparece no resultado da sincronização. Isso evita atribuir a programação ao canal errado.

## Estado e monitoramento

Depois de uma sincronização bem-sucedida, a lista de portadoras mostra **Modulador sincronizado** e o estado do multicast. O monitor é somente leitura: autentica no equipamento, localiza o par exato `IP:porta` e exige `TS Lock` e bitrate maior que zero.

Em **Configurações gerais**, ajuste **Monitorar moduladores a cada (horas)** entre 1 e 168 horas. O padrão é 6 horas. A verificação roda em segundo plano e não executa Parse Program, não grava PIDs e não interrompe o emissor multicast.

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

O retorno informa input criado/reutilizado, `IP<N>`, PIDs incluídos, programas lidos, TSID/ONID importados e a associação de cada SID.

## Diagnóstico

- Login recusado: confira credenciais e HTTP/HTTPS.
- Input solicitado mas ausente: o firmware pode exigir um campo adicional; capture o Add manual nessa versão.
- SID não localizado: confira se o nome do canal é único e equivalente no OMNIEPG e no Output TS.
- Multicast com falha: confira o par exato IP/porta, `TS Lock` e bitrate no inventário do equipamento.
- PID já existe: é normal; não haverá duplicação.
- Timeout: confira rota e firewall entre OMNIEPG e a gerência do Dexing.

Os testes em `epg-product/tests/test_dexing.py` cobrem índice do Output TS, reutilização de input, merge dos PIDs, idempotência, associação por Program Number e leitura de TSID/ONID.
