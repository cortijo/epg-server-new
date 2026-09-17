# Identidade visual OMNIEPG

Este documento é a referência oficial para reproduzir a interface do OMNIEPG
em outros sistemas. O objetivo é manter a mesma linguagem visual sem copiar a
estrutura técnica da aplicação original.

## 1. Personalidade

O OMNIEPG é uma interface de operação técnica: clara, estável, compacta e
orientada a diagnóstico. A aparência deve transmitir confiança e precisão.

- Nome sempre escrito como **OMNIEPG** em textos de produto.
- Azul-marinho identifica navegação e estrutura.
- Azul vivo identifica ações primárias e seleção.
- Verde, amarelo e vermelho são reservados para estado operacional.
- Painéis usam fundo branco, bordas discretas e pouco relevo.
- Evite gradientes decorativos, excesso de sombras e animações sem função.

## 2. Arquivos oficiais da marca

Os SVG ficam em `epg-product/assets/`:

| Arquivo | Uso |
| --- | --- |
| `omniepg_logotipo_escuro.svg` | Cabeçalhos ou superfícies escuras |
| `omniepg_logotipo_transparente.svg` | Barra lateral azul-marinho |
| `omniepg_icone.svg` | Favicon, barra recolhida e espaços quadrados |

Não altere cores internas, proporção ou tipografia dos SVG. Preserve a área
livre mínima equivalente a 20% da altura da marca. Não estique, incline, aplique
contorno ou substitua o símbolo por texto.

## 3. Cores

Use os tokens de `omniepg-tokens.css` em vez de repetir valores hexadecimais.

| Papel | Token | Cor |
| --- | --- | --- |
| Navegação | `--omni-navy` | `#062B78` |
| Ação primária | `--omni-blue` | `#0646BD` |
| Destaque | `--omni-cyan` | `#1677E8` |
| Fundo da aplicação | `--omni-background` | `#F5F7FA` |
| Superfície | `--omni-surface` | `#FFFFFF` |
| Texto principal | `--omni-text` | `#14263A` |
| Texto auxiliar | `--omni-muted` | `#6D7C8D` |
| Bordas | `--omni-border` | `#D4DBE5` |
| Normal | `--omni-success` | `#19A974` |
| Atenção | `--omni-warning` | `#D99A23` |
| Erro | `--omni-danger` | `#DF4C55` |

Estados nunca devem depender somente da cor: combine cor, texto e ícone.

## 4. Tipografia

Família: `Inter`, `Segoe UI`, `Arial`, `sans-serif`.

- Título da página: 20 px, peso 700.
- Título de cartão: 14–16 px, peso 700.
- Corpo: 13–14 px, peso 400.
- Metadados: 11–12 px, cor auxiliar.
- Rótulo técnico: 9–11 px, peso 800, caixa alta e espaçamento `0.05em`.
- Números de KPI: 28 px, peso 700.

## 5. Estrutura da aplicação

1. Barra lateral fixa azul-marinho com ícone e nome de cada módulo.
2. Cabeçalho branco compacto com marca, usuário e estado de conexão.
3. Conteúdo sobre fundo cinza-claro.
4. KPIs em linha no início de páginas operacionais.
5. Cartões de monitoramento em grade responsiva.
6. Tabelas para cadastro e operações em massa.

A barra lateral mede 218 px aberta e 48–58 px recolhida. O conteúdo deve usar
espaçamento base de 8 px, normalmente em múltiplos de 8 ou 4.

## 6. Componentes

### Botões

- Primário: fundo azul, texto branco, raio de 8 px.
- Secundário: fundo branco, borda cinza, texto azul-marinho.
- Perigoso: vermelho somente para exclusão ou interrupção destrutiva.
- Altura padrão: 36–40 px.
- Todo botão deve ter texto explícito; ícone sozinho exige `aria-label`.

### Cartão operacional

- Fundo branco, borda de 1 px e raio de 10 px.
- Borda superior de 4 px indica o estado.
- Cabeçalho: canal à esquerda e selo de estado à direita.
- Corpo: programa atual, próximo programa e diagnóstico.
- Grade: três colunas em telas largas, duas em médias e uma em celulares.

### Tabelas

- Cabeçalho `#E6EAF0`, texto técnico de 11 px.
- Linhas entre 42 e 52 px e separadores discretos.
- Colunas ordenáveis exibem `↕`, `▲` ou `▼` no cabeçalho.
- Ações ficam na última coluna.

### Formulários e modais

- Campos têm rótulo sempre visível; placeholder não substitui o rótulo.
- Modal fecha apenas por **Salvar**, **Cancelar** ou **Fechar**.
- Erro de validação aparece junto ao contexto e também em notificação curta.
- Confirmação é obrigatória para exclusão, restauração e reinício coletivo.

## 7. Estados operacionais

| Estado | Cor | Texto recomendado |
| --- | --- | --- |
| OK | Verde | Normal / Em transmissão |
| Atenção | Amarelo | Atenção / Aguardando sincronização |
| Erro | Vermelho | Erro / Emissor parado |
| Inativo | Cinza | Parado / Desativado |

O diagnóstico deve informar canal, portadora, fonte XMLTV e motivo. Uma visão
resumida pode usar apenas o selo, mas o detalhe completo deve estar acessível.

## 8. Responsividade e acessibilidade

- Ponto de quebra principal: 900 px.
- Grade de cartões: 3 colunas acima de 1250 px, 2 até 760 px, depois 1.
- Alvo interativo mínimo: 36 × 36 px.
- Contraste mínimo: WCAG AA.
- Foco de teclado deve ser visível.
- Use HTML semântico, `aria-expanded` em expansores e `aria-live` em alertas.
- Respeite `prefers-reduced-motion`.

## 9. Como adotar em outro sistema

1. Copie os três SVG oficiais preservando os nomes.
2. Importe `omniepg-tokens.css` no estilo global.
3. Use as classes do arquivo como base, podendo prefixá-las no framework.
4. Implemente primeiro navegação, botões, cartões, estados e tabelas.
5. Compare a interface em 1440 px, 1024 px e 390 px.
6. Valide contraste, teclado, textos longos e estados sem dados.

O arquivo `omniepg-tokens.json` fornece os mesmos valores para sistemas que
usam React, Vue, Flutter, aplicações nativas ou geração dinâmica de temas.

