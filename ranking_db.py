"""Persistencia do ranking Pac-Man em SQLite, sem dependencias externas.

O esquema SQL fica em ranking.sql, junto deste modulo. A primeira abertura gera
ranking.db automaticamente. Importa ranking.json legado, se presente.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from uuid import uuid4

DATABASE_FILE = Path(__file__).with_name('ranking.db')
SCHEMA_FILE = Path(__file__).with_name('ranking.sql')


def normalize_name(value: str) -> str:
    clean = ''.join(c for c in str(value) if c.isprintable() and c not in '\r\n\t')
    return clean.strip()[:14] or 'JOGADOR'


class Ranking:
    """Salva TODAS as partidas; a UI mostra somente as 10 melhores."""

    def __init__(self, filename: Path = DATABASE_FILE, legacy_file: Path | None = None):
        self.filename = Path(filename).expanduser().resolve()
        self.legacy_file = (Path(legacy_file) if legacy_file is not None
                            else self.filename.with_name('ranking.json'))
        self.entries: list[dict] = []
        self.last_save_ok: bool | None = None
        self.last_error: str | None = None
        try:
            self.filename.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()
            self._migrate_json()
            self.load()
        except (sqlite3.Error, OSError, ValueError) as exc:
            self.last_error = str(exc)
            print(f'[RANKING] Erro ao inicializar o SQLite: {exc}')

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.filename), timeout=5)

    def _initialize(self):
        sql = SCHEMA_FILE.read_text(encoding='utf-8')
        with closing(self._connect()) as connection:
            connection.executescript(sql)
            connection.commit()

    def _migrate_json(self):
        if not self.legacy_file.is_file():
            return
        with closing(self._connect()) as connection:
            if connection.execute("SELECT 1 FROM migracoes WHERE fonte = 'ranking.json'").fetchone():
                return
            try:
                obj = json.loads(self.legacy_file.read_text(encoding='utf-8'))
                records = obj.get('scores', []) if isinstance(obj, dict) else []
                if not isinstance(records, list):
                    records = []
            except (OSError, UnicodeError, ValueError) as exc:
                print(f'[RANKING] ranking.json antigo invalido; nao importado: {exc}')
                return
            imported = 0
            with connection:
                for item in records:
                    if not isinstance(item, dict):
                        continue
                    score, level = item.get('score'), item.get('level')
                    if type(score) is not int or type(level) is not int or score < 0 or level < 1:
                        continue
                    entry_id = str(item.get('id', ''))[:36] or uuid4().hex
                    when = str(item.get('date', '')).strip()[:19] or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    cursor = connection.execute(
                        'INSERT OR IGNORE INTO ranking (id, nome, pontos, fase, data_hora) '
                        'VALUES (?, ?, ?, ?, ?)',
                        (entry_id, normalize_name(item.get('name', 'JOGADOR')), score, level, when),
                    )
                    imported += cursor.rowcount
                connection.execute('INSERT INTO migracoes (fonte) VALUES (?)', ('ranking.json',))
            print(f'[RANKING] Importadas {imported} pontuacoes de ranking.json.')

    def load(self):
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    'SELECT id, nome, pontos, fase, data_hora FROM ranking '
                    'ORDER BY pontos DESC, fase DESC, data_hora ASC, id ASC LIMIT 10'
                ).fetchall()
            self.entries = [dict(id=r[0], name=r[1], score=r[2], level=r[3], date=r[4]) for r in rows]
            self.last_error = None
        except (sqlite3.Error, OSError) as exc:
            self.last_error = str(exc)
            print(f'[RANKING] Erro ao consultar dados: {exc}')
            self.entries = []

    @property
    def record(self) -> int:
        return self.entries[0]['score'] if self.entries else 0

    def add(self, name: str, score: int, level: int) -> str | None:
        entry_id = uuid4().hex
        try:
            with closing(self._connect()) as connection:
                with connection:
                    connection.execute(
                        'INSERT INTO ranking (id, nome, pontos, fase, data_hora) '
                        'VALUES (?, ?, ?, ?, ?)',
                        (entry_id, normalize_name(name), max(0, int(score)), max(1, int(level)),
                         datetime.now().strftime('%Y-%m-%d %H:%M:%S')),
                    )
            self.last_save_ok = True
            self.load()
            print(f'[RANKING] Partida salva no SQLite: {normalize_name(name)} | '
                  f'{score} pontos | fase {level} | {self.filename.name}')
        except (sqlite3.Error, OSError, TypeError, ValueError) as exc:
            self.last_save_ok = False
            self.last_error = str(exc)
            print(f'[RANKING] ERRO: pontuacao NAO salva: {exc}')
            return None
        return entry_id if any(e['id'] == entry_id for e in self.entries) else None

    def count(self) -> int:
        with closing(self._connect()) as connection:
            return connection.execute('SELECT COUNT(*) FROM ranking').fetchone()[0]
