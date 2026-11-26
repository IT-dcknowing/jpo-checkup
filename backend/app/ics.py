"""Invitation agenda (.ics). Abidjan est en UTC+0 sans changement d'heure : les heures sont écrites en UTC (suffixe Z)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta


def _esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lignes de 75 octets au plus (RFC 5545), coupées sur des caractères entiers."""
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode()) > (75 if not out else 74):
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n ".join(out)


def _utc(jour: str, hhmm: str) -> str:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(jour).replace(hour=int(h), minute=int(m), tzinfo=UTC).strftime("%Y%m%dT%H%M%SZ")


def build_ics(uid_base: str, journees: list[tuple[str, str, str, str]], lieu: str, entretien: tuple[str, str] | None = None,
              minutes: int = 45) -> bytes:
    """Un événement par jour de visite (jour, début, fin, description), plus l'entretien (jour, heure) s'il est réservé."""
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    events = [(f"{uid_base}-jpo-{jour}", "Journées Portes Ouvertes DC-KNOWING", _utc(jour, debut), _utc(jour, fin), description)
              for jour, debut, fin, description in journees]
    if entretien:
        jour, heure = entretien
        h, m = map(int, heure.split(":"))
        end = (datetime(2000, 1, 1, h, m) + timedelta(minutes=minutes)).strftime("%H:%M")
        events.append((f"{uid_base}-entretien", "Entretien individuel Check-up 360 (DC-KNOWING)", _utc(jour, heure), _utc(jour, end),
                       "Un expert DC-KNOWING commente vos chiffres avec vous. Arrivez à l'heure : l'accueil est compris dans le créneau."))
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//DC-KNOWING//JPO 2026//FR", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for uid, titre, start, end, desc in events:
        lines += ["BEGIN:VEVENT", f"UID:{uid}@dc-knowing.com", f"DTSTAMP:{stamp}", f"DTSTART:{start}", f"DTEND:{end}",
                  f"SUMMARY:{_esc(titre)}", f"LOCATION:{_esc(lieu)}", f"DESCRIPTION:{_esc(desc)}", "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(l) for l in lines) + "\r\n").encode("utf-8")
