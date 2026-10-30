"""Testes automatizados da lógica do PAC-MAN DELUXE.

Rode: python -m unittest -v test_pacman
Para testes gráficos reais, instale pygame-ce e use SDL_VIDEODRIVER=dummy.
"""
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pacman as pm


class LinkFake:
    connection = None
    port_name = None

    def __init__(self):
        self.sent = []
        self.presses = []

    def poll(self):
        return self.presses.pop(0) if self.presses else (None, False)

    def send_lives(self, lives, force=False):
        self.sent.append((lives, force))

    def connect(self):
        pass

    def close(self):
        pass


class AudioFake:
    def play(self, name):
        pass


class MazeTests(unittest.TestCase):
    def test_many_mazes_connected(self):
        for level in range(1, 161):
            maze = pm.create_maze(level)
            self.assertEqual(len(maze), pm.ROWS)
            self.assertEqual({len(row) for row in maze}, {pm.COLS})
            self.assertTrue(pm.is_floor(maze, (1, 1)))
            floors = {(x, y) for y in range(pm.ROWS) for x in range(pm.COLS)
                      if pm.is_floor(maze, (x, y))}
            found = pm.distances_from(maze, (1, 1))
            self.assertEqual(set(found), floors, level)
            self.assertTrue(all(v == "#" for v in maze[0]))
            self.assertTrue(all(v == "#" for v in maze[-1]))
            self.assertTrue(all(row[0] == row[-1] == "#" for row in maze))

    def test_interpolation_clamps(self):
        self.assertEqual(pm.interpolate((1, 2), (3, 2), 0.5), (2.0, 2.0))
        self.assertEqual(pm.interpolate((1, 2), (3, 2), -1), (1, 2))
        self.assertEqual(pm.interpolate((1, 2), (3, 2), 3), (3, 2))


