# Spec: canais alimentados por fonte XMLTV

- ID: `2026-09-09-canais-por-fonte-xmltv`
- Estado: `concluída`
- Solicitante: Julio Cortijo

## 1. Resumo

Exibir, dentro de Fontes XMLTV, quais canais usam cada fonte, distinguindo a
seleção explícita no canal da herança da fonte padrão da portadora.

## 2. Objetivos e aceite

- mostrar a quantidade de canais vinculados em cada fonte;
- abrir uma relação com canal, ID XMLTV, portadora, TSID, ONID e multicast;
- indicar se o vínculo é direto ou herdado da portadora;
- refletir imediatamente a configuração persistida, sem alterar emissores.

## 3. Fora de escopo

- alterar associações de fonte por esta tela;
- sincronizar XMLTV ou reiniciar portadoras;
- modificar PSI/SI ou multicast.

## 4. Desenho técnico

A interface cruza `state.carriers[].services[]` com `sources[]`. A fonte efetiva
é `service.source_id || carrier.source_id`. Como os dados já estão presentes na
API autenticada, não é criado endpoint nem campo persistido novo.

## 5. Riscos e rollback

Risco baixo e exclusivamente visual. A implementação deve escapar todos os
valores e manter a tela utilizável quando não houver canais. Rollback: reativar
a imagem anterior preservada no host.

## 6. Validação prevista

- teste unitário da presença da visualização e da regra de herança;
- suíte Python completa, sintaxe JavaScript e `git diff --check`;
- smoke test no host `181.233.106.46`, preservando emissores e imagem anterior.

## 7. Resultado

- suíte: 80 testes aprovados, 5 ignorados por ausência de Bash no Windows;
- JavaScript embutido: sintaxe válida;
- produção: `OMNIEPG 1.21.1`, licença válida, 51/100 canais;
- emissores preservados: 27;
- imagem ativa: `epgserver:v1.21.1-20260909`;
- rollback: `epg-stream-pre-v1.21.1-20260909`, imagem v1.21.0;
- tela autenticada confirmou o controle **Ver canais alimentados**.
