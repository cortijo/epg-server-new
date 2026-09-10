# Spec: exclusão segura de fonte XMLTV

## Resumo

Permitir excluir inclusive a fonte inicial BrazilTVEPG quando existirem outras
fontes, migrando referências persistidas para uma substituta escolhida pelo
administrador e sem deixar portadoras ou canais inválidos.

## Requisitos

- fonte referenciada exige seleção explícita de substituta;
- atualizar a fonte padrão da portadora e referências diretas dos canais;
- se a removida era padrão global, tornar a substituta padrão;
- reiniciar somente portadoras ativas cuja fonte efetiva possa mudar;
- impedir exclusão da última fonte e substituição pela própria fonte;
- manter compatibilidade com requisições antigas para fontes sem uso.

## Fora de escopo

- alterar IDs XMLTV ou programação;
- apagar arquivos de publicações XMLTV;
- alterar PSI/SI diretamente.

## Validação e rollback

Testar migração, bloqueios, interface, suíte completa e produção no host
`181.233.106.46`. Preservar a imagem anterior para rollback.
