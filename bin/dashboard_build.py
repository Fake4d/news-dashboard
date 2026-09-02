#!/usr/bin/env python3
"""Baut alle Dashboard-Seiten aus Vorlage, Themenliste und gemeinsamem State.

Kein Modellaufruf, keine Recherche - reines Rendern. Ein Thema (Block) hat genau
einen State unter state/dashboard/<id>.json; erscheint es in mehreren
Dashboards, wird derselbe Text auf allen Seiten ausgegeben. Dadurch kostet ein
gemeinsames Thema nur einen Recherche-Lauf.

Aufruf:  dashboard_build.py

Schreibt dashboard/gebaut/<dashboard-id>/index.html, aber nur wenn sich der
Inhalt tatsaechlich geaendert hat. Gibt auf stdout die IDs der geaenderten
Dashboards aus, eine pro Zeile - die ruft der Aufrufer dann hoch. Bei einem
Fehler (unbekannte Block-ID, fehlende Vorlage) Exit-Code 1 und nichts
geschrieben.
"""
import datetime
import html
import itertools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dashboard_farbe as farbe

CLAUDE = pathlib.Path("/home/gra/claude")
DASH = CLAUDE / "dashboard"
BLOCKS_JSON = DASH / "blocks.json"
DASHBOARDS_JSON = DASH / "dashboards.json"
VORLAGE = DASH / "template.html"
ZIEL = DASH / "gebaut"
STATE_DIR = CLAUDE / "state" / "dashboard"
ASSETS = DASH / "assets"

# Die Dunkelmodus-Farbe wird aus der Hellfarbe gerechnet, nicht von Hand
# gepflegt: gleicher Farbton, aber feste Buntheit und eine von drei
# Helligkeitsstufen (dunkel_stufe in blocks.json). Handgewaehlte Dunkelfarben
# waren alle aehnlich hell und aehnlich blass - damit blieb im Dunkelmodus der
# Farbton als einziges Unterscheidungsmerkmal uebrig, und bei 16 Kacheln auf
# einer Seite reicht das nicht. Die Stufe gibt die zweite Achse zurueck.
DUNKEL_STUFEN = {"hell": 0.88, "mittel": 0.78, "tief": 0.68}
DUNKEL_BUNTHEIT = 0.15

# Wachhund: zwei Kacheln derselben Seite duerfen nicht dieselbe Farbe zu haben
# scheinen. Abstand in OKLab, also ungefaehr das, was das Auge als Unterschied
# sieht. Unter WARNEN wird gemeckert, unter BLOCKIEREN bricht der Build ab -
# eine neue Kachel soll nicht stillschweigend die Farbe einer bestehenden
# uebernehmen.
ABSTAND_WARNEN = 0.075
ABSTAND_BLOCKIEREN = 0.030

MONATE = [
    "", "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
]


def fehler(msg):
    print(f"FEHLER: {msg}", file=sys.stderr)
    sys.exit(1)


def farbe_dunkel(block):
    """Dunkelmodus-Farbe eines Themas: gleicher Farbton, Helligkeit nach Stufe.

    Ein ausdruecklich eingetragenes farbe_dunkel hat Vorrang - fuer den Fall,
    dass ein Thema eine handgewaehlte Ausnahme braucht.
    """
    if block.get("farbe_dunkel"):
        return block["farbe_dunkel"]
    stufe = block.get("dunkel_stufe", "mittel")
    if stufe not in DUNKEL_STUFEN:
        fehler(f"Block {block['id']}: unbekannte dunkel_stufe {stufe!r} "
               f"(erlaubt: {', '.join(DUNKEL_STUFEN)})")
    _, _, ton = farbe.hex_zu_oklch(block["farbe"])
    return farbe.oklch_zu_hex(DUNKEL_STUFEN[stufe], DUNKEL_BUNTHEIT, ton)


def farben_pruefen(dashboards, blocks_nach_id):
    """Meldet Kachelpaare, die auf derselben Seite zu aehnlich aussehen."""
    zu_nah = []
    for d in dashboards:
        for a, b in itertools.combinations(d["blocks"], 2):
            ba, bb = blocks_nach_id[a], blocks_nach_id[b]
            # Themen einer Familie (z.B. Bundes- und Landespolitik) duerfen
            # verwandt aussehen - das ist Absicht, keine Kollision.
            if ba.get("familie") and ba.get("familie") == bb.get("familie"):
                continue
            for modus, wert in (("hell", "farbe"), ("dunkel", None)):
                fa = ba[wert] if wert else farbe_dunkel(ba)
                fb = bb[wert] if wert else farbe_dunkel(bb)
                ab = farbe.abstand(fa, fb)
                if ab < ABSTAND_WARNEN:
                    zu_nah.append((ab, d["id"], modus, a, fa, b, fb))
    for ab, did, modus, a, fa, b, fb in sorted(zu_nah):
        wie = "ZU AEHNLICH" if ab >= ABSTAND_BLOCKIEREN else "PRAKTISCH GLEICH"
        print(f"Farbe {wie} ({ab:.3f}) auf {did}, {modus}: {a} {fa} / {b} {fb}",
              file=sys.stderr)
    schlimmste = [z for z in zu_nah if z[0] < ABSTAND_BLOCKIEREN]
    if schlimmste:
        fehler(f"{len(schlimmste)} Kachelpaar(e) sind farblich nicht mehr zu "
               f"unterscheiden - blocks.json korrigieren "
               f"(bin/dashboard_farben.py hilft beim Vorschlag)")


