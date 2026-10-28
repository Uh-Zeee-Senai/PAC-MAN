"""Pac-Man + controle Arduino (joystick 5 pinos e LEDs de vidas).

Inicie com:
    python -m pip install -r requirements.txt
    python pacman.py                 # detecta o Arduino automaticamente
    python pacman.py --port COM3     # força a porta, se necessário
    python pacman.py --list-ports    # lista as portas disponíveis

Teclado alternativo: setas/WASD, ESPACO/ENTER, P, R e ESC.
Não requer imagens ou outros arquivos externos.
"""
from __future__ import annotations

import argparse
import math
import random
import time
from collections import deque

import pygame

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

COLS, ROWS = 23, 19
CELL = 28
BOARD_Y = 99
WIDTH, HEIGHT = COLS * CELL, BOARD_Y + ROWS * CELL + 66
FPS = 60
BAUD = 115200

BG = (7, 11, 27)
BOARD = (10, 16, 37)
NAVY = (15, 30, 78)
WALL_BORDER = (50, 111, 228)
CYAN = (104, 228, 255)
YELLOW = (255, 219, 49)
WHITE = (238, 246, 255)
MUTED = (136, 159, 197)
PINK = (255, 105, 161)
GHOST_COLORS = [(249, 89, 102), (246, 160, 218), (75, 218, 238)]
VECTORS = {"U": (0, -1), "D": (0, 1), "L": (-1, 0), "R": (1, 0)}
OPPOSITE = {"U": "D", "D": "U", "L": "R", "R": "L"}

# Altere aqui se seu joystick estiver montado em uma orientação diferente.
INVERTER_X = False
INVERTER_Y = False
LIMIAR_JOYSTICK = 175


class ArduinoLink:
    """USB serial sem bloqueio: Arduino -> J,x,y,botao / Python -> V:vidas."""

    def __init__(self, chosen_port: str | None = None):
        self.chosen_port = chosen_port
        self.connection = None
        self.port_name = None
        self.received = bytearray()
        self.pressed_before = False
        self.last_press_at = 0.0
        self.last_lives_sent = 0.0
        self.last_read_at = 0.0
        self.connect()

    @staticmethod
    def available_ports():
        if list_ports is None:
            return []
        return list(list_ports.comports())

    def connect(self):
        self.close()
        if serial is None:
            print("PySerial não instalado; jogue no teclado ou instale requirements.txt")
            return

        if self.chosen_port:
            candidates = [self.chosen_port]
        else:
            ports = self.available_ports()
            keywords = ("arduino", "ch340", "ch341", "usb serial", "usb-serial", "cp210", "ttyacm", "ttyusb")
            selected = [p.device for p in ports if any(k in (p.device + " " + p.description).lower() for k in keywords)]
            # Se houver somente uma porta serial, também é seguro tentar usá-la.
            if not selected and len(ports) == 1:
                selected = [ports[0].device]
            candidates = selected

        for name in candidates:
            try:
                self.connection = serial.Serial(name, BAUD, timeout=0, write_timeout=0.15)
                self.port_name = name
                self.received.clear()
                self.pressed_before = False
                self.last_lives_sent = 0.0
                self.last_read_at = time.monotonic()
                print(f"Arduino conectado em {name}")
                return
            except (OSError, serial.SerialException) as exc:
                print(f"Não foi possível abrir {name}: {exc}")
        print("Arduino não detectado. Modo teclado ativado (R = reconectar).")

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

    def send_lives(self, lives: int, force: bool = False):
        if self.connection is None:
            return
        now = time.monotonic()
        # Reenvio frequente: Arduino reinicia quando a porta USB é aberta.
        if not force and now - self.last_lives_sent < 0.8:
            return
        try:
            self.connection.write(f"V:{max(0, min(3, lives))}\n".encode("ascii"))
            self.last_lives_sent = now
        except (OSError, serial.SerialException):
            self.close()

    def poll(self):
        """Retorna (direção ou None, botão_foi_pressionado)."""
        if self.connection is None:
            return None, False
        direction = None
        button_edge = False
        try:
            available = self.connection.in_waiting
            if available:
                self.received.extend(self.connection.read(min(available, 1024)))
                self.last_read_at = time.monotonic()
            if len(self.received) > 4096:
                self.received.clear()
            while b"\n" in self.received:
                raw, _, remainder = self.received.partition(b"\n")
                self.received = bytearray(remainder)
                try:
                    parts = raw.strip().decode("ascii").split(",")
                    if len(parts) != 4 or parts[0] != "J":
                        continue
                    x, y, pressed = (int(value) for value in parts[1:])
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
                    if abs(dx) >= abs(dy):
                        direction = "R" if dx > 0 else "L"
                    else:
                        direction = "D" if dy > 0 else "U"

                active = pressed == 1
                now = time.monotonic()
                if active and not self.pressed_before and now - self.last_press_at > 0.28:
                    button_edge = True
                    self.last_press_at = now
                self.pressed_before = active
        except (OSError, serial.SerialException):
            print("Conexão com o Arduino perdida; usando teclado.")
            self.close()
        return direction, button_edge


