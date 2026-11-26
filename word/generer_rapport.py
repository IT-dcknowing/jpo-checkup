#!/usr/bin/env python3
"""
generer_rapport.py — Diagnostic JPO DC-KNOWING : JSON d'entrée -> rapport.md -> rapport.docx (4 pages)

Usage :
    python3 generer_rapport.py entree.json sortie.docx [--pdf]

Étapes :
  1. calculs et scores (moteur_rapport.py, dossier parent : calc, score_performance, score_conformite)
  2. rédaction du Markdown en langage simple (ce fichier) -> sortie.md, à côté du .docx
  3. mise en page Word : node md_vers_word.js sortie.md sortie.docx
  4. contrôle : conversion PDF (LibreOffice) et vérification « exactement 4 pages ».
     Si le rapport dépasse, on régénère en mode compact (niveaux 1 puis 2).

Pour corriger le texte à la main (ou par une IA) : modifier sortie.md, puis
    node md_vers_word.js sortie.md sortie.docx
La mise en page reste identique. Syntaxe : GUIDE_MARKDOWN.txt.
"""
import json, subprocess, sys, shutil, tempfile
from pathlib import Path

ICI = Path(__file__).resolve().parent
sys.path.insert(0, str(ICI.parent))
import moteur_rapport as M  # noqa: E402

MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
CONTACT = "27 22 42 14 43 · 07 67 13 19 93 · portaildck.dc-knowing.com"


# ------------------------------------------------------------------ mots plutôt que chiffres

def millions(x):
    """125 000 000 -> '125 millions FCFA' ; 6 750 000 -> '7 millions FCFA' ; 450 000 -> '450 000 FCFA'."""
    if x is None:
        return "n.d."
    a = abs(x)
    if a >= 1e9:
        s = M.fr_num(a / 1e9, 1).replace(",0", "") + " milliard" + ("s" if a >= 2e9 else "")
    elif a >= 9.5e6:
        s = M.fr_num(round(a / 1e6), 0) + " millions"
    elif a >= 2e6:
        s = M.fr_num(round(a / 1e6), 0) + " millions"
    elif a >= 1e6:
        s = M.fr_num(a / 1e6, 1).replace(",0", "") + " million"
    else:
        s = M.fr_num(round(a, -3), 0)
    return ("moins " if x < 0 else "") + s + " FCFA"


def duree(j):
    if j is None:
        return "n.d."
    for lim, txt in [(10, "moins de dix jours"), (20, "deux à trois semaines"), (38, "un mois"), (52, "un mois et demi"),
                     (68, "deux mois"), (83, "deux mois et demi"), (98, "trois mois"), (130, "quatre mois")]:
        if j < lim:
            return txt
    return f"plus de {round(j / 30)} mois"


def part(r):
    """ratio 0..n -> expression française."""
    for lim, txt in [(0.15, "une petite part"), (0.28, "environ un quart"), (0.40, "environ un tiers"), (0.55, "environ la moitié"),
                     (0.70, "près des deux tiers"), (0.85, "plus des trois quarts"), (1.05, "l'équivalent")]:
        if r < lim:
            return txt
    return "davantage que"


def date_fr(iso):
    a, mo, j = iso[:10].split("-")
    return f"{int(j)} {MOIS[int(mo) - 1]} {a}"


def statut(x, ok, warn, inverse=False):
    if x is None:
        return "gris"
    if inverse:
        return "vert" if x <= ok else "orange" if x <= warn else "rouge"
    return "vert" if x >= ok else "orange" if x >= warn else "rouge"


MOT = {"vert": "Bon", "orange": "À surveiller", "rouge": "À corriger", "gris": "Non évalué"}


# ------------------------------------------------------------------ rédaction

