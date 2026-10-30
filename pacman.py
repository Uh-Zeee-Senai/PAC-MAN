"""PAC-MAN DELUXE v5 | Tela cheia + pagina dedicada ao ranking SQLite.

Python 3.14: pygame-ce (import pygame) e pyserial.
Firmware anterior continua compativel; recomendado o novo sketch de 50 Hz.
"""
from __future__ import annotations

import argparse
import math
import random
import time
from array import array
from collections import deque
from pathlib import Path

import pygame

from ranking_db import Ranking, normalize_name, DATABASE_FILE

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

COLS, ROWS = 23, 19
CELL = 28
BOARD_X, BOARD_Y = 65, 164
BOARD_W, BOARD_H = COLS * CELL, ROWS * CELL
WIDTH, HEIGHT = 1280, 800
FPS = 60
BAUD = 115200
SCORE_FILE = DATABASE_FILE

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
# Configuracao inicial do controle. F7 = mais sensivel; F8 = menos sensivel.
JOY_ENTER = 190      # exigencia inicial para ativar um eixo (0..512)
JOY_MIN_ENTER = 110
JOY_MAX_ENTER = 310
JOY_SWITCH_RATIO = 1.22  # reduz oscilacao quando o joystick fica na diagonal
JOY_TURN_BUFFER = 0.68   # tempo para realizar uma curva indicada pouco antes


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


