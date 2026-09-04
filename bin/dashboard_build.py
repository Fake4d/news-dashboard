#!/usr/bin/env python3
"""Baut alle Dashboard-Seiten aus Vorlage, Themenliste und gemeinsamem State.

Kein Modellaufruf, keine Recherche - reines Rendern. Ein Thema (Block) hat genau
einen State unter state/dashboard/<id>.json; erscheint es in mehreren
Dashboards, wird derselbe Text auf allen Seiten ausgegeben. Dadurch kostet ein
gemeinsames Thema nur einen Recherche-Lauf.

Aufruf:  dashboard_build.py

Gebaut werden zwei Arten von Seiten:

  dashboard/gebaut/<dashboard-id>/index.html   ein Dashboard mit allen Kacheln
  dashboard/gebaut/thema/<block-id>/index.html eine einzelne Kachel zum Verschicken

Die Themenseite gibt es, damit man eine Kachel weitergeben kann, ohne das
ganze Dashboard herzugeben. Sie zieht ihren Text aus demselben State und
verlinkt bewusst NICHT zurueck - sonst waere der Zweck hinfaellig.

Geschrieben wird nur, wenn sich der Inhalt tatsaechlich geaendert hat. Auf
stdout kommt je geaenderter Seite eine Zeile mit drei 0x1F-getrennten Feldern:

    <lokales Verzeichnis>\x1f<Verzeichnis auf dem Server>\x1f<Pruef-URL>

Damit kann der Aufrufer beide Seitenarten durch dieselbe Upload-Schleife
schicken und muss nichts mehr nachschlagen. Bei einem Fehler (unbekannte
Block-ID, fehlende Vorlage) Exit-Code 1 und nichts geschrieben.
"""
import datetime
import html
import itertools
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dashboard_farbe as farbe

CLAUDE = pathlib.Path("/home/gra/claude")
DASH = CLAUDE / "dashboard"
BLOCKS_JSON = DASH / "blocks.json"
DASHBOARDS_JSON = DASH / "dashboards.json"
SITE_JSON = DASH / "site.json"
VORLAGE = DASH / "template.html"
VORLAGE_THEMA = DASH / "template-thema.html"
THEMA_INDEX = DASH / "thema-index.html"
ZIEL = DASH / "gebaut"
STATE_DIR = CLAUDE / "state" / "dashboard"
ASSETS = DASH / "assets"

# Trennzeichen der stdout-Zeilen. 0x1F und nicht Tab, aus demselben Grund wie
# in dashboard-update.sh: Tab ist fuer bash Whitespace, ein leeres Feld wuerde
# stillschweigend alle Folgespalten verschieben.
TRENN = "\x1f"

# Erlaubte Block-IDs. Die ID ist nicht nur Kosmetik: sie landet als
# Verzeichnisname unter gebaut/thema/, als Pfad in der Upload-Zeile, in einem
# href und in einem CSS-Variablennamen. Ein Tippfehler mit "/" oder ".." wuerde
# ausserhalb des Zielbaums schreiben, ein Anfuehrungszeichen das href
# aufbrechen. Die README bittet um Kleinbuchstaben - hier wird es durchgesetzt.
ID_MUSTER = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")

# Platzhalter in den Vorlagen. Nach dem Einsetzen darf keiner mehr uebrig sein.
# Bewusst eng gefasst: eine Pruefung auf blosse "%%" wuerde auch an einem
# Kacheltext mit "5%% Rendite" ausschlagen und den GESAMTEN Build abbrechen -
# und zwar erst, nachdem die teure Recherche gelaufen ist.
PLATZHALTER = re.compile(r"%%[A-Z_]+%%")

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


def kachel_titel(block, state):
    """Ueberschrift der Kachel.

    Kacheln, die sich nach ihrem Inhalt benennen (Top-Story), tragen ihren
    Titel im State. Solange sie noch nie befuellt wurden, greift der feste
    Titel aus blocks.json als Platzhalter.
    """
    return (state or {}).get("kachel_titel") or block.get("kachel_titel", block["titel"])


def klartext(state, aufzaehlung="• "):
    """Kacheltext ohne Auszeichnung, Listenmarker durch ein Zeichen ersetzt.

    Die Marker '*' und '~' sind eine interne Formatangabe des Skills. Sie
    duerfen weder in der Zwischenablage noch in der Link-Vorschau auftauchen -
    dort las man sonst "* Bei Waldbraenden ... * Syrien hat ...".
    """
    text = state["quintessenz_text"]
    if state.get("format") != "liste":
        return text.strip()
    zeilen = []
    for zeile in text.split("\n"):
        zeile = zeile.strip()
        if not zeile:
            continue
        zeilen.append(aufzaehlung + (zeile[1:].strip() if zeile[:1] in ("*", "~") else zeile))
    return "\n".join(zeilen)