def page1(e, k, perf, conf, nonconf, compact):
    phr_perf = {"A": "**Solide.** Votre entreprise gagne de l'argent et tient sa trésorerie.",
                "B": "**Saine.** Les bases sont bonnes ; quelques points sont à surveiller.",
                "C": "**Fragile.** " + ("Vos ventes progressent, mais l'entreprise" if (k["evol"] or 0) > 0 else "L'entreprise")
                     + " gagne peu" + (" et manque de trésorerie." if (k["tn"] or 0) < 0 else "."),
                "D": "**Critique.** L'entreprise perd de l'argent ou n'a plus de marge de sécurité."}[perf["grade"]]
    if perf.get("indicatif"):
        phr_perf = (f"**Note indicative.** Calculée sur {perf['couverture']} des 100 points : vos documents ne couvrent pas "
                    "toute votre activité. Les indicateurs gris de la page 2 n'ont pas pu être évalués.")
    n = len(nonconf)
    phr_conf = ("**Exemplaire.** Toutes vos obligations sont à jour." if n == 0 else
                f"**{'Correcte' if conf['score'] >= 60 else 'Insuffisante'}.** "
                + ("Vos déclarations sont à jour ; " if conf["lignes"][0]["statut"] == "A_JOUR" else "")
                + (f"{['', 'une obligation reste', 'deux obligations restent', 'trois obligations restent'][min(n, 3)] if n <= 3 else f'{n} obligations restent'} à régulariser."))

    # avis
    morceaux = []
    if (k["evol"] or 0) > 0:
        morceaux.append("Votre entreprise a une activité solide qui grandit")
    else:
        morceaux.append("Votre entreprise a une base de clients réelle")
    if k["rn_pct"] is not None and k["rn_pct"] < 5:
        morceaux.append("elle travaille beaucoup pour un bénéfice faible" if k["rn_pct"] >= 0 else "elle perd aujourd'hui de l'argent")
    if (k["tn"] or 0) < 0:
        morceaux.append("vit sur son découvert bancaire")
    avis = morceaux[0] + (", mais " + " et ".join(morceaux[1:]) if len(morceaux) > 1 else "") + "."
    if perf.get("indicatif"):
        avis += " Ce premier avis repose sur des documents partiels : avec votre balance générale, nous pourrons le compléter."
    avis += (" Les problèmes ont des causes claires et se corrigent : le plan d'action de la page 4 vous montre par où commencer."
             if perf["grade"] in "CD" or n else " Le plan d'action de la page 4 vous aide à consolider ces acquis.")

    retenir = []
    if perf.get("indicatif"):
        mesure = [f"chiffre d'affaires de **{millions(k['ca'])}**" if k["ca"] else None,
                  f"marge brute de **{M.fr_num(k['mb_pct'], 1)} %**" if k["mb_pct"] is not None else None,
                  f"masse salariale égale à **{M.fr_num(k['masse'], 1)} %** du chiffre d'affaires" if k["masse"] is not None else None]
        mesure = [x for x in mesure if x]
        if mesure:
            retenir.append("**Ce que vos documents permettent de mesurer :** " + ", ".join(mesure)
                           + ". Le reste (bénéfice, trésorerie, dettes) demande votre balance générale ou votre bilan.")
    if k["evol"] is not None:
        retenir.append(f"**Votre activité grandit.** Vous avez vendu davantage qu'en {int(k['_annee']) - 1} : vos clients vous font confiance, c'est votre principal atout."
                       if k["evol"] > 0 else "**Votre activité recule.** Vous avez vendu moins que l'année précédente : il faut comprendre quels clients ou produits ont baissé.")
    if k["rn_pct"] is not None:
        if k["rn_pct"] < 5:
            cause = ("le prix d'achat de vos marchandises" if (k["mb_pct"] or 100) < 40 else
                     "le poids des salaires" if (k["masse"] or 0) > 35 else "vos frais généraux")
            retenir.append(f"**Votre entreprise perd de l'argent.** Les dépenses dépassent les ventes. Le poste qui pèse le plus : {cause}." if k["rn_pct"] < 0 else
                           f"**Votre marge est trop faible.** Une fois les marchandises, les salaires et les frais payés, il reste très peu de bénéfice. La cause principale : {cause}.")
        else:
            retenir.append("**Votre entreprise est rentable.** Après toutes les dépenses, il vous reste un bénéfice confortable.")
    if k["dc"] is not None and k["df"] is not None and k["dc"] > k["df"] + 10:
        retenir.append("**Votre trésorerie est sous tension.** Vos clients vous paient bien plus tard que vous ne payez vos fournisseurs."
                       + (" C'est votre banque qui comble l'écart, et cela vous coûte des agios." if (k["tn"] or 0) < 0 else ""))
    elif k["tn"] is not None:
        retenir.append("**Votre trésorerie est positive.** Vous avez de quoi faire face à vos échéances." if k["tn"] > 0
                       else "**Votre trésorerie est négative.** Votre compte est durablement à découvert.")
    if nonconf:
        noms = " et ".join([", ".join(x["court"] for x in nonconf[:-1]), nonconf[-1]["court"]] if len(nonconf) > 1 else [nonconf[0]["court"]])
        nb = {1: "Une obligation est", 2: "Deux obligations sont", 3: "Trois obligations sont"}.get(len(nonconf), "Plusieurs obligations sont")
        retenir.append(f"**{nb} à régulariser vite :** {noms}.")
    else:
        retenir.append("**Vos obligations fiscales et sociales sont à jour.** Continuez ainsi : c'est une vraie protection en cas de contrôle.")

    out = [f"# Le diagnostic de votre entreprise", "",
           f"**{e['nom_affiche']}** · {e.get('effectif', '?')} salariés · Exercice {k['_annee']} · Rapport remis le {date_fr(e['jour_evenement'])}", "",
           "```scores",
           f"Santé financière | {perf['score']} | {perf['grade']} | {phr_perf}",
           f"Conformité | {conf['score']} | {conf['grade']} | {phr_conf}",
           "```", "",
           "> [!AVIS] Notre avis en une phrase", f"> {avis}", "",
           "## Ce qu'il faut retenir", ""] + [f"- {r}" for r in retenir] + [""]
    if compact < 2:
        out += ["## Comment lire ce rapport", "",
                "Chaque note va de **A** (très bien) à **D** (situation critique). Les couleurs suivent la même logique : **vert** pour ce qui va bien, "
                "**orange** pour ce qui est à surveiller, **rouge** pour ce qui doit être corrigé. La page 2 explique votre santé financière, "
                "la page 3 votre trésorerie et vos obligations, la page 4 ce que nous vous conseillons de faire.", ""]
    return out


