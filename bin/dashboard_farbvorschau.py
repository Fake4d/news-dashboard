#!/usr/bin/env python3
"""Baut eine Vergleichsseite fuer Farbvorschlaege (siehe dashboard_farben.py).

  dashboard_farbvorschau.py <ziel.html> [Name=vorschlag.json ...]

Ohne weitere Angaben wird nur der Live-Stand gezeigt. Jeder weitere Parameter
haengt eine Spalte an. Oben stehen die Kachelpaare, die sich HEUTE am
aehnlichsten sehen - daran misst sich ein Vorschlag; darunter die vollste
Seite einmal komplett, hell und dunkel.

Firefox am Server sieht /tmp nicht (Snap): Zieldatei ins Home legen.
"""
import itertools
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import dashboard_farbe as farbe
import dashboard_build as build

CLAUDE = pathlib.Path("/home/gra/claude")
blocks = {b["id"]: b for b in json.loads((CLAUDE / "dashboard/blocks.json").read_text(encoding="utf-8"))}
dashboards = json.loads((CLAUDE / "dashboard/dashboards.json").read_text(encoding="utf-8"))

STIL = """<style>
body{margin:0;padding:24px;font:14px/1.5 system-ui,sans-serif;background:#f4f5f8;color:#1c2230}
h1{font-size:1.35rem;margin:0 0 .2rem} h2{font-size:1.02rem;margin:22px 0 8px}
p.lead{margin:0 0 10px;color:#5a6478;max-width:78ch}
table{border-collapse:collapse;width:100%} th{font-size:.72rem;text-transform:uppercase;
letter-spacing:.07em;color:#8a92a4;text-align:left;padding:0 8px 6px;font-weight:600}
td{padding:4px 8px;vertical-align:top;border-top:1px solid #e3e6ec}
.paar{display:flex;gap:4px}
.f{flex:1;display:flex;border:1px solid;border-radius:6px;overflow:hidden;min-width:0}
.bal{width:5px;flex:none}
.ft{display:flex;flex-direction:column;padding:4px 7px;min-width:0;line-height:1.25}
.ft span:first-child{font-size:.72rem;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.wert{font:600 .78rem ui-monospace,monospace;padding-top:3px}
.gut{color:#0f7a45} .schlecht{color:#b3261e} .mittel{color:#a8690b}
.kopf{font-size:.78rem;color:#5a6478;margin:0 0 4px} .kopf b{color:#1c2230}
</style>"""


def dunkel(bid, v):
    if v[bid].get("dunkel_stufe") is None:
        return build.farbe_dunkel(blocks[bid])
    _, _, ton = farbe.hex_zu_oklch(v[bid]["farbe"])
    return farbe.oklch_zu_hex(build.DUNKEL_STUFEN[v[bid]["dunkel_stufe"]], build.DUNKEL_BUNTHEIT, ton)


def abstand(a, b, v, modus):
    return (farbe.abstand(v[a]["farbe"], v[b]["farbe"]) if modus == "hell"
            else farbe.abstand(dunkel(a, v), dunkel(b, v)))


def feld(bid, v, modus):
    c = v[bid]["farbe"] if modus == "hell" else dunkel(bid, v)
    grund, txt = ("#ffffff", "#1c2230") if modus == "hell" else ("#12151d", "#e8ecf5")
    return (f'<div class="f" style="background:{grund};border-color:{c}66">'
            f'<div class="bal" style="background:{c}"></div><div class="ft">'
            f'<span style="color:{c};font-weight:700">{blocks[bid]["nav"]}</span>'
            f'<span style="color:{txt}99;font-size:.62rem">{c}</span></div></div>')


def klasse(d):
    return "schlecht" if d < build.ABSTAND_BLOCKIEREN else ("mittel" if d < build.ABSTAND_WARNEN else "gut")


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    ziel = pathlib.Path(sys.argv[1])
    varianten = [("Heute live", {i: {"farbe": b["farbe"], "dunkel_stufe": None}
                                 for i, b in blocks.items()})]
    for arg in sys.argv[2:]:
        name, _, datei = arg.partition("=")
        varianten.append((name, json.loads(pathlib.Path(datei).read_text(encoding="utf-8"))))

    paare = sorted({tuple(sorted((a, b))) for d in dashboards
                    for a, b in itertools.combinations(d["blocks"], 2)})
    ist = varianten[0][1]
    schlimm = sorted((abstand(a, b, ist, m), m, a, b) for m in ("dunkel", "hell") for a, b in paare)[:7]

    t = ['<!doctype html><meta charset="utf-8"><title>Dashboard-Farben</title>', STIL,
         "<h1>Dashboard-Farben: die kritischen Paare</h1>",
         '<p class="lead">Jede Zeile ist ein Kachelpaar, das auf derselben Seite steht und '
         'sich heute zu ähnlich sieht. Die Zahl ist der wahrnehmungsnahe Abstand (OKLab): unter '
         f'{build.ABSTAND_BLOCKIEREN} praktisch gleich, unter {build.ABSTAND_WARNEN} spürbar '
         'ähnlich, darüber sicher unterscheidbar.</p>']

    for name, v in varianten:
        w = [min(abstand(a, b, v, "hell"), abstand(a, b, v, "dunkel")) for a, b in paare]
        unter = sum(1 for x in w if x < build.ABSTAND_WARNEN)
        kl = "schlecht" if unter > 5 else ("mittel" if unter else "gut")
        t.append(f'<p class="kopf">{name}: schlechtestes Paar <b>{min(w):.3f}</b>, '
                 f'<span class="{kl}">{unter} von {len(w)} Paaren zu ähnlich</span></p>')

    t.append("<table><tr><th style='width:8%'>Modus</th>")
    t += [f"<th>{n}</th>" for n, _ in varianten]
    t.append("</tr>")
    for _, modus, a, b in schlimm:
        t.append(f"<tr><td style='font-size:.75rem;color:#5a6478;padding-top:12px'>{modus}</td>")
        for _, v in varianten:
            d = abstand(a, b, v, modus)
            t.append(f"<td><div class='paar'>{feld(a, v, modus)}{feld(b, v, modus)}</div>"
                     f"<div class='wert {klasse(d)}'>{d:.3f}</div></td>")
        t.append("</tr>")
    t.append("</table>")

    voll = max(dashboards, key=lambda d: len(d["blocks"]))
    for modus, titel in (("dunkel", "im Dunkelmodus"), ("hell", "im Hellmodus")):
        t.append(f"<h2>{voll['titel']}, alle {len(voll['blocks'])} Kacheln {titel}</h2>")
        for name, v in varianten:
            grund = "#0a0c11" if modus == "dunkel" else "#eef1f6"
            felder = "".join(feld(i, v, modus) for i in voll["blocks"])
            t.append(f'<p class="kopf" style="margin:10px 0 4px"><b>{name}</b></p>'
                     f'<div style="background:{grund};padding:7px;border-radius:8px;display:grid;'
                     f'grid-template-columns:repeat(4,1fr);gap:5px">{felder}</div>')

    ziel.write_text("".join(t), encoding="utf-8")
    print(f"geschrieben: {ziel}")


if __name__ == "__main__":
    main()
