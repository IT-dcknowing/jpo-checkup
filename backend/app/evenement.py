"""Journées Portes Ouvertes des 16, 17 et 18 décembre 2026 : jours, créneaux d'entretien et textes des invitations.
Programme de référence : « Programme mis à jour » du 26/11/2026. Le programme affiché est dans app-visiteur.js (K.PROG)."""
from __future__ import annotations

JOURS = ["2026-12-16", "2026-12-17", "2026-12-18"]
NOMS = {"2026-12-16": "mercredi 16", "2026-12-17": "jeudi 17", "2026-12-18": "vendredi 18"}
HORAIRES = {"2026-12-16": ("08:30", "16:30"), "2026-12-17": ("10:30", "17:30"), "2026-12-18": ("08:30", "16:00")}

# Un entretien occupe 45 minutes (10 d'accueil, 30 avec l'expert, 5 de remise du rapport).
MINUTES_ENTRETIEN = 45
CRENEAUX = {
    "2026-12-16": ["09:00", "09:45", "10:30", "11:15", "12:00", "14:00", "14:45", "15:30"],
    "2026-12-17": ["14:15", "15:00", "15:45", "16:30"],
    "2026-12-18": ["14:00", "14:45"],  # rattrapage
}

# La Suite Flow n'est présentée que le vendredi.
DESCRIPTIONS = {
    "2026-12-16": "Ouverture des Journées Portes Ouvertes et entretiens individuels Check-up 360.",
    "2026-12-17": "Panels (fiscalité 2027, bénéficiaire effectif, intelligence artificielle) et entretiens individuels Check-up 360.",
    "2026-12-18": "Plénière de clôture et démonstration de La Suite Flow ; entretiens de rattrapage l'après-midi.",
}
ACCROCHES = {
    "2026-12-16": "Demain 08h30, les Journées Portes Ouvertes commencent.",
    "2026-12-17": "Demain 10h30, trois experts répondent à vos questions.",
    "2026-12-18": "Demain 08h30, on vous présente les solutions.",
}
PROGRAMMES = {
    "2026-12-16": ["08h30 · Accueil", "08h45 · Ouverture des Journées Portes Ouvertes",
                   "09h00 à 12h45 et 14h00 à 16h30 · Entretiens individuels, sur rendez-vous"],
    "2026-12-17": ["10h30 · Accueil", "11h00 · Panel 1 : Fiscalité 2027", "11h45 · Panel 2 : Bénéficiaire effectif",
                   "12h30 · Panel 3 : L'intelligence artificielle dans l'entreprise", "13h15 · Déjeuner et échanges",
                   "14h15 à 17h30 · Entretiens individuels, sur rendez-vous"],
    "2026-12-18": ["08h30 · Accueil", "09h00 · Plénière de clôture", "09h55 · Démonstration en direct de La Suite Flow",
                   "10h55 · L'offre des Journées Portes Ouvertes", "11h45 · Clôture et cocktail",
                   "14h00 à 16h00 · Entretiens de rattrapage"],
}


def heure_fr(hhmm: str) -> str:
    return hhmm.replace(":", "h")


def jour_long(jour: str) -> str:
    return f"{NOMS.get(jour, jour).capitalize()} décembre 2026"


def liste_fr(items: list[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} et {items[-1]}"


def jours_longs(jours: list[str]) -> str:
    """« Mercredi 16 et jeudi 17 décembre 2026 »."""
    texte = liste_fr([NOMS.get(j, j) for j in jours])
    return f"{texte[:1].upper()}{texte[1:]} décembre 2026"