def indicateur(st, titre, valeur, texte, conseil, compact):
    b = ["```indicateur", f"statut: {st}", f"titre: {titre}", f"valeur: {valeur}", f"mot: {MOT[st]}", f"texte: {texte}"]
    if conseil and compact < 2:
        b.append(f"conseil: {conseil}")
    return b + ["```", ""]


def page2(k, compact):
    out = ["===PAGE===", "", "# Votre santé financière, expliquée", "",
           "Voici votre entreprise vue sous quatre angles, et ce que cela veut dire pour vous.", ""]
    # Activité
    st = statut(k["evol"], 5, 0)
    if k["evol"] is None:
        out += indicateur("gris", "Activité", millions(k["ca"]) if k["ca"] else "Non évaluée",
                          "Le chiffre d'affaires de l'année précédente n'a pas été transmis : l'évolution ne peut pas être mesurée.", None, compact)
    else:
        out += indicateur(st, "Activité", f"{'+' if k['evol'] > 0 else ''}{M.fr_num(k['evol'], 0)} % de ventes",
                          f"Votre chiffre d'affaires atteint **{millions(k['ca'])}**, "
                          + (f"en hausse sur {int(k['_annee']) - 1} : vos clients vous restent fidèles." if k["evol"] > 0 else "en baisse par rapport à l'année précédente."),
                          "profitez de cette dynamique pour revoir vos prix plutôt que d'accorder davantage de remises." if k["evol"] > 0
                          else "identifiez les clients et produits qui ont reculé, et relancez-les en priorité.", compact)
    # Rentabilité
    r = k["rn_pct"]
    if r is None:
        out += indicateur("gris", "Rentabilité", "Non évaluée", "Le compte de résultat transmis est incomplet : le bénéfice ne peut pas être calculé.", None, compact)
    else:
        st = statut(r, 5, 2)
        val = (f"{M.fr_num(r, 0)} FCFA gagné{'s' if round(r) > 1 else ''} sur 100 FCFA vendus" if r >= 0.5
               else "Presque rien gagné" if r >= 0 else f"{M.fr_num(-r, 0)} FCFA perdus sur 100 FCFA vendus")
        if r < 0:
            txt = "Votre entreprise perd de l'argent : ses dépenses dépassent ce qu'elle encaisse. Il faut agir sur les charges et les prix sans attendre."
            cons = "repérez dès maintenant les trois dépenses qui ont le plus augmenté et renégociez-les."
        elif r < 5:
            txt = "C'est trop peu : viser au moins 5 FCFA est raisonnable."
            if k["achats"] and k["ca"]:
                txt += f" Les achats absorbent {part(k['achats'] / k['ca'])} de vos ventes et ne laissent presque rien pour couvrir le reste."
            cons = "gagner 2 points sur vos prix d'achat ou de vente " + ("suffirait à doubler votre bénéfice." if 0 < r <= 2 else "améliorerait nettement votre bénéfice.")
        else:
            txt, cons = "C'est un bon niveau : votre activité paie ses charges et dégage un vrai bénéfice.", "mettez une partie du bénéfice en réserve pour financer la croissance."
        out += indicateur(st, "Rentabilité", val, txt, cons, compact)
    # Trésorerie
    tn = k["tn"]
    if tn is None:
        out += indicateur("gris", "Trésorerie", "Non évaluée", "Les soldes bancaires n'ont pas été transmis.", None, compact)
    elif tn < 0:
        out += indicateur("rouge", "Trésorerie", "Négative",
                          "Votre compte en banque est à découvert de façon durable. "
                          + ("Ce n'est pas une perte : c'est de l'argent bloqué chez vos clients, qui tardent à payer (voir page 3)." if (k["dc"] or 0) > 60
                             else "Il faut en trouver la cause avant qu'elle ne pèse sur vos paiements."),
                          "suivez votre trésorerie chaque semaine et relancez vos clients dès le lendemain de l'échéance.", compact)
    else:
        out += indicateur("vert", "Trésorerie", "Positive", "Votre compte en banque est créditeur : vous pouvez faire face à vos échéances.",
                          "gardez une réserve équivalente à un mois de charges.", compact)
    # Endettement
    en = k["endet"]
    if k["cp"] is not None and k["cp"] <= 0:
        out += indicateur("rouge", "Endettement", "Fonds propres négatifs",
                          "Les pertes accumulées dépassent l'argent apporté par les associés : la loi impose de régulariser cette situation.",
                          "parlez-en à votre expert-comptable dès l'entretien.", compact)
    elif en is None:
        out += indicateur("gris", "Endettement", "Non évalué", "Les éléments du bilan transmis ne permettent pas de mesurer l'endettement.", None, compact)
    else:
        st = statut(en, 50, 80, inverse=True)
        val = "Faible" if en <= 30 else "Raisonnable" if en <= 80 else "Élevé"
        txt = f"Vos emprunts représentent {part(en / 100)} de vos fonds propres. " + (
            "C'est confortable." if en <= 50 else
            "C'est supportable, mais cela réduit votre marge de manœuvre" + (" si la trésorerie reste tendue." if (tn or 0) < 0 else ".") if en <= 80 else
            "C'est lourd : votre banque sera prudente pour un nouveau crédit.")
        out += indicateur(st, "Endettement", val, txt,
                          "évitez tout nouvel emprunt qui ne rapporte pas directement de l'activité." if en > 50 else "vous pouvez financer un investissement utile si besoin.", compact)
    # Répartition pour 100 FCFA
    ca = k["ca"]
    morceaux = [("Achats de marchandises", k["achats"], "#525252"), ("Salaires", k["perso"], "#A3A3A3"), ("Autres charges", k["autres"], "#D4D4D4"),
                ("Amortissements", k["dot"], "#E5E5E5"),
                ("Frais bancaires", (k["chf"] - k["prf"]) if M.avail(k["chf"], k["prf"]) else None, "#C9C9C9"), ("Impôt", k["imp"], "#EFEFEF")]
    if ca and all(x is not None for _, x, _ in morceaux) and (k["rn"] or 0) >= 0:
        out += ["```graphique repartition", "titre: Pour 100 FCFA vendus, où va votre argent ?"]
        out += [f"{n} | {round(x / ca * 100, 1)} | {c}" for n, x, c in morceaux if x > 0]
        out += [f"Bénéfice | {round(k['rn'] / ca * 100, 1)} | #FFD700", "```", ""]
        plus = max(morceaux[:3], key=lambda t: t[1])[0].lower()
        out += [("La barre jaune, presque invisible, est votre bénéfice. " if k["rn_pct"] < 3 else "La barre jaune est votre bénéfice. ")
                + f"Le vrai levier est la plus grande barre ({plus}) : chaque point gagné dessus devient du bénéfice.", ""]
        if compact < 1:
            out += ["**En clair :** votre entreprise ne manque pas de clients, elle manque de marge. C'est une bonne nouvelle : la marge se travaille "
                    "par la négociation et les prix, sans avoir à trouver de nouveaux marchés."
                    if (k["rn_pct"] or 0) < 5 and (k["evol"] or 0) > 0 else
                    "**En clair :** l'enjeu est de préserver cet équilibre en suivant ces quatre indicateurs chaque mois.", ""]
    return out


