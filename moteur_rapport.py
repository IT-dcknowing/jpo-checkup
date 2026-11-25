"""
Moteur de rapport — Journées Portes Ouvertes DC-KNOWING
Diagnostic financier & conformité (Côte d'Ivoire, SYSCOHADA révisé)

Entrée : JSON produit par l'IA (mapping, sans calcul) + données du formulaire.
Sortie : contexte calculé (scores, KPI, alertes, plan d'action) + scores et barèmes (le rendu Word est fait par word/generer_rapport.py).

Usage : importé par word/generer_rapport.py (la fonction main() historique produit
        l'ancien PDF WeasyPrint et n'est plus utilisée).
"""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

ND = "NON_DISPONIBLE"
TVA = 0.18            # taux normal TVA Côte d'Ivoire
IS_TAUX = 0.25        # IS taux normal
IMF_TAUX = 0.005      # IMF : 0,5 % du CA TTC
IMF_MIN, IMF_MAX = 3_000_000, 35_000_000

# ---------------------------------------------------------------- utilitaires

CONF_MIN = 0.60


def v(node):
    """Valeur d'un champ IA ({'valeur': x}) ou None si NON_DISPONIBLE ou confiance < 0,60."""
    if node is None:
        return None
    if isinstance(node, dict) and node.get("confiance", 1) < CONF_MIN:
        return None
    x = node.get("valeur", node) if isinstance(node, dict) else node
    return None if (x is None or x == ND or isinstance(x, str)) else float(x)


def avail(*xs):
    return all(x is not None for x in xs)


def fr_num(x, dec=0):
    if x is None:
        return "n.d."
    s = f"{x:,.{dec}f}".replace(",", " ").replace(".", ",")
    return s


def m(x, dec=1):
    """Montant en millions FCFA : 125,0 M"""
    return "n.d." if x is None else f"{fr_num(x / 1e6, dec)} M"


def pct(x, dec=1, sign=False):
    if x is None:
        return "n.d."
    s = fr_num(x, dec)
    return (("+" if x > 0 and sign else "") + s + " %")


def grade(score):
    return "A" if score >= 80 else "B" if score >= 60 else "C" if score >= 40 else "D"


# ---------------------------------------------------------------- calculs

def calc(d):
    cr, ac, pa = d["ia"]["compte_resultat"], d["ia"]["bilan"]["actif"], d["ia"]["bilan"]["passif"]
    tva_ok = not d.get("formulaire", {}).get("tva_exoneree", False)
    k = {}
    ca, ca1 = v(cr["chiffre_affaires_ht"]), v(cr.get("chiffre_affaires_n1"))
    ach_m, ach_p = v(cr["cout_achats_marchandises"]), v(cr.get("cout_achats_matieres"))
    achats = None if ach_m is None and ach_p is None else (ach_m or 0) + (ach_p or 0)
    perso, autres = v(cr["charges_personnel"]), v(cr["charges_exploitation_autres"])
    dot, chf, prf, imp = (v(cr[x]) for x in ["dotations_amortissements", "charges_financieres", "produits_financiers", "impot_benefices"])
    k.update(ca=ca, ca1=ca1, achats=achats, perso=perso, autres=autres, dot=dot, chf=chf, prf=prf, imp=imp)
    k["evol"] = (ca - ca1) / ca1 * 100 if avail(ca, ca1) and ca1 > 0 else None
    k["mb"] = ca - achats if avail(ca, achats) else None
    k["mb_pct"] = k["mb"] / ca * 100 if k["mb"] is not None and ca else None
    k["ebe"] = ca - achats - perso - autres if avail(ca, achats, perso, autres) else None
    k["ebe_pct"] = k["ebe"] / ca * 100 if k["ebe"] is not None and ca else None
    k["rai"] = k["ebe"] - dot - chf + prf if avail(k["ebe"], dot, chf, prf) else None   # résultat avant impôt
    k["rn"] = k["rai"] - imp if avail(k["rai"], imp) else None
    k["rn_pct"] = k["rn"] / ca * 100 if k["rn"] is not None and ca else None
    ta, tp = v(ac["tresorerie_actif"]), v(pa["tresorerie_passif"])
    cre, stk, fou = v(ac["creances_clients"]), v(ac["stocks"]), v(pa["dettes_fournisseurs"])
    k.update(ta=ta, tp=tp, cre=cre, stk=stk, fou=fou)
    k["tn"] = ta - tp if avail(ta, tp) else None
    coef = 1 + TVA if tva_ok else 1
    k["dc"] = cre / (ca * coef) * 365 if avail(cre, ca) and ca > 0 else None
    k["df"] = fou / (achats * coef) * 365 if avail(fou, achats) and achats > 0 else None
    k["bfr"] = cre + (stk or 0) - fou if avail(cre, fou) else None
    k["bfr_j"] = k["bfr"] / ca * 365 if k["bfr"] is not None and ca else None
    k["masse"] = perso / ca * 100 if avail(perso, ca) and ca > 0 else None
    df_, cp = v(pa["dettes_financieres"]), v(pa["capitaux_propres"])
    k.update(dfin=df_, cp=cp, imm=v(ac["immobilisations_nettes"]), acre=v(ac["autres_creances"]), dfs=v(pa["dettes_fiscales_sociales"]))
    k["endet"] = df_ / cp * 100 if avail(df_, cp) and cp > 0 else None
    # impôt minimum forfaitaire (régimes réels)
    k["imf"] = min(max(ca * (1 + TVA) * IMF_TAUX, IMF_MIN), IMF_MAX) if ca else None
    k["is_theorique"] = max(k["rai"], 0) * IS_TAUX if k["rai"] is not None else None
    return k


