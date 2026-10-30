-- PAC-MAN DELUXE v4 - esquema SQLite (nao MySQL)
-- O Python executa este arquivo automaticamente na primeira abertura.
-- ranking.db e gerado no notebook; o SQL define a estrutura.

CREATE TABLE IF NOT EXISTS ranking (
    id TEXT PRIMARY KEY NOT NULL,
    nome TEXT NOT NULL CHECK (length(nome) BETWEEN 1 AND 14),
    pontos INTEGER NOT NULL CHECK (pontos >= 0),
    fase INTEGER NOT NULL CHECK (fase >= 1),
    data_hora TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Maior pontuacao primeiro. Em empate, desempate por fase e data.
CREATE INDEX IF NOT EXISTS idx_ranking_melhores
    ON ranking (pontos DESC, fase DESC, data_hora ASC, id ASC);

-- Evita importar repetidamente o antigo ranking.json.
CREATE TABLE IF NOT EXISTS migracoes (
    fonte TEXT PRIMARY KEY NOT NULL,
    data_hora TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Consulta dos 10 melhores resultados:
-- SELECT nome, pontos, fase, data_hora FROM ranking
-- ORDER BY pontos DESC, fase DESC, data_hora ASC, id ASC LIMIT 10;