def create_maze(level: int):
    """Labirinto de caminhos conectados, com atalhos e sem itens inacessíveis."""
    rng = random.Random(1206 + level * 17)
    grid = [["#"] * COLS for _ in range(ROWS)]
    start = (1, 1)
    grid[1][1] = " "
    visited = {start}
    stack = [start]
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

    # Aberturas extras deixam o jogo menos parecido com um labirinto de árvore.
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
    for x, y in shortcuts[:int(len(shortcuts) * 0.37)]:
        grid[y][x] = " "
    return grid


def is_floor(maze, pos):
    x, y = pos
    return 0 <= x < COLS and 0 <= y < ROWS and maze[y][x] != "#"


def walkable_neighbors(maze, pos):
    x, y = pos
    for direction, (dx, dy) in VECTORS.items():
        new_pos = (x + dx, y + dy)
        if is_floor(maze, new_pos):
            yield direction, new_pos


def nearest_floor(maze, target):
    return min(((x, y) for y in range(ROWS) for x in range(COLS)
                if maze[y][x] == " "),
               key=lambda p: abs(p[0] - target[0]) + abs(p[1] - target[1]))


def distances_from(maze, target):
    distances = {target: 0}
    queue = deque([target])
    while queue:
        curr = queue.popleft()
        for _, adjacent in walkable_neighbors(maze, curr):
            if adjacent not in distances:
                distances[adjacent] = distances[curr] + 1
                queue.append(adjacent)
    return distances