def score_performance(k):
    blocs, alertes = [], []

    def bloc(nom, pts, mx, ok=True, detail=""):
        blocs.append(dict(nom=nom, pts=pts, max=mx, dispo=ok, detail=detail))

    # 1. CA & évolution (20)
    if k["ca"] is None:
        bloc("Chiffre d'affaires & croissance", 0, 20, False, "CA manquant")
    else:
        e = k["evol"] if k["evol"] is not None else 0
        p = 20 if e >= 15 else 16 if e >= 10 else 12 if e >= 5 else 8 if e >= 0 else 4 if e >= -5 else 0
        if k["ca"] >= 100_000_000:
            p = min(p + 2, 20)
        bloc("Chiffre d'affaires & croissance", p, 20, True, pct(k["evol"], 1, True) + " vs N-1")
    # 2. Marge brute + EBE (20)
    if k["mb_pct"] is None and k["ebe_pct"] is None:
        bloc("Marge brute & EBE", 0, 20, False)
    else:
        mb = k["mb_pct"]; eb = k["ebe_pct"]
        pm = 0 if mb is None else 10 if mb >= 40 else 7 if mb >= 25 else 4 if mb >= 15 else 1
        pe = 0 if eb is None else 10 if eb >= 20 else 7 if eb >= 10 else 4 if eb >= 5 else 2 if eb >= 0 else 0
        bloc("Marge brute & EBE", pm + pe, 20, True, f"MB {pct(mb)} · EBE {pct(eb)}")
    # 3. Résultat net (15)
    if k["rn_pct"] is None:
        bloc("Résultat net", 0, 15, False)
    else:
        r = k["rn_pct"]
        bloc("Résultat net", 15 if r >= 10 else 11 if r >= 5 else 7 if r >= 2 else 3 if r >= 0 else 0, 15, True, pct(r) + " du CA")
    # 4. Trésorerie & délais (20)
    if k["tn"] is None and k["dc"] is None and k["df"] is None:
        bloc("Trésorerie & délais", 0, 20, False)
    else:
        pt = 0 if k["tn"] is None else (10 if k["tn"] > 0 else 2)
        pdl = 0
        if k["dc"] is not None:
            pdl += 5 if k["dc"] <= 30 else 3 if k["dc"] <= 60 else 1 if k["dc"] <= 90 else 0
        if k["df"] is not None:
            pdl += 5 if k["df"] >= 45 else 3 if k["df"] >= 30 else 1
        bloc("Trésorerie & délais", pt + pdl, 20, True, f"Tréso {m(k['tn'])} · clients {fr_num(k['dc'] or 0)} j")
    # 5. Masse salariale (10)
    if k["masse"] is None:
        bloc("Masse salariale / CA", 0, 10, False)
    else:
        r = k["masse"]
        bloc("Masse salariale / CA", 10 if r <= 20 else 7 if r <= 35 else 4 if r <= 50 else 1, 10, True, pct(r))
    # 6. Endettement (15)
    if k["cp"] is not None and k["cp"] <= 0:
        bloc("Endettement / capitaux propres", 0, 15, True, "capitaux propres négatifs")
        alertes.append("Capitaux propres négatifs : situation à régulariser en priorité.")
    elif k["endet"] is None:
        bloc("Endettement / capitaux propres", 0, 15, False)
    else:
        r = k["endet"]
        bloc("Endettement / capitaux propres", 15 if r <= 30 else 11 if r <= 50 else 6 if r <= 80 else 3 if r <= 100 else 0, 15, True, pct(r))

    score = min(sum(b["pts"] for b in blocs), 100)
    couverture = sum(b["max"] for b in blocs if b["dispo"])  # points évaluables
    if score < 40:
        alertes.append("Performance financière très faible : risque critique.")
    elif score < 60:
        alertes.append("Performance fragile : des actions correctives sont nécessaires.")
    return dict(score=score, grade=grade(score), blocs=blocs, couverture=couverture, alertes=alertes,
                indicatif=couverture < 60)


REGIMES = {"TEE": "Taxe d'État de l'entreprenant", "RME": "Régime des microentreprises",
           "REEL_SIMPLIFIE": "Réel simplifié", "REEL_NORMAL": "Réel normal"}
ORDRE = ["TEE", "RME", "REEL_SIMPLIFIE", "REEL_NORMAL"]


def regime_attendu(ca):
    # Seuils CA HT (PwC, Tax facts & figures Côte d'Ivoire 2026) — paramétrables
    if ca <= 50_000_000:
        return "TEE"
    if ca <= 200_000_000:
        return "RME"
    if ca <= 500_000_000:
        return "REEL_SIMPLIFIE"
    return "REEL_NORMAL"


