# Gravador de perfis de modulador

Ambiente temporário e isolado para registrar, com Playwright Codegen, como uma
operação é realizada na interface web de um modulador. Ele não integra nem
altera os emissores OMNIEPG.

## Segurança

- publique a interface somente em `127.0.0.1` e acesse por túnel SSH;
- não grave usuário ou senha no repositório;
- o diretório `/recordings` pode conter valores operacionais sensíveis;
- revise e sanitize a gravação antes de convertê-la em perfil permanente;
- pare o container imediatamente após encerrar a demonstração.

## Acesso

Crie o túnel na estação do operador:

```bash
ssh -L 9323:127.0.0.1:9323 usuario@servidor
```

Abra `http://127.0.0.1:9323/vnc.html`, informe a senha VNC temporária e clique
em **Connect**. O navegador e o Playwright Inspector aparecerão na mesma área.
Realize o fluxo completo e use o botão de gravação do Inspector para encerrar.

As gravações ficam no diretório montado pelo operador (na implantação inicial,
`/home/julio/omniepg-modulator-recorder`). Para interromper:

```bash
docker stop omniepg-modulator-recorder
```

## Transformação em perfil

A gravação é evidência, não código pronto para produção. O perfil definitivo
deve possuir detecção de modelo/versão, seletores estáveis, leitura prévia,
modo simulação, confirmação explícita, verificação pós-gravação e rollback.
