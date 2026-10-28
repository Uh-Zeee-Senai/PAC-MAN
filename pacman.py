"""PAC-MAN DELUXE | Arduino + joystick + ranking local.

Python 3.14: use pygame-ce (which is imported as pygame) and pyserial.
The firmware and wiring from the original Arduino project remain unchanged.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from array import array
from collections import deque
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pygame

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

COLS, ROWS = 23, 19
CELL = 28
BOARD_Y = 112
BOARD_H = ROWS * CELL
WIDTH, HEIGHT = COLS * CELL, BOARD_Y + BOARD_H + 83
FPS = 60
BAUD = 115200
SCORE_FILE = Path(__file__).with_name("ranking.json")

# Ajustes de dificuldade: números maiores = movimentação MAIS LENTA.
PACMAN_STEP_SECONDS = 0.126
GHOST_STEP_SECONDS = 0.40
GHOST_MIN_SECONDS = 0.32
GHOST_LEVEL_ACCELERATION = 0.012
GHOST_FRIGHTENED_MULTIPLIER = 1.35
POWER_SECONDS = 8.0

# Ajuste a orientação se o joystick estiver invertido.
INVERTER_X = False
INVERTER_Y = False
LIMIAR_JOYSTICK = 175

BG = (7, 11, 26)
BOARD = (9, 15, 36)
PANEL = (15, 24, 52)
PANEL_LIGHT = (19, 35, 70)
WALL = (19, 37, 83)
WALL_EDGE = (50, 107, 228)
CYAN = (95, 226, 252)
YELLOW = (255, 217, 62)
WHITE = (240, 245, 255)
MUTED = (151, 170, 203)
PINK = (255, 106, 174)
GREEN = (119, 237, 166)
RED = (255, 107, 122)
GHOST_COLORS = ((251, 91, 103), (255, 156, 205), (91, 221, 248))
VECTORS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
OPPOSITE = {"U": "D", "D": "U", "L": "R", "R": "L"}


def normalize_name(value: str) -> str:
    """Limita texto recebido do teclado/JSON sem rejeitar acentos do português."""
    clean = "".join(c for c in value if c.isprintable() and c not in "\r\n\t")
    return clean.strip()[:14] or "JOGADOR"


class Ranking:
    """Top 10 persistente em JSON, sem depender de internet ou Arduino."""

    def __init__(self, filename: Path = SCORE_FILE):
        self.filename = Path(filename)
        self.entries: list[dict] = []
        self.load()

    def load(self):
        try:
            raw = json.loads(self.filename.read_text(encoding="utf-8"))
            data = raw.get("scores", []) if isinstance(raw, dict) else []
            clean = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                score, level = item.get("score"), item.get("level")
                if type(score) is not int or type(level) is not int or score < 0 or level < 1:
                    continue
                clean.append({
                    "id": str(item.get("id", ""))[:36],
                    "name": normalize_name(str(item.get("name", "JOGADOR"))),
                    "score": min(score, 99999999),
                    "level": min(level, 999),
                    "date": str(item.get("date", ""))[:20],
                })
            self.entries = self._sort(clean)[:10]
        except FileNotFoundError:
            self.entries = []
        except (OSError, UnicodeError, ValueError, TypeError) as exc:
            print(f"Ranking não pôde ser lido ({exc}); iniciando tabela vazia.")
            self.entries = []

    @staticmethod
    def _sort(entries):
        return sorted(entries, key=lambda x: (-x["score"], -x["level"], x["date"]))

    @property
    def record(self):
        return self.entries[0]["score"] if self.entries else 0

    def add(self, name: str, score: int, level: int) -> str | None:
        entry = {
            "id": uuid4().hex,
            "name": normalize_name(name),
            "score": max(0, int(score)),
            "level": max(1, int(level)),
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        self.entries = self._sort(self.entries + [entry])[:10]
        try:
            # Arquivo temporário + replace: evita ranking truncado em caso de erro.
            self.filename.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.filename.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "scores": self.entries},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.filename)
        except OSError as exc:
            print(f"Aviso: não foi possível salvar ranking: {exc}")
        return entry["id"] if entry in self.entries else None


class ArduinoLink:
    """Serial não bloqueante. Arduino -> J,x,y,botao ; Python -> V:0..3."""

    def __init__(self, port: str | None = None):
        self.chosen_port = port
        self.connection = None
        self.port_name = None
        self.received = bytearray()
        self.pressed_before = False
        self.last_press_at = 0.0
        self.last_lives_sent = 0.0
        self.connect()

    @staticmethod
    def available_ports():
        return list(list_ports.comports()) if list_ports else []

    def connect(self):
        self.close()
        if serial is None:
            print("PySerial ausente; usando teclado.")
            return
        if self.chosen_port:
            candidates = [self.chosen_port]
        else:
            ports = self.available_ports()
            keywords = ("arduino", "ch340", "ch341", "usb serial", "usb-serial", "cp210", "ttyacm", "ttyusb")
            candidates = [p.device for p in ports if any(
                k in (p.device + " " + p.description).lower() for k in keywords)]
            if not candidates and len(ports) == 1:
                candidates = [ports[0].device]
        for name in candidates:
            try:
                self.connection = serial.Serial(name, BAUD, timeout=0, write_timeout=0.03)
                self.port_name = name
                self.received.clear()
                self.pressed_before = False
                self.last_lives_sent = 0.0
                print(f"Arduino conectado em {name} (115200 baud)")
                return
            except (OSError, serial.SerialException) as exc:
                print(f"Falha ao abrir {name}: {exc}")
        print("Arduino desconectado. Jogável pelo teclado (F5 reconecta).")

    def close(self):
        if self.connection is not None:
            try:
                self.connection.close()
            except (OSError, serial.SerialException):
                pass
        self.connection = None
        self.port_name = None
        self.received.clear()
        self.pressed_before = False

    def send_lives(self, lives: int, force=False):
        if self.connection is None:
            return
        now = time.monotonic()
        if not force and now - self.last_lives_sent < 0.8:
            return
        try:
            self.connection.write(f"V:{max(0, min(3, lives))}\n".encode("ascii"))
            self.last_lives_sent = now
        except (OSError, serial.SerialException):
            self.close()

    def poll(self):
        if self.connection is None:
            return None, False
        direction, button_edge = None, False
        try:
            count = min(self.connection.in_waiting, 2048)
            if count:
                self.received.extend(self.connection.read(count))
            if len(self.received) > 4096:
                self.received.clear()
            # Consome todas as leituras para responder à MAIS RECENTE posição do joystick.
            while b"\n" in self.received:
                raw, _, remainder = self.received.partition(b"\n")
                self.received = bytearray(remainder)
                try:
                    parts = raw.strip().decode("ascii").split(",")
                    if len(parts) != 4 or parts[0] != "J":
                        continue
                    x, y, pressed = (int(v) for v in parts[1:])
                    if not (0 <= x <= 1023 and 0 <= y <= 1023 and pressed in (0, 1)):
                        continue
                except (UnicodeError, ValueError):
                    continue
                if INVERTER_X:
                    x = 1023 - x
                if INVERTER_Y:
                    y = 1023 - y
                dx, dy = x - 512, y - 512
                if max(abs(dx), abs(dy)) >= LIMIAR_JOYSTICK:
                    direction = ("R" if dx > 0 else "L") if abs(dx) >= abs(dy) else ("D" if dy > 0 else "U")
                else:
                    direction = None
                now = time.monotonic()
                active = pressed == 1
                if active and not self.pressed_before and now - self.last_press_at >= 0.25:
                    button_edge = True
                    self.last_press_at = now
                self.pressed_before = active
        except (OSError, serial.SerialException):
            print("USB desconectado. Usando teclado.")
            self.close()
        return direction, button_edge


def create_maze(level: int):
    rng = random.Random(1206 + level * 17)
    grid = [["#"] * COLS for _ in range(ROWS)]
    grid[1][1] = " "
    visited = {(1, 1)}
    stack = [(1, 1)]
    while stack:
        x, y = stack[-1]
        options = []
        for dx, dy in VECTORS.values():
            nx, ny = x + dx * 2, y + dy * 2
            if 1 <= nx < COLS - 1 and 1 <= ny < ROWS - 1 and (nx, ny) not in visited:
                options.append((nx, ny, dx, dy))
        if not options:
            stack.pop()
            continue
        nx, ny, dx, dy = rng.choice(options)
        grid[y + dy][x + dx] = " "
        grid[ny][nx] = " "
        visited.add((nx, ny))
        stack.append((nx, ny))
    shortcuts = []
    for y in range(1, ROWS - 1):
        for x in range(1, COLS - 1):
            if grid[y][x] != "#":
                continue
            horizontal = grid[y][x - 1] == grid[y][x + 1] == " "
            vertical = grid[y - 1][x] == grid[y + 1][x] == " "
            if horizontal or vertical:
                shortcuts.append((x, y))
    rng.shuffle(shortcuts)
    for x, y in shortcuts[:int(len(shortcuts) * 0.39)]:
        grid[y][x] = " "
    return grid


def is_floor(maze, pos):
    x, y = pos
    return 0 <= x < COLS and 0 <= y < ROWS and maze[y][x] != "#"


def walkable_neighbors(maze, pos):
    x, y = pos
    for direction, (dx, dy) in VECTORS.items():
        candidate = (x + dx, y + dy)
        if is_floor(maze, candidate):
            yield direction, candidate


def nearest_floor(maze, target):
    return min(((x, y) for y in range(ROWS) for x in range(COLS) if maze[y][x] == " "),
               key=lambda p: abs(p[0] - target[0]) + abs(p[1] - target[1]))


def distances_from(maze, target):
    distance = {target: 0}
    queue = deque([target])
    while queue:
        pos = queue.popleft()
        for _, neighbor in walkable_neighbors(maze, pos):
            if neighbor not in distance:
                distance[neighbor] = distance[pos] + 1
                queue.append(neighbor)
    return distance


def interpolate(start, end, progress):
    p = max(0.0, min(1.0, progress))
    return start[0] + (end[0] - start[0]) * p, start[1] + (end[1] - start[1]) * p


class SoundFX:
    """Efeitos de áudio 8-bit opcionais, sintetizados e mantidos em cache."""

    def __init__(self, disabled=False):
        self.muted = disabled
        self.sounds = {}
        if disabled:
            return
        try:
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=22050, size=-16, channels=1, buffer=512)
            for name, notes in {
                "dot": [(660, 0.045)],
                "power": [(400, 0.075), (610, 0.085), (850, 0.09)],
                "eat": [(630, 0.09), (840, 0.10)],
                "hit": [(330, 0.09), (250, 0.10), (160, 0.16)],
                "win": [(440, 0.08), (550, 0.08), (660, 0.08), (880, 0.17)],
            }.items():
                samples = array("h")
                for hz, duration in notes:
                    count = int(22050 * duration)
                    for i in range(count):
                        fade = (1 - i / max(1, count)) ** 1.4
                        samples.append(int(4300 * fade * math.sin(2 * math.pi * hz * i / 22050)))
                self.sounds[name] = pygame.mixer.Sound(buffer=samples.tobytes())
        except pygame.error as exc:
            print(f"Áudio indisponível ({exc}); jogo continua normalmente.")
            self.muted = True

    def play(self, name):
        if not self.muted and name in self.sounds:
            self.sounds[name].play()

    def toggle(self):
        if self.sounds:
            self.muted = not self.muted


class PacmanGame:
    def __init__(self, link: ArduinoLink, rankings_path: Path = SCORE_FILE, no_audio=False):
        pygame.mixer.pre_init(22050, -16, 1, 512)
        pygame.init()
        pygame.display.set_caption("PAC-MAN DELUXE | Arduino Edition")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.font_title = pygame.font.SysFont("arial", 39, bold=True)
        self.font_big = pygame.font.SysFont("arial", 30, bold=True)
        self.font_medium = pygame.font.SysFont("arial", 19, bold=True)
        self.font_body = pygame.font.SysFont("arial", 17)
        self.font_small = pygame.font.SysFont("consolas", 14)
        self.font_tiny = pygame.font.SysFont("consolas", 12)
        self.link = link
        self.ranking = Ranking(rankings_path)
        self.sounds = SoundFX(disabled=no_audio)
        self.running = True
        self.state = "menu"
        self.previous_screen = "menu"
        self.name = ""
        self.player_name = "JOGADOR"
        self.last_rank_id = None
        self.score = 0
        self.lives = 3
        self.level = 1
        self.pellets = set()
        self.power_pellets = set()
        self.pellets_start = 1
        self.power_time = 0.0
        self.invulnerable = 0.0
        self.requested = "R"
        self.current = "R"
        self.player = (1, 1)
        self.player_target = self.player
        self.player_progress = 0.0
        self.ghosts = []
        self.maze = []
        self.static_layer = None
        self.stars = [(random.Random(i * 143).randrange(WIDTH),
                       random.Random(i * 987).randrange(HEIGHT), i % 3 + 1)
                      for i in range(38)]
        self.load_level()
        self.link.send_lives(self.lives, force=True)
        pygame.key.start_text_input()

    @property
    def player_interval(self):
        return max(0.112, PACMAN_STEP_SECONDS - (self.level - 1) * 0.002)

    @property
    def ghost_interval(self):
        return max(GHOST_MIN_SECONDS, GHOST_STEP_SECONDS - (self.level - 1) * GHOST_LEVEL_ACCELERATION)

    def load_level(self):
        self.maze = create_maze(self.level)
        self.spawn = (1, 1)
        self.player = self.spawn
        self.player_target = self.spawn
        self.player_progress = 0.0
        self.current = self.requested = "R"
        self.power_time = 0.0
        self.invulnerable = 1.15
        walkable = {(x, y) for y in range(ROWS) for x in range(COLS)
                    if self.maze[y][x] == " "}
        corners = [(COLS - 2, 1), (COLS - 2, ROWS - 2), (1, ROWS - 2)]
        spawns = []
        for corner in corners:
            pos = nearest_floor(self.maze, corner)
            if pos == self.spawn or pos in spawns:
                pos = max(walkable - {self.spawn, *spawns},
                          key=lambda p: abs(p[0] - self.spawn[0]) + abs(p[1] - self.spawn[1]))
            spawns.append(pos)
        self.ghosts = [{"pos": p, "target": p, "progress": 0.0,
                        "spawn": p, "direction": "L", "color": GHOST_COLORS[i],
                        "cooldown": 0.6, "aggression": (0.76, 0.52, 0.39)[i]}
                       for i, p in enumerate(spawns)]
        self.power_pellets = set(spawns)
        self.pellets = walkable - {self.spawn} - self.power_pellets
        self.pellets_start = max(1, len(self.pellets) + len(self.power_pellets))
        self.make_static_layer()

    def make_static_layer(self):
        self.static_layer = pygame.Surface((WIDTH, BOARD_H)).convert()
        self.static_layer.fill(BOARD)
        for y in range(ROWS):
            for x in range(COLS):
                if self.maze[y][x] == "#":
                    r = pygame.Rect(x * CELL + 2, y * CELL + 2, CELL - 4, CELL - 4)
                    pygame.draw.rect(self.static_layer, WALL, r, border_radius=6)
                    pygame.draw.rect(self.static_layer, WALL_EDGE, r, 1, border_radius=6)
                    if (x + y) % 7 == 0:
                        pygame.draw.line(self.static_layer, (35, 65, 131),
                                         (r.x + 6, r.bottom - 4), (r.right - 6, r.bottom - 4), 1)

    def start_new_game(self):
        self.player_name = normalize_name(self.name)
        self.score = 0
        self.lives = 3
        self.level = 1
        self.last_rank_id = None
        self.load_level()
        self.state = "running"
        pygame.key.stop_text_input()
        self.link.send_lives(3, force=True)

    def open_menu(self):
        self.state = "menu"
        pygame.key.start_text_input()

    def start_next_level(self):
        self.level += 1
        self.load_level()
        self.state = "running"
        self.sounds.play("win")

    def handle_action(self):
        if self.state == "menu":
            self.start_new_game()
        elif self.state == "game_over":
            self.open_menu()
        elif self.state == "ranking":
            self.state = self.previous_screen
            if self.state == "menu":
                pygame.key.start_text_input()
        elif self.state == "level_clear":
            self.start_next_level()
        elif self.state == "paused":
            self.state = "running"
        elif self.state == "running":
            self.state = "paused"

    def toggle_ranking(self):
        if self.state in ("menu", "game_over", "paused"):
            self.previous_screen = self.state
            self.state = "ranking"
            pygame.key.stop_text_input()
        elif self.state == "ranking":
            self.handle_action()

    def input_name(self, value: str):
        if self.state == "menu":
            appended = "".join(c for c in value if c.isprintable() and c not in "\r\n\t")
            self.name = (self.name + appended)[:14]

    def finish_game(self):
        if self.state == "game_over":
            return
        self.state = "game_over"
        self.last_rank_id = self.ranking.add(self.player_name, self.score, self.level)
        self.sounds.play("hit")

    def consume_pellet(self):
        if self.player in self.pellets:
            self.pellets.remove(self.player)
            self.score += 10
            self.sounds.play("dot")
        elif self.player in self.power_pellets:
            self.power_pellets.remove(self.player)
            self.score += 50
            self.power_time = POWER_SECONDS
            self.sounds.play("power")
        if not self.pellets and not self.power_pellets and self.state == "running":
            self.state = "level_clear"
            self.sounds.play("win")

    def choose_player_direction(self):
        for candidate in (self.requested, self.current):
            dx, dy = VECTORS[candidate]
            new_pos = (self.player[0] + dx, self.player[1] + dy)
            if is_floor(self.maze, new_pos):
                self.current = candidate
                self.player_target = new_pos
                return True
        return False

    def advance_player(self, dt):
        remainder = dt
        for _ in range(5):  # lida com atrasos eventuais sem travar a renderização
            if self.player_target == self.player and not self.choose_player_direction():
                return
            needed = (1.0 - self.player_progress) * self.player_interval
            taken = min(needed, remainder)
            self.player_progress += taken / self.player_interval
            remainder -= taken
            if self.player_progress < 1.0 - 1e-8:
                return
            self.player = self.player_target
            self.player_progress = 0.0
            self.consume_pellet()
            if self.state != "running" or remainder <= 1e-9:
                return

    def choose_ghost_direction(self, ghost, distances):
        options = list(walkable_neighbors(self.maze, ghost["pos"]))
        if not options:
            return
        forward = [(d, p) for d, p in options if d != OPPOSITE[ghost["direction"]]]
        if forward:
            options = forward
        if self.power_time > 0:
            direction, dest = max(options, key=lambda v: distances.get(v[1], 0) + random.random() * 2)
        elif random.random() < ghost["aggression"]:
            direction, dest = min(options, key=lambda v: distances.get(v[1], 999) + random.random() * 2)
        else:
            direction, dest = random.choice(options)
        ghost["direction"] = direction
        ghost["target"] = dest

    def advance_ghosts(self, dt):
        distances = distances_from(self.maze, self.player_target)
        for ghost in self.ghosts:
            if ghost["cooldown"] > 0:
                ghost["cooldown"] = max(0.0, ghost["cooldown"] - dt)
                continue
            remainder = dt
            interval = self.ghost_interval * (GHOST_FRIGHTENED_MULTIPLIER if self.power_time > 0 else 1.0)
            for _ in range(4):
                if ghost["target"] == ghost["pos"]:
                    self.choose_ghost_direction(ghost, distances)
                    if ghost["target"] == ghost["pos"]:
                        break
                needed = (1.0 - ghost["progress"]) * interval
                taken = min(needed, remainder)
                ghost["progress"] += taken / interval
                remainder -= taken
                if ghost["progress"] < 1.0 - 1e-8:
                    break
                ghost["pos"] = ghost["target"]
                ghost["progress"] = 0.0
                if remainder <= 1e-9:
                    break

    def player_float_pos(self):
        return interpolate(self.player, self.player_target, self.player_progress)

    @staticmethod
    def ghost_float_pos(ghost):
        return interpolate(ghost["pos"], ghost["target"], ghost["progress"])

    def check_collisions(self):
        if self.state != "running":
            return
        px, py = self.player_float_pos()
        for ghost in self.ghosts:
            if ghost["cooldown"] > 0:
                continue
            gx, gy = self.ghost_float_pos(ghost)
            if (px - gx) ** 2 + (py - gy) ** 2 > 0.55 ** 2:
                continue
            if self.power_time > 0:
                self.score += 200
                ghost["pos"] = ghost["target"] = ghost["spawn"]
                ghost["progress"] = 0.0
                ghost["cooldown"] = 1.0
                self.sounds.play("eat")
            elif self.invulnerable <= 0:
                self.lives -= 1
                self.link.send_lives(self.lives, force=True)
                if self.lives <= 0:
                    self.finish_game()
                else:
                    self.player = self.player_target = self.spawn
                    self.player_progress = 0.0
                    self.requested = self.current = "R"
                    self.invulnerable = 2.0
                    self.power_time = 0.0
                    for reset in self.ghosts:
                        reset["pos"] = reset["target"] = reset["spawn"]
                        reset["progress"] = 0.0
                        reset["cooldown"] = 0.7
                    self.sounds.play("hit")
                return

    def update(self, dt):
        direction, pressed = self.link.poll()
        if self.state == "running" and direction:
            self.requested = direction
        if pressed:
            self.handle_action()
        self.link.send_lives(self.lives)
        if self.state != "running":
            return
        self.power_time = max(0.0, self.power_time - dt)
        self.invulnerable = max(0.0, self.invulnerable - dt)
        self.advance_player(dt)
        if self.state != "running":
            return
        self.advance_ghosts(dt)
        self.check_collisions()

    def text(self, value, font, color, pos, center=False):
        surface = font.render(str(value), True, color)
        bounds = surface.get_rect(center=pos) if center else surface.get_rect(topleft=pos)
        self.screen.blit(surface, bounds)

    def draw_pacman(self, x, y, radius, direction, t):
        pygame.draw.circle(self.screen, YELLOW, (round(x), round(y)), radius)
        facing = {"R": 0, "L": math.pi, "U": -math.pi / 2, "D": math.pi / 2}[direction]
        opening = math.radians(7 + 27 * abs(math.sin(t * 12)))
        points = [(round(x), round(y))]
        for offset in (-opening, opening):
            points.append((round(x + (radius + 3) * math.cos(facing + offset)),
                           round(y + (radius + 3) * math.sin(facing + offset))))
        pygame.draw.polygon(self.screen, BOARD, points)

    def draw_ghost(self, x, y, color, t, frightened):
        ix, iy = round(x), round(y)
        color = (87, 112, 249) if frightened else color
        pygame.draw.circle(self.screen, color, (ix, iy - 3), 10)
        pygame.draw.rect(self.screen, color, (ix - 10, iy - 3, 20, 13))
        wave = 2 if math.sin(t * 10) > 0 else 0
        pygame.draw.polygon(self.screen, color,
                            [(ix - 10, iy + 10), (ix - 5, iy + 7 - wave),
                             (ix, iy + 10), (ix + 5, iy + 7 - wave), (ix + 10, iy + 10)])
        for offset in (-4, 4):
            pygame.draw.circle(self.screen, WHITE, (ix + offset, iy - 3), 4)
            pygame.draw.circle(self.screen, (19, 43, 99), (ix + offset + 1, iy - 2), 2)

    def draw_hud(self, t):
        pygame.draw.rect(self.screen, PANEL, (12, 9, WIDTH - 24, 90), border_radius=14)
        self.draw_pacman(36, 34, 13, "R", t)
        self.text("PAC-MAN", self.font_medium, YELLOW, (58, 17))
        self.text("DELUXE / ARDUINO", self.font_tiny, CYAN, (58, 46))
        self.text(f"{self.score:06d}", self.font_big, WHITE, (256, 11))
        self.text("PONTOS", self.font_tiny, MUTED, (258, 54))
        self.text(f"FASE {self.level}", self.font_small, CYAN, (393, 25))
        for i in range(3):
            x = 522 + i * 31
            color = YELLOW if i < self.lives else (57, 70, 102)
            pygame.draw.circle(self.screen, color, (x, 35), 10)
            if i < self.lives:
                pygame.draw.polygon(self.screen, PANEL,
                                    [(x, 35), (x + 12, 29), (x + 12, 41)])
        self.text("VIDAS", self.font_tiny, MUTED, (532, 54))
        x, y, w = 13, 104, WIDTH - 26
        pygame.draw.rect(self.screen, (37, 48, 82), (x, y, w, 4), border_radius=2)
        completed = self.pellets_start - len(self.pellets) - len(self.power_pellets)
        progress = max(0, min(w, int(w * completed / self.pellets_start)))
        if progress:
            pygame.draw.rect(self.screen, GREEN, (x, y, progress, 4), border_radius=2)

    def draw_board(self, t):
        self.screen.blit(self.static_layer, (0, BOARD_Y))
        for x, y in self.pellets:
            pos = (x * CELL + CELL // 2, BOARD_Y + y * CELL + CELL // 2)
            pygame.draw.circle(self.screen, (251, 219, 171), pos, 3)
        for x, y in self.power_pellets:
            pos = (x * CELL + CELL // 2, BOARD_Y + y * CELL + CELL // 2)
            size = round(6 + 2 * abs(math.sin(t * 4)))
            pygame.draw.circle(self.screen, PINK, pos, size)
            pygame.draw.circle(self.screen, WHITE, pos, 2)
        px, py = self.player_float_pos()
        if self.invulnerable <= 0 or int(t * 8) % 2 == 0:
            self.draw_pacman(px * CELL + CELL // 2, BOARD_Y + py * CELL + CELL // 2,
                             11, self.current, t)
        for ghost in self.ghosts:
            gx, gy = self.ghost_float_pos(ghost)
            self.draw_ghost(gx * CELL + CELL // 2, BOARD_Y + gy * CELL + CELL // 2,
                            ghost["color"], t, self.power_time > 0)
        if self.power_time > 0 and self.state == "running":
            self.text(f"PODER {self.power_time:0.1f}s", self.font_small, PINK,
                      (WIDTH // 2, BOARD_Y + 14), center=True)

    def draw_footer(self):
        pygame.draw.rect(self.screen, PANEL, (12, BOARD_Y + BOARD_H + 10, WIDTH - 24, 61), border_radius=11)
        link_text = f"ARDUINO {self.link.port_name}" if self.link.connection else "TECLADO / USB OFF"
        self.text(f"{self.player_name[:14]}  |  {link_text}", self.font_tiny,
                  GREEN if self.link.connection else MUTED, (24, BOARD_Y + BOARD_H + 19))
        self.text(f"RECORDE: {max(self.ranking.record, self.score)}", self.font_tiny, CYAN,
                  (420, BOARD_Y + BOARD_H + 19))
        self.text("JOYSTICK / WASD / SETAS: MOVER   |   ESPACO: PAUSA   |   TAB: RANKING   |   M: SOM",
                  self.font_tiny, MUTED, (22, BOARD_Y + BOARD_H + 46))

    def modal_base(self, top=174, height=414):
        veil = pygame.Surface((WIDTH, BOARD_H), pygame.SRCALPHA)
        veil.fill((2, 7, 22, 213))
        self.screen.blit(veil, (0, BOARD_Y))
        box = pygame.Rect(52, top, WIDTH - 104, height)
        pygame.draw.rect(self.screen, PANEL, box, border_radius=21)
        pygame.draw.rect(self.screen, WALL_EDGE, box, 2, border_radius=21)
        return box

    def draw_menu(self, t):
        box = self.modal_base(171, 418)
        self.draw_pacman(WIDTH // 2 - 98, 225, 16, "R", t)
        self.text("PAC-MAN DELUXE", self.font_big, YELLOW, (WIDTH // 2 + 12, 225), center=True)
        self.text("JOYSTICK + 3 LEDS + RANKING", self.font_small, CYAN,
                  (WIDTH // 2, 263), center=True)
        self.text("DIGITE O NOME DO JOGADOR", self.font_small, WHITE, (96, 311))
        input_rect = pygame.Rect(92, 336, WIDTH - 184, 50)
        pygame.draw.rect(self.screen, PANEL_LIGHT, input_rect, border_radius=9)
        pygame.draw.rect(self.screen, CYAN, input_rect, 2, border_radius=9)
        label = self.name if self.name else "JOGADOR"
        self.text(label, self.font_medium, WHITE if self.name else MUTED, (105, 347))
        if int(t * 2) % 2 == 0 and len(self.name) < 14:
            xpos = min(input_rect.right - 12, 105 + self.font_medium.size(self.name)[0] + 3)
            pygame.draw.line(self.screen, CYAN, (xpos, 347), (xpos, 368), 2)
        self.text("ENTER / BOTAO DO JOYSTICK: COMECAR", self.font_small, GREEN,
                  (WIDTH // 2, 416), center=True)
        self.text("TAB: VER TOP 10    |    F5: RECONECTAR ARDUINO", self.font_tiny,
                  MUTED, (WIDTH // 2, 453), center=True)
        self.text(f"MELHOR PONTUACAO: {self.ranking.record}", self.font_medium, YELLOW,
                  (WIDTH // 2, 512), center=True)
        self.text("A cada derrota, a pontuacao entra no ranking local.", self.font_tiny,
                  MUTED, (WIDTH // 2, 545), center=True)

    def draw_ranking(self):
        self.modal_base(156, 460)
        self.text("TOP 10  |  RANKING LOCAL", self.font_big, YELLOW,
                  (WIDTH // 2, 196), center=True)
        self.text("POS    NOME                  PONTOS   FASE", self.font_small,
                  CYAN, (101, 238))
        if not self.ranking.entries:
            self.text("Ainda nao ha resultados. Jogue uma partida!", self.font_body, WHITE,
                      (WIDTH // 2, 391), center=True)
        else:
            for i, entry in enumerate(self.ranking.entries):
                yy = 274 + i * 28
                if entry["id"] == self.last_rank_id:
                    pygame.draw.rect(self.screen, (33, 56, 92),
                                     (91, yy - 3, WIDTH - 182, 27), border_radius=6)
                tone = YELLOW if i == 0 else (GREEN if entry["id"] == self.last_rank_id else WHITE)
                self.text(f"{i+1:02d}", self.font_small, tone, (104, yy))
                self.text(entry["name"][:14], self.font_small, tone, (166, yy))
                self.text(str(entry["score"]), self.font_small, tone, (414, yy))
                self.text(str(entry["level"]), self.font_small, tone, (516, yy))
        self.text("TAB / ENTER / BOTAO: VOLTAR", self.font_small, CYAN,
                  (WIDTH // 2, 585), center=True)

    def draw_game_over(self):
        self.modal_base(166, 432)
        self.text("FIM DE JOGO", self.font_title, RED, (WIDTH // 2, 219), center=True)
        self.text(self.player_name, self.font_medium, WHITE, (WIDTH // 2, 279), center=True)
        self.text(f"{self.score:06d} PONTOS", self.font_big, YELLOW,
                  (WIDTH // 2, 326), center=True)
        self.text(f"FASE ALCANCADA: {self.level}", self.font_small, CYAN,
                  (WIDTH // 2, 366), center=True)
        entered = any(e["id"] == self.last_rank_id for e in self.ranking.entries)
        self.text("ENTROU NO TOP 10!" if entered else "BOA PARTIDA!", self.font_medium,
                  GREEN if entered else MUTED, (WIDTH // 2, 417), center=True)
        self.text("TAB: RANKING", self.font_small, CYAN, (WIDTH // 2, 482), center=True)
        self.text("ENTER / BOTAO: NOVO JOGO E DIGITAR NOME", self.font_small,
                  WHITE, (WIDTH // 2, 528), center=True)

    def draw_pause_or_level(self):
        self.modal_base(218, 300)
        title = "FASE CONCLUIDA!" if self.state == "level_clear" else "PAUSADO"
        sub = f"PONTUACAO: {self.score}" if self.state == "level_clear" else "Partida congelada"
        self.text(title, self.font_big, YELLOW, (WIDTH // 2, 299), center=True)
        self.text(sub, self.font_medium, WHITE, (WIDTH // 2, 363), center=True)
        self.text("ENTER / BOTAO: CONTINUAR", self.font_small, CYAN,
                  (WIDTH // 2, 435), center=True)

    def draw(self):
        t = pygame.time.get_ticks() / 1000
        self.screen.fill(BG)
        self.draw_hud(t)
        self.draw_board(t)
        self.draw_footer()
        if self.state == "menu":
            self.draw_menu(t)
        elif self.state == "ranking":
            self.draw_ranking()
        elif self.state == "game_over":
            self.draw_game_over()
        elif self.state in ("paused", "level_clear"):
            self.draw_pause_or_level()
        pygame.display.flip()

    def handle_key(self, event):
        directions = {
            pygame.K_UP: "U", pygame.K_w: "U", pygame.K_DOWN: "D", pygame.K_s: "D",
            pygame.K_LEFT: "L", pygame.K_a: "L", pygame.K_RIGHT: "R", pygame.K_d: "R",
        }
        if event.key == pygame.K_ESCAPE:
            if self.state == "ranking":
                self.handle_action()
            else:
                self.running = False
        elif event.key == pygame.K_F5:
            self.link.connect()
            self.link.send_lives(self.lives, force=True)
        elif event.key == pygame.K_TAB:
            self.toggle_ranking()
        elif self.state == "menu":
            if event.key == pygame.K_BACKSPACE:
                self.name = self.name[:-1]
            elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                self.handle_action()
        elif self.state == "ranking":
            if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE):
                self.handle_action()
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_SPACE, pygame.K_p):
            self.handle_action()
        elif self.state == "running" and event.key in directions:
            self.requested = directions[event.key]
        elif self.state == "running" and event.key == pygame.K_m:
            self.sounds.toggle()
        elif event.key == pygame.K_r:
            self.link.connect()
            self.link.send_lives(self.lives, force=True)

    def run(self):
        try:
            while self.running:
                dt = min(self.clock.tick(FPS) / 1000.0, 0.05)
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False
                    elif event.type == pygame.TEXTINPUT:
                        self.input_name(event.text)
                    elif event.type == pygame.KEYDOWN:
                        self.handle_key(event)
                self.update(dt)
                self.draw()
        finally:
            self.link.close()
            pygame.quit()


def main():
    parser = argparse.ArgumentParser(description="PAC-MAN DELUXE com joystick Arduino e ranking")
    parser.add_argument("--port", help="Porta USB (ex.: COM3)")
    parser.add_argument("--list-ports", action="store_true", help="Exibe portas USB seriais")
    parser.add_argument("--scores", type=Path, default=SCORE_FILE,
                        help="Caminho para salvar o ranking (padrão: ranking.json na pasta do jogo)")
    parser.add_argument("--no-audio", action="store_true", help="Desabilita efeitos sonoros")
    args = parser.parse_args()
    if args.list_ports:
        if list_ports is None:
            print("PySerial não instalado. Instale com: python -m pip install pyserial")
        else:
            for port in ArduinoLink.available_ports():
                print(f"{port.device} | {port.description}")
        return
    PacmanGame(ArduinoLink(args.port), rankings_path=args.scores, no_audio=args.no_audio).run()


if __name__ == "__main__":
    main()