def rohtext(block, state, url):
    """Der Kacheltext als Klartext - das, was der Kopierknopf weitergibt.

    Bewusst ohne Auszeichnung: er landet in WhatsApp oder einer Mail, nicht in
    einem Browser. Die Adresse steht darunter, damit der Empfaenger die Quelle
    hat und spaeter nachsehen kann.
    """
    titel = kachel_titel(block, state)
    if state is None:
        return f"{titel}\n\n(noch nicht befüllt)\n\n{url}"
    kopf = f"{titel} — Stand: {deutsches_datum(state['stand_datum'])}"
    return f"{kopf}\n\n{klartext(state)}\n\n{url}"


def karte_bauen(block, permalink=None):
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

    titel = kachel_titel(block, state)

    # Breite Kacheln (z.B. der Nachrichtenueberblick) laufen ueber alle Spalten
    # des Rasters, damit eine lange Liste nicht als schmale Saeule dasteht.
    klassen = "card wide" if block.get("breit") else "card"

    # Weg zur Einzelseite dieses Themas - damit sich eine Kachel weitergeben
    # laesst, ohne das ganze Dashboard herzugeben.
    teilen = (
        f'\n        <a class="teilen" href="{permalink}" '
        f'title="Einzelseite zum Weitergeben">teilen&nbsp;↗</a>'
        if permalink else ""
    )

    return f'''    <article class="{klassen}" id="block-{bid}" style="--accent:var(--c-{bid})">
      <div class="card-body">
        <div class="card-head">
          <span class="icon">{html.escape(block["icon"])}</span>
          <div class="titles">
            <p class="card-eyebrow">{html.escape(block["eyebrow"])}</p>
            <p class="card-title">{html.escape(titel)}</p>
          </div>
        </div>
        {inhalt}
      </div>
      <div class="card-foot">
        <span class="stand">{stand}</span>
        <span class="cadence">{takt_text(block)}</span>{teilen}
      </div>
    </article>'''


def themenseite_bauen(block, vorlage, basis_url, thema_remote):
    """Einzelseite eines Themas: dieselbe Kachel, allein, mit Kopierknopf.

    Kein Rueckweg zum Dashboard und kein Verweis darauf - wer diesen Link
    bekommt, soll genau diese eine Kachel sehen und sonst nichts.
    """
    bid = block["id"]
    state = state_lesen(bid)
    url = f"{basis_url}/{thema_remote}/{bid}/"

    titel = kachel_titel(block, state)

    if state is None:
        inhalt = '<p class="text empty">Wird beim nächsten Lauf befüllt.</p>'
        stand = "Stand: —"
        beschreibung = f"{titel} — wird beim nächsten Lauf befüllt."
    else:
        if state.get("format") == "liste":
            inhalt = liste_rendern(state["quintessenz_text"])
        else:
            absaetze = [a for a in state["quintessenz_text"].split("\n\n") if a.strip()]
            inhalt = "".join(f'<p class="text">{html.escape(a)}</p>' for a in absaetze)
        stand = f"Stand: {deutsches_datum(state['stand_datum'])}"
        # Vorschautext fuer WhatsApp/iMessage: der Anfang des Textes, an einer
        # Wortgrenze gekappt. Ueber klartext(), sonst stuenden die Listenmarker
        # des Skills in der Vorschau - genau dort, wo der Link gelesen wird.
        roh = " ".join(klartext(state, aufzaehlung="").split())
        beschreibung = roh if len(roh) <= 200 else roh[:200].rsplit(" ", 1)[0] + " …"

    # Klartext fuer die Zwischenablage. Steckt in einem Attribut, deshalb
    # muessen auch die Zeilenumbrueche kodiert werden - roh wuerden manche
    # Parser sie zu Leerzeichen glaetten.
    kopiertext = html.escape(rohtext(block, state, url), quote=True).replace("\n", "&#10;")

    seite = vorlage
    for platzhalter, wert in [
        ("%%TITEL%%", html.escape(titel)),
        ("%%EYEBROW%%", html.escape(block["eyebrow"])),
        ("%%ICON%%", html.escape(block["icon"])),
        ("%%URL%%", html.escape(url)),
        ("%%BESCHREIBUNG%%", html.escape(beschreibung)),
        ("%%FARBE_HELL%%", block["farbe"]),
        ("%%FARBE_DUNKEL%%", farbe_dunkel(block)),
        ("%%INHALT%%", inhalt),
        ("%%STAND%%", stand),
        ("%%TAKT%%", takt_text(block)),
        ("%%ROHTEXT%%", kopiertext),
    ]:
        seite = seite.replace(platzhalter, wert)

    uebrig = sorted(set(PLATZHALTER.findall(seite)))
    if uebrig:
        fehler(f"Thema {bid}: unersetzte Platzhalter in der Vorlage: {uebrig[:3]}")

    return seite


def seite_bauen(dashboard, blocks_nach_id, vorlage, thema_remote="thema"):
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

    # Der teilen-Link zeigt ab dem Wurzelverzeichnis, nicht relativ: die
    # Dashboards liegen in verschiedenen Ordnern, die Themenseiten in einem.
    karten = [karte_bauen(b, f'/{thema_remote}/{b["id"]}/') for b in blocks]

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

    uebrig = sorted(set(PLATZHALTER.findall(seite)))
    if uebrig:
        fehler(f"Dashboard {dashboard['id']}: unersetzte Platzhalter in der Vorlage: {uebrig[:3]}")

    return seite