def page3(k, conf, nonconf, compact):
    out = ["===PAGE===", "", "# Votre trésorerie et vos obligations", ""]
    dc, df = k["dc"], k["df"]
    gain_cli = None
    if dc is not None and df is not None:
        tendu = dc > df + 10
        out += ["## Pourquoi votre trésorerie est " + ("tendue" if tendu else "équilibrée"), "",
                (f"L'argent rentre trop tard : vos clients vous paient en **{duree(dc)}**, vous payez vos fournisseurs en **{duree(df)}**. "
                 + ("Entre les deux, c'est votre découvert bancaire qui finance vos clients." if (k["tn"] or 0) < 0 else "Entre les deux, c'est votre propre argent qui finance vos clients.")
                 if tendu else f"Vos clients vous paient en **{duree(dc)}** et vous payez vos fournisseurs en **{duree(df)}** : l'équilibre est correct."), "",
                "```graphique comparaison", "titre: Délais de paiement, en jours", "unite: jours",
                f"Vos clients vous paient en | {round(dc)} | repère 60 j | {statut(dc, 60, 90, inverse=True)}",
                f"Vous payez vos fournisseurs en | {round(df)} | repère 45 j | {statut(df, 45, 30)}", "```", ""]
        if dc > 60 and k["ca"]:
            gain_cli = (dc - 60) / 365 * k["ca"] * (1 + M.TVA)
            out += ["> [!CONSEIL] Le bon réflexe", f"> Faire payer vos clients en deux mois libérerait environ **{millions(gain_cli)}**, sans vendre un produit de plus.", ""]
    else:
        out += ["## Votre trésorerie", "", "Les documents transmis ne permettent pas de calculer vos délais de paiement. Nous les mesurerons avec vous pendant l'entretien.", ""]

    c_lignes = conf["lignes"]
    lig = ["## Vos obligations fiscales et sociales", "", "| Obligation | Votre situation | Ce qu'il faut faire |", "|---|---|---|"]
    S = {"A_JOUR": ("vert", "À jour"), "EN_RETARD": ("rouge", "En retard"), "ACTIVE": ("vert", "Active"), "NON_APPLICABLE": ("vert", "Non concernée"),
         "EN_COURS_ACTIVATION": ("orange", "En cours"), "NON_ACTIVE": ("rouge", "Non activée"), M.ND: ("gris", "Non communiqué")}
    s = S.get(c_lignes[0]["statut"], S[M.ND])
    lig.append(f"| Déclarations fiscales (DGI) | {{{s[0]}}} {s[1]} | " + ("Rien à signaler. |" if s[0] == "vert" else "Déposer les déclarations manquantes au plus vite. |" if s[0] == "rouge" else "À vérifier ensemble. |"))
    s = S.get(c_lignes[1]["statut"], S[M.ND])
    lig.append("| Facture normalisée électronique (FNE) | " + f"{{{s[0]}}} {s[1]} | "
               + ("Rien à signaler. |" if s[0] == "vert" else "**Urgent :** obligatoire depuis 2025 (arrêté n°0337 du 9 mai 2025). |" if s[0] == "rouge" else "Finaliser l'activation. |"))
    reg = c_lignes[2]
    st_reg = "vert" if reg["pts"] == 25 else "orange" if reg["pts"] >= 18 else "rouge" if reg["pts"] else "gris"
    lig.append(f"| Régime fiscal | {{{st_reg}}} " + {"vert": "Cohérent", "orange": "À revoir", "rouge": "Incohérent", "gris": "Non communiqué"}[st_reg] + " | "
               + {"vert": "Rien à signaler. |", "orange": "Un régime plus simple est possible : à étudier. |",
                  "rouge": "Changer de régime pour éviter un redressement. |", "gris": "À vérifier ensemble. |"}[st_reg])
    soc = dict(CNPS=k["_conf"]["cnps"]["statut"], **{x.upper(): k["_conf"]["obligations_sociales"].get(x, M.ND) for x in ("its", "cmu", "disa")})
    ok = [n for n, v in soc.items() if v == "A_JOUR"]
    if ok:
        lig.append(f"| {', '.join(ok[:-1]) + ' et ' + ok[-1] if len(ok) > 1 else ok[0]} | {{vert}} À jour | Rien à signaler. |")
    for n, v in soc.items():
        if v == "EN_RETARD":
            lig.append(f"| {n} | {{{'rouge' if n == 'CNPS' else 'orange'}}} En retard | "
                       + ("Payer l'arriéré : des majorations courent chaque jour. |" if n == "CNPS" else "Payer l'arriéré, puis l'intégrer à la paie. |"))
        elif v not in ("A_JOUR",):
            lig.append(f"| {n} | {{gris}} Non communiqué | À vérifier ensemble. |")
    out += lig + [""]

    infos = []
    if k["imf"] and k["is_theorique"] is not None and k["imf"] > k["is_theorique"]:
        infos.append(f"> - **Impôt minimum :** vous payez le plancher de {millions(k['imf'])}. Améliorer votre marge ne vous coûtera donc pas d'impôt en plus au début.")
    infos.append("> - **Échéances :** TVA et ITS de décembre au 15 janvier 2027 ; régularisation de l'ITS au 15 février 2027.")
    if compact < 2 or len(infos) == 1:
        out += ["> [!INFO] Bon à savoir"] + infos + [""]
    return out, gain_cli


