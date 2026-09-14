#!/usr/bin/env bash
# Taeglich per Cron (nach dashboard-update.sh): prueft, ob seit der Umstellung
# auf "effort low" (nachrichten/topstory/hackernews, 13.09.2026) genug echte
# Cron-Tage in dashboard-kosten.log stehen, und schickt dann EINMAL eine Mail
# mit dem Kostenvergleich - Markerdatei verhindert eine zweite Mail.
# Grund: Claude laeuft nicht im Hintergrund weiter, "in ein paar Tagen
# beobachten" muss also ueber einen eigenen Cron-Job passieren, nicht ueber
# ein Versprechen im Chat.
set -uo pipefail

export PATH="/home/gra/.local/bin:$PATH"

LOGDIR=/home/gra/claude/logs
STATE=/home/gra/claude/state
LOG="$LOGDIR/dashboard-kosten-report.log"
MARKER="$STATE/dashboard-kosten-report-gesendet"
KOSTEN="$LOGDIR/dashboard-kosten.log"
STICHTAG="2026-09-14"   # erster Cron-Tag NACH der Umstellung (13.09. war schon der alte Code)
MINDESTTAGE=5

ts()  { date '+%Y-%m-%d %H:%M:%S'; }
log() { echo "$(ts) $*" >>"$LOG"; }

mkdir -p "$LOGDIR" "$STATE"

exec 9>"$LOGDIR/dashboard-kosten-report.lock"
if ! flock -n 9; then
    exit 0
fi

[ -f "$MARKER" ] && exit 0
[ -f "$KOSTEN" ] || { log "Kosten-Log fehlt noch, nichts zu tun"; exit 0; }

BERICHT=$(python3 - "$KOSTEN" "$STICHTAG" "$MINDESTTAGE" << 'PY'
import sys, datetime
from collections import defaultdict

pfad, stichtag, mindesttage = sys.argv[1], sys.argv[2], int(sys.argv[3])
stichtag = datetime.date.fromisoformat(stichtag)

tage = defaultdict(list)  # datum -> [(id, effort, cost)]
with open(pfad, encoding="utf-8") as f:
    for zeile in f:
        zeile = zeile.rstrip("\n")
        if not zeile:
            continue
        teile = zeile.split("\x1f")
        if len(teile) < 8:
            continue
        ts_str, bid, effort, cost = teile[0], teile[1], teile[2], teile[3]
        try:
            zeitpunkt = datetime.datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
            cost = float(cost)
        except ValueError:
            continue
        if zeitpunkt.date() < stichtag:
            continue
        if zeitpunkt.hour != 6:
            # Nur der echte 6:10-Cron-Lauf zaehlt, keine manuell erzwungenen
            # Testlaeufe (-F) zu anderen Tageszeiten.
            continue
        tage[zeitpunkt.date()].append((bid, effort, cost))

tage_sortiert = sorted(tage)
if len(tage_sortiert) < mindesttage:
    print(f"ZU_WENIG:{len(tage_sortiert)}")
    sys.exit(0)

zeilen = []
tagessummen = []
low_kosten = []
std_kosten = []
for d in tage_sortiert:
    eintraege = tage[d]
    summe = sum(c for _, _, c in eintraege)
    tagessummen.append(summe)
    zeilen.append(f"  {d.isoformat()}: {len(eintraege)} Themen, {summe:.2f} $")
    for bid, effort, cost in eintraege:
        (low_kosten if effort == "low" else std_kosten).append(cost)

avg_tag = sum(tagessummen) / len(tagessummen)
avg_low = sum(low_kosten) / len(low_kosten) if low_kosten else 0.0
avg_std = sum(std_kosten) / len(std_kosten) if std_kosten else 0.0

print("OK")
print(f"{len(tage_sortiert)} Cron-Tage seit der Umstellung ({stichtag.isoformat()}) ausgewertet.")
print(f"Durchschnittliche Tageskosten ueber alle faelligen Themen zusammen: {avg_tag:.2f} $")
print(f"Durchschnitt pro Lauf, gedrosselte Themen (nachrichten/topstory/hackernews, effort=low): {avg_low:.3f} $ (n={len(low_kosten)})")
print(f"Durchschnitt pro Lauf, restliche Themen (Standard-Effort): {avg_std:.3f} $ (n={len(std_kosten)})")
print("")
print("Tagesdetails:")
for z in zeilen:
    print(z)
PY
)
prc=$?

if [ "$prc" -ne 0 ] || [ -z "$BERICHT" ]; then
    log "FEHLER: Auswertung fehlgeschlagen (exit $prc)"
    exit 1
fi

if [[ "$BERICHT" == ZU_WENIG:* ]]; then
    N="${BERICHT#ZU_WENIG:}"
    log "noch nicht genug Cron-Tage seit $STICHTAG ($N von $MINDESTTAGE) - noch kein Bericht"
    exit 0
fi

log "genug Daten ($MINDESTTAGE Tage), schicke Bericht"

mailbox send >>"$LOG" 2>&1 <<EOF
From: Christians virtueller Server <claude@christian-grafe.de>
To: post@christian-grafe.de
Subject: Dashboard-Kosten: Bericht nach der effort-low-Umstellung

Du hattest am 13.09.2026 gefragt, welches Dashboard-Modul viel vom
5-Stunden-Fenster verbraucht, und danach zugestimmt, fuer nachrichten,
topstory und hackernews "--effort low" zu setzen. Hier der Kostenvergleich
der ersten $MINDESTTAGE echten Cron-Tage danach:

$BERICHT

"Gedrosselte Themen" sind die drei genannten (effort=low), "restliche
Themen" alle anderen faelligen Bloecke desselben Tages (Standard-Effort) -
so laesst sich sehen, ob die Drosselung den gewuenschten Abstand haelt.

Rohdaten (eine Zeile pro Thema und Lauf: Zeitstempel, Thema, effort,
Kosten in USD, Output-Tokens, Denk-Tokens, neue Cache-Tokens, Turns)
liegen unter ~/claude/logs/dashboard-kosten.log.

Christians virtueller Server 🤖
EOF
mrc=$?

if [ "$mrc" -eq 0 ]; then
    date '+%Y-%m-%d' > "$MARKER"
    log "Bericht verschickt, Markerdatei gesetzt"
else
    log "FEHLER: Mailversand fehlgeschlagen (exit $mrc) - Marker NICHT gesetzt, naechster Cron-Lauf versucht es erneut"
fi