def score_conformite(d, k):
    c = d["ia"]["conformite"]
    lignes, alertes = [], []
    S = lambda s: s if s in ("A_JOUR", "EN_RETARD", "ACTIVE", "NON_ACTIVE", "EN_COURS_ACTIVATION", "NON_APPLICABLE") else ND

    # 1. DGI (25)
    s = S(c["dgi"]["statut"])
    p = 25 if s == "A_JOUR" else 8 if s == "EN_RETARD" else 0
    pen = v(c["dgi"].get("penalites_connues"))
    if s == "EN_RETARD" and pen:
        p = max(p - 5, 0)
    lignes.append(dict(nom="Déclarations fiscales (DGI)", statut=s, pts=p, max=25,
                       commentaire={"A_JOUR": "Déclarations à jour.", "EN_RETARD": "Retard détecté : risque de pénalités et de redressement."}.get(s, "Statut non communiqué : à vérifier.")))
    if s != "A_JOUR":
        alertes.append(("bad" if s == "EN_RETARD" else "warn", "Déclarations DGI : " + ("retard détecté." if s == "EN_RETARD" else "statut inconnu.")))
    # 2. FNE (25)
    s = S(c["fne"]["statut"])
    p = {"ACTIVE": 25, "NON_APPLICABLE": 25, "EN_COURS_ACTIVATION": 12, "NON_ACTIVE": 3}.get(s, 0)
    lignes.append(dict(nom="Facture normalisée électronique (FNE)", statut=s, pts=p, max=25,
                       commentaire={"ACTIVE": "FNE active.", "NON_APPLICABLE": "Secteur exempté de FNE.", "EN_COURS_ACTIVATION": "Activation en cours : à finaliser.",
                                    "NON_ACTIVE": "Non activée alors qu'elle est obligatoire depuis 2025."}.get(s, "Statut non communiqué.")))
    if s == "NON_ACTIVE":
        alertes.append(("bad", "FNE non activée : obligation en vigueur depuis 2025 (arrêté n°0337 du 9 mai 2025)."))
    # 3. Régime fiscal (25)
    actuel = c["regime_fiscal"]["regime_actuel"]
    ca = k["ca"]
    if actuel not in ORDRE or ca is None:
        p, att, com = 0, None, "Régime non renseigné."
        alertes.append(("warn", "Régime fiscal non renseigné."))
    else:
        att = regime_attendu(ca)
        if actuel == att or (att == "RME" and ca >= 100_000_000 and actuel == "REEL_SIMPLIFIE"):
            p = 25
            com = "Cohérent avec le CA" + (" (option réel simplifié ouverte dès 100 M)." if actuel != att else ".")
        elif ORDRE.index(actuel) > ORDRE.index(att):
            p, com = 18, f"Régime plus lourd que nécessaire ({REGIMES[att]} possible)."
            alertes.append(("warn", com))
        else:
            p, com = 5, f"Incohérent : {REGIMES[att]} attendu pour ce CA."
            alertes.append(("bad", "Régime fiscal incohérent avec le chiffre d'affaires."))
    lignes.append(dict(nom="Régime fiscal", statut=REGIMES.get(actuel, ND), pts=p, max=25, commentaire=com))
    # 4. CNPS + ITS / CMU / DISA (25)
    s = S(c["cnps"]["statut"])
    pc = 15 if s == "A_JOUR" else 5 if s == "EN_RETARD" else 0
    soc = c["obligations_sociales"]
    vals = {x: S(soc.get(x, ND)) for x in ("its", "cmu", "disa")}
    connus = [x for x in vals.values() if x != ND]
    ps = round(sum(1 for x in connus if x == "A_JOUR") / len(connus) * 10) if connus else 0
    retard = [x.upper() for x, st in vals.items() if st == "EN_RETARD"]
    com = ("CNPS à jour" if s == "A_JOUR" else "CNPS en retard" if s == "EN_RETARD" else "CNPS non communiqué") + \
          (" · " + ", ".join(retard) + " en retard." if retard else " · ITS, CMU, DISA à jour.")
    lignes.append(dict(nom="CNPS & obligations sociales", statut="EN_RETARD" if (retard or s == "EN_RETARD") else s, pts=pc + ps, max=25, commentaire=com,
                       detail=vals))
    if s == "EN_RETARD":
        alertes.append(("bad", "Cotisations CNPS en retard : majorations de retard qui courent chaque jour."))
    for r in retard:
        alertes.append(("warn", f"{r} en retard : à régulariser."))
    score = min(sum(l["pts"] for l in lignes), 100)
    return dict(score=score, grade=grade(score), lignes=lignes, alertes=alertes, risque_redressement=score < 50, regime_attendu=att)


# ---------------------------------------------------------------- KPI, plan d'action, orientation

