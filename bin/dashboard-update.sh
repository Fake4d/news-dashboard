#!/usr/bin/env bash
# Per Cron (freies Zeitfenster): prueft pro Thema (Block), ob eine
# Aktualisierung faellig ist, und startet nur dann einen (kostenpflichtigen)
# Claude-Lauf - genau wie mailcheck/refcheck/boris.
#
# Aufbau seit 30.08.2026:
#   dashboard/blocks.json        Themen-Registry (ein Eintrag je Thema)
#   dashboard/dashboards.json    welches Dashboard zeigt welche Themen
#   dashboard/site.json          Basis-Adresse + Ordner der Themenseiten
#   dashboard/template.html      gemeinsame Design-Vorlage
#   dashboard/template-thema.html  Einzelseite je Thema (zum Weitergeben)
#   dashboard/thema-index.html   Sperrseite fuer /thema/
#   state/dashboard/<id>.json    Gedaechtnis je THEMA, nicht je Dashboard
#
# Ein Thema, das in mehreren Dashboards vorkommt (z.B. muenchen, eichenau),
# hat genau einen State und wird deshalb auch nur EINMAL recherchiert; der
# Text erscheint anschliessend auf allen Seiten, die es einbinden. Themen,
# die kein Dashboard verwendet, werden gar nicht geprueft.
#
# Faellig ist ein Thema, wenn seit seinem letzten Check (letzter_check im
# State, auch ein ergebnisloser zaehlt) mindestens so viele Tage vergangen
# sind wie seine cadence_days; weekday_only ueberspringt zusaetzlich Sa/So.
# Fehlt die State-Datei eines neu eingetragenen Themas, wird sie leer angelegt
# und das Thema ist sofort faellig.
#
#   dashboard-update.sh              alle faelligen Themen pruefen und ggf. aktualisieren
#   dashboard-update.sh -n           Probelauf: nur zeigen, was faellig waere
#   dashboard-update.sh -b <id> -F   genau dieses Thema erzwingen (Erstbefuellung/Test)
set -uo pipefail

export PATH="/home/gra/.local/bin:$PATH"

CLAUDE_BIN=/home/gra/.local/bin/claude
DASH=/home/gra/claude/dashboard
STATE=/home/gra/claude/state/dashboard
APPLY=/home/gra/claude/bin/dashboard_apply.py
BUILD=/home/gra/claude/bin/dashboard_build.py
CONF=/home/gra/claude/state/ftp.conf
LOGDIR=/home/gra/claude/logs
LOG="$LOGDIR/dashboard-update.log"
mkdir -p "$LOGDIR" "$STATE"

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) $*" >>"$LOG"; }

TROCKEN=0
NUR_BLOCK=""
ERZWINGEN=0
while getopts ":nb:F" opt; do
    case "$opt" in
        n) TROCKEN=1 ;;
        b) NUR_BLOCK=$OPTARG ;;
        F) ERZWINGEN=1 ;;
        *) echo "unbekannte Option" >&2; exit 1 ;;
    esac
done

# Nie zwei Laeufe parallel
exec 9>"$LOGDIR/dashboard-update.lock"
if ! flock -n 9; then
    log "Lauf uebersprungen, anderer Lauf haelt den Lock"
    exit 0
fi

