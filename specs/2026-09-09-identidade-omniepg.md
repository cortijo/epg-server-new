# Spec: identidade visual OMNIEPG

## Objetivo

Renomear a apresentação do produto para **OMNIEPG** e incorporar os três ativos
SVG oficiais sem alterar identificadores persistidos, volumes, nomes de
container ou o funcionamento dos emissores multicast.

## Implementação

- `omniepg_logotipo_transparente.svg`: marca exibida no menu lateral azul;
- `omniepg_logotipo_escuro.svg`: cabeçalho e tela Sobre;
- `omniepg_icone.svg`: favicon e marca compacta em telas menores;
- arquivos servidos por `/assets/` com MIME `image/svg+xml`, cache e `nosniff`;
- a imagem Docker copia os ativos para `/app/assets`;
- produto e autenticação HTTP se identificam como OMNIEPG;
- versão da entrega: `1.21.0`.

## Compatibilidade

A alteração é visual. API, configuração, volumes e processos de injeção EPG
mantêm o formato atual.