def schreiben(ziel, seite, remote, url):
    """Seite schreiben, wenn sie sich unterscheidet, und dann melden.

    Die Meldung geht an die Upload-Schleife des Aufrufers: lokales
    Verzeichnis, Zielverzeichnis auf dem Server, Adresse zum Nachpruefen.
    """
    ziel.parent.mkdir(parents=True, exist_ok=True)
    alt = ziel.read_text(encoding="utf-8") if ziel.exists() else None
    if alt != seite:
        ziel.write_text(seite, encoding="utf-8")
        print(TRENN.join([str(ziel.parent), remote, url]))


def main():
    for pfad in (BLOCKS_JSON, DASHBOARDS_JSON, SITE_JSON, VORLAGE, VORLAGE_THEMA,
                 THEMA_INDEX):
        if not pfad.exists():
            fehler(f"Datei fehlt: {pfad}")

    blocks = json.loads(BLOCKS_JSON.read_text(encoding="utf-8"))
    dashboards = json.loads(DASHBOARDS_JSON.read_text(encoding="utf-8"))
    site = json.loads(SITE_JSON.read_text(encoding="utf-8"))
    vorlage = VORLAGE.read_text(encoding="utf-8")
    vorlage_thema = VORLAGE_THEMA.read_text(encoding="utf-8")

    basis_url = site["basis_url"].rstrip("/")
    thema_remote = site["thema_remote"].strip("/")

    blocks_nach_id = {b["id"]: b for b in blocks}
    if len(blocks_nach_id) != len(blocks):
        fehler("blocks.json enthaelt doppelte IDs")

    krumm = [b["id"] for b in blocks if not ID_MUSTER.match(b["id"])]
    if krumm:
        fehler(f"unzulaessige Block-IDs {krumm} - erlaubt sind Kleinbuchstaben, "
               f"Ziffern und einzelne Bindestriche dazwischen. Die ID wird zum "
               f"Verzeichnisnamen und zum Teil einer Adresse.")

    farben_pruefen(dashboards, blocks_nach_id)

    # Eine Themenseite bekommt nur, was auch auf einem Dashboard steht - sonst
    # laege eine Seite auf dem Server, auf die nichts verlinkt. Reihenfolge
    # stabil halten, damit die Ausgabe zwischen zwei Laeufen vergleichbar ist.
    genutzt = [b["id"] for b in blocks
               if any(b["id"] in d["blocks"] for d in dashboards)]

    # Ein Thema, das aus allen Dashboards genommen wurde, wird nicht mehr
    # gebaut - seine Seite bleibt aber auf dem Server stehen und friert auf dem
    # letzten Stand ein. Loeschen kann der Generator nicht (er kennt den Server
    # nicht), aber schweigen soll er darueber auch nicht.
    themen_ordner = ZIEL / thema_remote
    if themen_ordner.is_dir():
        verwaist = sorted(p.name for p in themen_ordner.iterdir()
                          if p.is_dir() and p.name not in genutzt)
        for name in verwaist:
            print(f"VERWAIST: {thema_remote}/{name}/ wird von keinem Dashboard "
                  f"mehr genutzt, liegt aber noch auf dem Server. Von Hand "
                  f"loeschen (FTP) und {themen_ordner / name} entfernen.",
                  file=sys.stderr)

    # Erst alle Seiten rendern, dann schreiben: bei einem Fehler in Dashboard 2
    # soll Dashboard 1 nicht schon halb aktualisiert auf der Platte liegen.
    fertig = [(d, seite_bauen(d, blocks_nach_id, vorlage, thema_remote))
              for d in dashboards]
    fertig_themen = [
        (bid, themenseite_bauen(blocks_nach_id[bid], vorlage_thema, basis_url,
                                thema_remote))
        for bid in genutzt
    ]

    for bid, seite in fertig_themen:
        schreiben(ZIEL / thema_remote / bid / "index.html", seite,
                  f"{thema_remote}/{bid}", f"{basis_url}/{thema_remote}/{bid}/")

    # Sperrseite fuer /thema/ selbst: verhindert, dass Apache dort das
    # Verzeichnis auflistet und damit die ganze Themenliste ausstellt.
    schreiben(ZIEL / thema_remote / "index.html",
              THEMA_INDEX.read_text(encoding="utf-8"),
              thema_remote, f"{basis_url}/{thema_remote}/")

    for dashboard, seite in fertig:
        ziel = ZIEL / dashboard["id"] / "index.html"
        schreiben(ziel, seite, dashboard["remote"], dashboard["url"])

        # Statische Assets (Icons fuers iOS-Homescreen) unconditionally mit
        # ins Zielverzeichnis kopieren - referenziert von template.html, aber
        # nicht Teil des Text-Diffs oben, sonst heilt ein fehlendes Icon nie
        # von selbst nach, wenn gerade kein Thema faellig war.
        if ASSETS.is_dir():
            for quelle in ASSETS.glob("*.png"):
                (ziel.parent / quelle.name).write_bytes(quelle.read_bytes())


if __name__ == "__main__":
    main()