# -------------------------------------------------- Faellige Themen ermitteln --
# Reine Lese-/Vergleichslogik, kostet nichts. Eine Zeile pro Thema, Felder:
# id  titel  hinweis  zusatzhinweis  format  titel_dynamisch  cadence_days  faellig(0/1/wochenende)  stand  letzter_check  kernaussagen(||)  text
#
# Trennzeichen ist ASCII 0x1F (Unit Separator), NICHT Tab: Tab ist fuer bash ein
# Whitespace-Trennzeichen, deshalb wuerde `IFS=$'\t' read` zwei aufeinander-
# folgende Tabs zu einem zusammenfassen und bei einem leeren Feld (z.B. Thema
# ohne zusatzhinweis) alle Folgespalten verschieben. 0x1F ist kein Whitespace,
# damit bleiben leere Felder erhalten.
BLOCKS_TSV=$(python3 - "$NUR_BLOCK" "$ERZWINGEN" << 'PY'
import datetime, json, sys

force_id, erzwingen = sys.argv[1], sys.argv[2] == "1"
heute = datetime.date.today()

blocks = json.load(open("/home/gra/claude/dashboard/blocks.json", encoding="utf-8"))
dashboards = json.load(open("/home/gra/claude/dashboard/dashboards.json", encoding="utf-8"))

# Nur Themen pruefen, die mindestens ein Dashboard auch anzeigt.
genutzt = {bid for d in dashboards for bid in d["blocks"]}

_schlagzeilen = None
def schlagzeilen():
    """Aktuelle Top-Meldungen der Tagesschau mit Zeitstempel, als ein Absatz.

    Anlass 25.10.2026: Die Nachrichtenkachel brachte als 'Meldung des Tages'
    den DAX-Schluss vom 16.10. und die Septemberinflation (viermal in einer
    Woche), weil die Websuche nach 'Oktober 2026' den ganzen Monat liefert.
    WebFetch darf tagesschau.de nicht abrufen, ein direkter Abruf hier schon.
    Die Startseite ist redaktionell gewichtet, die News-Liste ergaenzt
    Inland/Ausland/Wirtschaft/Wissen der letzten 24 Stunden. Faellt der Abruf
    aus, laeuft das Thema ohne Vorlage wie frueher.
    """
    global _schlagzeilen
    if _schlagzeilen is not None:
        return _schlagzeilen
    import urllib.request
    def laden(url):
        req = urllib.request.Request(url, headers={"User-Agent": "news-dashboard"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r).get("news", [])
    # Die allgemeine News-Liste ist fast nur Regionales; die Ressort-Listen
    # sind ergiebiger. Je Ressort hoechstens 6, damit keins die Vorlage flutet.
    basis = "https://www.tagesschau.de/api2u/"
    try:
        quellen = [(laden(basis + "homepage/"), 12)]
        for ressort in ("inland", "ausland", "wirtschaft", "wissen"):
            quellen.append((laden(f"{basis}news/?ressort={ressort}"), 6))
    except Exception as e:
        print(f"Schlagzeilen nicht abrufbar: {e}", file=sys.stderr)
        _schlagzeilen = ""
        return _schlagzeilen
    # Die API liefert Ortszeit mit Offset ("2026-10-25T14:57:00.000+01:00"),
    # der Server laeuft ebenfalls auf deutscher Zeit - Textvergleich reicht.
    # 36 Stunden, damit der 6:10-Lauf auch den Vortag ganz sieht.
    grenze = (datetime.datetime.now() - datetime.timedelta(hours=36)).isoformat()
    gesehen, zeilen = set(), []
    for quelle, je_quelle in quellen:
        genommen = 0
        for n in quelle:
            if genommen >= je_quelle:
                break
            titel, datum = n.get("title"), n.get("date", "")
            if not titel or titel in gesehen or datum[:19] < grenze[:19]:
                continue
            # Sport und Wetter haben bei der Tagesschau kein Ressort.
            if not n.get("ressort"):
                continue
            genommen += 1
            gesehen.add(titel)
            teile = [f"[{datum[:16].replace('T', ' ')}, {n['ressort']}]",
                     f"{n.get('topline') + ': ' if n.get('topline') else ''}{titel}"]
            if n.get("firstSentence"):
                teile.append("— " + " ".join(n["firstSentence"].split())[:180])
            zeilen.append(" ".join(teile))
    _schlagzeilen = (
        "AKTUELLE SCHLAGZEILEN der Tagesschau (Startseite zuerst, mit Zeitstempel, "
        "abgerufen " + datetime.datetime.now().strftime("%d.%m.%Y %H:%M") + "): "
        + " || ".join(zeilen) if zeilen else "")
    return _schlagzeilen

for b in blocks:
    bid = b["id"]
    if bid not in genutzt:
        continue

    pfad = f"/home/gra/claude/state/dashboard/{bid}.json"
    try:
        state = json.load(open(pfad, encoding="utf-8"))
    except FileNotFoundError:
        # Neu eingetragenes Thema: leeren State anlegen, sofort faellig.
        state = {"stand_datum": "1970-01-01", "letzter_check": "1970-01-01",
                 "quintessenz_text": "", "kernaussagen": []}
        with open(pfad, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
            f.write("\n")

    # Massstab ist der letzte Check, nicht die letzte inhaltliche Aenderung -
    # sonst liefe ein Thema mit lange unveraendertem Inhalt taeglich statt im
    # vorgesehenen Takt.
    check = datetime.date.fromisoformat(state.get("letzter_check", state["stand_datum"]))
    # faellig ist "1", "0" oder "wochenende" - letzteres nur, damit das Log den
    # Grund nennt: ein werktags-taegliches Thema, das samstags als "nicht
    # faellig (Takt 1d)" dasteht, sieht sonst wie ein Fehler aus.
    faellig = "1" if (heute - check).days >= b["cadence_days"] else "0"
    if b.get("weekday_only") and heute.isoweekday() >= 6:
        faellig = "wochenende"
    # Ein noch nie befuelltes Thema (leere Kachel auf der Seite) ist immer
    # faellig, auch am Wochenende - sonst friert ein erster Lauf, der
    # 'unveraendert' meldet, die leere Kachel fuer cadence_days Tage ein.
    if not state.get("quintessenz_text"):
        faellig = "1"
    if erzwingen and bid == force_id:
        faellig = "1"

    def clean(s):
        # Zeilenumbrueche wuerden die Zeilenstruktur sprengen, 0x1F das Feldraster.
        return str(s).replace("\x1f", " ").replace("\t", " ").replace("\n", " ").replace("\r", " ")

    # Quellen, die frueheren Laeufen tatsaechlich etwas geliefert haben
    # (QUELLEN-Zeile des Skills, gepflegt von dashboard_apply.py) - haengen am
    # Hinweis statt an einem eigenen Feld, damit das 0x1F-Raster bleibt.
    hinweis = b["recherche_hinweis"]
    if state.get("bewaehrte_quellen"):
        hinweis += " Bewährte Quellen aus früheren Läufen: " + ", ".join(state["bewaehrte_quellen"]) + "."
    # Tagesthemen bekommen die aktuellen Schlagzeilen mit Zeitstempel vorab
    # (siehe schlagzeilen()) - nur fuer faellige Themen, der Abruf kostet Zeit.
    if b.get("schlagzeilen") == "tagesschau" and faellig == "1":
        hinweis += " " + schlagzeilen()

    kern = "||".join(state.get("kernaussagen", []))
    zeile = [
        bid, clean(b["titel"]), clean(hinweis),
        clean(b.get("zusatzabsatz_hinweis", "")),
        b.get("format", "text"),
        "1" if b.get("titel_dynamisch") else "0",
        clean(b.get("effort", "")),
        str(b["cadence_days"]), faellig,
        state["stand_datum"], check.isoformat(), clean(kern), clean(state.get("quintessenz_text", "")),
    ]
    print("\x1f".join(zeile))
PY
)

if [ -z "$BLOCKS_TSV" ]; then
    log "FEHLER: blocks.json/dashboards.json/State nicht lesbar oder kein Thema in Benutzung"
    exit 1
fi

LAEUFE=0

while IFS=$'\x1f' read -r ID TITEL HINWEIS ZUSATZ FORMAT TITELDYN EFFORT CADENCE FAELLIG STAND CHECK KERN TEXT; do
    [ -z "$ID" ] && continue

    if [ -n "$NUR_BLOCK" ] && [ "$ID" != "$NUR_BLOCK" ]; then
        continue
    fi

    if [ "$FAELLIG" = "wochenende" ]; then
        log "Thema $ID: nicht faellig, nur werktags (Stand $STAND, letzter Check $CHECK)"
        continue
    fi
    if [ "$FAELLIG" != "1" ]; then
        log "Thema $ID: nicht faellig (Stand $STAND, letzter Check $CHECK, Takt ${CADENCE}d)"
        continue
    fi

    if [ "$TROCKEN" = 1 ]; then
        log "[Probelauf] Thema $ID waere faellig (Stand $STAND, letzter Check $CHECK, Takt ${CADENCE}d)"
        continue
    fi

    LAEUFE=$((LAEUFE + 1))
    log "Thema $ID faellig, starte Claude-Lauf"

    [ -z "$KERN" ] && KERN="(noch keine)"
    [ -z "$TEXT" ] && TEXT="(noch keiner - erster Lauf fuer dieses Thema)"

    # Stillstands-Wachhund: steht ein Thema schon lange (mehr als 3 Takte,
    # mindestens 10 Tage) auf demselben Inhalt, bekommt der Lauf den Auftrag
    # zur gruendlichen Pruefung. Anlass 25.10.2026: Tourismus-Bayern hatte die
    # seit 09.10. veroeffentlichten Augustzahlen dreimal uebersehen, die
    # KI-Kachel Gemini 4 Argon - jeweils nach nur zwei Suchen 'unveraendert'.
    STILLSTAND_ZEILE=""
    if [ "$STAND" != "1970-01-01" ]; then
        ALTER=$(( ( $(date +%s) - $(date -d "$STAND" +%s) ) / 86400 ))
        SCHWELLE=$(( CADENCE * 3 > 10 ? CADENCE * 3 : 10 ))
        if [ "$ALTER" -ge "$SCHWELLE" ]; then
            log "Thema $ID: Stand $ALTER Tage alt (Schwelle $SCHWELLE) - gruendliche Pruefung"
            STILLSTAND_ZEILE="
Stillstand: Der bisherige Stand ist $ALTER Tage alt, obwohl das Thema alle $CADENCE Tage geprüft wird. Gründlich prüfen — siehe Abschnitt 'Langer Stillstand' im Skill."
        fi
    fi

    ZUSATZ_ZEILE=""
    if [ -n "$ZUSATZ" ]; then
        ZUSATZ_ZEILE="
Zusatzabsatz-Hinweis (zweiter, separat recherchierter Absatz zusätzlich zum Hauptabsatz): $ZUSATZ"
    fi

    FORMAT_ZEILE=""
    if [ "$FORMAT" = "liste" ]; then
        FORMAT_ZEILE="
Ausgabeformat: LISTE (nicht Fließtext) — QUINTESSENZ enthält je Meldung genau eine Zeile, eingeleitet mit '* ' für eine ernste und '~ ' für eine leichte/boulevardeske Meldung. Siehe Abschnitt 'Listenformat' im Skill."
    fi

    TITEL_ZEILE=""
    if [ "$TITELDYN" = "1" ]; then
        TITEL_ZEILE="
Kacheltitel: DYNAMISCH — diese Kachel heißt auf der Seite nach ihrem heutigen Inhalt. Gib deshalb zusätzlich eine TITEL-Zeile aus (direkt nach STAND). Siehe Abschnitt 'Dynamischer Kacheltitel' im Skill."
    fi

    # Kurz formatierte Bloecke (Liste/Kurztext) brauchen kein tiefes Reasoning;
    # blocks.json kann das per "effort" senken (gemessen 30.08.2026: nachrichten/
    # topstory/hackernews verbrauchten 50-60% ihres Outputs als unsichtbares
    # Denken fuer simple Formate - siehe project_dashboard.md).
    EFFORT_ARGS=()
    if [ -n "$EFFORT" ]; then
        EFFORT_ARGS=(--effort "$EFFORT")
    fi

    # stdin auf /dev/null: claude -p liest gepipetes stdin als Eingabe mit
    # und wuerde sonst die restlichen Themen-Zeilen der Schleife verschlucken
    # (nur das erste faellige Thema liefe, der Rest fiele stumm aus).
    # --output-format json statt text: liefert total_cost_usd + usage mit, damit
    # sich der Fensterverbrauch je Thema nachvollziehen laesst (siehe Kosten-Log
    # unten), ohne dafuer extra in den Session-Transkripten wuehlen zu muessen.
    LAUF_JSON=$(timeout 900 "$CLAUDE_BIN" -p --model sonnet \
        --allowedTools "Skill,WebSearch,WebFetch" \
        --max-turns 15 \
        --output-format json \
        "${EFFORT_ARGS[@]}" \
        "Führe den Skill 'dashboard-update' aus (Skill-Tool, skill: dashboard-update) für Block '$ID'.

Thema: $TITEL
Rechercheanweisung: $HINWEIS
$ZUSATZ_ZEILE$FORMAT_ZEILE$TITEL_ZEILE$STILLSTAND_ZEILE

Bisheriger Stand (zuletzt geändert am $STAND):
Kernaussagen bisher: $KERN
Bisheriger Text: $TEXT

Gib die Antwort EXAKT im vorgegebenen Format des Skills aus, sonst nichts - keine Einleitung, keine Erklärung davor oder danach." \
        </dev/null 2>>"$LOG")
    rc=$?

    MODELL_AUSGABE=""
    if [ "$rc" -eq 0 ]; then
        MODELL_AUSGABE=$(printf '%s' "$LAUF_JSON" | jq -er '.result' 2>>"$LOG")
    fi

    if [ "$rc" -ne 0 ] || [ -z "${MODELL_AUSGABE//[[:space:]]/}" ]; then
        log "Thema $ID: Claude-Lauf fehlgeschlagen (exit $rc, keine verwertbare Ausgabe) - uebersprungen"
        continue
    fi

    # Kosten-Log: ein Zeile je Lauf, damit sich der Effekt von "effort" (und
    # generell der Fensterverbrauch je Thema) ueber mehrere Tage nachvollziehen
    # laesst, ohne Session-Transkripte durchsuchen zu muessen.
    printf '%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\x1f%s\n' \
        "$(ts)" "$ID" "${EFFORT:-standard}" \
        "$(printf '%s' "$LAUF_JSON" | jq -r '.total_cost_usd // "?"')" \
        "$(printf '%s' "$LAUF_JSON" | jq -r '.usage.output_tokens // "?"')" \
        "$(printf '%s' "$LAUF_JSON" | jq -r '.usage.output_tokens_details.thinking_tokens // "?"')" \
        "$(printf '%s' "$LAUF_JSON" | jq -r '.usage.cache_creation_input_tokens // "?"')" \
        "$(printf '%s' "$LAUF_JSON" | jq -r '.num_turns // "?"')" \
        >> "$LOGDIR/dashboard-kosten.log"

    ANWENDUNG=$(printf '%s\n' "$MODELL_AUSGABE" | python3 "$APPLY" "$ID" "$FORMAT" "$TITELDYN" 2>>"$LOG")
    arc=$?
    if [ "$arc" -ne 0 ]; then
        log "Thema $ID: dashboard_apply.py fehlgeschlagen (exit $arc) - uebersprungen, nichts geschrieben"
        continue
    fi

    log "Thema $ID: $ANWENDUNG"
done <<< "$BLOCKS_TSV"

if [ "$TROCKEN" = 1 ]; then
    log "Probelauf beendet"
    exit 0
fi

# --------------------------------------------------------- Bauen + Upload --
# Immer bauen, auch wenn kein Thema lief: so wirken auch Aenderungen an
# Vorlage, blocks.json oder dashboards.json, und verlorene Dateien heilen von
# selbst. Gebaut wird nur geschrieben, was sich inhaltlich unterscheidet -
# der Generator meldet genau diese Seiten zurueck, je eine Zeile mit
# 0x1F-getrennten Feldern (siehe Upload-Schleife weiter unten).
GEBAUT=$(python3 "$BUILD" 2>>"$LOG")
brc=$?
if [ "$brc" -ne 0 ]; then
    log "FEHLER: dashboard_build.py fehlgeschlagen (exit $brc) - kein Upload"
    exit 1
fi

if [ -z "$GEBAUT" ]; then
    log "$LAEUFE Lauf/Laeufe, keine Seite veraendert, kein Upload"
    exit 0
fi

# shellcheck disable=SC1090
. "$CONF" || { log "FEHLER: $CONF fehlt"; exit 1; }
BASIS="ftp://$FTP_USER:$FTP_PASS@$FTP_HOST:$FTP_PORT/$FTP_ROOT"

FEHLER=0
# Der Generator meldet je geaenderter Seite drei 0x1F-getrennte Felder:
# lokales Verzeichnis, Zielverzeichnis auf dem Server, Adresse zum Nachpruefen.
# Dashboards und einzelne Themenseiten laufen dadurch durch dieselbe Schleife,
# hier muss nichts mehr nachgeschlagen werden. 0x1F statt Tab, weil bash Tab
# als Whitespace behandelt (siehe die Themenschleife weiter oben).
while IFS=$'\x1f' read -r LOKAL REMOTE URL; do
    [ -z "$LOKAL" ] && continue

    log "-- Upload $REMOTE/ --"

    # Statische Icon-Assets huckepack mitschicken, solange die Seite ohnehin
    # hochgeladen wird - kein eigener Aenderungs-Check noetig, die Dateien
    # sind klein und das haelt ein fehlendes Icon nie dauerhaft kaputt.
    # Themenseiten haben keine, dann laeuft die Schleife leer durch.
    for ASSET in "$LOKAL"/*.png; do
        [ -e "$ASSET" ] || continue
        curl -s --ftp-create-dirs -T "$ASSET" "$BASIS/$REMOTE/$(basename "$ASSET")" \
            || log "FEHLER: Upload $REMOTE/$(basename "$ASSET") fehlgeschlagen"
    done

    if ! curl -s --ftp-create-dirs -T "$LOKAL/index.html" "$BASIS/$REMOTE/index.html"; then
        log "FEHLER: Upload $REMOTE/index.html fehlgeschlagen"
        # Lokale gebaute Datei verwerfen: sie ist schon auf dem neuen Stand,
        # sonst gaelte die Seite beim naechsten Lauf als unveraendert und der
        # verpasste Upload wuerde nie nachgeholt. Ohne Datei baut und meldet
        # dashboard_build.py sie beim naechsten Lauf von selbst wieder.
        rm -f "$LOKAL/index.html"
        FEHLER=1
        continue
    fi

    CODE=$(curl -s -o /dev/null -w '%{http_code}' "$URL")
    if [ "$CODE" = "200" ]; then
        log "hochgeladen und geprueft: $URL (200)"
    else
        log "FEHLER: $URL liefert HTTP $CODE"
        rm -f "$LOKAL/index.html"
        FEHLER=1
    fi
done <<< "$GEBAUT"

[ "$FEHLER" = 0 ] || exit 1
log "fertig"
exit 0
