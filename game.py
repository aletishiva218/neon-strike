"""
NEON STRIKE - a complete 2D top-down shooter in ONE Python file.

Everything (graphics, glow effects, sound effects and music) is generated in
code with Pygame - no images, fonts or sound files are needed.

INSTALL
    pip install pygame

RUN
    python shooting_game.py              (fullscreen)
    python shooting_game.py --windowed   (1280x720 window)

CONTROLS
    WASD / Arrow keys ... move
    Mouse ............... aim
    Left mouse button ... shoot (hold for automatic fire)
    SPACE / Right click . launch a bomb (homes onto the enemy under the cursor)
    ESC / P ............. pause / resume
    M ................... mute / unmute sound
    F11 ................. toggle fullscreen
    (Paused) R restart, Q quit to menu     (Game over) R restart, ESC menu
"""
import array
import math
import os
import random
import sys

import pygame
from pygame.math import Vector2

IS_ANDROID = "ANDROID_ARGUMENT" in os.environ or "ANDROID_ROOT" in os.environ

# ----------------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------------
W, H = 1280, 720                 # internal resolution (scaled to the screen)
FPS = 60
TITLE = "NEON STRIKE"
HS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "neon_strike_best.txt")

PLAYER_SPEED_ACCEL = 2600        # movement acceleration
PLAYER_DAMPING = 6.5             # higher = stops faster (top speed = accel / damping)
PLAYER_MAX_HP = 100
FIRE_DELAY = 0.10                # seconds between shots
BULLET_SPEED = 1150
MAX_ENEMIES = 70
# bombs
BOMB_START = 3                   # bombs at the start of a game
BOMB_MAX = 5                     # maximum bombs that can be carried
BOMB_COOLDOWN = 0.8              # seconds between bomb launches
BOMB_SPEED = 680                 # top flight speed
BOMB_BLAST_RADIUS = 175          # explosion radius in pixels
BOMB_DAMAGE = 130                # damage at the centre (falls to half at the edge)
BOMB_DROP_EVERY = (18.0, 28.0)   # random seconds between bomb reward appearances
BOMB_DROP_LIFETIME = 10.0        # seconds an uncollected bomb reward stays on screen
METAL = (62, 66, 78)             # bomb body colours
METAL_DARK = (36, 39, 48)
METAL_LIGHT = (150, 158, 174)

# Mobile touch-control areas (logical 1280 × 720 coords)
_JOY_MAX_X  = int(W * 0.42)   # x < this  →  joystick zone
_JOY_RADIUS = 90               # px before direction saturates
_BOMB_BTN   = pygame.Rect(W - 162, H - 108, 150, 92)
_PAUSE_BTN  = pygame.Rect(6, 6, 58, 36)

# maximum number of each enemy type alive on screen at the same time
ENEMY_LIMITS = {
    "chaser": 10,                # red enemies
    "shooter": 3,                # green enemies
    "tank": 1,                   # purple enemy
}
ENEMY_SPEED_SCALE = 0.78         # enemy movement speed multiplier (1.0 = original speed)
DOUBLE_SHOT_DURATION = 15.0      # seconds a weapon upgrade lasts (reset by each new reward)
DOUBLE_SHOT_GAP = 7              # half the spacing between neighbouring parallel bullets
MAX_FORMATION_WIDTH = 150        # bullets squeeze closer together beyond this total width
WEAPON_DROP_EVERY = (12.0, 20.0) # random seconds between weapon upgrade appearances
WEAPON_DROP_LIFETIME = 10.0      # seconds an uncollected upgrade stays on screen

# Colours
CYAN = (60, 230, 255)
PINK = (255, 70, 140)
YELLOW = (255, 215, 80)
PURPLE = (170, 100, 255)
GREEN = (90, 255, 150)
ORANGE = (255, 150, 50)
RED = (255, 60, 70)
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def lerp_color(a, b, t):
    return (int(a[0] + (b[0] - a[0]) * t), int(a[1] + (b[1] - a[1]) * t), int(a[2] + (b[2] - a[2]) * t))


# ----------------------------------------------------------------------------
# Glow rendering (pre-rendered radial gradients, blended additively)
# ----------------------------------------------------------------------------
_GLOW_CACHE = {}


