#!/usr/bin/env python3
"""Farbrechnen fuers Dashboard: sRGB <-> OKLab/OKLCH, Abstand, Kontrast.

Eigenes kleines Modul statt Bibliothek - dashboard_build.py und die Werkzeuge
in bin/ sollen dieselbe Rechnung benutzen, sonst weichen Wachhund und
Generator voneinander ab.

OKLab ist hier wichtig, weil es wahrnehmungsnah ist: der Abstand zweier Farben
in diesem Raum entspricht ungefaehr dem, was das Auge als Unterschied sieht.
In Hex-Werten laesst sich das nicht ablesen (#0f7ea3 und #0e8a8f sehen fast
gleich aus, #0f9d63 und #5a8f29 auch, obwohl die Ziffern anders aussehen).
"""
import math

# ---------------------------------------------------------------- Grundlagen --

def _srgb_zu_linear(c):
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_zu_srgb(c):
    return 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055


def hex_zu_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_zu_hex(rgb):
    return "#" + "".join(f"{round(max(0.0, min(1.0, c)) * 255):02x}" for c in rgb)


def rgb_zu_oklab(rgb):
    r, g, b = (_srgb_zu_linear(c) for c in rgb)
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s)


def oklab_zu_rgb(lab):
    L, a, b = lab
    l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (L - 0.0894841775 * a - 1.2914855480 * b) ** 3
    return (_linear_zu_srgb(+4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
            _linear_zu_srgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
            _linear_zu_srgb(-0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s))


def hex_zu_oklch(h):
    L, a, b = rgb_zu_oklab(hex_zu_rgb(h))
    return L, math.hypot(a, b), math.degrees(math.atan2(b, a)) % 360


def _in_gamut(rgb, toleranz=1e-4):
    return all(-toleranz <= c <= 1 + toleranz for c in rgb)


def oklch_zu_hex(L, C, H):
    """OKLCH nach Hex, mit Gamut-Mapping: zu bunte Werte werden entsaettigt.

    Ohne das Mapping laufen kraeftige Blau- und Violetttoene aus dem sRGB-Raum
    heraus und wuerden beim Abschneiden einen anderen Farbton bekommen als
    verlangt. Deshalb lieber Buntheit zuruecknehmen und den Ton halten.
    """
    rad = math.radians(H)
    hoch = C
    tief = 0.0
    kandidat = (L, math.cos(rad) * C, math.sin(rad) * C)
    if not _in_gamut(oklab_zu_rgb(kandidat)):
        for _ in range(25):
            mitte = (hoch + tief) / 2
            probe = (L, math.cos(rad) * mitte, math.sin(rad) * mitte)
            if _in_gamut(oklab_zu_rgb(probe)):
                tief = mitte
            else:
                hoch = mitte
        kandidat = (L, math.cos(rad) * tief, math.sin(rad) * tief)
    return rgb_zu_hex(oklab_zu_rgb(kandidat))


# ------------------------------------------------------------------ Abstaende --

def abstand(hex_a, hex_b):
    """Wahrnehmungsnaher Abstand zweier Farben (0 = identisch)."""
    a = rgb_zu_oklab(hex_zu_rgb(hex_a))
    b = rgb_zu_oklab(hex_zu_rgb(hex_b))
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def kontrast(hex_a, hex_b):
    """WCAG-Kontrastverhaeltnis (1 = gleich, 21 = Schwarz auf Weiss)."""
    def hell(h):
        r, g, b = (_srgb_zu_linear(c) for c in hex_zu_rgb(h))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b
    a, b = sorted((hell(hex_a), hell(hex_b)), reverse=True)
    return (a + 0.05) / (b + 0.05)
