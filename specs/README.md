# Desenvolvimento spec-driven do TVStream

Este diretório contém as especificações executáveis das mudanças do sistema.
Uma spec não é apenas uma descrição: ela é o contrato entre pedido, código,
validação, implantação e commit.

Antes de executar esse fluxo, todo agente deve ler `../AGENTS.md`,
`../GUIA_OPERACIONAL_AGENTES.md` e `../DOCUMENTACAO_CODEBASE.md`. O guia
operacional define os portões de GitHub, imagem Docker, produção e rollback.

## Quando criar uma spec

Crie uma spec para qualquer mudança que envolva pelo menos um destes pontos:

- código C++ ou JavaScript do painel;
- pipeline GStreamer, codecs, PCR, PIDs, cache ou MPEG-TS;
- API, formato JSON ou persistência;
- YouTube, EPG, assinantes ou playout 24×7;
- Docker, dependências ou implantação;
- segurança, autenticação ou dados sensíveis;
- alteração que possa interromper um canal em produção.

Correções ortográficas isoladas podem dispensar spec. Se houver dúvida, use a
spec.

## Nome do arquivo

```text
specs/AAAA-MM-DD-descricao-curta.md
```

Exemplo:

```text
specs/2026-08-18-recuperacao-canal-audio.md
```

Nunca sobrescreva uma spec antiga para uma mudança diferente. Ela é parte do
histórico de engenharia.

## Estados

Use exatamente um estado no cabeçalho:

| Estado | Significado |
|---|---|
| `rascunho` | requisitos ainda incompletos; não implementar |
| `pronta` | critérios e plano suficientes para começar |
| `implementando` | alteração em andamento |
| `validando` | código concluído, testes em execução |
| `implantada` | produção atualizada e verificada |
| `concluída` | validação, commit e push confirmados |
| `bloqueada` | depende de decisão, acesso ou condição externa |
| `cancelada` | mudança abandonada conscientemente |

## Fluxo obrigatório

### 1. Descobrir

- leia `AGENTS.md` e `DOCUMENTACAO_CODEBASE.md`;
- confirme `git status --short`;
- localize os arquivos e fluxos com `rg`;
- leia o código que realmente executa a função;
- registre comportamento atual e evidência do problema.

### 2. Especificar

Copie `TEMPLATE.md`, preencha objetivo, escopo, contratos, critérios de aceite,
riscos, testes e rollback. Um critério de aceite deve ser observável, não apenas
“funcionar corretamente”.

Exemplo bom:

```text
Dado um canal somente áudio cujo processo morreu, quando a origem voltar,
então o canal deve ficar active=true sem reinício manual do container.
```

### 3. Planejar

Liste arquivos, componentes e sequência de implementação. Declare o que não
será alterado. Se a mudança afetar mídia, descreva a topologia antes e depois.

### 4. Implementar

- faça a menor mudança que satisfaça a spec;
- preserve compatibilidade e alterações existentes;
- atualize documentação e exemplos afetados;
- registre decisões novas na seção `Registro de execução`.

### 5. Validar

Execute a matriz definida na spec. Para C++, o build é obrigatório. Mudança em
fluxo de mídia deve ser testada em container isolado, com porta e multicast que
não colidam com produção. Registre comandos, resultados e evidências resumidas.

### 6. Implantar

- gere tag de imagem imutável;
- preserve o container anterior sem restart automático;
- publique a nova imagem;
- valide `/health`, `/api/state`, logs, processos e canais;
- execute o critério de rollback se houver regressão.

### 7. Concluir

- marque todos os critérios de aceite;
- execute `git diff --check` e auditoria de segredos;
- atualize o estado da spec para `concluída`;
- faça commit da spec junto com código e documentação;
- envie para `origin/main` no repositório `cortijo/epgserver`;
- registre hash, tag e imagem implantada.

## Portões de qualidade

Uma mudança só passa para o próximo estágio quando:

| De | Para | Portão |
|---|---|---|
| rascunho | pronta | requisitos e critérios de aceite claros |
| pronta | implementando | plano, riscos e rollback definidos |
| implementando | validando | código e documentação concluídos |
| validando | implantada | todos os testes obrigatórios passaram |
| implantada | concluída | produção saudável, commit e push verificados |

Falha em teste impede commit e implantação. Falha após implantação aciona o
rollback descrito na spec.

## Evidência mínima por tipo de mudança

| Tipo | Evidência mínima |
|---|---|
| documentação | `git diff --check` e revisão dos links/conteúdo |
| painel | build, HTML/API servido e conferência dos elementos alterados |
| API | build, request real e resposta esperada/erro esperado |
| pipeline | build, teste isolado, processo, logs e análise do transporte |
| recuperação | falha induzida, tentativas registradas e retorno automático |
| EPG | tabelas presentes, horário correto e teste VLC/modulador |
| Docker | build da imagem, startup, health e persistência |

## Regra de segurança

Nunca cole na spec cookies, JWTs, senhas, tokens, chaves privadas ou o conteúdo
de arquivos de produção. Use placeholders como `USUARIO`, `SENHA`, `TOKEN` e
`STREAM_ID`. IPs e URLs internos só devem aparecer quando forem necessários à
arquitetura e autorizados pelo operador.
