#!/usr/bin/env python3
"""Mechanischer Schritt fuer das Dashboard: kein Modellaufruf.

Nimmt die Modell-Ausgabe des Skills 'dashboard-update' auf stdin, dazu die
Block-ID als Argument, und schreibt daraus den State des Themas. Das Rendern
der HTML-Seiten macht danach dashboard_build.py - ein Thema kann in mehreren
Dashboards vorkommen, hat aber nur diesen einen State und wird deshalb nur
einmal recherchiert.

Aufruf:  dashboard_apply.py <block-id> [format] [titel-dynamisch]
         (Modell-Ausgabe auf stdin)

Format ist leer (Fliesstext-Absaetze, Standard) oder 'liste': dann besteht
QUINTESSENZ aus je einer Zeile pro Meldung, eingeleitet durch '* ' (ernst) oder
'~ ' (leicht/boulevardesk). Die Marker bleiben im State erhalten, damit
dashboard_build.py beide Sorten unterschiedlich rendern kann.

Titel-dynamisch ('1'): der Block heisst auf der Seite nach seinem Inhalt, nicht
nach einem festen Eintrag in blocks.json (Top-Story des Tages). Dann ist eine
zusaetzliche TITEL-Zeile Pflicht, die als kachel_titel in den State wandert;
dashboard_build.py zieht sie dem statischen Titel vor.

Gibt auf stdout genau ein Wort aus: 'geaendert' oder 'unveraendert'.
Bei unparsbarer Ausgabe: Fehlermeldung auf stderr, Exit-Code 1, nichts
geschrieben.
"""
import datetime
import json
import pathlib
import re
import sys

CLAUDE = pathlib.Path("/home/gra/claude")
STATE_DIR = CLAUDE / "state" / "dashboard"


def fehler(msg):
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def parse(ausgabe, block_id, formatart, titel_dynamisch):
    status_m = re.search(r"^STATUS:\s*(geaendert|unveraendert)\s*$", ausgabe, re.M)
    if not status_m:
        fehler(f"Block {block_id}: keine erkennbare STATUS-Zeile in der Modell-Ausgabe")
    status = status_m.group(1)

    stand_m = re.search(r"^STAND:\s*(\d{4}-\d{2}-\d{2})\s*$", ausgabe, re.M)
    if not stand_m:
        fehler(f"Block {block_id}: keine erkennbare STAND-Zeile in der Modell-Ausgabe")
    stand = stand_m.group(1)

    if not re.search(r"^ENDE\s*$", ausgabe, re.M):
        fehler(f"Block {block_id}: keine ENDE-Zeile in der Modell-Ausgabe")

    if status == "unveraendert":
        return {"status": status, "stand": stand}

    # Nur bei dynamischem Titel: die Kachel heisst nach ihrem heutigen Inhalt.
    # Fehlt die Zeile, faellt der ganze Lauf durch und die Kachel bleibt auf
    # dem Vortagesstand - lieber die gestrige Geschichte unter ihrer eigenen
    # Ueberschrift als die heutige unter der gestrigen.
    kachel_titel = None
    if titel_dynamisch:
        titel_m = re.search(r"^TITEL:\s*(\S.*?)\s*$", ausgabe, re.M)
        if not titel_m:
            fehler(f"Block {block_id}: TITEL-Zeile fehlt (dieser Block benennt sich nach seinem Inhalt)")
        kachel_titel = " ".join(titel_m.group(1).split()).rstrip(".")
        if len(kachel_titel) > 80:
            fehler(f"Block {block_id}: TITEL zu lang ({len(kachel_titel)} Zeichen, hoechstens 80)")

    kern_m = re.search(r"^KERNAUSSAGEN:\s*\n((?:-.*\n?)+)", ausgabe, re.M)
    if not kern_m:
        fehler(f"Block {block_id}: KERNAUSSAGEN-Abschnitt fehlt oder ist leer")
    kernaussagen = [
        line.strip().lstrip("-").strip()
        for line in kern_m.group(1).splitlines()
        if line.strip()
    ]

    quint_m = re.search(r"^QUINTESSENZ:\s*\n(.*?)\n^ENDE\s*$", ausgabe, re.M | re.S)
    if not quint_m or not quint_m.group(1).strip():
        fehler(f"Block {block_id}: QUINTESSENZ-Abschnitt fehlt oder ist leer")

    if formatart == "liste":
        # Eine Meldung je Zeile, Marker '*' (ernst) oder '~' (leicht). Leere
        # Zeilen sind hier bedeutungslos und werden einfach uebergangen.
        punkte = []
        for zeile in quint_m.group(1).splitlines():
            zeile = zeile.strip()
            if not zeile:
                continue
            m = re.match(r"^([*~])\s+(.+)$", zeile)
            if not m:
                fehler(f"Block {block_id}: Listenzeile ohne '* '/'~ '-Marker: {zeile[:60]!r}")
            punkte.append(f"{m.group(1)} {' '.join(m.group(2).split())}")
        if not punkte:
            fehler(f"Block {block_id}: Listenformat verlangt, aber keine Meldung gefunden")
        return {"status": status, "stand": stand, "kernaussagen": kernaussagen,
                "format": "liste", "teile": punkte, "kachel_titel": kachel_titel}

    # Absaetze sind durch eine komplett leere Zeile getrennt (z.B. Hauptabsatz +
    # Zusatzabsatz); innerhalb eines Absatzes wird alles zu einer Zeile.
    absaetze = [
        " ".join(teil.split())
        for teil in re.split(r"\n\s*\n", quint_m.group(1).strip())
        if teil.strip()
    ]
    if not absaetze:
        fehler(f"Block {block_id}: QUINTESSENZ-Abschnitt fehlt oder ist leer")

    return {"status": status, "stand": stand, "kernaussagen": kernaussagen,
            "format": "text", "teile": absaetze, "kachel_titel": kachel_titel}


def main():
    if len(sys.argv) not in (2, 3, 4):
        fehler("Aufruf: dashboard_apply.py <block-id> [format] [titel-dynamisch]")
    block_id = sys.argv[1]
    formatart = sys.argv[2] if len(sys.argv) >= 3 else ""
    titel_dynamisch = len(sys.argv) >= 4 and sys.argv[3] == "1"
    if formatart not in ("", "text", "liste"):
        fehler(f"unbekanntes Format {formatart!r} (erlaubt: leer, text, liste)")

    state_pfad = STATE_DIR / f"{block_id}.json"
    if not state_pfad.exists():
        fehler(f"State-Datei fehlt: {state_pfad}")

    ergebnis = parse(sys.stdin.read(), block_id, formatart, titel_dynamisch)

    state = json.loads(state_pfad.read_text(encoding="utf-8"))
    heute = datetime.date.today().isoformat()

    if ergebnis["status"] == "unveraendert":
        state["letzter_check"] = heute
        state_pfad.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("unveraendert")
        return

    state["stand_datum"] = ergebnis["stand"]
    state["letzter_check"] = heute
    # Fliesstext: Absaetze durch Leerzeile getrennt. Liste: eine Meldung je
    # Zeile, damit dashboard_build.py beides auseinanderhalten kann.
    trenner = "\n" if ergebnis["format"] == "liste" else "\n\n"
    state["quintessenz_text"] = trenner.join(ergebnis["teile"])
    state["format"] = ergebnis["format"]
    state["kernaussagen"] = ergebnis["kernaussagen"]
    if ergebnis["kachel_titel"]:
        state["kachel_titel"] = ergebnis["kachel_titel"]
    state_pfad.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("geaendert")


if __name__ == "__main__":
    main()