class JoystickFilter:
    """Interpreta analogico 2D com zona morta, histerese e eixo dominante.

    O valor de retorno None significa centro/neutralidade, e nao para o Pac-Man.
    Pode ser calibrado sem modificar a montagem: pressione C com joystick solto.
    """

    def __init__(self, threshold: int = JOY_ENTER):
        self.threshold = max(JOY_MIN_ENTER, min(JOY_MAX_ENTER, threshold))
        self.center_x = 512
        self.center_y = 512
        self.last_raw: tuple[int, int] | None = None
        self.active: str | None = None

    @property
    def release(self) -> int:
        return max(60, round(self.threshold * 0.58))

    def change_sensitivity(self, amount: int):
        self.threshold = max(JOY_MIN_ENTER, min(JOY_MAX_ENTER, self.threshold + amount))
        self.active = None

    def calibrate(self) -> bool:
        if self.last_raw is None:
            return False
        self.center_x, self.center_y = self.last_raw
        self.active = None
        return True

    def read(self, x: int, y: int) -> str | None:
        self.last_raw = (x, y)
        dx = (x - self.center_x) * (-1 if INVERTER_X else 1)
        dy = (y - self.center_y) * (-1 if INVERTER_Y else 1)
        sx, sy = abs(dx), abs(dy)
        if max(sx, sy) < self.release:
            self.active = None
            return None

        dominant = 'x' if sx >= sy else 'y'
        if self.active is not None:
            axis = 'x' if self.active in ('L', 'R') else 'y'
            this_strength = sx if axis == 'x' else sy
            other_strength = sy if axis == 'x' else sx
            this_sign = ('R' if dx >= 0 else 'L') if axis == 'x' else ('D' if dy >= 0 else 'U')
            # Manter a direcao durante pequenas variacoes na diagonal.
            if this_strength >= self.release and this_sign == self.active:
                if other_strength < max(self.threshold, this_strength * JOY_SWITCH_RATIO):
                    return self.active
            # Troca intencional: requer ativacao real do outro eixo ou do oposto.
        if (sx if dominant == 'x' else sy) < self.threshold:
            self.active = None
            return None
        self.active = ('R' if dx >= 0 else 'L') if dominant == 'x' else ('D' if dy >= 0 else 'U')
        return self.active


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
        self.joystick = JoystickFilter()
        self.last_packet_at = 0.0
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
                self.joystick.active = None
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
        self.joystick.active = None

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
                # Preserva pulsos curtos mesmo quando a ultima amostra ja esta neutra.
                decoded_direction = self.joystick.read(x, y)
                if decoded_direction is not None:
                    direction = decoded_direction
                now = time.monotonic()
                self.last_packet_at = now
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
        pygame.display.set_caption("PAC-MAN DELUXE v5 | Tela cheia + Ranking")
        self.fullscreen = False
        self.display = None
        self.configure_display(fullscreen=True)
        self.screen = pygame.Surface((WIDTH, HEIGHT)).convert()
        self.viewport = pygame.Rect(0, 0, WIDTH, HEIGHT)
        self.ui_buttons = {}
        self.clock = pygame.time.Clock()
        self.font_hero = pygame.font.SysFont("arial", 94, bold=True)
        self.font_title = pygame.font.SysFont("arial", 45, bold=True)
        self.font_big = pygame.font.SysFont("arial", 33, bold=True)
        self.font_medium = pygame.font.SysFont("arial", 24, bold=True)
        self.font_body = pygame.font.SysFont("arial", 20)
        self.font_small = pygame.font.SysFont("arial", 17, bold=True)
        self.font_tiny = pygame.font.SysFont("consolas", 15)
        self.font_number = pygame.font.SysFont("consolas", 36, bold=True)

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
        self.turn_buffer_age = 0.0
        self.player = (1, 1)
        self.player_target = self.player
        self.player_progress = 0.0
        self.ghosts = []
        self.maze = []
        self.static_layer = None
        self.stars = [(random.Random(i * 143).randrange(WIDTH),
                       random.Random(i * 987).randrange(HEIGHT), i % 3 + 1)
                      for i in range(53)]
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
        self.turn_buffer_age = 0.0
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
        self.static_layer = pygame.Surface((BOARD_W, BOARD_H)).convert()
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
        if self.state == "ranking":
            self.handle_action()
        elif self.state in ("menu", "game_over", "paused", "running"):
            # Abrir no meio da partida nao deve deixar os fantasmas em movimento.
            self.previous_screen = "paused" if self.state == "running" else self.state
            self.ranking.load()
            self.state = "ranking"
            pygame.key.stop_text_input()

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

    def request_direction(self, direction: str):
        if direction in VECTORS:
            self.requested = direction
            self.turn_buffer_age = 0.0

    def reverse_in_place(self):
        """Inverte imediatamente e sem salto durante o percurso entre celulas."""
        if (self.player != self.player_target and self.player_progress > 0
                and self.requested == OPPOSITE[self.current]):
            self.player, self.player_target = self.player_target, self.player
            self.player_progress = 1.0 - self.player_progress
            self.current = self.requested

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
        self.reverse_in_place()
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
                    self.turn_buffer_age = 0.0
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
        if self.state == "running":
            if direction:
                self.request_direction(direction)
            else:
                self.turn_buffer_age += dt
                # Uma puxada rapida funciona na proxima esquina, mas nao minutos depois.
                if self.turn_buffer_age > JOY_TURN_BUFFER:
                    self.requested = self.current
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

    def configure_display(self, fullscreen: bool):
        """Renderiza sempre no tamanho lógico 1280x800 e redimensiona ao monitor.

        Evita coordenadas quebradas em monitores 1366x768, 1920x1080 e 4K.
        """
        try:
            if fullscreen:
                self.display = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            else:
                sizes = pygame.display.get_desktop_sizes()
                sw, sh = sizes[0] if sizes else (1280, 800)
                self.display = pygame.display.set_mode(
                    (min(1280, max(640, sw - 100)), min(800, max(480, sh - 100))),
                    pygame.RESIZABLE)
            self.fullscreen = fullscreen
        except pygame.error as exc:
            print(f"Modo de vídeo solicitado indisponível: {exc}; abrindo janela.")
            self.display = pygame.display.set_mode((1100, 690), pygame.RESIZABLE)
            self.fullscreen = False

    def toggle_fullscreen(self):
        self.configure_display(not self.fullscreen)

    def to_logical_pos(self, physical_pos):
        ww, wh = self.display.get_size()
        scale = min(ww / WIDTH, wh / HEIGHT)
        ox = round((ww - WIDTH * scale) / 2)
        oy = round((wh - HEIGHT * scale) / 2)
        mx, my = physical_pos
        if not (ox <= mx < ox + WIDTH * scale and oy <= my < oy + HEIGHT * scale):
            return None
        return (round((mx - ox) / scale), round((my - oy) / scale))

    def present(self):
        ww, wh = self.display.get_size()
        if ww <= 0 or wh <= 0:
            return
        scale = min(ww / WIDTH, wh / HEIGHT)
        tw, th = max(1, round(WIDTH * scale)), max(1, round(HEIGHT * scale))
        ox, oy = (ww - tw) // 2, (wh - th) // 2
        self.display.fill((2, 5, 13))
        if (tw, th) == (WIDTH, HEIGHT):
            self.display.blit(self.screen, (ox, oy))
        else:
            # Nearest-neighbor: bordas nítidas e menos custo CPU que smoothscale.
            self.display.blit(pygame.transform.scale(self.screen, (tw, th)), (ox, oy))
        pygame.display.flip()

    def text(self, value, font, color, pos, center=False, right=False):
        surface = font.render(str(value), True, color)
        if center:
            bounds = surface.get_rect(center=pos)
        elif right:
            bounds = surface.get_rect(topright=pos)
        else:
            bounds = surface.get_rect(topleft=pos)
        self.screen.blit(surface, bounds)
        return bounds

    def card(self, rect, fill=PANEL, stroke=(39, 67, 111), radius=17):
        rect = pygame.Rect(rect)
        pygame.draw.rect(self.screen, fill, rect, border_radius=radius)
        pygame.draw.rect(self.screen, stroke, rect, 1, border_radius=radius)
        return rect

    def button(self, key, label, rect, variant="primary", font=None):
        rect = pygame.Rect(rect)
        self.ui_buttons[key] = rect
        if variant == "primary":
            fill, stroke, color = (244, 195, 51), (255, 233, 122), (18, 28, 48)
        elif variant == "danger":
            fill, stroke, color = (91, 31, 52), (226, 99, 119), WHITE
        else:
            fill, stroke, color = (24, 44, 78), (67, 117, 176), WHITE
        # O efeito hover usa coordenadas corrigidas para letterbox/fullscreen.
        try:
            point = self.to_logical_pos(pygame.mouse.get_pos())
        except pygame.error:
            point = None
        hovered = point is not None and rect.collidepoint(point)
        if hovered:
            fill = tuple(min(255, c + 22) for c in fill)
        self.card(rect, fill, stroke, 13)
        self.text(label, font or self.font_small, color, rect.center, center=True)

    def draw_background(self):
        self.screen.fill(BG)
        pygame.draw.rect(self.screen, (11, 20, 44), (0, 0, WIDTH, 120))
        pygame.draw.line(self.screen, (30, 52, 88), (0, 120), (WIDTH, 120), 1)
        for x, y, r in self.stars:
            pygame.draw.circle(self.screen, (27, 45, 79), (x, y), r)
        # Deixa a barra inferior com limites claros.
        pygame.draw.line(self.screen, (30, 52, 88), (0, 785), (WIDTH, 785), 1)

    def draw_header(self, heading="PAC-MAN", subtitle="DELUXE • ARDUINO EDITION"):
        self.draw_pacman(75, 59, 23, "R", pygame.time.get_ticks() / 1000)
        self.text(heading, self.font_big, YELLOW, (112, 22))
        self.text(subtitle, self.font_tiny, CYAN, (114, 68))
        status = f"●  ARDUINO {self.link.port_name}" if self.link.connection else "●  MODO TECLADO"
        self.card((975, 37, 231, 43), (18, 36, 63), (37, 83, 113), 22)
        self.text(status, self.font_tiny, GREEN if self.link.connection else MUTED,
                  (1090, 59), center=True)

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

    def draw_board(self, t):
        # Um painel físico no layout, sem ampliar as células do labirinto.
        self.card((BOARD_X - 10, BOARD_Y - 10, BOARD_W + 20, BOARD_H + 20),
                  (17, 30, 59), (51, 109, 211), 15)
        self.screen.blit(self.static_layer, (BOARD_X, BOARD_Y))
        for x, y in self.pellets:
            pos = (BOARD_X + x * CELL + CELL // 2, BOARD_Y + y * CELL + CELL // 2)
            pygame.draw.circle(self.screen, (251, 219, 171), pos, 3)
        for x, y in self.power_pellets:
            pos = (BOARD_X + x * CELL + CELL // 2, BOARD_Y + y * CELL + CELL // 2)
            size = round(6 + 2 * abs(math.sin(t * 4)))
            pygame.draw.circle(self.screen, PINK, pos, size)
            pygame.draw.circle(self.screen, WHITE, pos, 2)
        px, py = self.player_float_pos()
        if self.invulnerable <= 0 or int(t * 8) % 2 == 0:
            self.draw_pacman(BOARD_X + px * CELL + CELL // 2,
                             BOARD_Y + py * CELL + CELL // 2, 11, self.current, t)
        for ghost in self.ghosts:
            gx, gy = self.ghost_float_pos(ghost)
            self.draw_ghost(BOARD_X + gx * CELL + CELL // 2,
                            BOARD_Y + gy * CELL + CELL // 2,
                            ghost["color"], t, self.power_time > 0)
        if self.power_time > 0 and self.state == "running":
            self.card((BOARD_X + 250, BOARD_Y + 8, 145, 31), (18, 28, 75), (92, 98, 240), 12)
            self.text(f"PODER {self.power_time:0.1f}s", self.font_small, PINK,
                      (BOARD_X + 323, BOARD_Y + 23), center=True)

    def draw_sidebar(self):
        sx, sw = 734, 482
        self.card((sx, BOARD_Y - 10, sw, 156))
        self.text("PONTUAÇÃO", self.font_tiny, MUTED, (sx + 24, 172))
        self.text(f"{self.score:07d}", self.font_number, WHITE, (sx + 24, 201))
        self.text("RECORDE", self.font_tiny, MUTED, (sx + 295, 172))
        self.text(f"{max(self.ranking.record, self.score):07d}", self.font_medium,
                  YELLOW, (sx + 295, 208))
        pygame.draw.line(self.screen, (42, 67, 106), (sx + 24, 259), (sx + sw - 24, 259), 1)
        self.text(f"FASE {self.level:02d}", self.font_small, CYAN, (sx + 25, 271))
        for i in range(3):
            cx = sx + 337 + i * 38
            color = YELLOW if i < self.lives else (57, 70, 102)
            pygame.draw.circle(self.screen, color, (cx, 281), 12)
            if i < self.lives:
                pygame.draw.polygon(self.screen, PANEL, ((cx, 281), (cx + 14, 273), (cx + 14, 289)))
        self.card((sx, 324, sw, 157))
        self.text("PROGRESSO DO LABIRINTO", self.font_small, WHITE, (sx + 25, 343))
        done = self.pellets_start - len(self.pellets) - len(self.power_pellets)
        percent = max(0, min(100, round(done * 100 / max(1, self.pellets_start))))
        self.text(f"{percent}%", self.font_medium, GREEN, (sx + 423, 341), right=True)
        pygame.draw.rect(self.screen, (39, 53, 77), (sx + 25, 391, 430, 16), border_radius=8)
        if percent:
            pygame.draw.rect(self.screen, GREEN, (sx + 25, 391, round(430 * percent / 100), 16), border_radius=8)
        self.text(f"{done} de {self.pellets_start} pontos coletados", self.font_tiny,
                  MUTED, (sx + 25, 429))
        self.card((sx, 499, sw, 195))
        self.text("TOP 3  •  RANKING LOCAL", self.font_small, CYAN, (sx + 25, 520))
        if not self.ranking.entries:
            self.text("Ainda não há partidas salvas.", self.font_body, MUTED,
                      (sx + 25, 564))
        else:
            for i, entry in enumerate(self.ranking.entries[:3]):
                y = 556 + i * 35
                self.text(f"{i+1:02d}", self.font_small, YELLOW if i == 0 else MUTED,
                          (sx + 25, y))
                self.text(entry['name'][:14], self.font_small, WHITE, (sx + 80, y))
                self.text(f"{entry['score']:,}".replace(',', '.'), self.font_small,
                          CYAN, (sx + 445, y), right=True)
        self.button("ranking", "VER RANKING COMPLETO  [TAB]", (sx + 92, 652, 312, 34), "secondary", self.font_tiny)

    def draw_footer(self):
        self.card((55, 719, 1161, 58), (12, 23, 47), (34, 57, 96), 13)
        self.text(f"{self.player_name[:14].upper()}  •  {self.level:02d}ª FASE",
                  self.font_tiny, CYAN, (77, 740))
        self.text("SETAS/WASD mover   •   C calibrar   •   F6 salvar   •   F11 tela cheia",
                  self.font_tiny, MUTED, (1220, 740), right=True)

    def draw_game(self, t):
        self.draw_header()
        self.draw_board(t)
        self.draw_sidebar()
        self.draw_footer()
        self.button("fullscreen", "TELA CHEIA  [F11]" if not self.fullscreen else "JANELA  [F11]",
                    (748, 95, 195, 36), "secondary", self.font_tiny)

    def draw_menu(self, t):
        self.draw_header()
        self.card((73, 148, 687, 563), (13, 24, 52), (39, 84, 145), 22)
        self.text("JOGUE, MARQUE", self.font_medium, CYAN, (105, 181))
        self.text("PAC-MAN", self.font_hero, YELLOW, (99, 210))
        self.text("DELUXE", self.font_title, WHITE, (104, 318))
        self.text("Arcade clássico com controle Arduino e ranking SQL.",
                  self.font_body, MUTED, (105, 381))
        self.text("NOME DO JOGADOR", self.font_small, CYAN, (105, 440))
        rect = self.card((104, 471, 625, 61), (22, 40, 76), (83, 151, 210), 10)
        self.text(self.name if self.name else "Digite seu nome aqui...", self.font_medium,
                  WHITE if self.name else MUTED, (123, 484))
        if int(t * 2) % 2 == 0 and len(self.name) < 14:
            tx = 123 + self.font_medium.size(self.name)[0]
            pygame.draw.line(self.screen, CYAN, (tx, 484), (tx, 512), 2)
        self.button("start", "▶  COMEÇAR JOGO", (104, 562, 297, 64), font=self.font_medium)
        self.button("ranking", "★  VER RANKING", (422, 562, 307, 64), "secondary", self.font_medium)
        self.text("ENTER OU BOTÃO DO JOYSTICK PARA COMEÇAR", self.font_tiny,
                  MUTED, (107, 656))
        self.card((785, 148, 426, 563), (14, 27, 55), (39, 84, 145), 22)
        self.text("HALL DA FAMA", self.font_medium, YELLOW, (818, 183))
        pygame.draw.line(self.screen, (48, 81, 120), (818, 234), (1180, 234), 1)
        if self.ranking.entries:
            for i, entry in enumerate(self.ranking.entries[:5]):
                yy = 258 + i * 57
                self.text(f"{i+1:02d}", self.font_medium, YELLOW if i == 0 else CYAN, (819, yy))
                self.text(entry["name"], self.font_small, WHITE, (871, yy + 2))
                self.text(str(entry["score"]), self.font_small, GREEN,
                          (1176, yy + 2), right=True)
                pygame.draw.line(self.screen, (37, 58, 93), (817, yy + 42), (1181, yy + 42), 1)
        else:
            self.text("O primeiro recorde pode ser seu!", self.font_body, MUTED, (818, 277))
        self.text(f"RECORDE ATUAL: {self.ranking.record:,}".replace(',', '.'),
                  self.font_small, CYAN, (817, 592))
        self.text("Top 10 fica salvo no ranking.db", self.font_tiny, MUTED, (817, 638))
        self.button("fullscreen", "F11  •  TELA CHEIA / JANELA", (790, 666, 414, 35), "secondary", self.font_tiny)
        self.text("ESC: SAIR   •   TAB: RANKING", self.font_tiny, MUTED, (74, 738))

    def draw_ranking(self):
        self.draw_header("RANKING", "HALL DA FAMA • BANCO DE DADOS SQLITE")
        self.text("OS 10 MELHORES JOGADORES", self.font_title, WHITE, (88, 151))
        self.text("Pontuações gravadas automaticamente ao finalizar uma partida.",
                  self.font_body, MUTED, (89, 211))
        self.card((83, 259, 1114, 454), (14, 26, 55), (44, 78, 132), 17)
        self.card((100, 276, 1080, 47), (27, 47, 82), (49, 86, 137), 10)
        headers = [("POS.", 137), ("JOGADOR", 227), ("PONTOS", 782), ("FASE", 940), ("DATA / HORA", 1011)]
        for label, x in headers:
            self.text(label, self.font_tiny, CYAN, (x, 291))
        if not self.ranking.entries:
            self.text("SEM PONTUAÇÕES AINDA", self.font_big, MUTED, (640, 441), center=True)
            self.text("Inicie uma partida e pressione F6 para salvar um resultado.",
                      self.font_body, MUTED, (640, 493), center=True)
        else:
            for i, entry in enumerate(self.ranking.entries):
                y = 333 + i * 36
                if i % 2 == 0:
                    pygame.draw.rect(self.screen, (20, 37, 70), (100, y - 2, 1080, 35), border_radius=7)
                if entry['id'] == self.last_rank_id:
                    pygame.draw.rect(self.screen, (34, 77, 94), (100, y - 2, 1080, 35), border_radius=7)
                tone = YELLOW if i == 0 else (GREEN if i < 3 else WHITE)
                self.text(f"#{i+1:02d}", self.font_small, tone, (137, y + 3))
                self.text(entry['name'][:14], self.font_small, WHITE, (227, y + 3))
                self.text(f"{entry['score']:,}".replace(',', '.'), self.font_small, tone, (782, y + 3))
                self.text(f"{entry['level']:02d}", self.font_small, WHITE, (944, y + 3))
                when = str(entry['date'])[:16].replace('-', '/')
                self.text(when, self.font_tiny, MUTED, (1011, y + 4))
        self.button("back", "← VOLTAR", (88, 725, 220, 53), "secondary", self.font_medium)
        self.text("TAB / ESC / ENTER / BOTÃO: VOLTAR", self.font_tiny, MUTED,
                  (1210, 742), right=True)

    def draw_game_over(self):
        self.draw_header("FIM DE JOGO", "SUA PARTIDA FOI FINALIZADA")
        self.card((208, 171, 864, 507), (16, 29, 60), (66, 105, 161), 25)
        self.text("RESULTADO DA PARTIDA", self.font_medium, MUTED, (640, 218), center=True)
        self.text(self.player_name, self.font_title, WHITE, (640, 271), center=True)
        self.text(f"{self.score:07d}", self.font_hero, YELLOW, (640, 370), center=True)
        self.text(f"PONTOS  •  FASE {self.level}", self.font_medium, CYAN, (640, 439), center=True)
        entered = any(e['id'] == self.last_rank_id for e in self.ranking.entries)
        message = ("ERRO AO SALVAR NO BANCO" if self.ranking.last_save_ok is False else
                   "VOCÊ ENTROU NO TOP 10!" if entered else "PARTIDA SALVA NO SQLITE")
        self.text(message, self.font_small,
                  RED if self.ranking.last_save_ok is False else GREEN, (640, 499), center=True)
        self.button("new", "↻  NOVO JOGO", (267, 568, 343, 66), font=self.font_medium)
        self.button("ranking", "★  VER RANKING", (633, 568, 366, 66), "secondary", self.font_medium)
        self.text("ENTER OU BOTÃO: NOVO JOGO  •  TAB: RANKING", self.font_tiny,
                  MUTED, (640, 732), center=True)

    def draw_pause_or_level(self):
        # O jogo fica visível ao fundo; modal é apenas informativo.
        veil = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        veil.fill((3, 9, 21, 185))
        self.screen.blit(veil, (0, 0))
        self.card((321, 232, 638, 338), (17, 34, 66), (80, 143, 224), 24)
        title = "FASE CONCLUÍDA!" if self.state == "level_clear" else "JOGO PAUSADO"
        desc = (f"Você fez {self.score} pontos. Próxima fase pronta!"
                if self.state == "level_clear" else "Respire, o jogo está congelado.")
        self.text(title, self.font_title, YELLOW, (640, 302), center=True)
        self.text(desc, self.font_body, WHITE, (640, 377), center=True)
        self.button("continue", "▶  CONTINUAR", (399, 434, 277, 66), font=self.font_medium)
        self.button("ranking", "★  RANKING", (697, 434, 198, 66), "secondary", self.font_medium)
        self.text("ENTER OU BOTÃO: CONTINUAR   •   TAB: RANKING", self.font_tiny,
                  MUTED, (640, 532), center=True)

    def draw(self):
        t = pygame.time.get_ticks() / 1000
        self.ui_buttons = {}
        self.draw_background()
        if self.state == "menu":
            self.draw_menu(t)
        elif self.state == "ranking":
            self.draw_ranking()
        elif self.state == "game_over":
            self.draw_game_over()
        else:
            self.draw_game(t)
            if self.state in ("paused", "level_clear"):
                self.draw_pause_or_level()
        self.present()

    def handle_click(self, position):
        # Aceita coordenada física e converte para resolução lógica.
        point = self.to_logical_pos(position)
        if point is None:
            return
        for key, rect in list(self.ui_buttons.items()):
            if not rect.collidepoint(point):
                continue
            if key == "fullscreen":
                self.toggle_fullscreen()
            elif key == "ranking":
                self.toggle_ranking()
            elif key in ("start", "continue", "back", "new"):
                if key == "new":
                    self.open_menu()
                else:
                    self.handle_action()
            return

    def handle_key(self, event):
        directions = {
            pygame.K_UP: "U", pygame.K_w: "U", pygame.K_DOWN: "D", pygame.K_s: "D",
            pygame.K_LEFT: "L", pygame.K_a: "L", pygame.K_RIGHT: "R", pygame.K_d: "R",
        }
        if event.key == pygame.K_F11:
            self.toggle_fullscreen()
        elif event.key == pygame.K_ESCAPE:
            if self.state == "ranking":
                self.handle_action()
            elif self.state == "running":
                self.state = "paused"
            elif self.state == "paused":
                self.state = "running"
            elif self.state == "game_over":
                self.open_menu()
            elif self.state == "menu":
                self.running = False
        elif event.key == pygame.K_F6 and self.state in ("running", "paused"):
            self.lives = 0
            self.link.send_lives(0, force=True)
            self.finish_game()
        elif event.key == pygame.K_F5:
            self.link.connect()
            self.link.send_lives(self.lives, force=True)
        elif event.key == pygame.K_c and self.state in ("running", "paused"):
            joystick = getattr(self.link, "joystick", None)
            if joystick is not None:
                if joystick.calibrate():
                    self.requested = self.current
                    self.turn_buffer_age = JOY_TURN_BUFFER + 1
                    print(f"Joystick calibrado: X={joystick.center_x}, Y={joystick.center_y}")
                else:
                    print("Calibração indisponível: aguarde leituras do joystick.")
        elif event.key in (pygame.K_F7, pygame.K_F8):
            joystick = getattr(self.link, "joystick", None)
            if joystick is not None:
                joystick.change_sensitivity(-20 if event.key == pygame.K_F7 else 20)
                print(f"Joystick: limiar {joystick.threshold}")
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
            self.request_direction(directions[event.key])
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
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        self.handle_click(event.pos)
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
    parser.add_argument("--scores", "--db", type=Path, default=SCORE_FILE,
                        help="Arquivo SQLite (padrao: ranking.db na pasta do jogo)")
    parser.add_argument("--no-audio", action="store_true", help="Desabilita efeitos sonoros")
    parser.add_argument("--windowed", action="store_true", help="Inicia em janela em vez de tela cheia")
    args = parser.parse_args()
    if args.list_ports:
        if list_ports is None:
            print("PySerial não instalado. Instale com: python -m pip install pyserial")
        else:
            for port in ArduinoLink.available_ports():
                print(f"{port.device} | {port.description}")
        return
    game = PacmanGame(ArduinoLink(args.port), rankings_path=args.scores, no_audio=args.no_audio)
    if args.windowed:
        game.configure_display(fullscreen=False)
    game.run()


if __name__ == "__main__":
    main()
