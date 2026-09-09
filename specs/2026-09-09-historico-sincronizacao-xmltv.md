# Histórico de sincronização XMLTV

Cada tentativa de atualização registra persistentemente fonte, início, término,
duração, resultado, erro, tamanho, canais, programas e SHA-256. O hash informa se
o conteúdo recebido é uma nova versão ou permanece idêntico. O histórico mantém
os 500 eventos mais recentes em `/data/parsed-xml-cache/source-sync-history.json`.

O painel oferece histórico geral e por fonte. A função é somente leitura e não
altera o ciclo automático de sincronização nem os emissores multicast.
