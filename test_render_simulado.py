"""Smoke test de telas sem driver SDL.

Se pygame-ce estiver instalado, use-o com SDL_VIDEODRIVER=dummy;
caso contrario injeta fake leve para verificar chamadas, coordenadas e fluxos.
Nao substitui a validacao visual no notebook.
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
    pygame = types.ModuleType('pygame')
    pygame.SRCALPHA = 1
    pygame.FULLSCREEN = 2
    pygame.RESIZABLE = 3
    pygame.MOUSEBUTTONDOWN = 4
    pygame.QUIT = 5
    pygame.KEYDOWN = 6
    pygame.TEXTINPUT = 7

    class Rect:
        def __init__(self, *args):
            if len(args) == 1:
                thing = args[0]
                if isinstance(thing, Rect):
                    self.x, self.y, self.w, self.h = thing.x, thing.y, thing.w, thing.h
                else:
                    self.x, self.y, self.w, self.h = thing
            else:
                self.x, self.y, self.w, self.h = args
        @property
        def right(self): return self.x+self.w
        @property
        def bottom(self): return self.y+self.h
        @property
        def center(self): return self.x+self.w//2, self.y+self.h//2
        def collidepoint(self, p):
            return self.x <= p[0] < self.right and self.y <= p[1] < self.bottom

    class Surface:
        def __init__(self, size=(1280,800), *args, **kwargs):
            self.size = size
        def convert(self): return self
        def fill(self, *a, **k): return None
        def blit(self, *a, **k): return None
        def get_size(self): return self.size
        def get_rect(self, **kwargs):
            r=Rect((0,0,100,30))
            if 'center' in kwargs:
                x,y=kwargs['center'];r.x=x-50;r.y=y-15
            if 'topleft' in kwargs:
                r.x,r.y=kwargs['topleft']
            if 'topright' in kwargs:
                x,y=kwargs['topright'];r.x=x-100;r.y=y
            return r

    class Font:
        def render(self, *a, **k): return Surface((100,30))
        def size(self, text): return len(str(text))*11, 27

    pygame.Surface, pygame.Rect = Surface, Rect
    pygame.draw=types.SimpleNamespace(rect=lambda *a,**k:None,
        circle=lambda *a,**k:None, line=lambda *a,**k:None,polygon=lambda *a,**k:None)
    pygame.mixer=types.SimpleNamespace(pre_init=lambda *a,**k:None)
    pygame.font=types.SimpleNamespace(SysFont=lambda *a,**k:Font())
    pygame.display=types.SimpleNamespace(set_caption=lambda *a,**k:None,
        set_mode=lambda sz,*a,**k:Surface(sz if sz != (0,0) else (1920,1080)),
        get_desktop_sizes=lambda:[(1920,1080)], flip=lambda:None)
    pygame.key=types.SimpleNamespace(start_text_input=lambda:None,stop_text_input=lambda:None)
    pygame.time=types.SimpleNamespace(Clock=lambda:object(),get_ticks=lambda:1000)
    pygame.mouse=types.SimpleNamespace(get_pos=lambda:(0,0))
    pygame.transform=types.SimpleNamespace(scale=lambda s,size:Surface(size))
    pygame.init=lambda:None
    pygame.quit=lambda:None
    sys.modules['pygame']=pygame

import pacman

class FakeLink:
    connection=None
    port_name=None
    def poll(self): return None,False
    def send_lives(self,lives,force=False): pass
    def close(self): pass
    def connect(self): pass

with tempfile.TemporaryDirectory() as directory:
    db=Path(directory)/'ranking.db'
    game=pacman.PacmanGame(FakeLink(),rankings_path=db,no_audio=True)
    assert game.fullscreen
    def click(key):
        game.draw()
        x,y=game.ui_buttons[key].center
        ww,wh=game.display.get_size()
        sc=min(ww/pacman.WIDTH,wh/pacman.HEIGHT)
        px=round((ww-pacman.WIDTH*sc)/2+x*sc)
        py=round((wh-pacman.HEIGHT*sc)/2+y*sc)
        game.handle_click((px,py))
    game.draw()   # Menu
    assert 'ranking' in game.ui_buttons and 'start' in game.ui_buttons
    game.input_name('José')
    click('ranking'); assert game.state=='ranking'
    game.draw(); assert 'back' in game.ui_buttons
    click('back'); assert game.state=='menu'
    click('start'); assert game.state=='running'
    for i in range(1000):
        if i%30==0:
            import random
            game.requested=random.choice(list(pacman.VECTORS))
        game.update(1/60)
        game.draw()
        if game.state=='game_over':break
        if game.state=='level_clear':game.start_next_level()
    game.state='paused';game.draw()
    game.toggle_ranking();game.draw();game.handle_action()
    assert game.state=='paused'
    game.score=1230;game.finish_game();game.draw()
    assert game.ranking.entries[0]['score']==1230
    game.toggle_ranking();game.draw()
    assert game.state=='ranking'
    game.toggle_fullscreen();assert game.fullscreen is False
    game.draw()
    p=game.to_logical_pos((100,100))
    assert p is not None and 0<=p[0]<pacman.WIDTH
    reopened=pacman.Ranking(db)
    assert reopened.entries[0]['name']=='José'
print('PASS interface responsiva, menus, tela dedicada de ranking, SQL e fullscreen:',
      'Pygame SDL' if REAL_PYGAME else 'simulação sem SDL')
