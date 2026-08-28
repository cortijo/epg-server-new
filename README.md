# EPG Server

Aplicação independente para administrar fontes XMLTV e transmitir EPG
ISDB-TB em fluxos auxiliares UDP multicast. O produto não recebe, não
transcodifica e não retransmite vídeo ou áudio.

Para iniciar manutenção, diagnóstico ou desenvolvimento, leia primeiro
[`PROJETO.md`](PROJETO.md). Ele consolida arquitetura, estado real de produção
e candidato, regras de trabalho, testes, deploy e rollback para continuidade
por outro agente ou LLM.

## Componentes

- `epg-product/app.py`: painel web, autenticação, usuários, fontes e portadoras;
- `epg-product/license_client.py`: validação online e limite de canais;
- `license-server/`: autoridade de licenças em imagem Docker independente;
- `epg-product/app.py`: publicações XMLTV versionadas com URL permanente;
- `src/EpgOnlyMain.cpp`: emissor MPEG-TS EPG-only;
- `src/EpgInjector.cpp`: XMLTV, EIT, TDT e TOT;
- `scripts/`: auditorias de relógio e PSI/SI ISDB-TB;
- `epg-product/tests/`: testes automatizados do painel e do domínio;
- `DOCUMENTACAO_EPG_PRODUTO.md`: documentação técnica e operacional completa;
- `specs/`: decisões, critérios de aceite, validações e histórico funcional.

## Construção

```bash
docker build -f epg-product/Dockerfile \
  -t epgserver:v1.11.0 .
docker build -f license-server/Dockerfile \
  -t epg-license-server:v1.1.0 .
```

## Primeira execução

### Instalação automatizada (recomendada)

Em um servidor Linux, execute na raiz do repositório:

```bash
chmod +x scripts/install.sh
sudo ./scripts/install.sh
```

O instalador verifica o Docker, solicita porta, volume, container, tag da
imagem, fuso, URL pública opcional, servidor/chave/instalação da licença e,
somente em volume vazio, o primeiro administrador. Em
seguida compila, inicia, valida `/health` e recria o container sem manter as
credenciais iniciais no ambiente. Ao detectar uma instalação anterior, ele
constrói primeiro, faz backup, preserva o container antigo e oferece rollback
automático se a nova versão não ficar saudável. O firewall não é alterado.

Para servidores que precisam de uma política declarativa separada, use
`scripts/firewall-manager.sh`. Redes e portas ficam em
`/etc/epg-firewall.conf` e só são carregadas após `check`, `render` e `apply`.
O gerenciador mantém uma tabela nftables exclusiva e não limpa regras Docker.
Consulte a seção 10.8 de `DOCUMENTACAO_EPG_PRODUTO.md` antes da primeira
aplicação.

Para instalações que usam listas simples em `/opt/redes-liberadas` e
`/opt/portas-liberadas`, consulte
[`FIREWALL_REDES_LIBERADAS.md`](FIREWALL_REDES_LIBERADAS.md). Esse perfil é
separado do gerenciador avançado e preserva o multicast de saída.

Para ver as opções sem executar a instalação:

```bash
./scripts/install.sh --help
```

### Instalação manual

```bash
sudo install -d -o 10001 -g 10001 -m 0750 /srv/epg-stream
cp epg-product/.env.example epg-product/.env
# Defina credenciais iniciais exclusivas no arquivo .env.
docker compose --env-file epg-product/.env \
  -f epg-product/docker-compose.yml up -d
```

O painel usa TCP `9100` e a emissão multicast usa a rede do host. Leia
`epg-product/README.md` antes da implantação.

## Licenciamento

A partir da versão 1.10.0, o EPG Stream exige uma licença online válida. O
limite conta os **canais/serviços** de todas as portadoras, não a quantidade de
portadoras. Sem chave, com licença revogada/expirada, servidor indisponível ou
quantidade acima do limite, os emissores são interrompidos. Na versão 1.12,
fontes, publicações, grade e toda a gestão de portadoras retornam HTTP 402 e
ficam desabilitadas no painel; somente Usuários e Licença permanecem
operáveis. Uma licença novamente válida retoma automaticamente os fluxos que
não estavam parados manualmente.

O servidor independente escuta por padrão somente em `127.0.0.1:9200`. Na
versão 1.11/1.1, o administrador pode usar **Ver chave** e **Copiar chave**;
a recuperação é derivada de um segredo mestre externo e o JSON continua
persistindo apenas SHA-256, prefixo e versão. O painel EPG oferece **Licença**
para validar e instalar a chave atomicamente. Consulte
`license-server/README.md` antes de criar ou distribuir licenças.

## Validação

```bash
python3 -m py_compile epg-product/app.py
python3 -m unittest discover -s epg-product/tests -v
python3 scripts/verify_isdbtb_ts.py --help
python3 scripts/verify_epg_clock.py --help
```

## Publicações XMLTV da programadora

Em **Publicações XMLTV**, crie uma publicação e envie os arquivos periódicos da
programadora. O sistema acrescenta `-0300` aos horários sem fuso, reconcilia
IDs pelo código numérico do canal, descarta eventos sem duração e publica todas
as versões em uma única URL. A URL escolhe automaticamente a grade vigente e
permanece igual nos próximos uploads; copie-a para **Fontes XMLTV**.

## Simulador de TV / PIDs (v1.13.0)

Com uma portadora ativa, use **Simular TV / PIDs** na barra superior. O painel
captura oito segundos dos datagramas gerados sem interromper o multicast e
mostra PIDs, CRC, continuidade, identidades e os eventos EIT como o receptor os
reconstrói a partir dos descritores `0x4D` e `0x4E`. O teste cobre a saída do EPG
Server; a saída final do Dexing/RF continua exigindo captura no ponto final.

## Estado conhecido da versão 1.13.0

- EIT, TDT/TOT, SDT, BIT e CDT de logo são emitidos e possuem auditoria;
- o logo usa descritor SDT `0xCF` e CDT `0xC8` no PID `0x0029`;
- a BIT `0xC4` no PID `0x0024` anuncia a CDT `0xC8` por descritor de
  parâmetros SI `0xD7` no segundo loop;
- categorias do XMLTV são transmitidas pelo descritor EIT `0x54`; cada canal
  pode definir um fallback quando o evento não trouxer categoria reconhecida;
- os controles e miniaturas de logo estão ocultos no painel; estrutura, API e
  transporte de logos já cadastrados continuam preservados;
- a licença online controla o total de canais/serviços e opera em modo
  fail-closed;
- o painel permite reiniciar de uma vez todos os fluxos elegíveis;
- a homologação final sempre deve considerar a saída do multiplexador e o RF,
  não apenas o multicast auxiliar.

## Segurança

Nunca envie ao Git informações persistidas em `/srv/epg-stream`, arquivos
`.env`, URLs XMLTV privadas, credenciais, logs ou capturas de produção.
