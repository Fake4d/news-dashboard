#!/usr/bin/env python3
"""Werkzeug fuer die Dashboard-Farbpalette: pruefen, Vorschlag rechnen, anwenden.

Hintergrund: jede Kachel hat eine Akzentfarbe, und zwei Kacheln DERSELBEN Seite
sollen sich nicht aehnlich sehen. Von Hand ist das ab etwa einem Dutzend Kacheln
nicht mehr zu ueberblicken - Hex-Werte sagen nichts darueber aus, wie aehnlich
zwei Farben WIRKEN. Deshalb rechnet dieses Werkzeug in OKLab (wahrnehmungsnah).

  dashboard_farben.py pruefen
      Bericht: welche Kachelpaare stehen sich auf welcher Seite zu nah.

  dashboard_farben.py vorschlagen [--spielraum 25] [--ziel datei.json]
      Dreht die Farbtoene so weit noetig (hoechstens <spielraum> Grad je Thema)
      und verteilt die Helligkeitsstufen des Dunkelmodus so, dass der Abstand
      des schlechtesten Paars moeglichst gross wird. Der Spielraum ist die
      Bremse: das bestaetigte Design soll erkennbar bleiben, deshalb wird nur
      so weit gedreht wie noetig, nicht so weit wie moeglich.

  dashboard_farben.py anwenden <datei.json>
      Schreibt einen Vorschlag in blocks.json (farbe + dunkel_stufe).

Die Dunkelmodus-Farben stehen nicht mehr in blocks.json - dashboard_build.py
rechnet sie aus Farbton und Stufe. Siehe dort DUNKEL_STUFEN.
"""
import argparse
import itertools
import json
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dashboard_farbe as farbe
import dashboard_build as build

CLAUDE = pathlib.Path("/home/gra/claude")
BLOCKS_JSON = CLAUDE / "dashboard" / "blocks.json"
DASHBOARDS_JSON = CLAUDE / "dashboard" / "dashboards.json"


def laden():
    blocks = json.loads(BLOCKS_JSON.read_text(encoding="utf-8"))
    dashboards = json.loads(DASHBOARDS_JSON.read_text(encoding="utf-8"))
    return {b["id"]: b for b in blocks}, dashboards


def seitenpaare(dashboards):
    """Alle Themenpaare, die irgendwo gemeinsam auf einer Seite stehen."""
    return sorted({tuple(sorted((a, b)))
                   for d in dashboards for a, b in itertools.combinations(d["blocks"], 2)})


def bewerten(paare, hell, dunkel):
    werte = [(min(farbe.abstand(hell[a], hell[b]), farbe.abstand(dunkel[a], dunkel[b])), a, b)
             for a, b in paare]
    werte.sort()
    return werte


def cmd_pruefen(args):
    blocks, dashboards = laden()
    hell = {i: b["farbe"] for i, b in blocks.items()}
    dunkel = {i: build.farbe_dunkel(b) for i, b in blocks.items()}
    werte = bewerten(seitenpaare(dashboards), hell, dunkel)
    eng = [w for w in werte if w[0] < build.ABSTAND_WARNEN]
    print(f"{len(werte)} Paare stehen zusammen auf einer Seite, "
          f"{len(eng)} davon zu aehnlich (unter {build.ABSTAND_WARNEN})")
    for ab, a, b in eng:
        print(f"  {ab:.3f}  {a} ({hell[a]}/{dunkel[a]})  <->  {b} ({hell[b]}/{dunkel[b]})")
    print(f"schlechtestes Paar: {werte[0][0]:.3f}")


