# EPG Server

Aplicação independente para administrar fontes XMLTV e transmitir EPG
ISDB-TB em fluxos auxiliares UDP multicast. O produto não recebe, não
transcodifica e não retransmite vídeo ou áudio.

## Componentes

- `epg-product/app.py`: painel web, autenticação, usuários, fontes e portadoras;
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
  -t epgserver:v1.7.0 .
```

## Primeira execução

### Instalação automatizada (recomendada)

Em um servidor Linux, execute na raiz do repositório:

```bash
chmod +x scripts/install.sh
sudo ./scripts/install.sh
```

O instalador verifica o Docker, solicita porta, volume, container, tag da
imagem, fuso, URL pública opcional e, somente em volume vazio, o primeiro administrador. Em
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

## Estado conhecido da versão 1.7.0

- EIT, TDT/TOT, SDT, BIT e CDT de logo são emitidos e possuem auditoria;
- o logo usa descritor SDT `0xCF` e CDT `0xC8` no PID `0x0029`;
- a BIT `0xC4` no PID `0x0024` anuncia a CDT `0xC8` por descritor de
  parâmetros SI `0xD7` no segundo loop;
- a homologação final sempre deve considerar a saída do multiplexador e o RF,
  não apenas o multicast auxiliar.

## Segurança

Nunca envie ao Git informações persistidas em `/srv/epg-stream`, arquivos
`.env`, URLs XMLTV privadas, credenciais, logs ou capturas de produção.