def actions(k, nonconf, gain_cli):
    acts = []
    if nonconf:
        noms = " et ".join(x["court_titre"] for x in nonconf) if len(nonconf) <= 2 else ", ".join(x["court_titre"] for x in nonconf[:-1]) + " et " + nonconf[-1]["court_titre"]
        comment = " ; ".join(x["faire"] for x in nonconf[:4])
        modules = list(dict.fromkeys(x["module"] for x in nonconf))
        acts.append(dict(priorite=1, titre=f"Vous mettre en règle : {noms}",
                         pourquoi=(("ce sont les deux seuls points" if len(nonconf) == 2 else "ce sont les seuls points") + " de non-conformité de votre dossier, et les plus risqués." if len(nonconf) > 1
                                   else "c'est le seul point de non-conformité de votre dossier, et il expose à des pénalités."),
                         comment=comment + ".", delai="sous 30 jours", outil=", ".join(modules)))
    if k["dc"] is not None and k["dc"] > 60:
        acts.append(dict(priorite=2, titre="Faire payer vos clients plus vite",
                         pourquoi="c'est la cause principale de votre découvert." if (k["tn"] or 0) < 0 else "votre argent reste trop longtemps chez vos clients.",
                         comment="relance dès le lendemain de l'échéance et acompte à la commande.",
                         gain=f"environ {millions(gain_cli)} de trésorerie" if gain_cli else None, delai="3 à 6 mois", outil="Flow Sell"))
    if k["tn"] is not None and k["tn"] < 0:
        acts.append(dict(priorite=2, titre="Suivre votre trésorerie chaque semaine", pourquoi="anticiper les tensions au lieu de les subir.",
                         comment="un tableau des entrées et sorties prévues sur 13 semaines.", delai="dès ce mois-ci", outil="Flow Compta"))
    if k["rn_pct"] is not None and k["rn_pct"] < 5 and k["ca"]:
        acts.append(dict(priorite=3, titre="Gagner 2 points de marge", pourquoi="votre bénéfice est trop faible pour financer votre croissance.",
                         comment="renégocier vos achats, revoir prix et remises.", gain=f"environ {millions(0.02 * k['ca'])} par an",
                         delai="6 à 12 mois", outil="Flow Sell"))
    if k["df"] is not None and k["df"] < 30 and k["achats"]:
        acts.append(dict(priorite=3, titre="Obtenir plus de délai de vos fournisseurs", pourquoi="vous payez plus vite que nécessaire.",
                         comment="regrouper vos achats et négocier les échéances contre les volumes.",
                         gain=f"environ {millions((45 - k['df']) / 365 * k['achats'] * (1 + M.TVA))} de trésorerie", delai="3 à 6 mois", outil="Flow Compta"))
    if k["endet"] is not None and k["endet"] > 80:
        acts.append(dict(priorite=3, titre="Alléger votre endettement", pourquoi="vos emprunts pèsent lourd par rapport à vos fonds propres.",
                         comment="aucun nouvel emprunt non productif ; mettre le résultat en réserve.", delai="12 mois", outil="Flow Compta"))
    if not acts:
        acts.append(dict(priorite=3, titre="Piloter avec un tableau de bord mensuel", pourquoi="garder la maîtrise de vos bons résultats.",
                         comment="suivre chaque mois vos ventes, votre marge, votre trésorerie et vos délais.", delai="dès ce mois-ci", outil="Flow Compta"))
    return acts[:4]  # règle : 4 actions maximum