def cmd_vorschlagen(args):
    blocks, dashboards = laden()
    ids = list(blocks)
    ist = {i: farbe.hex_zu_oklch(blocks[i]["farbe"]) for i in ids}
    paare = seitenpaare(dashboards)
    stufen_namen = list(build.DUNKEL_STUFEN)

    def farbkarten(toene, stufen):
        hell = {i: farbe.oklch_zu_hex(ist[i][0], ist[i][1], toene[i]) for i in ids}
        dunkel = {i: farbe.oklch_zu_hex(build.DUNKEL_STUFEN[stufen_namen[stufen[i]]],
                                        build.DUNKEL_BUNTHEIT, toene[i]) for i in ids}
        return hell, dunkel

    def guete(toene, stufen):
        hell, dunkel = farbkarten(toene, stufen)
        return min(min(farbe.abstand(hell[a], hell[b]), farbe.abstand(dunkel[a], dunkel[b]))
                   for a, b in paare)

    # Zufallsstart plus lokale Suche mit immer feinerer Schrittweite. Die
    # Zielgroesse ist bewusst das SCHLECHTESTE Paar, nicht der Durchschnitt:
    # eine Palette ist so gut wie ihre schlechteste Unterscheidung.
    random.seed(args.saat)
    bestes = None
    for _ in range(args.runden):
        toene = {i: (ist[i][2] + random.uniform(-args.spielraum, args.spielraum)) % 360 for i in ids}
        stufen = {i: random.randrange(len(stufen_namen)) for i in ids}
        schritt = max(args.spielraum, 1.0)
        while schritt > 0.5:
            verbessert = False
            for i in ids:
                g = guete(toene, stufen)
                drift = (toene[i] - ist[i][2] + 180) % 360 - 180
                for kandidat in (max(-args.spielraum, drift - schritt),
                                 min(args.spielraum, drift + schritt)):
                    for s in range(len(stufen_namen)):
                        alt_t, alt_s = toene[i], stufen[i]
                        toene[i], stufen[i] = (ist[i][2] + kandidat) % 360, s
                        if guete(toene, stufen) > g:
                            g, verbessert = guete(toene, stufen), True
                        else:
                            toene[i], stufen[i] = alt_t, alt_s
            if not verbessert:
                schritt /= 2
        g = guete(toene, stufen)
        if bestes is None or g > bestes[0]:
            bestes = (g, dict(toene), dict(stufen))

    g, toene, stufen = bestes
    hell, dunkel = farbkarten(toene, stufen)
    vorschlag = {i: {"farbe": hell[i], "dunkel_stufe": stufen_namen[stufen[i]],
                     "drehung": round((toene[i] - ist[i][2] + 180) % 360 - 180, 1)}
                 for i in ids}
    werte = bewerten(paare, hell, dunkel)
    print(f"schlechtestes Paar: {werte[0][0]:.3f} (vorher "
          f"{bewerten(paare, {i: blocks[i]['farbe'] for i in ids}, {i: build.farbe_dunkel(blocks[i]) for i in ids})[0][0]:.3f})")
    print(f"Paare unter {build.ABSTAND_WARNEN}: "
          f"{sum(1 for w in werte if w[0] < build.ABSTAND_WARNEN)} von {len(werte)}")
    print(f"groesste Drehung: {max(abs(v['drehung']) for v in vorschlag.values()):.0f}°")
    if args.ziel:
        pathlib.Path(args.ziel).write_text(
            json.dumps(vorschlag, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"geschrieben: {args.ziel}")


def cmd_anwenden(args):
    vorschlag = json.loads(pathlib.Path(args.datei).read_text(encoding="utf-8"))
    blocks = json.loads(BLOCKS_JSON.read_text(encoding="utf-8"))
    for b in blocks:
        if b["id"] not in vorschlag:
            print(f"FEHLER: Vorschlag kennt Block {b['id']} nicht", file=sys.stderr)
            sys.exit(1)
        b["farbe"] = vorschlag[b["id"]]["farbe"]
        b["dunkel_stufe"] = vorschlag[b["id"]]["dunkel_stufe"]
        b.pop("farbe_dunkel", None)
    BLOCKS_JSON.write_text(json.dumps(blocks, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"blocks.json aktualisiert ({len(blocks)} Themen)")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    u = p.add_subparsers(dest="befehl", required=True)
    u.add_parser("pruefen").set_defaults(func=cmd_pruefen)
    v = u.add_parser("vorschlagen")
    v.add_argument("--spielraum", type=float, default=25.0,
                   help="wie viel Grad sich ein Farbton hoechstens drehen darf (Standard 25)")
    v.add_argument("--runden", type=int, default=8)
    v.add_argument("--saat", type=int, default=11)
    v.add_argument("--ziel")
    v.set_defaults(func=cmd_vorschlagen)
    a = u.add_parser("anwenden")
    a.add_argument("datei")
    a.set_defaults(func=cmd_anwenden)
    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