def kpis(k):
    def st(cond_ok, cond_warn):
        return "ok" if cond_ok else "warn" if cond_warn else "bad"
    out = [
        dict(lib="Chiffre d'affaires HT", val=m(k["ca"]), sous=pct(k["evol"], 1, True) + " vs N-1",
             st=st((k["evol"] or 0) >= 5, (k["evol"] or 0) >= 0)),
        dict(lib="Marge brute", val=pct(k["mb_pct"]), sous=m(k["mb"]) + " · repère ≥ 40 %", st=st((k["mb_pct"] or 0) >= 40, (k["mb_pct"] or 0) >= 25)),
        dict(lib="Excédent brut d'exploitation", val=pct(k["ebe_pct"]), sous=m(k["ebe"]) + " · repère ≥ 10 %", st=st((k["ebe_pct"] or 0) >= 10, (k["ebe_pct"] or 0) >= 5)),
        dict(lib="Résultat net", val=m(k["rn"], 2), sous=pct(k["rn_pct"]) + " du CA · repère ≥ 5 %", st=st((k["rn_pct"] or 0) >= 5, (k["rn_pct"] or 0) >= 0)),
        dict(lib="Trésorerie nette", val=m(k["tn"]), sous="banque & caisse − découverts", st=st((k["tn"] or 0) > 0, False)),
        dict(lib="Délai de paiement clients", val=f"{fr_num(k['dc'])} j" if k["dc"] is not None else "n.d.", sous="repère ≤ 60 j", st=st((k["dc"] or 999) <= 60, (k["dc"] or 999) <= 90)),
        dict(lib="Délai de paiement fournisseurs", val=f"{fr_num(k['df'])} j" if k["df"] is not None else "n.d.", sous="repère ≥ 45 j", st=st((k["df"] or 0) >= 45, (k["df"] or 0) >= 30)),
        dict(lib="Endettement / fonds propres", val=pct(k["endet"], 0), sous="repère ≤ 50 %", st=st((k["endet"] or 999) <= 50, (k["endet"] or 999) <= 80)),
    ]
    return out


def plan_action(k, conf, d):
    acts = []
    c = d["ia"]["conformite"]
    ca_ttc = k["ca"] * (1 + TVA) if k["ca"] else None
    if c["fne"]["statut"] not in ("ACTIVE", "NON_APPLICABLE"):
        acts.append(dict(prio=0, action="Activer la facture normalisée électronique (FNE)",
                         detail="Inscription DGI, puis facturation via l'API ou la plateforme web FNE.",
                         impact="Mise en conformité obligatoire", delai="Sous 30 jours", module="Flow Sell · Flow Compta"))
    soc = c["obligations_sociales"]
    retard = [x.upper() for x in ("its", "cmu", "disa") if soc.get(x) == "EN_RETARD"]
    if c["cnps"]["statut"] == "EN_RETARD" or retard:
        acts.append(dict(prio=1, action="Régulariser " + ", ".join((["CNPS"] if c["cnps"]["statut"] == "EN_RETARD" else []) + retard),
                         detail="Payer les arriérés et automatiser les cotisations sur la paie.",
                         impact="Stoppe les majorations de retard", delai="Sous 30 jours", module="Flow RH"))
    if k["tn"] is not None and k["tn"] < 0:
        acts.append(dict(prio=1, action="Mettre en place un plan de trésorerie à 13 semaines",
                         detail="Suivi hebdomadaire des encaissements et décaissements.",
                         impact=f"Sortir du découvert ({m(k['tn'])})", delai="Sous 30 jours", module="Flow Compta"))
    if k["dc"] is not None and k["dc"] > 60:
        gain = (k["dc"] - 60) / 365 * ca_ttc
        acts.append(dict(prio=1, action=f"Ramener le délai clients de {fr_num(k['dc'])} j à 60 j",
                         detail="Relance dès J+1 d'échéance, acompte à la commande.",
                         impact=f"+{m(gain)} de trésorerie libérée", delai="3 à 6 mois", module="Flow Sell"))
    if k["df"] is not None and k["df"] < 45 and k["achats"]:
        gain = (45 - k["df"]) / 365 * k["achats"] * (1 + TVA)
        acts.append(dict(prio=2, action=f"Négocier 45 j de paiement fournisseurs (contre {fr_num(k['df'])} j)",
                         detail="Regrouper les achats et négocier les échéances contre les volumes.",
                         impact=f"+{m(gain)} de trésorerie", delai="3 à 6 mois", module="Flow Compta"))
    if k["mb_pct"] is not None and k["mb_pct"] < 40:
        gain = 0.02 * k["ca"]
        acts.append(dict(prio=2, action="Gagner 2 points de marge brute",
                         detail="Revoir prix et remises des références qui font l'essentiel du CA.",
                         impact=f"+{m(gain)} d'EBE par an", delai="6 à 12 mois", module="Flow Sell"))
    if k["endet"] is not None and k["endet"] > 50:
        acts.append(dict(prio=3, action="Stabiliser l'endettement",
                         detail="Pas de nouvel emprunt non productif ; mettre le résultat en réserve.",
                         impact=f"Ratio {pct(k['endet'], 0)} → ≤ 50 %", delai="12 mois", module="Flow Compta"))
    acts.sort(key=lambda a: a["prio"])
    return acts[:6]