def page4(k, acts, nonconf, compact):
    out = ["===PAGE===", "", "# Votre plan d'action", ""]
    for a in acts:
        out.append("```action")
        out += [f"{c}: {a[c]}" for c in ("priorite", "titre", "pourquoi", "comment", "gain", "delai", "outil") if a.get(c)]
        out += ["```", ""]
    c1 = [(" et ".join(x["court_titre"] for x in nonconf) + " en règle" if len(nonconf) <= 2 else "Obligations en règle") if nonconf else "Obligations maintenues à jour",
          "Trésorerie suivie chaque semaine" if (k["tn"] or 0) < 0 else "Réserve de trésorerie constituée", "Tableau de bord mensuel"]
    c2 = ["Clients payés en 2 mois" if (k["dc"] or 0) > 60 else "Délais clients mesurés" if k["dc"] is None else "Délais clients maintenus", "Fournisseurs renégociés", "Stocks ajustés aux ventes"]
    c3 = ["+2 points de marge" if (k["rn_pct"] or 0) < 5 else "Marge préservée", "5 FCFA gagnés sur 100" if (k["rn_pct"] or 0) < 5 else "Bénéfice mis en réserve",
          "Croissance sans découvert" if (k["tn"] or 0) < 0 else "Croissance autofinancée"]
    if compact < 2:
        out += ["## Votre feuille de route sur 12 mois", "", "| 0-3 mois : sécuriser | 3-6 mois : libérer du cash | 6-12 mois : rentabiliser |", "|---|---|---|"]
        out += [f"| {a} | {b} | {c} |" for a, b, c in zip(c1, c2, c3)] + [""]
    out += ["> [!AVIS] Prochaine étape : votre entretien individuel",
            "> Un expert DC-KNOWING chiffre ce plan avec vous et vous montre les outils Flow qui l'automatisent.", f"> **{CONTACT}**", "",
            "<!-- mention -->",
            "Diagnostic établi sur vos seuls documents, calculs selon le SYSCOHADA révisé. Ce n'est ni un audit, ni une certification des comptes, ni un avis juridique.", ""]
    return out


