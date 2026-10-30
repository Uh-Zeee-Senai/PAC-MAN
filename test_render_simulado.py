"""Smoke test da renderização usando substituto mínimo do Pygame.

Não substitui uma execução gráfica real; verifica que todas as telas chamam
os elementos esperados sem erros de Python, mesmo num ambiente sem SDL.
"""
import sys
import types
import tempfile
from pathlib import Path

try:
    import pygame
    REAL_PYGAME = True
except ImportError:
    REAL_PYGAME = False
    pygame = types.ModuleType("pygame")
    pygame.SRCALPHA = 1
    class Surface:
        def __init__(self, *args, **kwargs): pass
        def convert(self): return self
        def fill(self, *args, **kwargs): pass
        def blit(self, *args, **kwargs): pass
        def get_rect(self, *args, **kwargs): return object()
    class Rect:
        def __init__(self, x, y, w, h):
            self.x, self.y, self.w, self.h = x, y, w, h
            self.right, self.bottom = x + w, y + h
    class Font:
        def render(self, *args, **kwargs): return Surface()
        def size(self, text): return (len(text) * 10, 20)
    pygame.Surface, pygame.Rect = Surface, Rect
    pygame.draw = types.SimpleNamespace(rect=lambda *a, **k:None,
        circle=lambda *a, **k:None, line=lambda *a, **k:None, polygon=lambda *a, **k:None)
    pygame.mixer = types.SimpleNamespace(pre_init=lambda *a,**k:None)
    pygame.font = types.SimpleNamespace(SysFont=lambda *a,**k:Font())
    pygame.display = types.SimpleNamespace(set_caption=lambda *a,**k:None,
        set_mode=lambda *a,**k:Surface(), flip=lambda *a,**k:None)
    pygame.key = types.SimpleNamespace(start_text_input=lambda:None, stop_text_input=lambda:None)
    pygame.time = types.SimpleNamespace(Clock=lambda:object(), get_ticks=lambda:1000)
    pygame.init = lambda:None
    sys.modules["pygame"] = pygame

import pacman

class FakeLink:
    connection = None
    port_name = None
    def poll(self): return None,False
    def send_lives(self, lives, force=False): pass
    def close(self): pass
    def connect(self): pass

with tempfile.TemporaryDirectory() as directory:
    game = pacman.PacmanGame(FakeLink(), rankings_path=Path(directory)/"rank.json", no_audio=True)
    game.draw()
    game.input_name("José")
    game.draw()
    game.handle_action()  # start
    for i in range(800):
        if i % 20 == 0:
            import random
            game.requested = random.choice(list(pacman.VECTORS))
        game.update(1/60)
        game.draw()
        if game.state == "game_over": break
        if game.state == "level_clear": game.start_next_level()
    game.state = "paused"
    game.draw()
    game.toggle_ranking()
    game.draw()
    game.handle_action()
    game.state = "game_over"
    game.last_rank_id = game.ranking.add("José",999,3)
    game.draw()
    game.toggle_ranking()
    game.draw()
print('PASS simulacao de UI:', 'pygame SDL real' if REAL_PYGAME else 'Pygame simulado')