def orientation(k, plan):
    faits = {x["action"].split(" ")[0] + x["action"].split(" ")[1] for x in plan}
    has = lambda mot: any(mot in x["action"] for x in plan)
    a1 = []
    a1.append("FNE active et opérationnelle" if has("FNE") else "Conformité fiscale maintenue")
    if has("Régulariser"):
        a1.append("Cotisations sociales à jour")
    a1.append("Plan de trésorerie hebdomadaire" if has("trésorerie à 13") else "Tableau de bord mensuel des 8 indicateurs")
    if len(a1) < 3:
        a1.append("Tableau de bord mensuel des 8 indicateurs")
    a2 = ["Délai clients ≤ 60 j" if has("délai clients") else "Délai clients maintenu ≤ 60 j",
          "Délai fournisseurs ≥ 45 j" if has("fournisseurs") else "Conditions fournisseurs préservées",
          "Stock ajusté aux ventes réelles"]
    a3 = ["+2 points de marge brute" if has("marge brute") else "Marge brute préservée",
          "EBE ≥ 10 % du CA" if (k["ebe_pct"] or 0) < 10 else "EBE maintenu au-dessus de 10 %",
          "Endettement ramené sous 50 %" if has("endettement") else "Croissance financée sans nouveau découvert"]
    axes = [dict(periode="0 – 3 mois", titre="Sécuriser", points=a1[:3]),
            dict(periode="3 – 6 mois", titre="Libérer du cash", points=a2),
            dict(periode="6 – 12 mois", titre="Rentabiliser la croissance", points=a3)]
    cibles = [
        ("Marge brute", pct(k["mb_pct"]), pct((k["mb_pct"] or 0) + 2) if (k["mb_pct"] or 0) < 40 else pct(k["mb_pct"])),
        ("EBE / CA", pct(k["ebe_pct"]), "≥ 10,0 %"),
        ("Délai clients", f"{fr_num(k['dc'])} j" if k["dc"] is not None else "n.d.", "≤ 60 j"),
        ("Trésorerie nette", m(k["tn"]), "> 0"),
    ]
    return axes, cibles


def infos(k, d):
    out = []
    if k["imf"] and k["is_theorique"] is not None and k["imf"] > k["is_theorique"]:
        seuil = k["imf"] / IS_TAUX
        out.append(f"Votre impôt sur les bénéfices ({m(k['imp'])}) correspond à l'impôt minimum forfaitaire : 0,5 % du CA TTC avec un plancher de 3 M FCFA. "
                   f"L'IS à 25 % ne dépasserait ce plancher qu'au-delà d'environ {m(seuil, 0)} de résultat fiscal (estimation).")
    out.append("TVA : taux normal de 18 %, taux réduit de 9 % sur certains produits. Les factures de vente doivent être émises via la FNE.")
    return out


CALENDRIER = [  # entreprises rattachées à un centre des impôts — à paramétrer selon le centre et le secteur
    ("15 janv. 2027", "Déclaration et paiement TVA et ITS du mois de décembre"),
    ("15 févr. 2027", "Régularisation annuelle de l'ITS"),
    ("15 avr. 2027", "1er acompte d'impôt sur les bénéfices (puis 15 juin, 15 sept.)"),
    ("30 mai 2027", "Déclaration annuelle de résultat (30 juin si audit obligatoire)"),
]


# ---------------------------------------------------------------- graphiques SVG

INK, GOLD, GOLD_L, GOLD_D, LINE, TRACK, OK, WARN, BAD, SLATE = "#000000", "#FFD700", "#FEF9E7", "#785C03", "#E0E0E0", "#EDEDED", "#15803D", "#92400E", "#DC2626", "#525252"


def svg_ring(score, color, size=128, stroke=14, label=""):
    r = (size - stroke) / 2
    c = 2 * math.pi * r
    off = c * (1 - score / 100)
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}">'
            f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{TRACK if color == INK else GOLD_L}" stroke-width="{stroke}"/>'
            f'<circle cx="{size/2}" cy="{size/2}" r="{r}" fill="none" stroke="{color}" stroke-width="{stroke}" stroke-linecap="round" '
            f'stroke-dasharray="{c:.1f}" stroke-dashoffset="{off:.1f}" transform="rotate(-90 {size/2} {size/2})"/>'
            f'<text x="{size/2}" y="{size/2 + 4}" text-anchor="middle" font-family="JetBrains Mono" font-weight="700" font-size="{size*0.27:.0f}" fill="{INK}">{score}</text>'
            f'<text x="{size/2}" y="{size/2 + size*0.2:.0f}" text-anchor="middle" font-family="Plus Jakarta Sans" font-weight="600" font-size="{size*0.085:.0f}" fill="{SLATE}">/ 100</text>'
            f'</svg>')


def svg_vide(w, h, txt="Données insuffisantes pour ce graphique"):
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}"><rect x="0" y="0" width="{w}" height="{h}" rx="8" fill="{GOLD_L}"/>'
            f'<text x="{w/2}" y="{h/2 + 3}" text-anchor="middle" font-family="Plus Jakarta Sans" font-weight="600" font-size="9" fill="{GOLD_D}">{txt}</text></svg>')