def glow_surface(radius, color):
    radius = max(2, int(radius) // 2 * 2)
    key = (radius, color)
    surf = _GLOW_CACHE.get(key)
    if surf is None:
        if len(_GLOW_CACHE) > 2500:
            _GLOW_CACHE.clear()
        surf = pygame.Surface((radius * 2, radius * 2))
        surf.fill(BLACK)
        for i in range(radius, 0, -1):
            k = (1 - i / radius) ** 2.2
            pygame.draw.circle(surf, (int(color[0] * k), int(color[1] * k), int(color[2] * k)),
                               (radius, radius), i)
        _GLOW_CACHE[key] = surf
    return surf


def draw_glow(surf, pos, radius, color, intensity=1.0):
    """Additive soft glow. Intensity is quantised so the cache stays small."""
    q = round(clamp(intensity, 0, 1) * 6) / 6
    if q <= 0 or radius < 2:
        return
    col = (int(color[0] * q), int(color[1] * q), int(color[2] * q))
    g = glow_surface(radius, col)
    r = g.get_width() // 2
    surf.blit(g, (pos[0] - r, pos[1] - r), special_flags=pygame.BLEND_ADD)


# ----------------------------------------------------------------------------
# Procedural audio: sound effects and a looping synth track
# ----------------------------------------------------------------------------
class Audio:
    def __init__(self):
        self.enabled = False
        self.muted = False
        self.sounds = {}
        self.music = None
        self.music_channel = None
        try:
            info = pygame.mixer.get_init()
            if not info:
                return
            self.rate, size, self.channels = info
            if size != -16:
                return
            pygame.mixer.set_num_channels(24)
            self._build_sounds()
            self.music = self._build_music()
            self.enabled = True
        except Exception:
            self.enabled = False

    def _render(self, duration, fn, volume):
        n = int(self.rate * duration)
        ch = self.channels
        buf = array.array("h", [0]) * (n * ch)
        for i in range(n):
            v = fn(i / self.rate, i / n)
            s = int(clamp(v, -1.0, 1.0) * volume * 32767)
            base = i * ch
            for c in range(ch):
                buf[base + c] = s
        return pygame.mixer.Sound(buffer=buf.tobytes())

    def _build_sounds(self):
        tau = math.tau

        def sweep(f0, f1, dur, square=0.5):
            def fn(t, p):
                ph = tau * (f0 * t + (f1 - f0) * t * t / (2 * dur))
                s = math.sin(ph)
                sq = 1.0 if s > 0 else -1.0
                return (sq * square + s * (1 - square)) * (1 - p) ** 2
            return fn

        def noise_hit(t, p):
            return random.uniform(-1, 1) * (1 - p) ** 3 * 0.8 + math.sin(tau * 240 * t) * (1 - p) * 0.4

        lp = [0.0]

        def explosion(t, p):
            lp[0] += (random.uniform(-1, 1) - lp[0]) * (0.18 - 0.14 * p)
            rumble = math.sin(tau * (70 - 40 * p) * t) * (1 - p) * 0.6
            return lp[0] * 3.2 * (1 - p) ** 1.5 + rumble

        def hurt(t, p):
            return sweep(240, 70, 0.3, 0.8)(t, p) * 0.8 + random.uniform(-1, 1) * (1 - p) ** 2 * 0.3

        def pickup(t, p):
            f = (660, 880, 1320)[min(2, int(p * 3))]
            return math.sin(tau * f * t) * (1 - (p * 3) % 1) ** 0.5

        def level_up(t, p):
            f = (523, 659, 784, 1046)[min(3, int(p * 4))]
            return (math.sin(tau * f * t) * 0.7 + math.sin(tau * f * 2 * t) * 0.2) * (1 - p) ** 0.4

        def bomb_launch(t, p):
            whoosh = random.uniform(-1, 1) * (1 - p) * p * 2.5
            return whoosh * 0.5 + sweep(160, 520, 0.35, 0.2)(t, p) * 0.6

        lp2 = [0.0]

        def big_boom(t, p):
            lp2[0] += (random.uniform(-1, 1) - lp2[0]) * (0.12 - 0.1 * p)
            thump = math.sin(tau * (55 - 30 * p) * t) * (1 - p) ** 1.2
            crackle = random.uniform(-1, 1) * (random.random() < 0.02) * (1 - p)
            return lp2[0] * 3.5 * (1 - p) ** 1.3 + thump * 0.8 + crackle * 0.6

        def alarm(t, p):
            f = 660 if int(p * 6) % 2 == 0 else 440
            return math.sin(tau * f * t) * 0.8 * (1 - (p * 6) % 1) ** 0.3

        self.sounds = {
            "shoot": self._render(0.09, sweep(1100, 260, 0.09, 0.4), 0.22),
            "hit": self._render(0.07, noise_hit, 0.30),
            "explode": self._render(0.6, explosion, 0.55),
            "hurt": self._render(0.3, hurt, 0.45),
            "pickup": self._render(0.3, pickup, 0.35),
            "enemy_shot": self._render(0.14, sweep(420, 900, 0.14, 0.2), 0.16),
            "level": self._render(0.6, level_up, 0.35),
            "start": self._render(0.5, sweep(200, 1200, 0.5, 0.3), 0.3),
            "bomb_launch": self._render(0.35, bomb_launch, 0.45),
            "bomb": self._render(1.1, big_boom, 0.75),
            "alarm": self._render(0.9, alarm, 0.32),
            "boss_shot": self._render(0.18, sweep(300, 120, 0.18, 0.7), 0.3),
        }

    def _build_music(self):
        """A 4-bar synthwave loop: kick, hats, bass, arpeggio and pad."""
        tau = math.tau
        bpm = 124
        step = 60 / bpm / 4                       # one 16th note
        chords = [(57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62)]  # Am F C G

        def midi(n):
            return 440.0 * 2 ** ((n - 69) / 12)

        def fn(t, p):
            s = int(t / step)
            chord = chords[(s // 16) % 4]
            tin = t - s * step
            note = midi(chord[s % 3] + 12 + 12 * ((s // 3) % 2))
            env = math.exp(-tin * 16)
            ph = math.sin(tau * note * t)
            lead = ((1 if ph > 0 else -1) * 0.10 + ph * 0.10) * env
            e = int(t / (step * 2))
            tb = t - e * step * 2
            bass = math.sin(tau * midi(chord[0] - 12) * t) * 0.32 * math.exp(-tb * 5)
            bt = t % (step * 4)
            kick = math.sin(tau * (45 + 110 * math.exp(-bt * 28)) * bt) * math.exp(-bt * 11) * 0.55
            ht = (t + step * 2) % (step * 4)
            hat = random.uniform(-1, 1) * math.exp(-ht * 70) * 0.10
            pad = (math.sin(tau * midi(chord[1]) * t) + math.sin(tau * midi(chord[2]) * t)) * 0.04
            return lead + bass + kick + hat + pad

        return self._render(step * 64, fn, 0.55)

    def play(self, name, volume=1.0):
        if self.enabled and not self.muted:
            snd = self.sounds.get(name)
            if snd:
                ch = snd.play()
                if ch:
                    ch.set_volume(volume)

    def start_music(self):
        if self.enabled and self.music and self.music_channel is None:
            self.music_channel = self.music.play(loops=-1)
            if self.music_channel:
                self.music_channel.set_volume(0 if self.muted else 0.45)

    def toggle_mute(self):
        self.muted = not self.muted
        if self.music_channel:
            self.music_channel.set_volume(0 if self.muted else 0.45)


# ----------------------------------------------------------------------------
# Effects
# ----------------------------------------------------------------------------
class Particle:
    __slots__ = ("pos", "vel", "life", "max_life", "size", "color", "drag", "glow")

    def __init__(self, pos, vel, life, size, color, drag=3.0, glow=True):
        self.pos = Vector2(pos)
        self.vel = Vector2(vel)
        self.life = self.max_life = life
        self.size = size
        self.color = color
        self.drag = drag
        self.glow = glow

    def update(self, dt):
        self.vel *= max(0.0, 1 - self.drag * dt)
        self.pos += self.vel * dt
        self.life -= dt

    def draw(self, surf):
        k = max(0.0, self.life / self.max_life)
        r = self.size * (0.35 + 0.65 * k)
        if self.glow:
            draw_glow(surf, self.pos, r * 3.2, self.color, k)
            pygame.draw.circle(surf, lerp_color(self.color, WHITE, k * 0.6), self.pos, max(1, int(r)))
        else:
            pygame.draw.circle(surf, lerp_color((10, 10, 25), self.color, k * 0.5), self.pos, max(1, int(r)))


class Ring:
    """Expanding shockwave ring."""

    def __init__(self, pos, max_radius, life, color):
        self.pos = Vector2(pos)
        self.max_radius = max_radius
        self.life = self.max_life = life
        self.color = color

    def update(self, dt):
        self.life -= dt

    def draw(self, surf):
        k = 1 - max(0.0, self.life / self.max_life)
        radius = self.max_radius * (1 - (1 - k) ** 3)
        width = max(1, int(7 * (1 - k)))
        if radius > width:
            pygame.draw.circle(surf, lerp_color(self.color, BLACK, k * 0.8), self.pos, int(radius), width)


class FloatText:
    def __init__(self, pos, text, font, color):
        self.pos = Vector2(pos)
        self.image = font.render(text, True, color)
        self.life = self.max_life = 0.9

    def update(self, dt):
        self.pos.y -= 55 * dt
        self.life -= dt

    def draw(self, surf):
        self.image.set_alpha(int(255 * clamp(self.life / self.max_life * 1.5, 0, 1)))
        surf.blit(self.image, self.image.get_rect(center=(int(self.pos.x), int(self.pos.y))))


# ----------------------------------------------------------------------------
# Game objects
# ----------------------------------------------------------------------------
# ---- white fighter jet sprite (built once, then rotated from a cache) ----
JET_SIZE = 100                   # sprite size in pixels
JET_SS = 4                       # supersampling factor for smooth, sharp edges
JET_HI = (255, 255, 255)         # highlight
JET_BASE = (238, 241, 246)       # lit white surfaces
JET_SHADE = (212, 218, 228)      # shaded white surfaces
JET_DARK = (182, 190, 204)       # deep shading / engine parts
JET_LINE = (150, 160, 178)       # outlines and panel lines


def build_jet_sprite():
    """Draw a detailed top-down white fighter jet facing right (+x)."""
    size = JET_SIZE * JET_SS
    c = size / 2
    big = pygame.Surface((size, size), pygame.SRCALPHA)

    def P(x, y):
        return (c + x * JET_SS, c + y * JET_SS)

    def poly(points, color, outline=True):
        pts = [P(x, y) for x, y in points]
        pygame.draw.polygon(big, color, pts)
        if outline:
            pygame.draw.polygon(big, JET_LINE, pts, JET_SS)

    def line(a, b, color, width=1):
        pygame.draw.line(big, color, P(*a), P(*b), max(1, int(width * JET_SS)))

    for s in (-1, 1):                         # s = -1 is the lit side, +1 the shaded side
        lit = JET_BASE if s < 0 else JET_SHADE
        # horizontal tail stabilisers
        poly([(-17, 6 * s), (-27, 15 * s), (-31, 15 * s), (-28, 6 * s)], lit)
        # swept main wings with highlighted leading edge and wingtip rails
        poly([(12, 5 * s), (-8, 26 * s), (-15, 27 * s), (-16, 6 * s)], lit)
        line((11, 5.6 * s), (-7.5, 25.4 * s), JET_HI, 1.2)
        line((-1, 26.6 * s), (-15, 27.6 * s), JET_DARK, 1.6)
        line((-4, 13 * s), (-14, 14 * s), JET_LINE, 0.5)          # flap line
        # canards / leading-edge extensions
        poly([(22, 3 * s), (10, 9.5 * s), (8, 5 * s)], lit)
        # twin engines and exhaust nozzles
        poly([(-18, 1.5 * s), (-31, 1.8 * s), (-31, 6.2 * s), (-18, 6.4 * s)], JET_SHADE)
        poly([(-31, 1.8 * s), (-35, 2.3 * s), (-35, 5.7 * s), (-31, 6.2 * s)], JET_DARK)
        pygame.draw.circle(big, (120, 128, 145), P(-34.2, 4 * s), 1.6 * JET_SS)
        pygame.draw.circle(big, JET_DARK, P(-34.2, 4 * s), 0.8 * JET_SS)

    # fuselage: lit base, shaded lower half, bright spine highlight
    body = [(34, 0), (26, 2.5), (16, 4.5), (4, 5.5), (-14, 6), (-24, 5.5), (-27, 3.5),
            (-27, -3.5), (-24, -5.5), (-14, -6), (4, -5.5), (16, -4.5), (26, -2.5)]
    poly(body, JET_BASE)
    poly([(34, 0), (26, 2.5), (16, 4.5), (4, 5.5), (-14, 6), (-24, 5.5), (-27, 3.5), (-27, 0)],
         JET_SHADE, outline=False)
    poly([(29, -0.6), (16, -2.6), (-20, -3.6), (-20, -1.6), (16, -0.8)], JET_HI, outline=False)
    pygame.draw.polygon(big, JET_LINE, [P(x, y) for x, y in body], JET_SS)

    for s in (-1, 1):
        # air intakes on the sides of the fuselage
        poly([(9, 5.4 * s), (-6, 6.6 * s), (-6, 8.4 * s), (7, 7.4 * s)], JET_SHADE)
        line((8, 6.6 * s), (-5, 7.6 * s), JET_DARK, 0.8)
        # twin canted vertical tail fins
        poly([(-12, 3.5 * s), (-25, 9 * s), (-28.5, 9 * s), (-21, 3.5 * s)], JET_HI)

    # panel lines
    line((4, -5.2), (4, 5.2), JET_LINE, 0.4)
    line((-14, -5.6), (-14, 5.6), JET_LINE, 0.4)
    line((-14, 0), (-26, 0), JET_LINE, 0.4)

    # cockpit canopy with a glossy reflection
    canopy = pygame.Rect(0, 0, int(13 * JET_SS), int(5.6 * JET_SS))
    canopy.center = tuple(int(v) for v in P(16.5, 0))
    pygame.draw.ellipse(big, (196, 212, 236), canopy)
    pygame.draw.ellipse(big, JET_LINE, canopy, JET_SS)
    shine = pygame.Rect(0, 0, int(6 * JET_SS), int(1.6 * JET_SS))
    shine.center = tuple(int(v) for v in P(18.5, -1.1))
    pygame.draw.ellipse(big, JET_HI, shine)
    line((10.5, -2.2), (10.5, 2.2), JET_LINE, 0.5)               # canopy frame

    jet = pygame.transform.smoothscale(big, (JET_SIZE, JET_SIZE))

    # soft white halo made from a blurred copy of the silhouette
    halo = pygame.transform.smoothscale(jet, (JET_SIZE // 5, JET_SIZE // 5))
    halo = pygame.transform.smoothscale(halo, (JET_SIZE, JET_SIZE))
    halo.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
    final = pygame.Surface((JET_SIZE, JET_SIZE), pygame.SRCALPHA)
    final.blit(halo, (0, 0))
    final.blit(jet, (0, 0))
    return final


class Player:
    RADIUS = 17
    _sprite = None
    _rotated = {}
    ENGINES = (Vector2(-36, -4), Vector2(-36, 4))

    def __init__(self, pos):
        self.pos = Vector2(pos)
        self.vel = Vector2()
        self.angle = 0.0
        self.hp = PLAYER_MAX_HP
        self.invuln = 0.0
        self.flash = 0.0
        self.move_dir = Vector2()

    @property
    def forward(self):
        return Vector2(1, 0).rotate(self.angle)

    def update(self, dt, keys, mouse, touch_dir=None):
        if touch_dir is not None and touch_dir.length_squared() > 0.01:
            d = Vector2(touch_dir)
            if d.length() > 1:
                d.scale_to_length(1.0)
        else:
            d = Vector2((keys[pygame.K_d] or keys[pygame.K_RIGHT]) - (keys[pygame.K_a] or keys[pygame.K_LEFT]),
                        (keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP]))
            if d.length_squared() > 0:
                d = d.normalize()
        self.move_dir = d
        self.vel += d * PLAYER_SPEED_ACCEL * dt
        self.vel *= max(0.0, 1 - PLAYER_DAMPING * dt)
        self.pos += self.vel * dt
        r = self.RADIUS
        if not r <= self.pos.x <= W - r:
            self.pos.x = clamp(self.pos.x, r, W - r)
            self.vel.x = 0
        if not r <= self.pos.y <= H - r:
            self.pos.y = clamp(self.pos.y, r, H - r)
            self.vel.y = 0
        to_mouse = Vector2(mouse) - self.pos
        if to_mouse.length_squared() > 4:
            self.angle = math.degrees(math.atan2(to_mouse.y, to_mouse.x))
        self.invuln = max(0.0, self.invuln - dt)
        self.flash = max(0.0, self.flash - dt)

    def draw(self, surf, t):
        if self.invuln > 0 and int(t * 20) % 2 == 0:
            return                                   # blink while invulnerable
        if Player._sprite is None:
            Player._sprite = build_jet_sprite()
        step = int(round(self.angle / 3)) % 120                                  # 3-degree steps
        img = Player._rotated.get(step)
        if img is None:
            img = pygame.transform.rotozoom(Player._sprite, -step * 3, 1.0)
            Player._rotated[step] = img

        draw_glow(surf, self.pos, 58, (200, 210, 230), 0.3)                       # subtle white aura
        # engine afterburners (brighter while moving)
        thrust = 0.45 + 0.45 * min(1.0, self.vel.length() / 300)
        flicker = random.uniform(0.85, 1.15)
        for e in self.ENGINES:
            nozzle = self.pos + e.rotate(self.angle)
            draw_glow(surf, nozzle, 15 * thrust * flicker, (190, 215, 255), thrust)
        surf.blit(img, img.get_rect(center=(int(self.pos.x), int(self.pos.y))))
        for e in self.ENGINES:
            pygame.draw.circle(surf, WHITE, self.pos + e.rotate(self.angle), 2)
        if self.flash > 0:                                                         # muzzle flash
            nose = self.pos + self.forward * 37
            draw_glow(surf, nose, 40, YELLOW, 1.0)
            pygame.draw.circle(surf, WHITE, nose, 7)


class Bullet:
    def __init__(self, pos, vel, damage, friendly=True, color=PINK, core=(255, 150, 200)):
        self.color = color
        self.core = core
        self.pos = Vector2(pos)
        self.vel = Vector2(vel)
        self.damage = damage
        self.friendly = friendly
        self.life = 1.4 if friendly else 4.0
        self.radius = 4 if friendly else 7

    def update(self, dt):
        self.pos += self.vel * dt
        self.life -= dt

    def alive(self):
        return self.life > 0 and -60 < self.pos.x < W + 60 and -60 < self.pos.y < H + 60

    def draw(self, surf):
        if self.friendly:
            tail = self.pos - self.vel.normalize() * 20
            draw_glow(surf, self.pos, 16, YELLOW, 0.8)
            pygame.draw.line(surf, (255, 200, 90), tail, self.pos, 5)
            pygame.draw.line(surf, WHITE, tail.lerp(self.pos, 0.4), self.pos, 2)
        else:
            draw_glow(surf, self.pos, 22, self.color, 0.9)
            pygame.draw.circle(surf, self.core, self.pos, 6)
            pygame.draw.circle(surf, WHITE, self.pos, 3)


# ---- red attack helicopter graphics (built once, rotated/animated from caches) ----
HELI_SIZE = 84                   # body sprite size in pixels
HELI_SS = 4                      # supersampling factor
HELI_SCALE = 1.25                # drawing scale of the helicopter design
HELI_TAIL_ROTOR = Vector2(-27, 2.8) * HELI_SCALE   # tail rotor position relative to the main rotor hub
ROTOR_FRAMES = 15                # main rotor: 4 blades -> 90 deg of symmetry, 6 deg per frame
TAIL_FRAMES = 15                 # tail rotor: 2 blades -> 180 deg of symmetry, 12 deg per frame
H_RED = (205, 40, 46)
H_RED_LIT = (240, 78, 76)
H_RED_SHADE = (150, 24, 32)
H_RED_DARK = (105, 16, 24)
H_OUTLINE = (48, 10, 14)
H_METAL = (58, 60, 68)
H_GLASS = (70, 120, 165)
_HELI = {}


def _heli_canvas(size):
    return pygame.Surface((size * HELI_SS, size * HELI_SS), pygame.SRCALPHA)


def build_heli_body():
    """Top-down attack helicopter facing right (+x); main rotor hub at the centre."""
    big = _heli_canvas(HELI_SIZE)
    c = HELI_SIZE * HELI_SS / 2
    k = HELI_SS * HELI_SCALE
    ow = int(k)                                  # outline width

    def P(x, y):
        return (c + x * k, c + y * k)

    def poly(points, color, outline=True):
        pts = [P(x, y) for x, y in points]
        pygame.draw.polygon(big, color, pts)
        if outline:
            pygame.draw.polygon(big, H_OUTLINE, pts, ow)

    def line(a, b, color, width=1.0):
        pygame.draw.line(big, color, P(*a), P(*b), max(1, int(width * k)))

    def ellipse(cx, cy, w, h, color, width=0):
        r = pygame.Rect(0, 0, int(w * k), int(h * k))
        r.center = (int(c + cx * k), int(c + cy * k))
        pygame.draw.ellipse(big, color, r, width)

    for s in (-1, 1):
        # landing skids with struts
        line((-7, 8 * s), (13, 8 * s), (40, 40, 48), 1.6)
        line((13, 8 * s), (15.5, 6.8 * s), (40, 40, 48), 1.4)
        line((-3, 8 * s), (-3, 5 * s), (40, 40, 48), 1.0)
        line((8, 8 * s), (8, 5.5 * s), (40, 40, 48), 1.0)
        # stub wings carrying rocket pods and missiles
        poly([(4, 4 * s), (-1, 4 * s), (-2, 12.5 * s), (3, 12.5 * s)], H_RED_DARK)
        poly([(-5, 11 * s), (7, 11 * s), (8.5, 12.5 * s), (7, 14 * s), (-5, 14 * s)], H_METAL)
        for yy in (11.8, 13.2):
            pygame.draw.circle(big, (25, 25, 30), P(7.6, yy * s), 0.5 * k)
        line((-4, 15.6 * s), (6, 15.6 * s), (95, 98, 108), 1.2)
        pygame.draw.circle(big, H_RED_LIT, P(6.4, 15.6 * s), 0.7 * k)
        # tail stabilisers
        poly([(-20, 0), (-22, 6 * s), (-25, 6 * s), (-24, 0)], H_RED_SHADE if s > 0 else H_RED)

    # tail boom and fin
    poly([(-8, 2.4), (-27, 1.3), (-27, -1.3), (-8, -2.4)], H_RED)
    poly([(-8, 0), (-27, 0), (-27, 1.3), (-8, 2.4)], H_RED_SHADE, outline=False)
    poly([(-23, 0.9), (-29.5, 1.6), (-30.5, 0), (-29.5, -1.6), (-23, -0.9)], H_RED_DARK)

    # main fuselage: lit top half, shaded lower half, highlight along the spine
    body = [(19, 0), (17, 2.8), (12, 5), (4, 6.5), (-4, 6), (-9, 3.6), (-10.5, 0),
            (-9, -3.6), (-4, -6), (4, -6.5), (12, -5), (17, -2.8)]
    poly(body, H_RED)
    poly([(19, 0), (17, 2.8), (12, 5), (4, 6.5), (-4, 6), (-9, 3.6), (-10.5, 0)], H_RED_SHADE, outline=False)
    poly([(16, -1.4), (10, -3.6), (-6, -4.4), (-6, -2.6), (10, -1.8)], H_RED_LIT, outline=False)
    pygame.draw.polygon(big, H_OUTLINE, [P(x, y) for x, y in body], ow)

    # engine housing with exhausts and intake grille
    poly([(3, 3.6), (-8, 3.2), (-8, -3.2), (3, -3.6)], (88, 26, 32))
    for yy in (-1.9, 1.9):
        poly([(-8, yy - 1), (-10.5, yy - 0.9), (-10.5, yy + 0.9), (-8, yy + 1)], (52, 52, 58))
    for xx in (1.5, -0.5, -2.5):
        line((xx, -2.6), (xx, 2.6), (60, 16, 22), 0.4)

    # tandem glass cockpit with reflections
    for cx, w in ((13, 7), (7, 6.2)):
        ellipse(cx, 0, w, 5.2, H_GLASS)
        ellipse(cx, 0, w, 5.2, (25, 30, 40), ow)
        ellipse(cx + 0.8, -1.1, w * 0.45, 1.3, (190, 225, 255))
    line((10, -2.4), (10, 2.4), (25, 30, 40), 0.6)

    # nose sensor turret and chin cannon
    line((16, 0.6), (23.5, 0.6), (45, 45, 52), 1.2)
    pygame.draw.circle(big, (40, 40, 46), P(18.5, 0), 1.9 * k)
    pygame.draw.circle(big, (120, 185, 230), P(18.9, -0.4), 0.7 * k)

    return pygame.transform.smoothscale(big, (HELI_SIZE, HELI_SIZE))


def build_rotor_frames(size, radius, blades, frames, step_deg, blade_w):
    """Pre-rendered spinning white rotor frames with motion-blur ghost blades and a soft glow."""
    out = []
    k = HELI_SS
    for f in range(frames):
        big = _heli_canvas(size)
        c = size * k / 2
        pygame.draw.circle(big, (255, 255, 255, 18), (c, c), radius * k)       # blurred rotor disc
        base = f * step_deg
        for b in range(blades):
            for lag, alpha in ((24, 30), (16, 55), (8, 100), (0, 255)):
                a = math.radians(base + b * 360 / blades - lag)
                d = Vector2(math.cos(a), math.sin(a))
                n = Vector2(-d.y, d.x)
                root, tip = Vector2(c, c) + d * 2 * k, Vector2(c, c) + d * radius * k
                w0, w1 = blade_w * k / 2, blade_w * 0.7 * k / 2
                col = (255, 255, 255, alpha)                                   # pure white blades
                pygame.draw.polygon(big, col, [root + n * w0, tip + n * w1, tip - n * w1, root - n * w0])
                if lag == 0:                                                   # bright blade tip
                    pygame.draw.line(big, (255, 255, 255, 255), tip - d * 3 * k, tip, max(1, int(w1 * 2)))
        pygame.draw.circle(big, (200, 200, 210, 255), (c, c), 2.6 * k)           # hub
        pygame.draw.circle(big, (255, 255, 255, 255), (c, c), 1.1 * k)
        frame = pygame.transform.smoothscale(big, (size, size))
        # subtle white glow baked around the blades (no extra cost while playing)
        glow = pygame.transform.smoothscale(frame, (max(1, size // 4), max(1, size // 4)))
        glow = pygame.transform.smoothscale(glow, (size, size))
        glow.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
        glow.fill((255, 255, 255, 150), special_flags=pygame.BLEND_RGBA_MULT)
        final = pygame.Surface((size, size), pygame.SRCALPHA)
        final.blit(glow, (0, 0))
        final.blit(frame, (0, 0))
        out.append(final)
    return out


def heli_assets():
    if not _HELI:
        body = build_heli_body()
        flash = body.copy()
        flash.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)   # white hit flash
        _HELI["body"], _HELI["flash"] = body, flash
        _HELI["rotor"] = build_rotor_frames(62, 27.5, 4, ROTOR_FRAMES, 90 / ROTOR_FRAMES, 3.2)
        _HELI["tail"] = build_rotor_frames(20, 7.5, 2, TAIL_FRAMES, 180 / TAIL_FRAMES, 1.8)
        _HELI["rot"] = {}
    return _HELI


# ---- green military battle tank graphics (hull and turret sprites, rotated from caches) ----
TANK_SS = 4                      # supersampling factor
TANK_HULL_SIZE = 64
TANK_TURRET_SIZE = 78
T_OLIVE = (80, 100, 54)
T_OLIVE_LIT = (104, 126, 70)
T_OLIVE_SHADE = (60, 78, 40)
T_OLIVE_DARK = (44, 58, 30)
T_CAMO = ((52, 70, 36), (96, 88, 56), (116, 134, 82), (40, 54, 30))
T_OUTLINE = (26, 34, 20)
T_TRACK = (32, 34, 30)
T_TRACK_IN = (54, 56, 50)
T_WHEELS = (-17, -10, -3, 4, 11, 18)   # road wheel x positions
_TANK = {}


def _camo(draw_poly, rng, x_range, y_range, count, size):
    """Random camouflage blotches kept inside the given box."""
    for _ in range(count):
        cx, cy = rng.uniform(*x_range), rng.uniform(*y_range)
        r = rng.uniform(size * 0.5, size)
        pts = []
        for i in range(7):
            a = i / 7 * math.tau
            rr = r * rng.uniform(0.6, 1.0)
            pts.append((clamp(cx + math.cos(a) * rr, *x_range), clamp(cy + math.sin(a) * rr * 0.75, *y_range)))
        draw_poly(pts, rng.choice(T_CAMO))


def build_tank_hull():
    """Top-down tank hull with tracks, facing right (+x)."""
    k = TANK_SS
    big = pygame.Surface((TANK_HULL_SIZE * k, TANK_HULL_SIZE * k), pygame.SRCALPHA)
    c = TANK_HULL_SIZE * k / 2

    def P(x, y):
        return (c + x * k, c + y * k)

    def poly(points, color, outline=True):
        pts = [P(x, y) for x, y in points]
        pygame.draw.polygon(big, color, pts)
        if outline:
            pygame.draw.polygon(big, T_OUTLINE, pts, k)

    def line(a, b, color, width=1.0):
        pygame.draw.line(big, color, P(*a), P(*b), max(1, int(width * k)))

    def rrect(x1, y1, x2, y2, color, radius=2):
        r = pygame.Rect(int(c + x1 * k), int(c + y1 * k), int((x2 - x1) * k), int((y2 - y1) * k))
        pygame.draw.rect(big, color, r, border_radius=int(radius * k))
        pygame.draw.rect(big, T_OUTLINE, r, k, border_radius=int(radius * k))

    for s in (-1, 1):
        y_out, y_in = 17.5 * s, 10.5 * s
        rrect(-24, min(y_out, y_in), 24, max(y_out, y_in), T_TRACK, 3)              # track
        rrect(-22.5, min(13 * s, 15 * s), 22.5, max(13 * s, 15 * s), T_TRACK_IN, 1)  # wheel channel
        for x in T_WHEELS:                                                          # road wheels
            pygame.draw.circle(big, (78, 82, 72), P(x, 14 * s), 2.9 * k)
            pygame.draw.circle(big, (36, 38, 34), P(x, 14 * s), 2.9 * k, k)
            pygame.draw.circle(big, (46, 48, 42), P(x, 14 * s), 1.1 * k)
        for x in (-21.5, 21.5):                                                     # sprocket & idler
            pygame.draw.circle(big, (66, 70, 62), P(x, 14 * s), 2.4 * k)
            pygame.draw.circle(big, (30, 32, 28), P(x, 14 * s), 2.4 * k, k)
        poly([(-23, 10 * s), (23, 10 * s), (23, 11.6 * s), (-23, 11.6 * s)], T_OLIVE_DARK)  # side skirt

    hull = [(23, -10), (26, -7), (26, 7), (23, 10), (-22, 10), (-25, 8), (-25, -8), (-22, -10)]
    poly(hull, T_OLIVE)
    poly([(26, 0), (26, 7), (23, 10), (-22, 10), (-25, 8), (-25, 0)], T_OLIVE_SHADE, outline=False)
    _camo(lambda pts, col: poly(pts, col, outline=False), random.Random(11), (-23, 24), (-9, 9), 9, 4.5)
    poly([(18, -9), (23, -10), (26, -7), (26, 7), (23, 10), (18, 9)], T_OLIVE_LIT)      # front glacis plate
    line((19, -8.5), (19, 8.5), T_OLIVE_DARK, 0.5)
    pygame.draw.polygon(big, T_OUTLINE, [P(x, y) for x, y in hull], k)
    # engine deck: grilles and exhausts
    rrect(-23, -7, -13, 7, T_OLIVE_DARK, 1)
    for x in (-21, -19, -17, -15):
        line((x, -6), (x, 6), (30, 40, 22), 0.5)
    for y in (-5, 5):
        rrect(-26.5, y - 1.5, -24, y + 1.5, (50, 50, 46), 0.5)
    # driver hatch, periscopes, headlights, tools and tow hooks
    rrect(19.5, -6, 23.5, -1.5, T_OLIVE_DARK, 1)
    line((20.5, -5.2), (22.5, -5.2), (130, 170, 190), 0.6)
    for y in (-8, 8):
        pygame.draw.circle(big, (225, 220, 170), P(25, y * 0.85), 0.9 * k)
        line((26, y * 0.6), (27.2, y * 0.6), T_OUTLINE, 0.8)
    line((-8, 8.6), (8, 8.6), (110, 92, 60), 0.7)                                   # spade / tools
    line((-8, -8.6), (4, -8.6), (90, 90, 86), 0.7)
    return pygame.transform.smoothscale(big, (TANK_HULL_SIZE, TANK_HULL_SIZE))


def build_tank_turret():
    """Turret with a long cannon, facing right (+x); pivot at the sprite centre."""
    k = TANK_SS
    big = pygame.Surface((TANK_TURRET_SIZE * k, TANK_TURRET_SIZE * k), pygame.SRCALPHA)
    c = TANK_TURRET_SIZE * k / 2

    def P(x, y):
        return (c + x * k, c + y * k)

    def poly(points, color, outline=True):
        pts = [P(x, y) for x, y in points]
        pygame.draw.polygon(big, color, pts)
        if outline:
            pygame.draw.polygon(big, T_OUTLINE, pts, k)

    def line(a, b, color, width=1.0):
        pygame.draw.line(big, color, P(*a), P(*b), max(1, int(width * k)))

    # cannon: barrel, fume extractor, muzzle brake and a top highlight
    poly([(11, -1.4), (34, -1.4), (34, 1.4), (11, 1.4)], T_OLIVE_SHADE)
    line((12, -0.7), (33, -0.7), T_OLIVE_LIT, 0.6)
    poly([(21, -2.1), (25, -2.1), (25, 2.1), (21, 2.1)], T_OLIVE)
    poly([(33, -2.2), (37, -2.2), (37, 2.2), (33, 2.2)], (58, 62, 52))
    line((35, -2.2), (35, 2.2), T_OUTLINE, 0.5)
    poly([(9, -3.4), (14, -3), (14, 3), (9, 3.4)], T_OLIVE_DARK)                   # gun mantlet

    turret = [(12, -4.5), (12, 4.5), (6, 9), (-8, 9.5), (-12, 6.5), (-12, -6.5), (-8, -9.5), (6, -9)]
    poly(turret, T_OLIVE)
    poly([(12, 0), (12, 4.5), (6, 9), (-8, 9.5), (-12, 6.5), (-12, 0)], T_OLIVE_SHADE, outline=False)
    _camo(lambda pts, col: poly(pts, col, outline=False), random.Random(5), (-10, 10), (-7.5, 7.5), 6, 3.6)
    poly([(10, -4), (6, -7.5), (-6, -8), (-6, -6.2), (5, -5.8)], T_OLIVE_LIT, outline=False)   # lit armour edge
    pygame.draw.polygon(big, T_OUTLINE, [P(x, y) for x, y in turret], k)
    # bustle rack at the back
    for y in (-5, -2, 1, 4):
        line((-12, y), (-14.5, y), T_OUTLINE, 0.6)
    line((-14.5, -6), (-14.5, 5.5), T_OUTLINE, 0.6)
    # commander cupola with periscopes and machine gun, loader hatch, sensors
    pygame.draw.circle(big, T_OLIVE_DARK, P(-3, -4), 3.4 * k)
    pygame.draw.circle(big, T_OUTLINE, P(-3, -4), 3.4 * k, k)
    pygame.draw.circle(big, T_OLIVE_LIT, P(-3, -4), 2.1 * k)
    for a in range(0, 360, 60):
        v = Vector2(1, 0).rotate(a) * 2.9
        pygame.draw.circle(big, (130, 170, 190), P(-3 + v.x, -4 + v.y), 0.45 * k)
    line((-1, -4), (7, -4.8), (40, 42, 38), 0.7)
    pygame.draw.circle(big, T_OLIVE_DARK, P(-3, 4.2), 2.5 * k)
    pygame.draw.circle(big, T_OUTLINE, P(-3, 4.2), 2.5 * k, k)
    line((-4.3, 4.2), (-1.7, 4.2), T_OUTLINE, 0.5)
    pygame.draw.rect(big, (50, 54, 46), pygame.Rect(int(c + 4 * k), int(c + 5 * k), int(3 * k), int(2 * k)))
    pygame.draw.circle(big, (120, 190, 230), P(5.5, 6), 0.6 * k)
    line((-9, 7.5), (-9, 13), (40, 42, 38), 0.4)                                   # radio antenna
    return pygame.transform.smoothscale(big, (TANK_TURRET_SIZE, TANK_TURRET_SIZE))


def tank_assets():
    if not _TANK:
        for name, img in (("hull", build_tank_hull()), ("turret", build_tank_turret())):
            flash = img.copy()
            flash.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)    # white hit flash
            _TANK[name], _TANK[name + "_flash"] = img, flash
        _TANK["rot"] = {}
    return _TANK


# ---- purple boss fighter jet graphics ----
BOSS_SIZE = 132                  # sprite size in pixels (about 1.7x the player's jet)
BOSS_SS = 3                      # supersampling factor
BOSS_ENGINES = (Vector2(-56, -7), Vector2(-56, 7))
BOSS_NOSE = Vector2(58, 0)
B_BASE = (64, 36, 96)
B_LIT = (96, 60, 138)
B_SHADE = (42, 22, 64)
B_DEEP = (26, 14, 40)
B_GLOW = (178, 108, 255)
B_OUTLINE = (16, 8, 26)
B_METAL = (70, 66, 86)
BOSS_SHOT_COLOR = (190, 90, 255)
_BOSS = {}


def build_boss_jet():
    """Large dark-purple stealth fighter, facing right (+x), with a violet halo."""
    k = BOSS_SS
    big = pygame.Surface((BOSS_SIZE * k, BOSS_SIZE * k), pygame.SRCALPHA)
    c = BOSS_SIZE * k / 2

    def P(x, y):
        return (c + x * k, c + y * k)

    def poly(points, color, outline=True):
        pts = [P(x, y) for x, y in points]
        pygame.draw.polygon(big, color, pts)
        if outline:
            pygame.draw.polygon(big, B_OUTLINE, pts, k)

    def line(a, b, color, width=1.0):
        pygame.draw.line(big, color, P(*a), P(*b), max(1, int(width * k)))

    for s in (-1, 1):
        lit = B_LIT if s < 0 else B_BASE
        shade = B_BASE if s < 0 else B_SHADE
        # all-moving tail stabilators
        poly([(-36, 10 * s), (-50, 27 * s), (-57, 27 * s), (-52, 9 * s)], shade)
        # big swept main wings with armour panels, hardpoints and glowing edges
        poly([(14, 10 * s), (-24, 46 * s), (-38, 46 * s), (-36, 12 * s)], lit)
        poly([(2, 18 * s), (-22, 41 * s), (-30, 41 * s), (-28, 18 * s)], shade, outline=False)
        line((13, 11 * s), (-23, 45 * s), B_GLOW, 1.0)                       # leading-edge glow
        line((-24, 46.5 * s), (-38, 46.5 * s), B_GLOW, 1.4)                  # wingtip light strip
        line((-10, 22 * s), (-32, 22 * s), B_OUTLINE, 0.5)                   # flap line
        for y, ln in ((26, 16), (34, 12)):                                   # under-wing missiles
            line((-4 - (y - 26) * 0.6, y * s), (-4 - ln - (y - 26) * 0.6, y * s), B_METAL, 1.8)
            pygame.draw.circle(big, B_GLOW, P(-4 - (y - 26) * 0.6, y * s), 1.1 * k)
        # canards
        poly([(34, 6 * s), (20, 20 * s), (15, 20 * s), (18, 8 * s)], lit)
        # engine nacelles and nozzles
        poly([(-28, 3 * s), (-52, 3.5 * s), (-52, 11 * s), (-28, 11.5 * s)], B_SHADE)
        poly([(-52, 3.5 * s), (-58, 4.2 * s), (-58, 10.3 * s), (-52, 11 * s)], B_METAL)
        pygame.draw.circle(big, (30, 18, 40), P(-57.2, 7.2 * s), 2.6 * k)
        pygame.draw.circle(big, (255, 130, 255), P(-57.2, 7.2 * s), 2.6 * k, k)

    # fuselage with shading and a glowing spine
    body = [(58, 0), (46, 4), (32, 7.5), (12, 10.5), (-20, 11.5), (-42, 10), (-50, 7), (-50, -7),
            (-42, -10), (-20, -11.5), (12, -10.5), (32, -7.5), (46, -4)]
    poly(body, B_BASE)
    poly([(58, 0), (46, 4), (32, 7.5), (12, 10.5), (-20, 11.5), (-42, 10), (-50, 7), (-50, 0)],
         B_SHADE, outline=False)
    poly([(50, -0.8), (32, -4), (-36, -5.2), (-36, -2.6), (32, -1.4)], B_LIT, outline=False)
    line((44, 0), (-46, 0), B_GLOW, 0.5)
    pygame.draw.polygon(big, B_OUTLINE, [P(x, y) for x, y in body], k)

    for s in (-1, 1):
        # side air intakes
        poly([(22, 8.6 * s), (4, 10.6 * s), (4, 14 * s), (19, 12 * s)], B_DEEP)
        # twin canted vertical fins with glowing tips
        poly([(-22, 6 * s), (-44, 19 * s), (-49, 19 * s), (-38, 5 * s)], B_LIT if s < 0 else B_BASE)
        line((-44, 19 * s), (-49, 19 * s), B_GLOW, 1.2)

    # armour panel lines and sensor
    for x in (24, 6, -14, -30):
        line((x, -9.5), (x, 9.5), B_OUTLINE, 0.4)
    pygame.draw.circle(big, (255, 90, 160), P(52, 0), 1.2 * k)

    # cockpit glass with reflection
    canopy = pygame.Rect(0, 0, int(20 * k), int(7.5 * k))
    canopy.center = (int(c + 34 * k), int(c))
    pygame.draw.ellipse(big, (56, 34, 110), canopy)
    pygame.draw.ellipse(big, (150, 110, 230), canopy, k)
    shine = pygame.Rect(0, 0, int(9 * k), int(2 * k))
    shine.center = (int(c + 37 * k), int(c - 1.6 * k))
    pygame.draw.ellipse(big, (215, 190, 255), shine)

    jet = pygame.transform.smoothscale(big, (BOSS_SIZE, BOSS_SIZE))
    halo = pygame.transform.smoothscale(jet, (BOSS_SIZE // 6, BOSS_SIZE // 6))
    halo = pygame.transform.smoothscale(halo, (BOSS_SIZE, BOSS_SIZE))
    halo.fill((255, 255, 255, 0), special_flags=pygame.BLEND_RGBA_MAX)
    halo.fill((170, 90, 255, 255), special_flags=pygame.BLEND_RGBA_MULT)       # violet halo
    final = pygame.Surface((BOSS_SIZE, BOSS_SIZE), pygame.SRCALPHA)
    final.blit(halo, (0, 0))
    final.blit(jet, (0, 0))
    return final


def boss_assets():
    if not _BOSS:
        _BOSS["jet"] = build_boss_jet()
        _BOSS["rot"] = {}
    return _BOSS


ENEMY_TYPES = {
    #           radius  hp   speed colour  score damage  kamikaze
    "chaser":  (14,     30,  175,  RED,    100,  12,     True),
    "shooter": (17,     55,  120,  GREEN,  200,  10,     True),
    "tank":    (36,     650, 95,   PURPLE, 2000, 25,     False),   # purple boss fighter jet
}


class Enemy:
    def __init__(self, kind, pos, difficulty):
        r, hp, speed, color, score, dmg, kamikaze = ENEMY_TYPES[kind]
        self.kind = kind
        self.pos = Vector2(pos)
        self.vel = Vector2()
        self.radius = r
        self.max_hp = self.hp = hp * (1 + (difficulty - 1) * 0.7)
        self.speed = speed * min(1.7, 0.92 + 0.12 * difficulty) * ENEMY_SPEED_SCALE
        self.color = color
        self.score = score
        self.damage = dmg
        self.kamikaze = kamikaze
        self.flash = 0.0
        self.angle = random.uniform(0, 360)
        self.spin = random.uniform(-90, 90)
        self.timer = random.uniform(1.0, 2.2)
        self.spawn_t = 0.45
        self.orbit = random.choice((-1, 1))
        self.wobble = random.uniform(0, math.tau)
        self.facing = Vector2(1, 0)
        self.rotor_phase = random.uniform(0, 360)      # each helicopter spins independently
        self.rotor_speed = random.uniform(1050, 1250)  # degrees per second
        self.hull_angle = None                          # tank visuals: hull heading, turret aim,
        self.turret_angle = None                        # track scroll, cannon recoil
        self.track_scroll = 0.0
        self.recoil = 0.0
        self._last_draw_t = None
        self._last_timer = self.timer
        self.jet_angle = None                           # boss fighter heading (turns smoothly)
        self.volley = 0

    def update(self, dt, game):
        player = game.player
        to_p = player.pos - self.pos
        dist = to_p.length() or 1.0
        dirv = to_p / dist
        side = Vector2(-dirv.y, dirv.x)
        self.wobble += dt * 3
        self.timer -= dt
        desired = dirv * self.speed

        if self.kind == "chaser":
            desired = (dirv + side * math.sin(self.wobble) * 0.35) * self.speed
        elif self.kind == "shooter":
            if dist > 380:
                desired = dirv * self.speed
            elif dist < 250:
                desired = -dirv * self.speed
            else:
                desired = side * self.orbit * self.speed * 0.8
            on_screen = 0 < self.pos.x < W and 0 < self.pos.y < H
            if self.timer <= 0 and on_screen and self.spawn_t <= 0:
                lead = player.pos + player.vel * (dist / 380) * 0.6       # aim ahead of the player
                aim = (lead - self.pos)
                if aim.length_squared() > 0:
                    aim = aim.normalize()
                    game.enemy_bullets.append(Bullet(self.pos + aim * 20, aim * 380, 10, friendly=False))
                    game.audio.play("enemy_shot", 0.7)
                self.timer = random.uniform(1.3, 2.1) / game.difficulty ** 0.3

        elif self.kind == "tank":                      # purple boss fighter jet
            face = math.degrees(math.atan2(dirv.y, dirv.x))
            if self.jet_angle is None:
                self.jet_angle = face
            diff = (face - self.jet_angle + 180) % 360 - 180
            self.jet_angle += clamp(diff, -110 * dt, 110 * dt)       # turns like an aircraft
            if dist > 340:
                desired = dirv * self.speed * 1.6
            elif dist < 200:
                desired = -dirv * self.speed
            else:
                desired = side * self.orbit * self.speed * 1.3 + dirv * self.speed * 0.2
            heading = Vector2(1, 0).rotate(self.jet_angle)
            for e_off in BOSS_ENGINES:                                 # exhaust trail
                if random.random() < 0.7:
                    nozzle = self.pos + e_off.rotate(self.jet_angle)
                    v = -heading * random.uniform(80, 170) + Vector2(random.uniform(-25, 25), random.uniform(-25, 25))
                    game.particles.append(Particle(nozzle, v, random.uniform(0.2, 0.4), random.uniform(2, 3.5),
                                                   random.choice(((200, 90, 255), (255, 120, 220), B_GLOW)), 3))
            on_screen = 0 < self.pos.x < W and 0 < self.pos.y < H
            if self.timer <= 0 and on_screen and self.spawn_t <= 0:
                self.volley += 1
                nose = self.pos + BOSS_NOSE.rotate(self.jet_angle)
                lead = player.pos + player.vel * (dist / 430) * 0.6
                aim = lead - nose
                if aim.length_squared() > 0:
                    aim = aim.normalize()
                    angles = range(-36, 37, 12) if self.volley % 3 == 0 else (-8, 0, 8)   # fan or triple
                    for a in angles:
                        d = aim.rotate(a)
                        game.enemy_bullets.append(Bullet(nose + d * 6, d * 430, 12, friendly=False,
                                                         color=BOSS_SHOT_COLOR, core=(225, 170, 255)))
                    game.audio.play("boss_shot", 0.9)
                    game.particles.append(Particle(nose, (0, 0), 0.12, 10, B_GLOW, 0))
                self.timer = random.uniform(1.6, 2.2) / game.difficulty ** 0.3

        # keep a little space from other enemies
        push = Vector2()
        for o in game.enemies:
            if o is self:
                continue
            d = self.pos - o.pos
            min_d = self.radius + o.radius + 6
            l2 = d.length_squared()
            if 0 < l2 < min_d * min_d:
                l = math.sqrt(l2)
                push += d / l * (min_d - l)

        if push.length_squared() > 36:
            push.scale_to_length(6)
        self.vel += (desired - self.vel) * min(1.0, 4.0 * dt)
        self.pos += self.vel * dt + push * 0.5
        self.angle += self.spin * dt
        self.flash = max(0.0, self.flash - dt)
        self.spawn_t = max(0.0, self.spawn_t - dt)
        self.facing = dirv

    def hit(self, damage, direction):
        self.hp -= damage
        self.flash = 0.08
        self.vel += direction * (140 * 14 / self.radius)

    def draw(self, surf, t):
        scale = 1 - self.spawn_t / 0.45
        r = self.radius * (0.2 + 0.8 * scale)
        col = WHITE if self.flash > 0 else self.color
        draw_glow(surf, self.pos, r * 2.8, self.color, 0.55 * scale)
        face = math.degrees(math.atan2(self.facing.y, self.facing.x))

        if self.kind == "chaser":
            self.draw_helicopter(surf, t, scale, face)
            self.draw_health_bar(surf)
            return
        if self.kind == "shooter":
            self.draw_battle_tank(surf, t, scale, face)
            self.draw_health_bar(surf)
            return
        if self.kind == "tank":
            self.draw_boss_jet(surf, t, scale, face)              # health shown on the boss bar
            return
        self.draw_health_bar(surf)

    def draw_boss_jet(self, surf, t, scale, face):
        assets = boss_assets()
        angle = self.jet_angle if self.jet_angle is not None else face
        center = (int(self.pos.x), int(self.pos.y))
        # engine afterburners
        flicker = random.uniform(0.85, 1.15)
        for e_off in BOSS_ENGINES:
            nozzle = self.pos + e_off.rotate(angle) * scale
            draw_glow(surf, nozzle, 26 * flicker * scale, (210, 100, 255), 0.95)
            draw_glow(surf, nozzle, 12 * scale, (255, 200, 255), 0.9)
        if scale < 0.999:
            img = pygame.transform.rotozoom(assets["jet"], -angle, max(0.05, scale))
        else:
            step = int(round(angle / 3)) % 120
            img = assets["rot"].get(step)
            if img is None:
                img = pygame.transform.rotozoom(assets["jet"], -step * 3, 1.0)
                assets["rot"][step] = img
        surf.blit(img, img.get_rect(center=center))
        pulse = 0.5 + 0.5 * math.sin(t * 4)
        for side in (-1, 1):                                       # blinking wingtip lights
            tip = self.pos + Vector2(-31, 46.5 * side).rotate(angle) * scale
            draw_glow(surf, tip, 12, B_GLOW, 0.4 + 0.6 * pulse)
        if self.flash > 0:                                         # hit flash
            draw_glow(surf, self.pos, 60, (230, 200, 255), 0.8)

    def draw_helicopter(self, surf, t, scale, face):
        assets = heli_assets()
        src = assets["flash"] if self.flash > 0 else assets["body"]
        center = (int(self.pos.x), int(self.pos.y))
        spawning = scale < 0.999
        if spawning:
            img = pygame.transform.rotozoom(src, -face, max(0.05, scale))
        else:
            step = int(round(face / 3)) % 120
            key = (step, self.flash > 0)
            img = assets["rot"].get(key)
            if img is None:
                img = pygame.transform.rotozoom(src, -step * 3, 1.0)
                assets["rot"][key] = img
        surf.blit(img, img.get_rect(center=center))

        # tail rotor (fast) and main rotor (on top), each spinning on its own phase
        tail_pos = self.pos + HELI_TAIL_ROTOR.rotate(face) * scale
        tail = assets["tail"][int((self.rotor_phase * 2 + t * 2600) / (180 / TAIL_FRAMES)) % TAIL_FRAMES]
        rotor = assets["rotor"][int((self.rotor_phase + t * self.rotor_speed) / (90 / ROTOR_FRAMES)) % ROTOR_FRAMES]
        if spawning:
            tail = pygame.transform.rotozoom(tail, 0, max(0.05, scale))
            rotor = pygame.transform.rotozoom(rotor, 0, max(0.05, scale))
        surf.blit(tail, tail.get_rect(center=(int(tail_pos.x), int(tail_pos.y))))
        surf.blit(rotor, rotor.get_rect(center=center))

    def draw_battle_tank(self, surf, t, scale, face):
        assets = tank_assets()
        dt = 0.0 if self._last_draw_t is None else clamp(t - self._last_draw_t, 0.0, 0.05)
        self._last_draw_t = t

        def turn_toward(current, target, rate):
            diff = (target - current + 180) % 360 - 180
            return current + clamp(diff, -rate, rate)

        # hull follows the direction of travel, turret smoothly tracks the player
        speed = self.vel.length()
        move_angle = math.degrees(math.atan2(self.vel.y, self.vel.x)) if speed > 1 else face
        if self.hull_angle is None:
            self.hull_angle, self.turret_angle = move_angle, face
        if speed > 15:
            self.hull_angle = turn_toward(self.hull_angle, move_angle, 160 * dt)
        self.turret_angle = turn_toward(self.turret_angle, face, 220 * dt)
        hull_dir = Vector2(1, 0).rotate(self.hull_angle)
        along = self.vel.x * hull_dir.x + self.vel.y * hull_dir.y          # forward speed
        self.track_scroll = (self.track_scroll + along * dt) % 300
        # cannon recoil when the tank has just fired (its attack timer was reset)
        if self.timer > self._last_timer + 0.3:
            self.recoil = 1.0
        self._last_timer = self.timer
        self.recoil = max(0.0, self.recoil - dt * 4)

        flash = self.flash > 0
        spawning = scale < 0.999

        def rotated(name, angle):
            src = assets[name + ("_flash" if flash else "")]
            if spawning:
                return pygame.transform.rotozoom(src, -angle, max(0.05, scale))
            step = int(round(angle / 3)) % 120
            key = (name, step, flash)
            img = assets["rot"].get(key)
            if img is None:
                img = pygame.transform.rotozoom(src, -step * 3, 1.0)
                assets["rot"][key] = img
            return img

        center = (int(self.pos.x), int(self.pos.y))
        hull = rotated("hull", self.hull_angle)
        surf.blit(hull, hull.get_rect(center=center))

        if not flash:
            # moving track links and spinning road wheels
            def world(x, y):
                return self.pos + Vector2(x, y).rotate(self.hull_angle) * scale
            off = self.track_scroll % 3.5
            for side in (-1, 1):
                for i in range(14):
                    x = -23 + ((i * 3.5 + off) % 46)
                    for y0, y1 in ((10.8, 12.8), (15.2, 17.2)):
                        pygame.draw.line(surf, (18, 19, 17), world(x, y0 * side), world(x, y1 * side), 1)
                spin = self.track_scroll / 2.9
                for wx in T_WHEELS:
                    d = Vector2(math.cos(spin), math.sin(spin)) * 2.3
                    a, b = world(wx + d.x, 14 * side + d.y), world(wx - d.x, 14 * side - d.y)
                    pygame.draw.line(surf, (34, 36, 32), a, b, 1)

        turret = rotated("turret", self.turret_angle)
        back = Vector2(1, 0).rotate(self.turret_angle) * (-3 * self.recoil * scale)
        surf.blit(turret, turret.get_rect(center=(int(self.pos.x + back.x), int(self.pos.y + back.y))))
        if self.recoil > 0.6:                                                    # muzzle flash
            muzzle = self.pos + Vector2(38, 0).rotate(self.turret_angle) * scale + back
            draw_glow(surf, muzzle, 22, ORANGE, self.recoil)
            pygame.draw.circle(surf, (255, 240, 200), muzzle, 3)

    def draw_health_bar(self, surf):
        if self.hp < self.max_hp:                    # health bar
            w = self.radius * 2.2
            x, y = self.pos.x - w / 2, self.pos.y - self.radius - 14
            pygame.draw.rect(surf, (40, 10, 20), (int(x), int(y), int(w), 5), border_radius=2)
            pygame.draw.rect(surf, lerp_color(RED, GREEN, max(0.0, self.hp / self.max_hp)),
                             (int(x), int(y), int(w * max(0.0, self.hp / self.max_hp)), 5), border_radius=2)


class Pickup:
    def __init__(self, pos):
        self.pos = Vector2(pos)
        self.life = 10.0
        self.t = random.uniform(0, 6)

    def update(self, dt, player):
        self.life -= dt
        self.t += dt
        d = player.pos - self.pos
        if d.length_squared() < 170 * 170 and d.length_squared() > 0:
            self.pos += d.normalize() * 260 * dt        # magnet towards the player

    def draw(self, surf):
        if self.life < 3 and int(self.t * 8) % 2 == 0:
            return
        pulse = 0.7 + 0.3 * math.sin(self.t * 6)
        draw_glow(surf, self.pos, 34 * pulse, GREEN, 0.8)
        pygame.draw.circle(surf, (20, 70, 40), self.pos, 13)
        pygame.draw.circle(surf, GREEN, self.pos, 13, 2)
        x, y = int(self.pos.x), int(self.pos.y)
        pygame.draw.rect(surf, WHITE, (x - 2, y - 7, 4, 14))
        pygame.draw.rect(surf, WHITE, (x - 7, y - 2, 14, 4))


class WeaponPowerUp:
    """Glowing collectible that gives a temporary double-bullet weapon."""

    def __init__(self, pos, font):
        self.pos = Vector2(pos)
        self.life = WEAPON_DROP_LIFETIME
        self.t = 0.0
        self.label = font.render("+1", True, WHITE)

    def update(self, dt):
        self.life -= dt
        self.t += dt

    def draw(self, surf):
        if self.life < 3 and int(self.t * 8) % 2 == 0:
            return                                       # blink before it disappears
        pulse = 0.75 + 0.25 * math.sin(self.t * 6)
        appear = min(1.0, self.t / 0.3)
        r = 20 * appear
        draw_glow(surf, self.pos, 60 * pulse * appear, ORANGE, 0.9)
        draw_glow(surf, self.pos, 30 * appear, YELLOW, 1.0)
        # rotating outer star
        star = []
        for i in range(10):
            rad = r * (1.25 if i % 2 == 0 else 0.7)
            a = math.radians(self.t * 90 + i * 36)
            star.append(self.pos + Vector2(math.cos(a), math.sin(a)) * rad)
        if r > 2:
            pygame.draw.polygon(surf, (90, 50, 10), star)
            pygame.draw.polygon(surf, YELLOW, star, 2)
            pygame.draw.circle(surf, ORANGE, self.pos, max(1, int(r * 0.6)))
            surf.blit(self.label, self.label.get_rect(center=(int(self.pos.x), int(self.pos.y))))


class Bomb:
    """Dark metallic homing bomb with a fiery trail."""
    HIT_RADIUS = 9
    SHAPE = [(15, 0), (11, 4.5), (-7, 5.5), (-11, 4.5), (-11, -4.5), (-7, -5.5), (11, -4.5)]
    FIN = [(-8, 4.5), (-16, 10), (-18, 10), (-14, 4.5)]

    def __init__(self, pos, direction, target_pos, target_enemy=None):
        self.pos = Vector2(pos)
        self.vel = Vector2(direction) * 260
        self.target = Vector2(target_pos)
        self.enemy = target_enemy
        self.life = 2.6
        self.t = 0.0
        self.done = False

    def update(self, dt, game):
        self.t += dt
        self.life -= dt
        if self.enemy is not None:
            if self.enemy.hp > 0:
                self.target = Vector2(self.enemy.pos)          # follow the locked enemy
            else:
                self.enemy = None                              # target died: fly to its last spot
        to_t = self.target - self.pos
        dist = to_t.length()
        speed = min(BOMB_SPEED, 260 + self.t * 1400)
        if dist > 0:
            desired = to_t / dist * speed
            self.vel += (desired - self.vel) * min(1.0, 7 * dt)
        step = self.vel.length() * dt
        self.pos += self.vel * dt
        # fire and smoke trail
        if self.vel.length_squared() > 0:
            back = self.pos - self.vel.normalize() * 14
            for _ in range(2):
                v = -self.vel * random.uniform(0.05, 0.2) + Vector2(random.uniform(-30, 30), random.uniform(-30, 30))
                game.particles.append(Particle(back, v, random.uniform(0.18, 0.35), random.uniform(2.5, 4.5),
                                               random.choice((ORANGE, YELLOW, (255, 100, 40))), 3))
            if random.random() < 0.5:
                game.particles.append(Particle(back, Vector2(random.uniform(-20, 20), random.uniform(-20, 20)),
                                               random.uniform(0.4, 0.7), random.uniform(4, 7),
                                               (110, 105, 120), 1.5, glow=False))
        # impact: touching an enemy, reaching the target point, or running out of fuel
        for e in game.enemies:
            if e.hp > 0 and e.pos.distance_squared_to(self.pos) < (e.radius + self.HIT_RADIUS) ** 2:
                self.done = True
                return
        if dist <= max(14.0, step) or self.life <= 0 or not (-80 < self.pos.x < W + 80 and -80 < self.pos.y < H + 80):
            self.done = True

    def draw(self, surf):
        ang = math.degrees(math.atan2(self.vel.y, self.vel.x)) if self.vel.length_squared() > 0 else 0

        k = 1.3                                                       # drawing scale

        def pts(shape, mirror=1):
            return [self.pos + Vector2(x * k, y * mirror * k).rotate(ang) for x, y in shape]

        tail = self.pos + Vector2(-13 * k, 0).rotate(ang)
        draw_glow(surf, tail, 26, ORANGE, 0.9)                       # engine fire
        draw_glow(surf, self.pos, 34, (255, 120, 60), 0.35)          # warm aura
        for m in (1, -1):
            fin = pts(self.FIN, m)
            pygame.draw.polygon(surf, METAL_DARK, fin)
            pygame.draw.polygon(surf, METAL_LIGHT, fin, 1)
        body = pts(self.SHAPE)
        pygame.draw.polygon(surf, METAL, body)
        pygame.draw.polygon(surf, METAL_LIGHT, body, 1)
        a, b = pts([(10, -2.6), (-8, -3.2)])                          # metallic highlight
        pygame.draw.line(surf, METAL_LIGHT, a, b, 2)
        blink = 0.5 + 0.5 * math.sin(self.t * 30)
        b1, b2 = pts([(3, -5.2), (3, 5.2)])                           # glowing warning band
        band = lerp_color((255, 70, 40), YELLOW, blink)
        pygame.draw.line(surf, band, b1, b2, 3)
        draw_glow(surf, self.pos + Vector2(3 * k, 0).rotate(ang), 18, (255, 80, 40), 0.5 + 0.5 * blink)
        pygame.draw.circle(surf, (110, 115, 128), self.pos + Vector2(14 * k, 0).rotate(ang), 2)
        pygame.draw.circle(surf, WHITE, tail, 2)


class BombPickup:
    """Collectible that adds one bomb."""

    def __init__(self, pos, font):
        self.pos = Vector2(pos)
        self.life = BOMB_DROP_LIFETIME
        self.t = 0.0
        self.label = font.render("BOMB", True, (255, 200, 140))

    def update(self, dt):
        self.life -= dt
        self.t += dt

    def draw(self, surf):
        if self.life < 3 and int(self.t * 8) % 2 == 0:
            return
        appear = min(1.0, self.t / 0.3)
        pulse = 0.75 + 0.25 * math.sin(self.t * 5)
        r = 15 * appear
        draw_glow(surf, self.pos, 58 * pulse * appear, (255, 90, 40), 0.85)
        if r < 2:
            return
        pygame.draw.circle(surf, (255, 120, 50), self.pos, int(r + 6), 2)        # glowing ring
        pygame.draw.circle(surf, METAL, self.pos, int(r))                         # bomb sphere
        pygame.draw.circle(surf, METAL_LIGHT, self.pos + Vector2(-5, -5) * appear, max(1, int(r * 0.3)))
        pygame.draw.circle(surf, METAL_LIGHT, self.pos, int(r), 1)
        fuse_base = self.pos + Vector2(8, -11) * appear
        fuse_tip = fuse_base + Vector2(6, -6) * appear
        pygame.draw.line(surf, (200, 190, 170), fuse_base, fuse_tip, 2)
        spark = 0.6 + 0.4 * math.sin(self.t * 40)
        draw_glow(surf, fuse_tip, 14 * spark, YELLOW, 1.0)
        pygame.draw.circle(surf, WHITE, fuse_tip, 2)
        surf.blit(self.label, self.label.get_rect(center=(int(self.pos.x), int(self.pos.y + r + 18))))


# ----------------------------------------------------------------------------
# The game
# ----------------------------------------------------------------------------
class Game:
    def __init__(self, windowed=False):
        self.fullscreen = not windowed
        self.screen = self._create_display()
        pygame.display.set_caption(TITLE)
        pygame.mouse.set_visible(False)
        self.clock = pygame.time.Clock()
        self.world = pygame.Surface((W, H))

        self.f_small = pygame.font.Font(None, 26)
        self.f_med = pygame.font.Font(None, 40)
        self.f_big = pygame.font.Font(None, 76)
        self.f_huge = pygame.font.Font(None, 140)
        self._glow_text_cache = {}

        self._loading_screen()
        self.audio = Audio()
        self.background = self._make_background()
        self.vignette_dark = self._make_vignette((0, 0, 0), 200)
        self.vignette_red = self._make_vignette((255, 0, 30), 255)
        self.stars = [[random.uniform(0, W), random.uniform(0, H), random.uniform(0.2, 1.0),
                       random.uniform(0, math.tau)] for _ in range(200)]
        self.best = self._load_best()
        self.state = "menu"
        self.t = 0.0
        self.running = True
        self._touch = {
            "joy_id": None, "joy_origin": Vector2(160, H * 0.73),
            "joy_vec": Vector2(),
            "aim_id": None,  "aim_pos": Vector2(W * 0.75, H * 0.5),
            "shooting": False,
        }
        self.reset()
        self.audio.start_music()

    # ------------------------------------------------------------- setup
    def _create_display(self):
        if IS_ANDROID:
            try:
                return pygame.display.set_mode((W, H), pygame.FULLSCREEN | pygame.SCALED)
            except (pygame.error, TypeError):
                return pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        flags = pygame.SCALED | (pygame.FULLSCREEN if self.fullscreen else 0)
        try:
            return pygame.display.set_mode((W, H), flags, vsync=1)
        except (pygame.error, TypeError):
            try:
                return pygame.display.set_mode((W, H), flags)
            except pygame.error:
                return pygame.display.set_mode((W, H), pygame.FULLSCREEN if self.fullscreen else 0)

    def _loading_screen(self):
        self.screen.fill((8, 6, 20))
        img = self.f_med.render("Generating sounds and graphics...", True, CYAN)
        self.screen.blit(img, img.get_rect(center=(W // 2, H // 2)))
        pygame.display.flip()
        pygame.event.pump()

    def _make_background(self):
        bg = pygame.Surface((W, H))
        top, bottom = (6, 4, 22), (28, 10, 45)
        for y in range(H):
            pygame.draw.line(bg, lerp_color(top, bottom, y / H), (0, y), (W, y))
        # soft nebula clouds: draw small, then scale up for a blur
        neb = pygame.Surface((W // 8, H // 8))
        neb.fill(BLACK)
        for _ in range(9):
            c = random.choice([(90, 30, 140), (20, 70, 140), (140, 30, 90), (30, 110, 120)])
            x, y = random.uniform(0, W // 8), random.uniform(0, H // 8)
            for r in range(26, 0, -2):
                k = (1 - r / 26) ** 1.5 * 0.35
                pygame.draw.circle(neb, (int(c[0] * k), int(c[1] * k), int(c[2] * k)), (x, y), r)
        neb = pygame.transform.smoothscale(neb, (W, H))
        bg.blit(neb, (0, 0), special_flags=pygame.BLEND_ADD)
        grid = pygame.Surface((W, H), pygame.SRCALPHA)
        for x in range(0, W, 64):
            pygame.draw.line(grid, (80, 120, 255, 18), (x, 0), (x, H))
        for y in range(0, H, 64):
            pygame.draw.line(grid, (80, 120, 255, 18), (0, y), (W, y))
        bg.blit(grid, (0, 0))
        return bg

    def _make_vignette(self, color, max_alpha):
        small = pygame.Surface((64, 36), pygame.SRCALPHA)
        for y in range(36):
            for x in range(64):
                d = math.hypot((x - 31.5) / 32, (y - 17.5) / 18)
                a = clamp((d - 0.55) / 0.6, 0, 1) ** 1.6
                small.set_at((x, y), (*color, int(a * max_alpha)))
        return pygame.transform.smoothscale(small, (W, H))

    def _load_best(self):
        try:
            with open(HS_FILE) as f:
                return int(f.read().strip() or 0)
        except (OSError, ValueError):
            return 0

    def _save_best(self):
        try:
            with open(HS_FILE, "w") as f:
                f.write(str(self.best))
        except OSError:
            pass

    def reset(self):
        self.player = Player((W / 2, H / 2))
        self.bullets, self.enemy_bullets, self.enemies = [], [], []
        self.particles, self.rings, self.texts, self.pickups = [], [], [], []
        self.score = self.kills = 0
        self.time = 0.0
        self.level = 1
        self.spawn_timer = 1.2
        self.fire_cd = 0.0
        self.trauma = 0.0
        self.hurt = 0.0
        self.banner = ("", 0.0)
        self.cross_kick = 0.0
        self.over_t = 0.0
        self.new_best = False
        self.weapon_drops = []
        self.weapon_drop_timer = random.uniform(*WEAPON_DROP_EVERY)
        self.double_shot_time = 0.0
        self.weapon_mult = 1             # bullets per shot: 1x normally, +1 for every reward collected
        self.bombs = BOMB_START
        self.pending_booms = []
        self.bomb_cd = 0.0
        self.bomb_list = []
        self.bomb_drops = []
        self.bomb_drop_timer = random.uniform(*BOMB_DROP_EVERY)

    def start_game(self):
        self.reset()
        self.state = "playing"
        self.banner = ("SURVIVE!", 1.6)
        self.audio.play("start")
        if IS_ANDROID:
            self._touch.update({"joy_id": None, "joy_vec": Vector2(),
                                "aim_id": None, "shooting": False})

    @property
    def difficulty(self):
        return 1 + self.time / 60

    # ------------------------------------------------------------- effects
    def add_trauma(self, amount):
        self.trauma = min(1.0, self.trauma + amount)

    def sparks(self, pos, direction, color, count=6, speed=320):
        base = math.degrees(math.atan2(direction.y, direction.x))
        for _ in range(count):
            v = Vector2(1, 0).rotate(base + random.uniform(-35, 35)) * random.uniform(0.3, 1) * speed
            self.particles.append(Particle(pos, v, random.uniform(0.15, 0.35), random.uniform(1.5, 3), color, 5))

    def explosion(self, pos, color, size):
        n = int(size * 2.4)
        power = (size / 14) ** 0.5
        for _ in range(n):
            v = Vector2(1, 0).rotate(random.uniform(0, 360)) * random.uniform(60, 430) * power
            c = random.choice((color, color, ORANGE, YELLOW, WHITE))
            self.particles.append(Particle(pos, v, random.uniform(0.35, 0.9), random.uniform(2, 4.5), c, 3.2))
        for _ in range(int(size * 0.5)):                                       # smoke
            v = Vector2(1, 0).rotate(random.uniform(0, 360)) * random.uniform(20, 90)
            self.particles.append(Particle(pos, v, random.uniform(0.6, 1.2), random.uniform(5, 10),
                                           (120, 110, 150), 2, glow=False))
        self.particles.append(Particle(pos, (0, 0), 0.16, size * 1.1, WHITE, 0))  # bright flash
        self.rings.append(Ring(pos, size * 4.5, 0.4, color))
        self.add_trauma(0.12 + size / 140)
        self.audio.play("explode", clamp(0.35 + size / 50, 0.35, 1))

    # ------------------------------------------------------------- gameplay
    def fire(self):
        p = self.player
        fwd = p.forward
        direction = fwd.rotate(random.uniform(-2.2, 2.2))
        nose = p.pos + fwd * 28
        damage = 12 * (1 + 0.06 * (self.level - 1))
        n = self.weapon_mult if self.double_shot_time > 0 else 1
        if n > 1:
            # n parallel bullets, evenly spaced and centred on the aiming line
            spacing = min(DOUBLE_SHOT_GAP * 2, MAX_FORMATION_WIDTH / (n - 1))
            side = Vector2(-direction.y, direction.x)
            for i in range(n):
                offset = (i - (n - 1) / 2) * spacing
                self.bullets.append(Bullet(nose + side * offset, direction * BULLET_SPEED, damage))
        else:
            self.bullets.append(Bullet(nose, direction * BULLET_SPEED, damage))
        p.flash = 0.05
        p.vel -= fwd * 40                                # recoil
        self.cross_kick = 1.0
        self.add_trauma(0.05)
        self.sparks(nose, direction, YELLOW, 3, 260)
        self.audio.play("shoot", 0.6)

    def spawn_enemy(self):
        m = 50
        side = random.randrange(4)
        if side == 0:
            pos = (random.uniform(0, W), -m)
        elif side == 1:
            pos = (W + m, random.uniform(0, H))
        elif side == 2:
            pos = (random.uniform(0, W), H + m)
        else:
            pos = (-m, random.uniform(0, H))
        t = self.time
        weights = {
            "chaser": 10,
            "shooter": 0 if t < 30 else 3 + t / 35,
            "tank": 0 if t < 45 else 2 + t / 45,
        }
        # count only enemies that are currently alive, and skip types at their limit
        active = {}
        for e in self.enemies:
            if e.hp > 0:
                active[e.kind] = active.get(e.kind, 0) + 1
        for k in weights:
            if active.get(k, 0) >= ENEMY_LIMITS.get(k, MAX_ENEMIES):
                weights[k] = 0
        if sum(weights.values()) <= 0:
            return                                   # every type is at its limit: wait for a kill
        kind = random.choices(list(weights), list(weights.values()))[0]
        self.enemies.append(Enemy(kind, pos, self.difficulty))
        if kind == "tank":
            self.banner = ("WARNING: ENEMY ACE INBOUND", 2.2)
            self.audio.play("alarm")

    def damage_player(self, amount, source_pos):
        p = self.player
        if p.invuln > 0 or self.state != "playing":
            return
        p.hp -= amount
        p.invuln = 0.6
        self.hurt = 1.0
        self.add_trauma(0.45)
        away = p.pos - Vector2(source_pos)
        if away.length_squared() > 0:
            p.vel += away.normalize() * 450
        self.sparks(p.pos, away if away.length_squared() else Vector2(1, 0), RED, 14, 380)
        self.audio.play("hurt")
        if p.hp <= 0:
            p.hp = 0
            self.game_over()

    def launch_bomb(self):
        if self.state != "playing" or self.bomb_cd > 0:
            return
        p = self.player
        if self.bombs <= 0:
            self.texts.append(FloatText(p.pos - Vector2(0, 40), "NO BOMBS", self.f_small, (200, 200, 210)))
            self.bomb_cd = 0.4
            return
        self.bombs -= 1
        self.bomb_cd = BOMB_COOLDOWN
        mouse = Vector2(self._touch["aim_pos"]) if IS_ANDROID else Vector2(pygame.mouse.get_pos())
        # lock onto the enemy closest to the cursor, if one is near it
        target = None
        best = 75 * 75
        for e in self.enemies:
            d = e.pos.distance_squared_to(mouse)
            if e.hp > 0 and d < best:
                target, best = e, d
        fwd = p.forward
        start = p.pos + fwd * 30
        self.bomb_list.append(Bomb(start, fwd, target.pos if target else mouse, target))
        self.rings.append(Ring(start, 34, 0.25, ORANGE))
        for _ in range(10):
            v = fwd.rotate(random.uniform(-50, 50)) * random.uniform(60, 220)
            self.particles.append(Particle(start, v, random.uniform(0.2, 0.4), random.uniform(2, 4),
                                           random.choice((ORANGE, YELLOW, WHITE)), 4))
        self.add_trauma(0.12)
        self.audio.play("bomb_launch")

    def bomb_explode(self, pos):
        pos = Vector2(pos)
        R = BOMB_BLAST_RADIUS
        # visuals: shockwaves, fireballs, flames, embers and smoke
        self.rings.append(Ring(pos, R, 0.55, ORANGE))
        self.rings.append(Ring(pos, R * 0.7, 0.4, YELLOW))
        self.rings.append(Ring(pos, R * 1.15, 0.7, (255, 80, 40)))
        self.particles.append(Particle(pos, (0, 0), 0.22, 34, WHITE, 0))          # bright core flash
        for _ in range(14):                                                        # rolling fireballs
            off = Vector2(1, 0).rotate(random.uniform(0, 360)) * random.uniform(0, R * 0.45)
            self.particles.append(Particle(pos + off, off * random.uniform(0.8, 1.6), random.uniform(0.45, 0.8),
                                           random.uniform(14, 24), random.choice((ORANGE, (255, 110, 40), YELLOW)), 3))
        for _ in range(110):                                                       # flames and embers
            v = Vector2(1, 0).rotate(random.uniform(0, 360)) * random.uniform(120, 620)
            self.particles.append(Particle(pos, v, random.uniform(0.4, 1.1), random.uniform(2, 5),
                                           random.choice((ORANGE, YELLOW, (255, 80, 30), WHITE)), 2.8))
        for _ in range(26):                                                        # smoke
            v = Vector2(1, 0).rotate(random.uniform(0, 360)) * random.uniform(30, 160)
            self.particles.append(Particle(pos, v, random.uniform(0.8, 1.5), random.uniform(7, 13),
                                           (110, 100, 115), 1.6, glow=False))
        self.add_trauma(0.75)
        self.audio.play("bomb")
        # damage every enemy inside the blast (more damage closer to the centre)
        for e in self.enemies:
            if e.hp <= 0:
                continue
            d = math.sqrt(e.pos.distance_squared_to(pos))
            if d <= R + e.radius:
                falloff = 1 - 0.5 * clamp((d - e.radius) / R, 0, 1)
                away = e.pos - pos
                direction = away.normalize() if away.length_squared() > 0 else Vector2(1, 0)
                e.hit(BOMB_DAMAGE * falloff, direction)
                e.vel += direction * 260 * falloff
                self.sparks(e.pos, direction, ORANGE, 6)
                if e.hp <= 0:
                    self.kill_enemy(e)
        # the blast also wipes out enemy bullets inside it
        for b in self.enemy_bullets:
            if b.pos.distance_squared_to(pos) < R * R:
                b.life = 0

    def kill_enemy(self, e, scored=True):
        self.explosion(e.pos, e.color, e.radius)
        if e.kind == "tank":                                     # spectacular boss destruction
            self.explosion(e.pos, (230, 140, 255), 60)
            self.rings.append(Ring(e.pos, 320, 0.9, B_GLOW))
            self.particles.append(Particle(e.pos, (0, 0), 0.3, 46, WHITE, 0))
            for i in range(8):
                off = Vector2(1, 0).rotate(random.uniform(0, 360)) * random.uniform(20, 70)
                self.pending_booms.append([0.12 * (i + 1), e.pos + off, random.uniform(16, 30)])
            self.add_trauma(1.0)
            if scored:
                self.banner = ("ENEMY ACE DESTROYED!", 2.2)
        if scored:
            gain = int(e.score * (1 + 0.25 * (self.level - 1)))
            self.score += gain
            self.kills += 1
            self.texts.append(FloatText(e.pos, f"+{gain}", self.f_small, e.color))
            if random.random() < (0.12 if e.kind == "tank" else 0.06):
                self.pickups.append(Pickup(e.pos))

    def game_over(self):
        self.state = "gameover"
        self.over_t = 0.0
        self.explosion(self.player.pos, CYAN, 40)
        self.explosion(self.player.pos, WHITE, 25)
        self.add_trauma(1.0)
        if self.score > self.best:
            self.best = self.score
            self.new_best = True
            self._save_best()

    def update_effects(self, dt):
        for group in (self.particles, self.rings, self.texts):
            for obj in group:
                obj.update(dt)
        _max_p = 600 if IS_ANDROID else 1800
        self.particles = [p for p in self.particles if p.life > 0][-_max_p:]
        self.rings = [r for r in self.rings if r.life > 0]
        self.texts = [t for t in self.texts if t.life > 0]
        self.trauma = max(0.0, self.trauma - 1.6 * dt)
        self.hurt = max(0.0, self.hurt - 1.8 * dt)
        self.cross_kick = max(0.0, self.cross_kick - 6 * dt)
        text, bt = self.banner
        self.banner = (text, max(0.0, bt - dt))
        for boom in self.pending_booms:                          # chained boss explosions
            boom[0] -= dt
            if boom[0] <= 0:
                self.explosion(boom[1], random.choice((PURPLE, (230, 140, 255), ORANGE)), boom[2])
        self.pending_booms = [b for b in self.pending_booms if b[0] > 0]

    def update_playing(self, dt):
        self.time += dt
        new_level = 1 + int(self.time // 30)
        if new_level > self.level:
            self.level = new_level
            self.banner = (f"LEVEL {self.level}", 2.2)
            self.player.hp = min(PLAYER_MAX_HP, self.player.hp + 15)
            self.audio.play("level")

        keys  = pygame.key.get_pressed()
        mouse = self._touch["aim_pos"] if IS_ANDROID else pygame.mouse.get_pos()
        p     = self.player
        p.update(dt, keys, mouse, touch_dir=self._touch["joy_vec"] if IS_ANDROID else None)
        if p.move_dir.length_squared() > 0 and random.random() < 0.8:      # engine trail
            back = p.pos - p.move_dir * 16
            v = -p.move_dir * random.uniform(120, 240) + Vector2(random.uniform(-40, 40), random.uniform(-40, 40))
            self.particles.append(Particle(back, v, random.uniform(0.2, 0.4), random.uniform(2, 3.5),
                                           random.choice((CYAN, (80, 140, 255))), 2.5))

        # weapon upgrade: countdown and random appearance
        self.double_shot_time = max(0.0, self.double_shot_time - dt)
        if self.double_shot_time <= 0:
            self.weapon_mult = 1                        # upgrade expired: back to a single bullet
        self.weapon_drop_timer -= dt
        if self.weapon_drop_timer <= 0:
            if not self.weapon_drops:
                for _ in range(20):                     # pick a spot not right on top of the player
                    pos = Vector2(random.uniform(90, W - 90), random.uniform(130, H - 90))
                    if pos.distance_squared_to(p.pos) > 200 * 200:
                        break
                self.weapon_drops.append(WeaponPowerUp(pos, self.f_small))
            self.weapon_drop_timer = random.uniform(*WEAPON_DROP_EVERY)
        for wd in self.weapon_drops:
            wd.update(dt)
            if wd.pos.distance_squared_to(p.pos) < (p.RADIUS + 20) ** 2:
                wd.life = 0
                self.weapon_mult += 1                   # stacks: 2x, 3x, 4x, ...
                self.double_shot_time = DOUBLE_SHOT_DURATION
                self.banner = (f"{self.weapon_mult}x SHOT!", 1.8)
                self.texts.append(FloatText(wd.pos, f"WEAPON UPGRADE  {self.weapon_mult}x", self.f_small, YELLOW))
                self.rings.append(Ring(wd.pos, 90, 0.45, YELLOW))
                self.rings.append(Ring(wd.pos, 55, 0.3, ORANGE))
                for _ in range(30):
                    v = Vector2(1, 0).rotate(random.uniform(0, 360)) * random.uniform(80, 340)
                    self.particles.append(Particle(wd.pos, v, random.uniform(0.3, 0.7), random.uniform(2, 4),
                                                   random.choice((YELLOW, ORANGE, WHITE)), 3))
                self.audio.play("level")
        self.weapon_drops = [wd for wd in self.weapon_drops if wd.life > 0]

        # bombs: cooldown, flight, impact, and bomb rewards
        self.bomb_cd = max(0.0, self.bomb_cd - dt)
        for bomb in self.bomb_list:
            bomb.update(dt, self)
            if bomb.done:
                self.bomb_explode(bomb.pos)
        self.bomb_list = [bomb for bomb in self.bomb_list if not bomb.done]
        self.bomb_drop_timer -= dt
        if self.bomb_drop_timer <= 0:
            if not self.bomb_drops:
                for _ in range(20):
                    pos = Vector2(random.uniform(90, W - 90), random.uniform(130, H - 110))
                    if pos.distance_squared_to(p.pos) > 200 * 200:
                        break
                self.bomb_drops.append(BombPickup(pos, self.f_small))
            self.bomb_drop_timer = random.uniform(*BOMB_DROP_EVERY)
        for bd in self.bomb_drops:
            bd.update(dt)
            if bd.pos.distance_squared_to(p.pos) < (p.RADIUS + 18) ** 2:
                bd.life = 0
                if self.bombs < BOMB_MAX:
                    self.bombs += 1
                    self.texts.append(FloatText(bd.pos, "+1 BOMB", self.f_small, ORANGE))
                else:
                    self.texts.append(FloatText(bd.pos, "BOMBS FULL", self.f_small, (220, 220, 230)))
                self.rings.append(Ring(bd.pos, 60, 0.35, ORANGE))
                for _ in range(16):
                    v = Vector2(1, 0).rotate(random.uniform(0, 360)) * random.uniform(60, 240)
                    self.particles.append(Particle(bd.pos, v, random.uniform(0.25, 0.5), random.uniform(2, 3.5),
                                                   random.choice((ORANGE, YELLOW)), 3))
                self.audio.play("pickup")
        self.bomb_drops = [bd for bd in self.bomb_drops if bd.life > 0]

        self.fire_cd -= dt
        _shooting = self._touch["shooting"] if IS_ANDROID else pygame.mouse.get_pressed()[0]
        if _shooting and self.fire_cd <= 0:
            self.fire()
            self.fire_cd = FIRE_DELAY

        self.spawn_timer -= dt
        if self.spawn_timer <= 0:
            if len(self.enemies) < MAX_ENEMIES:
                count = 1 + (random.random() < min(0.6, self.time / 150))
                for _ in range(count):
                    self.spawn_enemy()
            self.spawn_timer = max(0.28, 1.3 - self.time * 0.011)

        for e in self.enemies:
            e.update(dt, self)
        for b in self.bullets:
            b.update(dt)
        for b in self.enemy_bullets:
            b.update(dt)
        for pk in self.pickups:
            pk.update(dt, p)

        # player bullets vs enemies
        for b in self.bullets:
            for e in self.enemies:
                if e.hp > 0 and e.spawn_t < 0.3 and b.pos.distance_squared_to(e.pos) < (e.radius + b.radius) ** 2:
                    direction = b.vel.normalize()
                    e.hit(b.damage, direction)
                    b.life = 0
                    self.sparks(b.pos, -direction, e.color, 7)
                    self.audio.play("hit", 0.5)
                    if e.hp <= 0:
                        self.kill_enemy(e)
                    break

        # enemies vs player
        for e in self.enemies:
            if e.hp > 0 and e.spawn_t <= 0 and e.pos.distance_squared_to(p.pos) < (e.radius + p.RADIUS) ** 2:
                self.damage_player(e.damage, e.pos)
                if e.kamikaze:
                    e.hp = 0
                    self.kill_enemy(e, scored=False)
                else:
                    away = e.pos - p.pos
                    if away.length_squared() > 0:
                        e.vel += away.normalize() * 350

        # enemy bullets vs player
        for b in self.enemy_bullets:
            if b.pos.distance_squared_to(p.pos) < (b.radius + p.RADIUS) ** 2:
                b.life = 0
                self.damage_player(b.damage, b.pos)

        # pickups
        for pk in self.pickups:
            if pk.pos.distance_squared_to(p.pos) < (p.RADIUS + 14) ** 2:
                pk.life = 0
                p.hp = PLAYER_MAX_HP                    # health reward: always refill to 100%
                self.texts.append(FloatText(pk.pos, "FULL HEALTH", self.f_small, GREEN))
                self.rings.append(Ring(pk.pos, 50, 0.35, GREEN))
                self.audio.play("pickup")

        self.enemies = [e for e in self.enemies if e.hp > 0]
        self.bullets = [b for b in self.bullets if b.alive()]
        self.enemy_bullets = [b for b in self.enemy_bullets if b.alive()]
        self.pickups = [pk for pk in self.pickups if pk.life > 0]

    # ------------------------------------------------------------- touch input (Android)
    def _fpos(self, ev):
        """Normalised 0-1 finger coords → logical pixel Vector2."""
        return Vector2(ev.x * W, ev.y * H)

    def _touch_down(self, fid, pos):
        t = self._touch
        if _PAUSE_BTN.collidepoint(pos):
            if self.state == "playing":
                self.state = "paused"
            elif self.state == "paused":
                self.state = "playing"
            return
        if _BOMB_BTN.collidepoint(pos):
            if self.state == "playing":
                self.launch_bomb()
            elif self.state == "paused":
                self.start_game()
            elif self.state == "gameover" and self.over_t > 0.8:
                self.start_game()
            return
        if self.state == "menu":
            self.start_game()
            return
        if self.state == "gameover" and self.over_t > 0.8:
            self.start_game()
            return
        if self.state != "playing":
            return
        if pos.x < _JOY_MAX_X and t["joy_id"] is None:
            t["joy_id"]     = fid
            t["joy_origin"] = Vector2(pos)
            t["joy_vec"]    = Vector2()
        elif t["aim_id"] is None:
            t["aim_id"]   = fid
            t["aim_pos"]  = Vector2(pos)
            t["shooting"] = True

    def _touch_up(self, fid):
        t = self._touch
        if t["joy_id"] == fid:
            t["joy_id"]  = None
            t["joy_vec"] = Vector2()
        if t["aim_id"] == fid:
            t["aim_id"]   = None
            t["shooting"] = False

    def _touch_move(self, fid, pos):
        t = self._touch
        if t["joy_id"] == fid:
            delta = pos - t["joy_origin"]
            if delta.length() > _JOY_RADIUS:
                delta.scale_to_length(_JOY_RADIUS)
                t["joy_origin"] = pos - delta
            t["joy_vec"] = delta / _JOY_RADIUS if delta.length() > 8 else Vector2()
        elif t["aim_id"] == fid:
            t["aim_pos"] = Vector2(pos)

    # ------------------------------------------------------------- events
    def handle_events(self, events):
        for e in events:
            if e.type == pygame.QUIT:
                self.running = False
            elif e.type == getattr(pygame, "WINDOWFOCUSLOST", -1) and self.state == "playing":
                self.state = "paused"
            elif e.type == pygame.FINGERDOWN and IS_ANDROID:
                self._touch_down(e.finger_id, self._fpos(e))
            elif e.type == pygame.FINGERUP and IS_ANDROID:
                self._touch_up(e.finger_id)
            elif e.type == pygame.FINGERMOTION and IS_ANDROID:
                self._touch_move(e.finger_id, self._fpos(e))
            elif e.type == pygame.KEYDOWN:
                if e.key == pygame.K_F11:
                    pygame.display.toggle_fullscreen()
                    self.fullscreen = not self.fullscreen
                elif self.state == "menu":
                    if e.key in (pygame.K_RETURN, pygame.K_SPACE):
                        self.start_game()
                    elif e.key == pygame.K_ESCAPE:
                        self.running = False
                elif self.state == "playing":
                    if e.key in (pygame.K_ESCAPE, pygame.K_p):
                        self.state = "paused"
                    elif e.key == pygame.K_m:
                        self.audio.toggle_mute()
                    elif e.key == pygame.K_SPACE:
                        self.launch_bomb()
                elif self.state == "paused":
                    if e.key in (pygame.K_ESCAPE, pygame.K_p):
                        self.state = "playing"
                    elif e.key == pygame.K_r:
                        self.start_game()
                    elif e.key == pygame.K_q:
                        self.reset()
                        self.state = "menu"
                    elif e.key == pygame.K_m:
                        self.audio.toggle_mute()
                elif self.state == "gameover" and self.over_t > 0.8:
                    if e.key in (pygame.K_r, pygame.K_RETURN, pygame.K_SPACE):
                        self.start_game()
                    elif e.key == pygame.K_ESCAPE:
                        self.reset()
                        self.state = "menu"
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
                if self.state == "menu" or (self.state == "gameover" and self.over_t > 0.8):
                    self.start_game()
            elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3 and self.state == "playing":
                self.launch_bomb()

    def update(self, dt):
        self.t += dt
        if self.state == "playing":
            self.update_playing(dt)
            self.update_effects(dt)
        elif self.state == "gameover":
            self.over_t += dt
            for b in self.enemy_bullets:
                b.update(dt)
            self.update_effects(dt)
        elif self.state == "menu":
            for s in self.stars:
                s[0] = (s[0] - 25 * s[2] * dt) % W

    # ------------------------------------------------------------- drawing
    def glow_text(self, surf, text, font, color, center, glow_strength=2):
        key = (text, id(font), color)
        cached = self._glow_text_cache.get(key)
        if cached is None:
            base = font.render(text, True, color, BLACK)
            pad = 30
            big = pygame.Surface((base.get_width() + pad * 2, base.get_height() + pad * 2))
            big.fill(BLACK)
            big.blit(base, (pad, pad))
            w, h = big.get_size()
            blur = pygame.transform.smoothscale(big, (max(1, w // 6), max(1, h // 6)))
            blur = pygame.transform.smoothscale(blur, (w, h))
            cached = (font.render(text, True, color), blur)
            self._glow_text_cache[key] = cached
        img, blur = cached
        for _ in range(glow_strength):
            surf.blit(blur, blur.get_rect(center=center), special_flags=pygame.BLEND_ADD)
        surf.blit(img, img.get_rect(center=center))

    def text(self, surf, msg, font, color, **pos):
        img = font.render(msg, True, color)
        rect = img.get_rect(**pos)
        shadow = font.render(msg, True, BLACK)
        shadow.set_alpha(150)
        surf.blit(shadow, rect.move(2, 2))
        surf.blit(img, rect)
        return rect

    def draw_stars(self, surf):
        px = (self.player.pos.x - W / 2) * 0.06
        py = (self.player.pos.y - H / 2) * 0.06
        for x, y, z, ph in self.stars:
            sx = (x - px * z) % W
            sy = (y - py * z) % H
            b = 0.55 + 0.45 * math.sin(self.t * (1 + z * 2) + ph)
            c = int(120 + 135 * b * z)
            pygame.draw.circle(surf, (c, c, min(255, c + 30)), (sx, sy), 1 if z < 0.7 else 2)

    def draw_world(self):
        w = self.world
        w.blit(self.background, (0, 0))
        self.draw_stars(w)
        for r in self.rings:
            r.draw(w)
        for pk in self.pickups:
            pk.draw(w)
        for wd in self.weapon_drops:
            wd.draw(w)
        for bd in self.bomb_drops:
            bd.draw(w)
        for e in self.enemies:
            e.draw(w, self.t)
        for b in self.enemy_bullets:
            b.draw(w)
        for b in self.bullets:
            b.draw(w)
        for bomb in self.bomb_list:
            bomb.draw(w)
        if self.state != "gameover":
            self.player.draw(w, self.t)
        for p in self.particles:
            p.draw(w)
        for t in self.texts:
            t.draw(w)
        w.blit(self.vignette_dark, (0, 0))

        shake = 0 if self.state == "paused" else (self.trauma ** 2) * 16
        off = (random.uniform(-shake, shake), random.uniform(-shake, shake))
        self.screen.fill(BLACK)
        self.screen.blit(w, off)
        if self.hurt > 0:
            self.vignette_red.set_alpha(int(220 * self.hurt))
            self.screen.blit(self.vignette_red, (0, 0))

    def draw_hud(self):
        s = self.screen
        p = self.player
        # health bar
        x, y, bw, bh = 28, 24, 320, 22
        ratio = clamp(p.hp / PLAYER_MAX_HP, 0, 1)
        col = lerp_color(RED, GREEN, ratio)
        pygame.draw.rect(s, (20, 15, 35), (x - 4, y - 4, bw + 8, bh + 8), border_radius=8)
        pygame.draw.rect(s, (60, 20, 40), (x, y, bw, bh), border_radius=6)
        if ratio > 0:
            pygame.draw.rect(s, col, (x, y, int(bw * ratio), bh), border_radius=6)
            pygame.draw.rect(s, lerp_color(col, WHITE, 0.5), (x + 4, y + 3, int((bw - 8) * ratio), 5),
                             border_radius=3)
            draw_glow(s, (x + bw * ratio, y + bh / 2), 26, col, 0.7)
        pygame.draw.rect(s, (200, 220, 255), (x - 4, y - 4, bw + 8, bh + 8), 2, border_radius=8)
        self.text(s, f"HEALTH  {int(p.hp)} / {PLAYER_MAX_HP}", self.f_small, WHITE, topleft=(x, y + bh + 10))

        # double shot countdown
        if self.double_shot_time > 0:
            left = self.double_shot_time
            ty = y + bh + 40
            frac = left / DOUBLE_SHOT_DURATION
            warn = left < 3 and int(self.t * 6) % 2 == 0
            col = RED if warn else YELLOW
            self.text(s, f"{self.weapon_mult}x SHOT  {left:4.1f}s", self.f_med, col, topleft=(x, ty))
            pygame.draw.rect(s, (50, 35, 10), (x, ty + 34, 220, 8), border_radius=4)
            pygame.draw.rect(s, col, (x, ty + 34, int(220 * frac), 8), border_radius=4)
            draw_glow(s, (x + 220 * frac, ty + 38), 18, ORANGE, 0.7)

        # boss health bar
        boss = next((e for e in self.enemies if e.kind == "tank" and e.hp > 0), None)
        if boss:
            bw2, bx2, by2 = 460, W // 2 - 230, 126
            frac = clamp(boss.hp / boss.max_hp, 0, 1)
            self.text(s, "ENEMY ACE FIGHTER", self.f_small, B_GLOW, midbottom=(W // 2, by2 - 4))
            pygame.draw.rect(s, (20, 10, 30), (bx2 - 4, by2 - 4, bw2 + 8, 22), border_radius=6)
            pygame.draw.rect(s, (60, 20, 70), (bx2, by2, bw2, 14), border_radius=4)
            if frac > 0:
                pygame.draw.rect(s, (170, 70, 255), (bx2, by2, int(bw2 * frac), 14), border_radius=4)
                pygame.draw.rect(s, (220, 170, 255), (bx2 + 3, by2 + 2, int((bw2 - 6) * frac), 3),
                                 border_radius=2)
                draw_glow(s, (bx2 + bw2 * frac, by2 + 7), 24, B_GLOW, 0.8)
            pygame.draw.rect(s, B_GLOW, (bx2 - 4, by2 - 4, bw2 + 8, 22), 2, border_radius=6)

        # timer and level
        mins, secs = divmod(int(self.time), 60)
        self.text(s, f"{mins:02d}:{secs:02d}", self.f_big, WHITE, midtop=(W // 2, 14))
        self.text(s, f"LEVEL {self.level}", self.f_small, CYAN, midtop=(W // 2, 70))

        # score and kills
        self.text(s, f"SCORE  {self.score}", self.f_med, YELLOW, topright=(W - 28, 20))
        self.text(s, f"ENEMIES DEFEATED  {self.kills}", self.f_small, PINK, topright=(W - 28, 58))
        self.text(s, f"BEST  {self.best}", self.f_small, (180, 180, 220), topright=(W - 28, 84))

        # bomb counter (bottom right): one slot per storage space
        bx, by = W - 40, H - 40
        for i in range(BOMB_MAX):
            cx = bx - i * 34
            if i < self.bombs:
                draw_glow(s, (cx, by), 22, (255, 100, 40), 0.6)
                pygame.draw.circle(s, METAL, (cx, by), 11)
                pygame.draw.circle(s, METAL_LIGHT, (cx - 3, by - 3), 3)
                pygame.draw.circle(s, (255, 140, 60), (cx, by), 11, 2)
                pygame.draw.line(s, (200, 190, 170), (cx + 6, by - 8), (cx + 10, by - 13), 2)
                pygame.draw.circle(s, YELLOW, (cx + 10, by - 13), 2)
            else:
                pygame.draw.circle(s, (70, 70, 90), (cx, by), 11, 2)
        label_col = ORANGE if self.bombs > 0 else (150, 150, 165)
        self.text(s, f"BOMBS  {self.bombs} / {BOMB_MAX}", self.f_small, label_col,
                  bottomright=(W - 26, by - 22))
        if self.bomb_cd > 0 and self.bombs > 0:                       # cooldown bar
            frac = self.bomb_cd / BOMB_COOLDOWN
            pygame.draw.rect(s, (60, 40, 30), (W - 186, by + 16, 160, 4))
            pygame.draw.rect(s, ORANGE, (W - 186, by + 16, int(160 * (1 - frac)), 4))

        if not IS_ANDROID:
            hint = ("ESC pause    M " + ("unmute" if self.audio.muted else "mute") +
                    "    SPACE / RIGHT CLICK bomb    F11 fullscreen")
            self.text(s, hint, self.f_small, (120, 130, 170), bottomleft=(24, H - 16))

        text, bt = self.banner
        if bt > 0:
            img = self.f_big.render(text, True, WHITE)
            scale = 1 + max(0.0, bt - 1.6) * 0.6
            if scale != 1:
                img = pygame.transform.smoothscale(img, (int(img.get_width() * scale), int(img.get_height() * scale)))
            img.set_alpha(int(255 * clamp(bt, 0, 1)))
            s.blit(img, img.get_rect(center=(W // 2, H // 3)))

    def draw_crosshair(self):
        if IS_ANDROID:
            return
        mx, my = pygame.mouse.get_pos()
        s = self.screen
        gap = 10 + self.cross_kick * 8
        col = (255, 255, 255)
        draw_glow(s, (mx, my), 26, CYAN, 0.5)
        pygame.draw.circle(s, col, (mx, my), int(gap + 6), 2)
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            pygame.draw.line(s, col, (mx + dx * gap, my + dy * gap), (mx + dx * (gap + 12), my + dy * (gap + 12)), 2)
        pygame.draw.circle(s, PINK, (mx, my), 2)

    def draw_touch_ui(self):
        if not IS_ANDROID:
            return
        s = self.screen
        t = self._touch

        # virtual joystick
        if t["joy_id"] is not None:
            ox, oy = int(t["joy_origin"].x), int(t["joy_origin"].y)
            pygame.draw.circle(s, (90, 100, 155), (ox, oy), _JOY_RADIUS, 3)
            pad = t["joy_origin"] + t["joy_vec"] * _JOY_RADIUS
            pygame.draw.circle(s, (160, 170, 220), (int(pad.x), int(pad.y)), 28)
            pygame.draw.circle(s, (220, 225, 255), (int(pad.x), int(pad.y)), 28, 2)
        elif self.state == "playing":
            pygame.draw.circle(s, (50, 55, 90), (160, int(H * 0.73)), _JOY_RADIUS, 2)
            pygame.draw.circle(s, (50, 55, 90), (160, int(H * 0.73)), 24, 1)
            self.text(s, "MOVE", self.f_small, (65, 70, 110), center=(160, int(H * 0.73)))

        # bomb / context button
        if self.state != "menu":
            if self.state == "playing":
                active = self.bombs > 0 and self.bomb_cd <= 0
                bg   = (38, 22, 8) if active else (20, 20, 25)
                edge = ORANGE       if active else (80, 80, 100)
                lbl  = f"BOMB x{self.bombs}" if active else "BOMB"
            elif self.state == "paused":
                bg, edge, lbl = (10, 25, 35), CYAN, "RESTART"
            else:
                bg, edge, lbl = (10, 30, 15), GREEN, "RETRY"
            pygame.draw.rect(s, bg,   _BOMB_BTN, border_radius=14)
            pygame.draw.rect(s, edge, _BOMB_BTN, 3, border_radius=14)
            self.text(s, lbl, self.f_med, edge, center=_BOMB_BTN.center)

        # pause / resume button
        if self.state in ("playing", "paused"):
            icon = "PLAY" if self.state == "paused" else "||"
            pygame.draw.rect(s, (25, 20, 42), _PAUSE_BTN, border_radius=8)
            pygame.draw.rect(s, (120, 130, 190), _PAUSE_BTN, 2, border_radius=8)
            self.text(s, icon, self.f_small, (180, 190, 240), center=_PAUSE_BTN.center)

        # aim indicator while finger held
        if t["aim_id"] is not None and self.state == "playing":
            ax, ay = int(t["aim_pos"].x), int(t["aim_pos"].y)
            draw_glow(s, (ax, ay), 32, CYAN, 0.4)
            pygame.draw.circle(s, (100, 210, 255), (ax, ay), 20, 2)

    def draw_overlay(self, alpha):
        shade = pygame.Surface((W, H), pygame.SRCALPHA)
        shade.fill((5, 3, 15, alpha))
        self.screen.blit(shade, (0, 0))

    def draw_menu(self):
        s = self.screen
        s.blit(self.background, (0, 0))
        self.draw_stars(s)
        # orbiting neon orbs around the title
        for i in range(8):
            a = self.t * 0.6 + i * math.tau / 8
            pos = (W / 2 + math.cos(a) * 420, H / 2 - 120 + math.sin(a) * 110)
            col = (CYAN, PINK, YELLOW, GREEN, PURPLE)[i % 5]
            draw_glow(s, pos, 40, col, 0.8)
            pygame.draw.circle(s, WHITE, pos, 4)
        s.blit(self.vignette_dark, (0, 0))
        pulse = 2 + int(math.sin(self.t * 3) > 0)
        self.glow_text(s, TITLE, self.f_huge, CYAN, (W // 2, H // 2 - 130), pulse)
        self.text(s, "survive the neon swarm", self.f_med, PINK, center=(W // 2, H // 2 - 50))

        if IS_ANDROID:
            lines = ["LEFT THUMB  -  move",
                     "RIGHT THUMB  -  aim + shoot (hold)",
                     "BOMB button  -  launch bomb",
                     "||  button  -  pause"]
        else:
            lines = ["WASD / ARROWS  -  move", "MOUSE  -  aim", "LEFT CLICK  -  shoot (hold)",
                     "SPACE / RIGHT CLICK  -  launch bomb",
                     "ESC / P  -  pause      M  -  mute      F11  -  fullscreen"]
        for i, line in enumerate(lines):
            self.text(s, line, self.f_small, (200, 210, 240), center=(W // 2, H // 2 + 30 + i * 32))
        if int(self.t * 2) % 2 == 0:
            msg = "TAP anywhere to start" if IS_ANDROID else "CLICK or press ENTER to start"
            self.text(s, msg, self.f_med, YELLOW, center=(W // 2, H - 150))
        self.text(s, f"BEST SCORE  {self.best}", self.f_small, (180, 180, 220), center=(W // 2, H - 100))
        self.text(s, "ESC to quit", self.f_small, (120, 130, 170), center=(W // 2, H - 60))

    def draw_pause(self):
        self.draw_overlay(170)
        s = self.screen
        self.glow_text(s, "PAUSED", self.f_huge, YELLOW, (W // 2, H // 2 - 80))
        if IS_ANDROID:
            lines = ("tap  ||  to resume", "tap  RESTART  to replay")
        else:
            lines = ("ESC / P  -  resume", "R  -  restart", "Q  -  quit to menu",
                     "M  -  " + ("unmute" if self.audio.muted else "mute"))
        for i, line in enumerate(lines):
            self.text(s, line, self.f_med, WHITE, center=(W // 2, H // 2 + 20 + i * 44))

    def draw_game_over(self):
        self.draw_overlay(int(clamp(self.over_t / 0.8, 0, 1) * 175))
        if self.over_t < 0.4:
            return
        s = self.screen
        self.glow_text(s, "GAME OVER", self.f_huge, RED, (W // 2, H // 2 - 150))
        mins, secs = divmod(int(self.time), 60)
        stats = [(f"SCORE  {self.score}", YELLOW), (f"SURVIVED  {mins:02d}:{secs:02d}", WHITE),
                 (f"ENEMIES DEFEATED  {self.kills}", PINK), (f"LEVEL REACHED  {self.level}", CYAN),
                 (f"BEST  {self.best}", (190, 190, 230))]
        for i, (line, col) in enumerate(stats):
            self.text(s, line, self.f_med, col, center=(W // 2, H // 2 - 50 + i * 44))
        if self.new_best and int(self.t * 3) % 2 == 0:
            self.glow_text(s, "NEW BEST!", self.f_big, GREEN, (W // 2, H // 2 + 180), 1)
        if self.over_t > 0.8:
            if IS_ANDROID:
                self.text(s, "Tap  RETRY  to play again", self.f_med, WHITE, center=(W // 2, H - 60))
            else:
                self.text(s, "R / CLICK  -  play again        ESC  -  menu", self.f_med, WHITE,
                          center=(W // 2, H - 60))

    def draw(self):
        if self.state == "menu":
            self.draw_menu()
        else:
            self.draw_world()
            if self.state in ("playing", "paused"):
                self.draw_hud()
            if self.state == "paused":
                self.draw_pause()
            elif self.state == "gameover":
                self.draw_game_over()
        self.draw_crosshair()
        self.draw_touch_ui()

    # ------------------------------------------------------------- main loop
    def run(self):
        while self.running:
            dt = min(self.clock.tick(FPS) / 1000.0, 0.05)
            self.handle_events(pygame.event.get())
            self.update(dt)
            self.draw()
            pygame.display.flip()
        pygame.quit()


def main():
    pygame.mixer.pre_init(22050, -16, 2, 512)
    pygame.init()
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init(22050, -16, 2, 512)
    except pygame.error:
        pass                                  # no audio device: play silently
    Game(windowed="--windowed" in sys.argv).run()


if __name__ == "__main__":
    main()