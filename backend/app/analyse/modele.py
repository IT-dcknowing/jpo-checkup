"""Contrats partagés du nouveau moteur de diagnostic (paquet backend/app/analyse/).

Principe : l'IA comprend (carte des données), le code calcule (DuckDB, 100 % des lignes), le code vérifie.
Chaque chiffre du rapport est un `Fait` rattaché à une `Requete` exécutée et stockée.
Aucune règle propre à un client, un produit, un vendeur ou un fichier : uniquement des règles génériques.

Flux du lot 1 :
    lecture.py      fichiers -> Tableau (en-tête, titre, lignes, n° de ligne Excel)
    profilage.py    Tableau / PDF / fiche -> ProfilFeuille (statistiques, échantillon réparti) + Dossier
    carte.py        profils (+ fiche) -> Carte (JSON validé ; IA, sinon carte déduite par règles)
    entrepot.py     Carte + tableaux -> DuckDB (tables brutes + vues normalisées) ; requêtes bridées et tracées
    controles.py    Carte + entrepôt -> ResultatControle (continuité des soldes, totaux, qté×PU, sources croisées)
    references.py   entrepôt -> Fait (socle fixe : période, CA, achats, charges, personnel, trésorerie, bilan…)
    etats_pdf.py    texte PDF d'états financiers -> lignes `etats_financiers` vérifiées dans le texte
    fournisseur.py  appels au modèle (Anthropic / OpenRouter / simulé), coût réel, plafond de budget
    evaluation.py   export JSON complet d'un dossier (carte, contrôles, requêtes, faits, coût, rapport)
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ====================================================================== lecture (utilisé par lecture.py)
@dataclass
class Tableau:
    """Un tableau lu dans une feuille : en-têtes normalisés + lignes de données (après la ligne d'en-tête)."""
    fichier: str
    feuille: str
    entetes: list[str]
    entetes_bruts: list[str]
    lignes: list[list]
    numeros: list[int]              # numéro de ligne Excel de chaque ligne de `lignes`
    titre: str = ""
    empreinte_fichier: str = ""
    lignes_total: list[int] = field(default_factory=list)
    lignes_sans_date: list[int] = field(default_factory=list)
    dates_heritees: list[int] = field(default_factory=list)


# ====================================================================== profilage
@dataclass
class ProfilColonne:
    index: int
    entete: str                     # en-tête brut (ou col_<n>)
    type_dominant: Literal["date", "nombre", "texte", "vide", "mixte"]
    part_vides: float               # 0..1
    distincts: int
    exemples: list[str]             # 5 valeurs distinctes au plus, tronquées à 40 caractères
    min: float | None = None        # nombres
    max: float | None = None
    somme: float | None = None
    part_entiers: float | None = None
    part_negatifs: float | None = None
    date_min: str | None = None     # ISO
    date_max: str | None = None
    longueur_moyenne: float | None = None  # textes


@dataclass
class ProfilFeuille:
    id: str                         # identifiant stable « t1 », « t2 »… (ordre de lecture)
    fichier: str
    feuille: str
    format: Literal["xlsx", "csv", "pdf", "texte"]
    empreinte_fichier: str
    nb_lignes: int                  # lignes de données (après l'en-tête)
    ligne_entete: int | None        # n° Excel de la ligne d'en-tête (None : pas d'en-tête trouvé)
    titre: str                      # texte au-dessus de l'en-tête
    colonnes: list[ProfilColonne]
    echantillon: list[dict]         # 30 lignes réparties : {"ligne": n° Excel, "valeurs": [str…]}
    lignes_suspectes_total: list[int] = field(default_factory=list)  # n° Excel contenant un mot de total
    texte_pages: list[str] | None = None   # PDF : texte par page (tronqué à 6 000 caractères par page)


@dataclass
class Dossier:
    """Ce que le moteur sait du dossier avant la carte."""
    profils: list[ProfilFeuille]
    fiche_entreprise: str           # texte de la fiche (.md/.txt/.docx), éventuellement vide
    formulaire: dict                # réponses du formulaire (régime, FNE, statuts, effectif, secteur, tva…)
    fichiers_ignores: list[dict] = field(default_factory=list)  # {"fichier", "raison"} (doublon d'empreinte, format…)


# ====================================================================== carte des données (sortie IA, validée)
Nature = Literal["ventes", "achats", "depenses", "banque", "caisse", "mobile_money", "paie", "stock", "emprunt",
                 "immobilisations", "creances", "balance", "grand_livre", "bilan", "compte_resultat", "autre"]
RoleColonne = Literal["date", "date_valeur", "echeance", "piece", "tiers", "libelle", "designation", "reference", "famille",
                      "categorie", "qte", "pu", "remise", "remise_taux", "montant_brut", "montant", "montant_ht",
                      "montant_ttc", "tva", "debit", "credit", "entree", "sortie", "solde", "frais", "mode_paiement",
                      "site", "canal", "vendeur", "compte", "intitule", "compte_tresorerie", "type_operation", "statut",
                      "matricule", "salarie", "brut", "net", "cnps_salarie", "cnps_employeur", "its",
                      "charges_patronales", "qte_comptee", "qte_theorique", "valeur_stock", "emplacement",
                      "capital_restant", "capital", "interets", "mensualite", "valeur_origine", "duree",
                      "reste_a_payer", "poste", "valeur_n", "valeur_n1", "periode", "ignorer"]
Poste = Literal["ca", "achats", "frais_generaux", "personnel", "tresorerie", "stocks", "dettes_financieres", "creances",
                "capitaux_propres", "immobilisations"]
CategorieLibelle = Literal["vente", "encaissement_client", "loyer", "salaire", "impot", "cotisation_sociale",
                           "prelevement_dirigeant", "depense_personnelle", "publicite", "frais_bancaires", "agios",
                           "emprunt_deblocage", "emprunt_echeance", "virement_interne", "fournisseur", "transport",
                           "douane", "caution", "autre"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ColonneCarte(_Strict):
    index: int = Field(ge=0)
    nom: str
    role: RoleColonne
    unite: Literal["FCFA", "quantite", "taux", "date", "texte", "autre"] = "autre"
    signe: Literal["positif_entree", "positif_sortie", "signe", "sans_objet"] = "sans_objet"
    ht_ttc: Literal["HT", "TTC", "inconnu", "sans_objet"] = "sans_objet"


class PeriodeCarte(_Strict):
    debut: str | None = None        # AAAA-MM-JJ
    fin: str | None = None


class PerimetreCarte(_Strict):
    site: str | None = None
    compte: str | None = None       # nom du compte de trésorerie (banque, caisse d'un site, wallet)
    canal: str | None = None


class LignesExclues(_Strict):
    totaux: list[int] = Field(default_factory=list)   # n° Excel
    titres: list[int] = Field(default_factory=list)
    autres: list[int] = Field(default_factory=list)


class TableauCarte(_Strict):
    id: str                          # = ProfilFeuille.id
    nature: Nature
    confiance: float = Field(ge=0, le=1)
    justification: str
    sans_entete: bool = False
    lignes_a_exclure: LignesExclues = Field(default_factory=LignesExclues)
    periode: PeriodeCarte = Field(default_factory=PeriodeCarte)
    perimetre: PerimetreCarte = Field(default_factory=PerimetreCarte)
    colonnes: list[ColonneCarte]
    date_inventaire: str | None = None   # stock : date de comptage (AAAA-MM-JJ) si elle n'est pas dans les lignes
    exercice: str | None = None          # balance / états financiers : « 2025 »


class RelationCarte(_Strict):
    type: Literal["memes_flux", "virement_interne", "doublon_probable", "rapprochement"]
    tableaux: list[str]
    cle: str = ""
    justification: str


class SourcePrincipale(_Strict):
    poste: Poste
    tableau: str                     # id du tableau principal (un poste = une source principale)
    secondaires: list[str] = Field(default_factory=list)   # pour rapprochement, jamais additionnés
    justification: str


class CategorieCarte(_Strict):
    motif: str                       # sous-chaîne (insensible à la casse et aux accents) cherchée dans libellé/tiers
    categorie: CategorieLibelle
    tableaux: list[str] = Field(default_factory=list)  # vide = tous
    exemples: list[str] = Field(default_factory=list)


class EntrepriseCarte(_Strict):
    activite: str | None = None
    sites: list[str] = Field(default_factory=list)
    devise: str = "XOF"
    tva_assujetti: bool | None = None
    decouvert_autorise: float | None = None
    emprunts: list[str] = Field(default_factory=list)
    source: Literal["fiche", "formulaire", "deduit", "inconnu"] = "inconnu"


class NonExploite(_Strict):
    tableau: str
    raison: str


class Carte(_Strict):
    entreprise: EntrepriseCarte = Field(default_factory=EntrepriseCarte)
    tableaux: list[TableauCarte]
    relations: list[RelationCarte] = Field(default_factory=list)
    sources_principales: list[SourcePrincipale] = Field(default_factory=list)
    categories_libelles: list[CategorieCarte] = Field(default_factory=list)
    non_exploites: list[NonExploite] = Field(default_factory=list)
    origine: Literal["ia", "regles"] = "ia"


# ====================================================================== requêtes et faits (traçabilité)
@dataclass
class Requete:
    id: str                          # « q1 », « q2 »…
    sql: str
    objectif: str
    tables: list[str]                # tables/vues utilisées
    colonnes: list[str]              # colonnes du résultat
    lignes: list[list]               # résultat (200 lignes au plus)
    nb_lignes: int                   # nombre total de lignes du résultat
    duree_ms: int
    auteur: Literal["code", "ia"] = "code"
    erreur: str | None = None


@dataclass
class Fait:
    """Un chiffre vérifié et traçable : la seule matière du rapport."""
    id: str
    libelle: str
    valeur: float | str | None       # None = inconnu
    unite: str                       # « FCFA », « % », « jours », « mois », « statut », « nombre », « texte »
    periode: str = ""                # « 2026-01-01/2026-11-25 »
    requete: str | None = None       # id de la Requete d'où vient la valeur (obligatoire si valeur numérique)
    cellule: str = ""                # « ligne 0, colonne ca » : où lire la valeur dans le résultat
    sources: list[str] = field(default_factory=list)   # « fichier › feuille »
    formule: str = ""
    alerte: Literal["", "vert", "orange", "rouge"] = ""
    impact_fcfa: float | None = None
    preuves_requete: str | None = None
    preuves: list[str] = field(default_factory=list)   # 5 lignes au plus
    outil_flow: str = ""
    hypotheses: list[str] = field(default_factory=list)
    estimation: bool = False
    statut: Literal["calcule", "verifie", "rejete"] = "calcule"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ResultatControle:
    code: str                        # « continuite_solde », « lignes_total », « qte_pu_montant », « sources_croisees »
    tableau: str | None
    reussi: bool
    message: str
    mesure: float | None = None      # ex. part des lignes conformes
    seuil: float | None = None
    requete: str | None = None
    bloquant: bool = True            # un contrôle bloquant échoué renvoie la carte à l'IA


@dataclass
class Cout:
    modele: str
    etape: str                       # « carte », « etats_pdf », « agent », « redaction », « relecture »
    tokens_entree: int = 0
    tokens_cache_lus: int = 0
    tokens_cache_ecrits: int = 0
    tokens_sortie: int = 0
    usd: float = 0.0