def svg_waterfall(k, w=500, h=168):
    if not avail(k["ca"], k["achats"], k["perso"], k["autres"], k["dot"], k["chf"], k["prf"], k["imp"]):
        return svg_vide(w, 90)
    steps = [("CA HT", k["ca"], "total"), ("Achats", -k["achats"], "neg"), ("Personnel", -k["perso"], "neg"),
             ("Autres charges", -k["autres"], "neg"), ("EBE", k["ebe"], "sub"), ("Amortiss.", -k["dot"], "neg"),
             ("Financier", -(k["chf"] - k["prf"]), "neg"), ("Impôt", -k["imp"], "neg"), ("Résultat net", k["rn"], "total")]
    top, base_h, left = 18, h - 50, 8
    mx = k["ca"]
    y = lambda val: top + base_h * (1 - val / mx)
    n = len(steps); slot = (w - left * 2) / n; bw = slot * 0.62
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">',
           f'<line x1="{left}" y1="{y(0):.1f}" x2="{w-left}" y2="{y(0):.1f}" stroke="{LINE}" stroke-width="1"/>']
    run = 0
    for i, (lab, val, kind) in enumerate(steps):
        x = left + i * slot + (slot - bw) / 2
        if kind in ("total", "sub"):
            a, b = 0, val; run = val
            fill = INK if kind == "total" else GOLD
        else:
            a, b = run + val, run; run = run + val
            fill = "#D4D4D4"
        y1, y2 = y(max(a, b)), y(min(a, b))
        out.append(f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bw:.1f}" height="{max(y2 - y1, 1.5):.1f}" rx="3" fill="{fill}"/>')
        if i < n - 1:
            out.append(f'<line x1="{x + bw:.1f}" y1="{y(run):.1f}" x2="{x + slot:.1f}" y2="{y(run):.1f}" stroke="#A3A3A3" stroke-width="0.8" stroke-dasharray="2 2"/>')
        txt = fr_num(abs(val) / 1e6, 1)
        out.append(f'<text x="{x + bw/2:.1f}" y="{y1 - 4:.1f}" text-anchor="middle" font-family="JetBrains Mono" font-weight="700" font-size="9" fill="{INK}">{"−" if kind == "neg" else ""}{txt}</text>')
        out.append(f'<text x="{x + bw/2:.1f}" y="{h - 22}" text-anchor="middle" font-family="Plus Jakarta Sans" font-weight="600" font-size="8" fill="{SLATE}">{lab}</text>')
    out.append(f'<text x="{w - left}" y="{h - 6}" text-anchor="end" font-family="Plus Jakarta Sans" font-size="7.5" fill="{SLATE}">en millions FCFA</text>')
    out.append("</svg>")
    return "".join(out)


def svg_blocs(blocs, w=250, row=26):
    h = row * len(blocs) + 4
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    for i, b in enumerate(blocs):
        y0 = i * row
        frac = b["pts"] / b["max"]
        col = OK if frac >= 0.75 else GOLD if frac >= 0.5 else BAD
        out.append(f'<text x="0" y="{y0 + 9}" font-family="Plus Jakarta Sans" font-weight="600" font-size="8.2" fill="{INK}">{b["nom"]}</text>')
        out.append(f'<text x="{w}" y="{y0 + 9}" text-anchor="end" font-family="JetBrains Mono" font-weight="700" font-size="8.2" fill="{INK}">{b["pts"]}/{b["max"]}</text>')
        out.append(f'<rect x="0" y="{y0 + 13}" width="{w}" height="7" rx="3.5" fill="{TRACK}"/>')
        out.append(f'<rect x="0" y="{y0 + 13}" width="{max(w * frac, 3):.1f}" height="7" rx="3.5" fill="{col}"/>')
    out.append("</svg>")
    return "".join(out)


def svg_delais(k, w=250, h=96):
    mx = 120
    X = lambda j: 70 + (w - 80) * min(j, mx) / mx
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    rows = [("Clients", k["dc"], 60, "≤ 60 j", True), ("Fournisseurs", k["df"], 45, "≥ 45 j", False)]
    if all(r[1] is None for r in rows):
        return svg_vide(w, 60, "Délais non calculables")
    for i, (lab, val, rep, rep_lab, lower_better) in enumerate(rows):
        if val is None:
            out.append(f'<text x="0" y="{8 + i * 38 + 12}" font-family="Plus Jakarta Sans" font-weight="700" font-size="8.2" fill="{SLATE}">{lab} : non disponible</text>')
            continue
        y0 = 8 + i * 38
        good = (val <= rep) if lower_better else (val >= rep)
        col = OK if good else BAD
        out.append(f'<text x="0" y="{y0 + 9}" font-family="Plus Jakarta Sans" font-weight="700" font-size="8.2" fill="{INK}">{lab}</text>')
        out.append(f'<text x="0" y="{y0 + 19}" font-family="JetBrains Mono" font-weight="700" font-size="9" fill="{col}">{fr_num(val)} j</text>')
        out.append(f'<rect x="70" y="{y0 + 4}" width="{w - 80}" height="11" rx="5.5" fill="{TRACK}"/>')
        out.append(f'<rect x="70" y="{y0 + 4}" width="{X(val) - 70:.1f}" height="11" rx="5.5" fill="{col}"/>')
        out.append(f'<line x1="{X(rep):.1f}" y1="{y0}" x2="{X(rep):.1f}" y2="{y0 + 19}" stroke="{INK}" stroke-width="1.6"/>')
        out.append(f'<text x="{X(rep):.1f}" y="{y0 + 29}" text-anchor="middle" font-family="Plus Jakarta Sans" font-size="7" fill="{SLATE}">repère {rep_lab}</text>')
    out.append("</svg>")
    return "".join(out)