def non_conformites(conf_in):
    out = []
    if conf_in["dgi"]["statut"] == "EN_RETARD":
        out.append(dict(court="les déclarations fiscales en retard", court_titre="DGI", faire="dépôt des déclarations en retard", module="Flow Compta"))
    if conf_in["fne"]["statut"] in ("NON_ACTIVE", "EN_COURS_ACTIVATION"):
        out.append(dict(court="la facture normalisée électronique (FNE) n'est pas activée", court_titre="FNE",
                        faire="inscription à la FNE auprès de la DGI", module="Flow Sell"))
    if conf_in["cnps"]["statut"] == "EN_RETARD":
        out.append(dict(court="les cotisations CNPS sont en retard", court_titre="CNPS", faire="paiement de l'arriéré CNPS", module="Flow RH"))
    for x in ("its", "cmu", "disa"):
        if conf_in["obligations_sociales"].get(x) == "EN_RETARD":
            n = x.upper()
            out.append(dict(court=f"la cotisation {n} est en retard" if x == "cmu" else f"l'{n} est en retard" if x == "its" else "la DISA n'est pas déposée",
                            court_titre=n, faire=f"paiement de l'arriéré {n}, puis calcul automatique sur la paie" if x != "disa" else "dépôt de la DISA",
                            module="Flow RH"))
    return out