class PacmanGame:
    def __init__(self, link: ArduinoLink):
        pygame.init()
        pygame.display.set_caption("PAC-MAN | Arduino Edition")
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        self.clock = pygame.time.Clock()
        self.big_font = pygame.font.SysFont("arial", 36, bold=True)
        self.title_font = pygame.font.SysFont("arial", 25, bold=True)
        self.medium_font = pygame.font.SysFont("arial", 18, bold=True)
        self.small_font = pygame.font.SysFont("consolas", 13)
        self.link = link
        self.running = True
        self.state = "menu"
        self.score = 0
        self.lives = 3
        self.level = 1
        self.requested = "R"
        self.current = "R"
        self.player_timer = 0.0
        self.ghost_timer = 0.0
        self.power_time = 0.0
        self.invulnerable = 0.0
        self.maze = []
        self.pellets = set()
        self.power_pellets = set()
        self.ghosts = []
        self.player = (1, 1)
        self.spawn = (1, 1)
        self.load_level()
        self.link.send_lives(self.lives, force=True)

    def load_level(self):
        self.maze = create_maze(self.level)
        self.spawn = (1, 1)
        self.player = self.spawn
        self.current = "R"
        self.requested = "R"
        self.player_timer = self.ghost_timer = 0.0
        self.power_time = self.invulnerable = 0.0
        walkable = {(x, y) for y in range(ROWS) for x in range(COLS)
                    if self.maze[y][x] == " "}
        corners = [(COLS - 2, 1), (COLS - 2, ROWS - 2), (1, ROWS - 2)]
        spawn_tiles = []
        for corner in corners:
            point = nearest_floor(self.maze, corner)
            if point == self.spawn or point in spawn_tiles:
                point = max(walkable - {self.spawn, *spawn_tiles},
                            key=lambda p: abs(p[0] - self.spawn[0]) + abs(p[1] - self.spawn[1]))
            spawn_tiles.append(point)
        self.ghosts = [{"pos": pos, "spawn": pos, "color": GHOST_COLORS[i],
                        "direction": "L"} for i, pos in enumerate(spawn_tiles)]
        self.pellets = walkable - {self.spawn}
        powers = [self.spawn, *spawn_tiles]
        self.power_pellets = set(powers) - {self.spawn}
        self.pellets -= self.power_pellets

    def start_new_game(self):
        self.score = 0
        self.lives = 3
        self.level = 1
        self.load_level()
        self.state = "running"
        self.link.send_lives(self.lives, force=True)

    def handle_action(self):
        if self.state in ("menu", "game_over"):
            self.start_new_game()
        elif self.state == "level_clear":
            self.level += 1
            self.load_level()
            self.state = "running"
        elif self.state == "running":
            self.state = "paused"
        elif self.state == "paused":
            self.state = "running"

    def consume_pellet(self):
        if self.player in self.pellets:
            self.pellets.remove(self.player)
            self.score += 10
        elif self.player in self.power_pellets:
            self.power_pellets.remove(self.player)
            self.score += 50
            self.power_time = 7.5
        if not self.pellets and not self.power_pellets:
            self.state = "level_clear"

    def player_step(self):
        for candidate in (self.requested, self.current):
            dx, dy = VECTORS[candidate]
            point = (self.player[0] + dx, self.player[1] + dy)
            if is_floor(self.maze, point):
                self.player = point
                self.current = candidate
                break
        self.consume_pellet()
        self.check_collisions()

    def ghost_step(self):
        paths_to_player = distances_from(self.maze, self.player)
        frightened = self.power_time > 0
        for ghost in self.ghosts:
            options = list(walkable_neighbors(self.maze, ghost["pos"]))
            if not options:
                continue
            # Evita andar para trás em todo corredor, a menos que seja obrigatório.
            forward = [(d, p) for d, p in options if d != OPPOSITE[ghost["direction"]]]
            if forward:
                options = forward
            if random.random() < 0.17:
                direction, pos = random.choice(options)
            elif frightened:
                direction, pos = max(options, key=lambda item: paths_to_player.get(item[1], 0)
                                     + random.random() * 1.5)
            else:
                direction, pos = min(options, key=lambda item: paths_to_player.get(item[1], 999)
                                     + random.random() * 1.5)
            ghost["direction"] = direction
            ghost["pos"] = pos
        self.check_collisions()

    def check_collisions(self):
        if self.state != "running":
            return
        for ghost in self.ghosts:
            if ghost["pos"] != self.player:
                continue
            if self.power_time > 0:
                self.score += 200
                ghost["pos"] = ghost["spawn"]
                ghost["direction"] = "L"
            elif self.invulnerable <= 0:
                self.lives -= 1
                self.link.send_lives(self.lives, force=True)
                self.power_time = 0.0
                if self.lives == 0:
                    self.state = "game_over"
                else:
                    self.player = self.spawn
                    self.current = self.requested = "R"
                    self.invulnerable = 2.0
                    for reset_ghost in self.ghosts:
                        reset_ghost["pos"] = reset_ghost["spawn"]
                        reset_ghost["direction"] = "L"
                return

    def update(self, dt):
        direction, pressed = self.link.poll()
        if direction:
            self.requested = direction
        if pressed:
            self.handle_action()
        self.link.send_lives(self.lives)
        if self.state != "running":
            return
        self.power_time = max(0.0, self.power_time - dt)
        self.invulnerable = max(0.0, self.invulnerable - dt)
        self.player_timer += dt
        self.ghost_timer += dt
        player_interval = max(0.085, 0.133 - (self.level - 1) * 0.005)
        ghost_interval = max(0.13, 0.245 - (self.level - 1) * 0.012)
        if self.player_timer >= player_interval:
            self.player_timer %= player_interval
            self.player_step()
        if self.state != "running":
            return
        if self.ghost_timer >= ghost_interval:
            self.ghost_timer %= ghost_interval
            self.ghost_step()

    def draw_text(self, text, font, color, position, center=False):
        surface = font.render(text, True, color)
        rectangle = surface.get_rect(center=position) if center else surface.get_rect(topleft=position)
        self.screen.blit(surface, rectangle)

    def draw_pacman(self, x, y, radius, direction, t):
        center = (int(x), int(y))
        pygame.draw.circle(self.screen, YELLOW, center, radius)
        angle = {"R": 0, "L": math.pi, "U": -math.pi / 2, "D": math.pi / 2}[direction]
        opening = math.radians(8 + 25 * abs(math.sin(t * 11)))
        polygon = [center]
        for tilt in (-opening, opening):
            theta = angle + tilt
            polygon.append((int(x + (radius + 2) * math.cos(theta)),
                            int(y + (radius + 2) * math.sin(theta))))
        pygame.draw.polygon(self.screen, BOARD, polygon)

    def draw_ghost(self, x, y, color, t, frightened):
        radius = 10
        ix, iy = int(x), int(y)
        ghost_color = (73, 112, 246) if frightened else color
        pygame.draw.circle(self.screen, ghost_color, (ix, iy - 2), radius)
        pygame.draw.rect(self.screen, ghost_color, (ix - radius, iy - 2, radius * 2, 13))
        wave = 2 if math.sin(t * 8) > 0 else 0
        points = [(ix - radius, iy + 10), (ix - 6, iy + 6 - wave),
                  (ix, iy + 10), (ix + 6, iy + 6 - wave), (ix + radius, iy + 10)]
        pygame.draw.polygon(self.screen, ghost_color, points)
        for offset in (-4, 4):
            pygame.draw.circle(self.screen, WHITE, (ix + offset, iy - 2), 4)
            pygame.draw.circle(self.screen, (15, 35, 85), (ix + offset + 1, iy - 1), 2)

    def draw(self):
        self.screen.fill(BG)
        t = pygame.time.get_ticks() / 1000
        # Cabeçalho
        pygame.draw.rect(self.screen, (12, 20, 49), (12, 10, WIDTH - 24, 77), border_radius=15)
        self.draw_pacman(34, 34, 13, "R", t)
        self.draw_text("PAC-MAN", self.title_font, YELLOW, (54, 18))
        self.draw_text("ARDUINO EDITION", self.small_font, MUTED, (56, 52))
        self.draw_text(f"PONTOS  {self.score:05}", self.medium_font, WHITE, (259, 20))
        self.draw_text(f"FASE  {self.level}", self.small_font, MUTED, (261, 52))
        self.draw_text("VIDAS", self.small_font, MUTED, (474, 19))
        for i in range(3):
            cx = 485 + 30 * i
            color = YELLOW if i < self.lives else (55, 65, 89)
            pygame.draw.circle(self.screen, color, (cx, 55), 10)
            if i < self.lives:
                pygame.draw.polygon(self.screen, (12, 20, 49),
                                    [(cx, 55), (cx + 12, 50), (cx + 12, 60)])

        pygame.draw.rect(self.screen, BOARD, (0, BOARD_Y, WIDTH, ROWS * CELL))
        for y in range(ROWS):
            for x in range(COLS):
                cx = x * CELL + CELL // 2
                cy = BOARD_Y + y * CELL + CELL // 2
                if self.maze[y][x] == "#":
                    r = pygame.Rect(x * CELL + 2, BOARD_Y + y * CELL + 2, CELL - 4, CELL - 4)
                    pygame.draw.rect(self.screen, NAVY, r, border_radius=6)
                    pygame.draw.rect(self.screen, WALL_BORDER, r, width=1, border_radius=6)
                elif (x, y) in self.pellets:
                    pygame.draw.circle(self.screen, (252, 217, 168), (cx, cy), 3)
                elif (x, y) in self.power_pellets:
                    radius = int(6 + 1.5 * abs(math.sin(t * 5)))
                    pygame.draw.circle(self.screen, PINK, (cx, cy), radius)
                    pygame.draw.circle(self.screen, WHITE, (cx, cy), 2)

        if self.invulnerable <= 0 or int(t * 8) % 2 == 0:
            px = self.player[0] * CELL + CELL // 2
            py = BOARD_Y + self.player[1] * CELL + CELL // 2
            self.draw_pacman(px, py, 11, self.current, t)
        for ghost in self.ghosts:
            gx, gy = ghost["pos"]
            self.draw_ghost(gx * CELL + CELL // 2, BOARD_Y + gy * CELL + CELL // 2,
                            ghost["color"], t, self.power_time > 0)
        if self.power_time > 0:
            self.draw_text(f"SUPER PODER: {self.power_time:.1f}s", self.small_font, PINK,
                           (WIDTH // 2, BOARD_Y + 9), center=True)

        status = f"ARDUINO: {self.link.port_name}" if self.link.connection else "ARDUINO: DESCONECTADO"
        self.draw_text(status, self.small_font, CYAN if self.link.connection else MUTED,
                       (18, HEIGHT - 54))
        self.draw_text("Joystick/Setas: mover  |  Botao/Espaco: jogar/pausar  |  R: USB",
                       self.small_font, MUTED, (18, HEIGHT - 28))

        if self.state != "running":
            layer = pygame.Surface((WIDTH, ROWS * CELL), pygame.SRCALPHA)
            layer.fill((5, 8, 26, 197))
            self.screen.blit(layer, (0, BOARD_Y))
            messages = {
                "menu": ("PAC-MAN", "COLETE TODOS OS PONTOS!", "Aperte o joystick ou ESPACO para jogar"),
                "paused": ("PAUSADO", "JOGO EM PAUSA", "Aperte o joystick ou ESPACO para continuar"),
                "game_over": ("FIM DE JOGO", f"PONTUACAO: {self.score}", "Aperte o joystick ou ESPACO para reiniciar"),
                "level_clear": ("FASE CONCLUIDA!", f"PONTUACAO: {self.score}", "Aperte o joystick ou ESPACO para proxima fase"),
            }
            title, subtitle, instruction = messages[self.state]
            center_y = BOARD_Y + ROWS * CELL // 2
            self.draw_text(title, self.big_font, YELLOW, (WIDTH // 2, center_y - 45), center=True)
            self.draw_text(subtitle, self.medium_font, WHITE, (WIDTH // 2, center_y + 5), center=True)
            self.draw_text(instruction, self.small_font, CYAN,
                           (WIDTH // 2, center_y + 44), center=True)

        pygame.display.flip()

    def run(self):
        key_to_dir = {
            pygame.K_UP: "U", pygame.K_w: "U",
            pygame.K_DOWN: "D", pygame.K_s: "D",
            pygame.K_LEFT: "L", pygame.K_a: "L",
            pygame.K_RIGHT: "R", pygame.K_d: "R",
        }
        try:
            while self.running:
                dt = min(self.clock.tick(FPS) / 1000, 0.08)
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        self.running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key == pygame.K_ESCAPE:
                            self.running = False
                        elif event.key in key_to_dir:
                            self.requested = key_to_dir[event.key]
                        elif event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_p):
                            self.handle_action()
                        elif event.key == pygame.K_r:
                            self.link.connect()
                            self.link.send_lives(self.lives, force=True)
                self.update(dt)
                self.draw()
        finally:
            self.link.close()
            pygame.quit()


def main():
    parser = argparse.ArgumentParser(description="Jogo Pac-Man com joystick Arduino e LEDs de vidas")
    parser.add_argument("--port", help="Porta USB do Arduino, ex.: COM3 ou /dev/ttyACM0")
    parser.add_argument("--list-ports", action="store_true", help="Listar portas seriais e encerrar")
    args = parser.parse_args()
    if args.list_ports:
        if list_ports is None:
            print("Instale pyserial: python -m pip install pyserial")
        else:
            ports = ArduinoLink.available_ports()
            for port in ports:
                print(f"{port.device}  |  {port.description}")
            if not ports:
                print("Nenhuma porta serial encontrada.")
        return
    PacmanGame(ArduinoLink(args.port)).run()


if __name__ == "__main__":
    main()