def svg_bilan(k, w=250, h=128):
    actif = [("Immobilisations", k["imm"], "#525252"), ("Stocks", k["stk"], "#A3A3A3"), ("Créances clients", k["cre"], GOLD),
             ("Autres créances", k["acre"], "#D4D4D4"), ("Trésorerie", k["ta"], OK)]
    passif = [("Capitaux propres", k["cp"], INK), ("Dettes financières", k["dfin"], "#525252"), ("Fournisseurs", k["fou"], "#A3A3A3"),
              ("Fiscal & social", k["dfs"], "#D4D4D4"), ("Découverts", k["tp"], BAD)]
    tot = max(sum(x[1] or 0 for x in actif), sum(x[1] or 0 for x in passif))
    bw, top, ch = 34, 14, h - 18
    xa, xp = w / 2 - bw - 3, w / 2 + 3
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    for titre, items, x, side in (("ACTIF", actif, xa, -1), ("PASSIF", passif, xp, 1)):
        out.append(f'<text x="{x + bw/2}" y="9" text-anchor="middle" font-family="Plus Jakarta Sans" font-weight="800" font-size="7.5" letter-spacing="1" fill="{INK}">{titre}</text>')
        y = top
        for lab, val, fill in items:
            hh = ch * (val or 0) / tot
            out.append(f'<rect x="{x}" y="{y:.1f}" width="{bw}" height="{max(hh - 1, 0.5):.1f}" fill="{fill}"/>')
            if hh > 8:
                tx = x - 4 if side < 0 else x + bw + 4
                anc = "end" if side < 0 else "start"
                out.append(f'<text x="{tx}" y="{y + hh/2 + 2.5:.1f}" text-anchor="{anc}" font-family="Plus Jakarta Sans" font-size="6.8" fill="{INK}">{lab} <tspan font-family="JetBrains Mono" font-weight="700">{fr_num((val or 0)/1e6, 1)}</tspan></text>')
            y += hh
    out.append("</svg>")
    return "".join(out)


def svg_cent(k, w=700, h=62):
    if not avail(k["ca"], k["achats"], k["perso"], k["autres"], k["dot"], k["chf"], k["prf"], k["imp"], k["rn"]) or k["rn"] < 0:
        return svg_vide(w, 40, "Répartition indisponible (données manquantes ou résultat négatif)")
    ca = k["ca"]
    seg = [("Achats", k["achats"], "#525252", "#fff"), ("Personnel", k["perso"], "#A3A3A3", "#000"), ("Autres charges", k["autres"], "#D4D4D4", "#000"),
           ("Amortissements", k["dot"], "#E5E5E5", "#000"), ("Frais financiers", k["chf"] - k["prf"], "#C9C9C9", "#000"), ("Impôt", k["imp"], "#EFEFEF", "#000"),
           ("Bénéfice", max(k["rn"], 0), GOLD, "#000")]
    out = [f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}">']
    x = 0
    for lab, val, fill, txt in seg:
        part = val / ca * 100
        ww = w * part / 100
        out.append(f'<rect x="{x:.1f}" y="2" width="{max(ww - 1.2, 1.5):.1f}" height="26" fill="{fill}"/>')
        if ww > len(f"{lab} {fr_num(part, 1)}") * 5 + 12:
            out.append(f'<text x="{x + 6:.1f}" y="19" font-family="Plus Jakarta Sans" font-weight="700" font-size="8" fill="{txt}">{lab} <tspan font-family="JetBrains Mono">{fr_num(part, 1)}</tspan></text>')
        x += ww
    # légende des petits postes
    lx = 0
    for lab, val, fill, txt in seg:
        part = val / ca * 100
        if w * part / 100 > len(f"{lab} {fr_num(part, 1)}") * 5 + 12:
            continue
        out.append(f'<rect x="{lx}" y="42" width="9" height="9" rx="2" fill="{fill}" stroke="#A3A3A3" stroke-width="0.4"/>')
        t = f'{lab} {fr_num(part, 1)}'
        out.append(f'<text x="{lx + 13}" y="50" font-family="Plus Jakarta Sans" font-weight="600" font-size="8" fill="{INK}">{t}</text>')
        lx += 13 + len(t) * 4.6 + 14
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- rendu

def ratios(k):
    def st(x, ok, warn, lower=False):
        if x is None:
            return "nd"
        if lower:
            return "ok" if x <= ok else "warn" if x <= warn else "bad"
        return "ok" if x >= ok else "warn" if x >= warn else "bad"
    return [
        ("Croissance du CA", pct(k["evol"], 1, True), "≥ +5 %", st(k["evol"], 5, 0)),
        ("Marge brute / CA", pct(k["mb_pct"]), "≥ 40 %", st(k["mb_pct"], 40, 25)),
        ("EBE / CA", pct(k["ebe_pct"]), "≥ 10 %", st(k["ebe_pct"], 10, 5)),
        ("Résultat net / CA", pct(k["rn_pct"]), "≥ 5 %", st(k["rn_pct"], 5, 2)),
        ("Masse salariale / CA", pct(k["masse"]), "≤ 20 %", st(k["masse"], 20, 35, True)),
        ("Dettes financières / fonds propres", pct(k["endet"], 0), "≤ 50 %", st(k["endet"], 50, 80, True)),
        ("Trésorerie nette", m(k["tn"]), "> 0", "nd" if k["tn"] is None else "ok" if k["tn"] > 0 else "bad"),
    ]


