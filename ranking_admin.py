"""Ferramenta de terminal para o ranking do PAC-MAN. Nao precisa pygame.

Uso:
  python ranking_admin.py testar
  python ranking_admin.py listar
  python ranking_admin.py adicionar --nome JOSE --pontos 1230 --fase 2
"""
from __future__ import annotations

import argparse
import tempfile
from pathlib import Path
from ranking_db import Ranking, DATABASE_FILE


def main():
    parser = argparse.ArgumentParser(description='Gerenciar ranking SQLite do Pac-Man')
    parser.add_argument('--db', type=Path, default=DATABASE_FILE,
                        help='Caminho para ranking.db (padrao: pasta do programa)')
    sub = parser.add_subparsers(dest='acao', required=True)
    sub.add_parser('testar', help='Testa escrita e reabertura de um banco TEMPORARIO')
    sub.add_parser('listar', help='Mostra o top 10 salvo no banco REAL e total de partidas')
    add = sub.add_parser('adicionar', help='Adiciona partida ao banco REAL para um teste manual')
    add.add_argument('--nome', required=True)
    add.add_argument('--pontos', type=int, required=True)
    add.add_argument('--fase', type=int, default=1)
    args = parser.parse_args()

    if args.acao == 'testar':
        with tempfile.TemporaryDirectory(prefix='pacman_sql_') as folder:
            filename = Path(folder) / 'teste.db'
            ranking = Ranking(filename)
            if ranking.last_error:
                raise SystemExit(f'FALHA ao inicializar: {ranking.last_error}')
            ranking.add('TESTE SQL', 1230, 2)
            reopened = Ranking(filename)
            assert reopened.last_error is None
            assert reopened.count() == 1
            assert reopened.entries[0]['name'] == 'TESTE SQL'
            assert reopened.entries[0]['score'] == 1230
        print('OK: escrita, fechamento, reabertura e leitura do banco SQLite.')
        print('Nenhum resultado de teste foi adicionado ao ranking REAL.')
        return

    ranking = Ranking(args.db)
    if ranking.last_error:
        raise SystemExit(f'Erro no banco: {ranking.last_error}')
    if args.acao == 'adicionar':
        if args.pontos < 0 or args.fase < 1:
            raise SystemExit('Pontos precisam ser >= 0 e fase >= 1.')
        ranking.add(args.nome, args.pontos, args.fase)
        if not ranking.last_save_ok:
            raise SystemExit(f'Erro ao salvar: {ranking.last_error}')
    print(f'Banco: {ranking.filename}')
    print(f'Partidas registradas: {ranking.count()}')
    print('TOP 10:')
    for pos, e in enumerate(ranking.entries, 1):
        print(f'{pos:2}. {e["name"]:<14} | {e["score"]:>8} pts | fase {e["level"]} | {e["date"]}')
    if not ranking.entries:
        print('(vazio - termine uma partida ou aperte F6 no jogo)')


if __name__ == '__main__':
    main()