class RankingTests(unittest.TestCase):
    def test_save_reload_sort_top10_and_utf8(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'ranking.db'
            ranking = pm.Ranking(db)
            self.assertEqual(ranking.entries, [])
            for score in range(0, 150, 10):
                ranking.add('José ão', score, 1 + score // 20)
            self.assertEqual(len(ranking.entries), 10)
            self.assertEqual(ranking.count(), 15)
            self.assertEqual(ranking.record, 140)
            self.assertEqual(ranking.entries[-1]['score'], 50)
            loaded = pm.Ranking(db)
            self.assertEqual(loaded.entries, ranking.entries)
            self.assertEqual(loaded.entries[0]['name'], 'José ão')
            self.assertEqual(loaded.count(), 15)

    def test_import_legacy_json_only_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'ranking.db'
            old = Path(tmp) / 'ranking.json'
            old.write_text(json.dumps({'scores': [
                {'id': 'antigo', 'name': 'ANA', 'score': 30, 'level': 2,
                 'date': '2026-10-28 15:00'},
                {'name': 'bad', 'score': -4, 'level': 1},
            ]}), encoding='utf-8')
            a = pm.Ranking(db)
            self.assertEqual(a.count(), 1)
            self.assertEqual(a.entries[0]['name'], 'ANA')
            b = pm.Ranking(db)
            self.assertEqual(b.count(), 1)

    def test_corrupt_json_does_not_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'ranking.db'
            (Path(tmp) / 'ranking.json').write_text('[broken', encoding='utf-8')
            r = pm.Ranking(db)
            self.assertEqual(r.entries, [])
            self.assertEqual(r.count(), 0)

    def test_name_clean(self):
        self.assertEqual(pm.normalize_name('    '), 'JOGADOR')
        self.assertEqual(pm.normalize_name('  José\n  '), 'José')
        self.assertEqual(len(pm.normalize_name('ABCDEFGHIJKLMNOPQ')), 14)




class JoystickTests(unittest.TestCase):
    def test_neutral_noise_and_hysteresis(self):
        joy = pm.JoystickFilter()
        for x, y in [(505, 520), (560, 475), (620, 520), (510, 400)]:
            self.assertIsNone(joy.read(x, y))
        self.assertEqual(joy.read(900, 510), 'R')
        # Apos ativado, mantem o eixo mesmo na diagonal quase equilibrada.
        self.assertEqual(joy.read(780, 800), 'R')
        self.assertEqual(joy.read(515, 910), 'D')
        self.assertIsNone(joy.read(515, 510))

    def test_direction_and_fast_opposite(self):
        joy = pm.JoystickFilter()
        self.assertEqual(joy.read(50, 510), 'L')
        self.assertEqual(joy.read(970, 510), 'R')
        self.assertEqual(joy.read(512, 0), 'U')
        self.assertEqual(joy.read(512, 1023), 'D')

    def test_calibration_and_sensitivity(self):
        joy = pm.JoystickFilter()
        self.assertFalse(joy.calibrate())
        joy.read(655, 436)
        self.assertTrue(joy.calibrate())
        self.assertEqual((joy.center_x, joy.center_y), (655, 436))
        self.assertIsNone(joy.read(655, 436))
        self.assertEqual(joy.read(950, 436), 'R')
        joy.change_sensitivity(-1000)
        self.assertEqual(joy.threshold, pm.JOY_MIN_ENTER)
        joy.change_sensitivity(+1000)
        self.assertEqual(joy.threshold, pm.JOY_MAX_ENTER)

    def test_noise_around_threshold_does_not_flicker(self):
        joy = pm.JoystickFilter()
        self.assertEqual(joy.read(800, 512), 'R')
        for x in [700, 665, 680, 660, 630, 670]:
            self.assertEqual(joy.read(x, 512), 'R')
        self.assertIsNone(joy.read(600, 512))


class SerialProtocolTests(unittest.TestCase):
    class FakeSerial:
        def __init__(self):
            self.pending = bytearray()
            self.sent = []

        @property
        def in_waiting(self):
            return len(self.pending)

        def read(self, count):
            data, self.pending = self.pending[:count], self.pending[count:]
            return bytes(data)

        def write(self, data):
            self.sent.append(data)
            return len(data)

    def setUp(self):
        self.link = pm.ArduinoLink.__new__(pm.ArduinoLink)
        self.link.connection = self.FakeSerial()
        self.link.received = bytearray()
        self.link.joystick = pm.JoystickFilter()
        self.link.last_press_at = 0.0
        self.link.pressed_before = False
        self.link.last_packet_at = 0.0
        self.link.last_lives_sent = 0.0

    def test_short_tap_is_not_lost_between_frames(self):
        self.link.connection.pending.extend(b"J,950,510,0\nJ,512,512,0\n")
        self.assertEqual(self.link.poll(), ('R', False))
        self.assertEqual(self.link.poll(), (None, False))

    def test_invalid_packet_and_button_edge(self):
        self.link.connection.pending.extend(b"oops\nJ,513,500,1\n")
        self.assertEqual(self.link.poll(), (None, True))
        self.link.connection.pending.extend(b"J,512,500,1\n")
        self.assertEqual(self.link.poll(), (None, False))
        self.link.connection.pending.extend(b"J,512,500,0\n")
        self.assertEqual(self.link.poll(), (None, False))

    def test_life_commands_protocol(self):
        self.link.send_lives(2, force=True)
        self.assertEqual(self.link.connection.sent[-1], b'V:2\n')
        self.link.send_lives(0, force=True)
        self.assertEqual(self.link.connection.sent[-1], b'V:0\n')


class GameLogicTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.game = pm.PacmanGame.__new__(pm.PacmanGame)
        g = self.game
        g.ranking = pm.Ranking(Path(self.directory.name) / "ranking.db")
        g.link = LinkFake()
        g.sounds = AudioFake()
        g.make_static_layer = lambda: None
        g.level = 1
        g.state = "running"
        g.score = 0
        g.lives = 3
        g.name = "ANA"
        g.player_name = "ANA"
        g.last_rank_id = None
        g.load_level()
        g.invulnerable = 0.0

    def test_player_is_smooth_and_consumes(self):
        g = self.game
        direction, target = next(iter(pm.walkable_neighbors(g.maze, g.player)))
        g.requested = direction
        g.current = direction
        start = g.player
        g.advance_player(g.player_interval / 2)
        self.assertEqual(g.player, start)
        self.assertEqual(g.player_target, target)
        self.assertAlmostEqual(g.player_progress, 0.5)
        self.assertEqual(g.player_float_pos(), pm.interpolate(start, target, 0.5))
        g.advance_player(g.player_interval / 2)
        self.assertEqual(g.player, target)
        self.assertAlmostEqual(g.player_progress, 0)
        self.assertEqual(g.score, 10)

    def test_ghosts_slow_by_default(self):
        self.assertGreater(self.game.ghost_interval, self.game.player_interval * 2)
        self.game.level = 300
        self.assertEqual(self.game.ghost_interval, pm.GHOST_MIN_SECONDS)

    def test_collision_decrements_life_and_saves_on_game_over(self):
        g = self.game
        g.score = 120
        for lives_left in (2, 1, 0):
            enemy = g.ghosts[0]
            enemy["pos"] = enemy["target"] = g.player
            enemy["progress"] = enemy["cooldown"] = 0
            g.invulnerable = 0
            g.power_time = 0
            g.check_collisions()
            self.assertEqual(g.lives, lives_left)
            self.assertEqual(g.link.sent[-1], (lives_left, True))
        self.assertEqual(g.state, "game_over")
        self.assertEqual(len(g.ranking.entries), 1)
        self.assertEqual(g.ranking.entries[0]["score"], 120)
        self.assertEqual(g.ranking.entries[0]["name"], "ANA")
        g.finish_game()
        self.assertEqual(len(g.ranking.entries), 1)

    def test_power_collision_eats_ghost_without_life_loss(self):
        g = self.game
        ghost = g.ghosts[0]
        ghost["pos"] = ghost["target"] = g.player
        ghost["progress"] = ghost["cooldown"] = 0
        g.power_time = 3.0
        g.check_collisions()
        self.assertEqual(g.score, 200)
        self.assertEqual(g.lives, 3)
        self.assertGreater(ghost["cooldown"], 0)


    def test_mid_cell_reversal_is_continuous(self):
        g = self.game
        direction, dest = next(iter(pm.walkable_neighbors(g.maze, g.player)))
        g.request_direction(direction)
        g.current = direction
        g.advance_player(g.player_interval * .4)
        before = g.player_float_pos()
        g.request_direction(pm.OPPOSITE[direction])
        g.reverse_in_place()
        self.assertEqual(g.player_float_pos(), before)
        self.assertEqual(g.current, pm.OPPOSITE[direction])
        g.advance_player(g.player_interval * .4)
        after = g.player_float_pos()
        self.assertLess(sum((a-b)**2 for a,b in zip(after, g.spawn)),
                        sum((a-b)**2 for a,b in zip(before, g.spawn)))

    def test_turn_request_expires_when_not_held(self):
        g = self.game
        g.current = 'R'
        g.request_direction('U')
        self.assertEqual(g.requested, 'U')
        g.update(pm.JOY_TURN_BUFFER + .01)
        self.assertEqual(g.requested, 'R')

    def test_joystick_turn_is_buffered_for_corner(self):
        g = self.game
        g.request_direction('U')
        g.update(.05)
        self.assertEqual(g.requested, 'U')
        # 0.68s e mantido para permitir virar perto da esquina.
        self.assertLess(g.turn_buffer_age, pm.JOY_TURN_BUFFER)

    def test_long_running_simulated_game(self):
        g = self.game
        rng = random.Random(123)
        for _ in range(10000):
            if g.state == "game_over":
                break
            if g.state == "level_clear":
                g.level += 1
                g.load_level()
                g.state = "running"
            g.requested = rng.choice(list(pm.VECTORS))
            g.update(1 / 60)
            self.assertTrue(pm.is_floor(g.maze, g.player))
            self.assertTrue(pm.is_floor(g.maze, g.player_target))
            self.assertTrue(0 <= g.player_progress < 1.000001)
            for ghost in g.ghosts:
                self.assertTrue(pm.is_floor(g.maze, ghost["pos"]))
                self.assertTrue(pm.is_floor(g.maze, ghost["target"]))
                self.assertTrue(0 <= ghost["progress"] < 1.000001)


if __name__ == "__main__":
    unittest.main()