def texte_delais(k):
    if k["dc"] is None or k["df"] is None:
        return "Les délais de paiement ne sont pas tous calculables avec les documents transmis."
    ecart = k["dc"] - k["df"]
    debut = (f"Vous encaissez <b>{fr_num(ecart)} jours</b> après avoir payé vos fournisseurs." if ecart > 0
             else f"Vos fournisseurs vous laissent <b>{fr_num(-ecart)} jours</b> de plus que vous n'en laissez à vos clients.")
    if k["bfr"] is not None:
        debut += f" Besoin en fonds de roulement : <b class=\"mono\">{m(k['bfr'])}</b>, soit <b>{fr_num(k['bfr_j'])} jours</b> de chiffre d'affaires à financer."
    return debut


def construire_contexte(d):
    k = calc(d)
    perf = score_performance(k)
    conf = score_conformite(d, k)
    plan = plan_action(k, conf, d)
    axes, cibles = orientation(k, plan)
    nb_fields, nb_ok = 0, 0
    for sect in ("compte_resultat",):
        for f in d["ia"][sect].values():
            nb_fields += 1; nb_ok += v(f) is not None
    for side in ("actif", "passif"):
        for f in d["ia"]["bilan"][side].values():
            nb_fields += 1; nb_ok += v(f) is not None
    couverture = round(nb_ok / nb_fields * 100)
    meta = d["ia"]["meta"]
    pts_forts, pts_faibles = [], []
    for b in perf["blocs"]:
        (pts_forts if b["pts"] / b["max"] >= 0.75 else pts_faibles if b["pts"] / b["max"] < 0.5 else []).append(b)
    verdict = {
        "A": "Entreprise en bonne santé : consolider et préparer la croissance.",
        "B": "Entreprise saine avec des points de vigilance à traiter.",
        "C": "Situation fragile : la rentabilité et la trésorerie demandent des actions rapides.",
        "D": "Situation critique : un plan de redressement s'impose sans attendre.",
    }[perf["grade"]]
    alertes = conf["alertes"] + ([("bad", f"Trésorerie nette négative ({m(k['tn'])}) : l'activité repose sur le découvert.")] if (k["tn"] or 0) < 0 else [])
    return dict(
        e=d["entreprise"], meta=meta, k=k, perf=perf, conf=conf, couverture=couverture,
        periode=f"{meta['period_start'][8:10]}/{meta['period_start'][5:7]}/{meta['period_start'][:4]} – {meta['period_end'][8:10]}/{meta['period_end'][5:7]}/{meta['period_end'][:4]}",
        date_rapport=date(2026, 12, 16).strftime("%d/%m/%Y"),
        verdict=verdict, kpis=kpis(k), plan=plan, axes=axes, cibles=cibles, infos=infos(k, d),
        calendrier=CALENDRIER, alertes=alertes[:4], ratios=ratios(k), texte_delais=texte_delais(k),
        bien=([dict(t=b["nom"], v=f'{b["pts"]}/{b["max"]}', s=b["detail"]) for b in perf["blocs"] if b["dispo"] and b["pts"] / b["max"] >= 0.6]
              + [dict(t=l["nom"], v="", s=l["commentaire"]) for l in conf["lignes"] if l["pts"] == l["max"]])[:5],
        traiter=([dict(n=n, t=t, v="", s="") for n, t in alertes]
                 + [dict(n="warn", t=b["nom"], v=f'{b["pts"]}/{b["max"]}', s=b["detail"]) for b in perf["blocs"] if b["dispo"] and b["pts"] / b["max"] < 0.5]
                 + [dict(n="warn", t=b["nom"], v="non évalué", s="donnée manquante") for b in perf["blocs"] if not b["dispo"]])[:6], interpretation=d.get("interpretation_ia", []),
        ring_perf=svg_ring(perf["score"], GOLD), ring_conf=svg_ring(conf["score"], INK),
        g_waterfall=svg_waterfall(k), g_cent=svg_cent(k), g_blocs=svg_blocs(perf["blocs"]), g_delais=svg_delais(k), g_bilan=svg_bilan(k),
        m=m, pct=pct, fr=fr_num, regimes=REGIMES,
    )


def main(src, dst):
    from jinja2 import Environment, FileSystemLoader
    from weasyprint import HTML
    base = Path(__file__).parent
    d = json.loads(Path(src).read_text(encoding="utf-8"))
    ctx = construire_contexte(d)
    env = Environment(loader=FileSystemLoader(base / "templates"), autoescape=False)
    html = env.get_template("rapport.html.j2").render(**ctx)
    (base / "out" / "rapport_exemple.html").write_text(html, encoding="utf-8")
    doc = HTML(string=html, base_url=str(base / "templates")).render()
    if len(doc.pages) != 3:  # mode compact : 5 actions, interprétation raccourcie
        ctx["plan"] = ctx["plan"][:5]
        ctx["interpretation"] = [p.split(". ")[0] + "." for p in ctx["interpretation"]]
        html = env.get_template("rapport.html.j2").render(**ctx)
        doc = HTML(string=html, base_url=str(base / "templates")).render()
    if len(doc.pages) != 3:
        raise RuntimeError(f"Le rapport fait {len(doc.pages)} pages au lieu de 3")
    doc.write_pdf(dst)
    resume = dict(performance=ctx["perf"]["score"], grade_perf=ctx["perf"]["grade"], conformite=ctx["conf"]["score"],
                  grade_conf=ctx["conf"]["grade"], couverture=ctx["couverture"], plan=[a["action"] for a in ctx["plan"]])
    print(json.dumps(resume, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