def deutsches_datum(iso):
    d = datetime.date.fromisoformat(iso)
    return f"{d.day}. {MONATE[d.month]} {d.year}"


def takt_text(block):
    tage = block["cadence_days"]
    if tage == 1:
        return "täglich, werktags" if block.get("weekday_only") else "täglich"
    if tage == 7:
        return "wöchentlich"
    return f"alle {tage} Tage"


def state_lesen(block_id):
    """State eines Themas, oder None wenn es noch nie befuellt wurde."""
    pfad = STATE_DIR / f"{block_id}.json"
    if not pfad.exists():
        return None
    state = json.loads(pfad.read_text(encoding="utf-8"))
    if not state.get("quintessenz_text"):
        return None
    return state


def stand_datum(block_id):
    """Datum der letzten inhaltlichen Aenderung, oder None wenn nie befuellt."""
    state = state_lesen(block_id)
    return state["stand_datum"] if state else None


def liste_rendern(text):
    """Meldungsliste: je Zeile eine Meldung, Marker '*' ernst / '~' leicht."""
    zeilen = []
    for zeile in text.split("\n"):
        zeile = zeile.strip()
        if not zeile:
            continue
        leicht = zeile.startswith("~")
        meldung = zeile[1:].strip() if zeile[:1] in ("*", "~") else zeile
        klasse = ' class="leicht"' if leicht else ""
        zeilen.append(f"<li{klasse}>{html.escape(meldung)}</li>")
    return '<ul class="news">' + "".join(zeilen) + "</ul>"


def karte_bauen(block):
    bid = block["id"]
    state = state_lesen(bid)

    if state is None:
        inhalt = '<p class="text empty">Wird beim nächsten Lauf befüllt.</p>'
        stand = "Stand: —"
    elif state.get("format") == "liste":
        inhalt = liste_rendern(state["quintessenz_text"])
        stand = f"Stand: {deutsches_datum(state['stand_datum'])}"
    else:
        absaetze = [a for a in state["quintessenz_text"].split("\n\n") if a.strip()]
        inhalt = "".join(f'<p class="text">{html.escape(a)}</p>' for a in absaetze)
        stand = f"Stand: {deutsches_datum(state['stand_datum'])}"

    # Kacheln, die sich nach ihrem Inhalt benennen (Top-Story), tragen ihren
    # Titel im State. Solange sie noch nie befuellt wurden, greift der feste
    # Titel aus blocks.json als Platzhalter.
    titel = (state or {}).get("kachel_titel") or block.get("kachel_titel", block["titel"])

    # Breite Kacheln (z.B. der Nachrichtenueberblick) laufen ueber alle Spalten
    # des Rasters, damit eine lange Liste nicht als schmale Saeule dasteht.
    klassen = "card wide" if block.get("breit") else "card"

    return f'''    <article class="{klassen}" id="block-{bid}" style="--accent:var(--c-{bid})">
      <div class="card-body">
        <div class="card-head">
          <span class="icon">{block["icon"]}</span>
          <div class="titles">
            <p class="card-eyebrow">{html.escape(block["eyebrow"])}</p>
            <p class="card-title">{html.escape(titel)}</p>
          </div>
        </div>
        {inhalt}
      </div>
      <div class="card-foot">
        <span class="stand">{stand}</span>
        <span class="cadence">{takt_text(block)}</span>
      </div>
    </article>'''