def rediger(d, compact=0):
    k = M.calc(d)
    k["_annee"] = d["ia"]["meta"]["period_end"][:4]
    k["_conf"] = d["ia"]["conformite"]
    perf, conf = M.score_performance(k), M.score_conformite(d, k)
    nc = non_conformites(d["ia"]["conformite"])
    e = d["entreprise"]
    meta = d["ia"]["meta"]
    p3, gain_cli = page3(k, conf, nc, compact)
    acts = actions(k, nc, gain_cli)
    front = ["---", f"entreprise: {e['nom_affiche']}", f"periode: Exercice {k['_annee']} (du {meta['period_start'][8:10]}/{meta['period_start'][5:7]}/{meta['period_start'][:4]} "
             f"au {meta['period_end'][8:10]}/{meta['period_end'][5:7]}/{meta['period_end'][:4]})",
             f"date: {e['jour_evenement'][8:10]}/{e['jour_evenement'][5:7]}/{e['jour_evenement'][:4]}", f"reference: {e['reference_job']}", "---", ""]
    md = front + page1(e, k, perf, conf, nc, compact) + page2(k, compact) + p3 + page4(k, acts, nc, compact)
    resume = dict(performance=perf["score"], note_perf=perf["grade"], conformite=conf["score"], note_conf=conf["grade"],
                  actions=[a["titre"] for a in acts], mode_compact=compact,
                  plan=[dict(priorite=f"P{a['priorite']}", action=a["titre"], delai=a["delai"], module=a["outil"]) for a in acts])
    return "\n".join(md), resume


# ------------------------------------------------------------------ rendu et contrôle

def pages_pdf(docx):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None, None
    tmp = Path(tempfile.mkdtemp())
    subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", str(tmp), str(docx)], capture_output=True, timeout=180)
    pdf = tmp / (Path(docx).stem + ".pdf")
    if not pdf.exists():
        return None, None
    info = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    n = int([l for l in info.splitlines() if l.startswith("Pages:")][0].split()[1])
    return n, pdf


def main():
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(1)
    src, dst = Path(sys.argv[1]), Path(sys.argv[2]).resolve()
    garder_pdf = "--pdf" in sys.argv
    d = json.loads(src.read_text(encoding="utf-8"))
    md_path = dst.with_suffix(".md")
    for compact in (0, 1, 2):
        md, resume = rediger(d, compact)
        md_path.write_text(md, encoding="utf-8")
        subprocess.run(["node", str(ICI / "md_vers_word.js"), str(md_path), str(dst)], check=True, cwd=str(ICI))
        subprocess.run(["zip", "-d", str(dst), "*/"], capture_output=True)
        n, pdf = pages_pdf(dst)
        resume["pages"] = n
        if n is None or n <= 4:
            break
    if n is not None and n > 4:
        print(f"ATTENTION : le rapport fait {n} pages même en mode compact — raccourcir {md_path.name} puis relancer md_vers_word.js", file=sys.stderr)
    if garder_pdf and pdf:
        shutil.copy(pdf, dst.with_suffix(".pdf"))
    print(json.dumps(resume, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