def seite_bauen(dashboard, blocks_nach_id, vorlage):
    ids = dashboard["blocks"]
    unbekannt = [i for i in ids if i not in blocks_nach_id]
    if unbekannt:
        fehler(f"Dashboard {dashboard['id']}: unbekannte Block-IDs {unbekannt} (nicht in blocks.json)")
    if not ids:
        fehler(f"Dashboard {dashboard['id']}: keine Blocks eingetragen")

    blocks = [blocks_nach_id[i] for i in ids]

    # Die Puls-Farbe des Live-Badges richtet sich nach dem ersten Thema der
    # Seite - sonst haette ein Dashboard ohne den Aktienmarkt-Block gar keine.
    farben_hell = [f'    --c-live:{blocks[0]["farbe"]};']
    farben_dunkel = [f'      --c-live:{farbe_dunkel(blocks[0])};']
    for b in blocks:
        farben_hell.append(f'    --c-{b["id"]}:{b["farbe"]};')
        farben_dunkel.append(f'      --c-{b["id"]}:{farbe_dunkel(b)};')

    # Die Sprungleiste ist zugleich die Uebersicht "was ist heute neu": heute
    # inhaltlich geaenderte Themen stehen kraeftig da, der Bestand tritt
    # zurueck. Massstab ist stand_datum (echte Aenderung), NICHT letzter_check -
    # ein geprueftes, aber unveraendertes Thema ist eben nichts Neues.
    heute = datetime.date.today().isoformat()
    staende = {b["id"]: stand_datum(b["id"]) for b in blocks}
    frisch_ids = [b["id"] for b in blocks if staende[b["id"]] == heute]

    nav = []
    for b in blocks:
        ist_frisch = staende[b["id"]] == heute
        klasse = "frisch" if ist_frisch else "matt"
        titel = ' title="heute aktualisiert"' if ist_frisch else ""
        nav.append(
            f'    <a class="{klasse}" href="#block-{b["id"]}"{titel} style="--c:var(--c-{b["id"]})">'
            f'<span class="dot"></span>{html.escape(b["nav"])}</a>'
        )

    # Zusatz im Live-Badge. Ohne frische Kachel (z.B. morgens vor dem Cron)
    # lieber das juengste vorhandene Datum nennen als eine magere "0".
    if frisch_ids:
        # "heute" stimmt nur am Bautag: die Seite steht bis zum naechsten
        # Cron-Lauf, ab Mitternacht waere die Aussage falsch. Deshalb das
        # Baudatum mitgeben und einen datumsfesten Ersatztext - ein paar
        # Zeilen Skript in der Vorlage tauschen ihn dann selbst um.
        live_zusatz = (
            f'<span class="neu" data-stand="{heute}"'
            f' data-alt="{len(frisch_ids)} neu am {deutsches_datum(heute)}">'
            f'{len(frisch_ids)} heute neu</span>'
        )
    else:
        vorhanden = sorted(d for d in staende.values() if d)
        live_zusatz = (
            f'<span class="neu">zuletzt {deutsches_datum(vorhanden[-1])}</span>'
            if vorhanden else ""
        )

    karten = [karte_bauen(b) for b in blocks]

    seite = vorlage
    for platzhalter, wert in [
        ("%%TITEL%%", html.escape(dashboard["titel"])),
        ("%%H1%%", html.escape(dashboard["h1"])),
        ("%%LEAD%%", html.escape(dashboard["lead"])),
        ("%%URL%%", html.escape(dashboard["url"])),
        ("%%FARBEN_HELL%%", "\n".join(farben_hell)),
        ("%%FARBEN_DUNKEL%%", "\n".join(farben_dunkel)),
        ("%%NAV%%", "\n".join(nav)),
        ("%%LIVE_ZUSATZ%%", live_zusatz),
        ("%%KARTEN%%", "\n\n".join(karten)),
    ]:
        seite = seite.replace(platzhalter, wert)

    uebrig = [z for z in seite.splitlines() if "%%" in z]
    if uebrig:
        fehler(f"Dashboard {dashboard['id']}: unersetzte Platzhalter in der Vorlage: {uebrig[:3]}")

    return seite


def main():
    for pfad in (BLOCKS_JSON, DASHBOARDS_JSON, VORLAGE):
        if not pfad.exists():
            fehler(f"Datei fehlt: {pfad}")

    blocks = json.loads(BLOCKS_JSON.read_text(encoding="utf-8"))
    dashboards = json.loads(DASHBOARDS_JSON.read_text(encoding="utf-8"))
    vorlage = VORLAGE.read_text(encoding="utf-8")

    blocks_nach_id = {b["id"]: b for b in blocks}
    if len(blocks_nach_id) != len(blocks):
        fehler("blocks.json enthaelt doppelte IDs")

    farben_pruefen(dashboards, blocks_nach_id)

    # Erst alle Seiten rendern, dann schreiben: bei einem Fehler in Dashboard 2
    # soll Dashboard 1 nicht schon halb aktualisiert auf der Platte liegen.
    fertig = [(d, seite_bauen(d, blocks_nach_id, vorlage)) for d in dashboards]

    for dashboard, seite in fertig:
        ziel = ZIEL / dashboard["id"] / "index.html"
        ziel.parent.mkdir(parents=True, exist_ok=True)
        alt = ziel.read_text(encoding="utf-8") if ziel.exists() else None
        if alt != seite:
            ziel.write_text(seite, encoding="utf-8")
            print(dashboard["id"])

        # Statische Assets (Icons fuers iOS-Homescreen) unconditionally mit
        # ins Zielverzeichnis kopieren - referenziert von template.html, aber
        # nicht Teil des Text-Diffs oben, sonst heilt ein fehlendes Icon nie
        # von selbst nach, wenn gerade kein Thema faellig war.
        if ASSETS.is_dir():
            for quelle in ASSETS.glob("*.png"):
                (ziel.parent / quelle.name).write_bytes(quelle.read_bytes())


if __name__ == "__main__":
    main()
