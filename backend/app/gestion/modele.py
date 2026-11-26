"""Structures de données partagées du chemin « journaux de gestion » (refonte du diagnostic).

Contrat entre les modules de backend/app/gestion/ :
    tableaux.py      fichiers -> Tableau (lignes brutes + références de source, totaux et dates traités)
    typage.py        Tableau -> TableauType (type de document + rôle de chaque colonne + confiance)
    normalisation.py TableauType -> list[Ligne] (une ligne « fait » par opération, montants signés)
    rapprochement.py lignes -> doublons et virements internes marqués (jamais supprimés : `exclue` + `motif`)
    periode.py       dates -> Periodes (période en cours, même période N-1, dernière année complète)
    tresorerie.py    lignes banque/caisse/mobile money -> soldes de fin, découvert, agios, statut unique
    conformite.py    lignes banque/paie/dépenses + formulaire -> déclaré contre observé
    faits.py         lignes + périodes -> Fait (chiffres traçables) selon la table de priorité des sources

Règle d'or : les chiffres sont calculés ici, par le code. Chaque Fait porte ses sources (fichier, feuille,
numéros de ligne Excel) et sa formule. Aucun nom de client, produit, vendeur ou fichier du jeu de test
ne doit apparaître dans ce paquet : uniquement des règles génériques.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum


# ------------------------------------------------------------------ types de documents
class TypeTableau(str, Enum):
    VENTES = "ventes"
    ACHATS = "achats"
    DEPENSES = "depenses"
    BANQUE = "banque"
    CAISSE = "caisse"
    MOBILE_MONEY = "mobile_money"
    PAIE = "paie"
    STOCK = "stock"
    EMPRUNT = "emprunt"
    IMMOBILISATIONS = "immobilisations"
    CREANCES = "creances"
    BALANCE = "balance"
    GRAND_LIVRE = "grand_livre"
    BILAN = "bilan"
    COMPTE_RESULTAT = "compte_resultat"
    INCONNU = "inconnu"


# Types relevant du chemin « comptabilité tenue » (moteur historique)
TYPES_COMPTABLES = {TypeTableau.BALANCE, TypeTableau.GRAND_LIVRE, TypeTableau.BILAN, TypeTableau.COMPTE_RESULTAT}
# Types de comptes de trésorerie (un relevé = un compte)
TYPES_TRESORERIE = {TypeTableau.BANQUE, TypeTableau.CAISSE, TypeTableau.MOBILE_MONEY}


# ------------------------------------------------------------------ rôles de colonnes
class Role(str, Enum):
    DATE = "date"                  # date d'opération / de facture / de pièce
    DATE_VALEUR = "date_valeur"    # relevé bancaire
    ECHEANCE = "echeance"          # date d'échéance (créances, emprunts, déclarations)
    PIECE = "piece"                # n° facture / ticket / pièce / transaction
    TIERS = "tiers"                # client, fournisseur, bénéficiaire, contrepartie
    LIBELLE = "libelle"            # libellé libre de l'opération
    DESIGNATION = "designation"    # produit / article
    REFERENCE = "reference"        # référence article / code produit
    FAMILLE = "famille"            # famille / catégorie de produit
    CATEGORIE = "categorie"        # catégorie de dépense
    QTE = "qte"
    PU = "pu"                      # prix unitaire (de vente, ou d'achat dans un journal d'achats)
    REMISE = "remise"              # montant ou taux de remise
    MONTANT_BRUT = "montant_brut"  # avant remise
    MONTANT = "montant"            # montant net de la ligne (le montant de référence)
    MONTANT_HT = "montant_ht"
    MONTANT_TTC = "montant_ttc"
    TVA = "tva"
    DEBIT = "debit"                # sortie d'argent (relevé) / débit (balance)
    CREDIT = "credit"              # entrée d'argent (relevé) / crédit (balance)
    ENTREE = "entree"              # caisse : recette
    SORTIE = "sortie"              # caisse : dépense
    SOLDE = "solde"                # solde courant après l'opération
    FRAIS = "frais"                # frais de transaction (mobile money, banque)
    MODE_PAIEMENT = "mode_paiement"
    SITE = "site"                  # point de vente / boutique / caisse / agence
    CANAL = "canal"                # canal de vente (boutique, en ligne, revendeur…)
    VENDEUR = "vendeur"
    COMPTE = "compte"              # n° de compte SYSCOHADA (balance / grand livre uniquement)
    COMPTE_TRESORERIE = "compte_tresorerie"  # nom du compte / wallet (ex. colonne « opérateur »)
    STATUT = "statut"              # statut de paiement / déclaration
    MATRICULE = "matricule"
    SALARIE = "salarie"
    BRUT = "brut"
    NET = "net"
    CNPS_SALARIE = "cnps_salarie"
    CNPS_EMPLOYEUR = "cnps_employeur"
    ITS = "its"
    CHARGES_PATRONALES = "charges_patronales"
    QTE_COMPTEE = "qte_comptee"    # inventaire
    QTE_THEORIQUE = "qte_theorique"
    VALEUR_STOCK = "valeur_stock"
    EMPLACEMENT = "emplacement"
    CAPITAL_RESTANT = "capital_restant"
    CAPITAL = "capital"
    INTERETS = "interets"
    MENSUALITE = "mensualite"
    VALEUR_ORIGINE = "valeur_origine"
    DUREE = "duree"
    RESTE_A_PAYER = "reste_a_payer"
    AUTRE = "autre"


# ------------------------------------------------------------------ source et traçabilité
@dataclass(frozen=True)
class Source:
    """Où se trouve une donnée : fichier d'origine, feuille, numéro de ligne Excel (1 = première ligne)."""
    fichier: str
    feuille: str
    ligne: int | None = None

    def court(self) -> str:
        return f"{self.fichier} › {self.feuille}" + (f" l.{self.ligne}" if self.ligne else "")


# ------------------------------------------------------------------ tableaux
@dataclass
class Tableau:
    """Un tableau lu dans une feuille : en-têtes normalisés + lignes de données (après la ligne d'en-tête)."""
    fichier: str                    # nom d'origine du fichier
    feuille: str
    entetes: list[str]              # en-têtes normalisés (minuscules, sans accents)
    entetes_bruts: list[str]
    lignes: list[list]              # valeurs brutes (date, float, str, None)
    numeros: list[int]              # numéro de ligne Excel de chaque ligne de `lignes`
    titre: str = ""                 # texte des lignes au-dessus de l'en-tête (titre de feuille)
    empreinte_fichier: str = ""     # SHA-256 du fichier d'origine
    lignes_total: list[int] = field(default_factory=list)       # n° Excel des lignes reconnues comme totaux (écartées)
    lignes_sans_date: list[int] = field(default_factory=list)   # n° Excel écartées faute de date
    dates_heritees: list[int] = field(default_factory=list)     # n° Excel dont la date a été héritée


@dataclass
class TableauType:
    tableau: Tableau
    type: TypeTableau
    confiance: float                # 0 à 1
    roles: dict[Role, int]          # rôle -> index de colonne
    signaux: list[str] = field(default_factory=list)   # pourquoi ce type (mots-clés trouvés…)
    propose_par_ia: bool = False


# ------------------------------------------------------------------ lignes normalisées (« faits élémentaires »)
@dataclass
class Ligne:
    """Une opération. `montant` est TOUJOURS positif ; `sens` dit si l'argent entre ou sort (pour la trésorerie),
    ou s'il s'agit d'une vente / d'un achat / d'une charge (pour les journaux)."""
    type: TypeTableau
    source: Source
    date: date | None
    montant: float
    sens: str                       # "entree" | "sortie" | "vente" | "retour" | "achat" | "charge" | "stock" | "info"
    libelle: str = ""
    tiers: str = ""
    piece: str = ""
    designation: str = ""
    reference: str = ""
    famille: str = ""
    categorie: str = ""
    qte: float | None = None
    pu: float | None = None
    remise: float | None = None     # montant de remise de la ligne (en FCFA)
    montant_brut: float | None = None
    mode_paiement: str = ""
    site: str = ""
    canal: str = ""
    vendeur: str = ""
    compte: str = ""                # compte de trésorerie (nom du relevé / wallet / caisse) ou compte SYSCOHADA
    solde: float | None = None      # solde courant après l'opération (relevés)
    frais: float | None = None
    statut: str = ""
    extra: dict = field(default_factory=dict)  # colonnes spécifiques (brut, cnps, its, qte comptée, capital restant…)
    exclue: bool = False            # True : ne compte pas dans les totaux (doublon, virement interne, double comptage)
    motif_exclusion: str = ""


# ------------------------------------------------------------------ faits calculés (ce que lit le rapport)
@dataclass
class Fait:
    """Un chiffre vérifié et traçable. C'est la seule matière du rapport (gabarit ou IA rédactrice)."""
    id: str                          # identifiant stable, ex. "ca_periode", "tresorerie_banque_fin"
    libelle: str
    valeur: float | str | None       # None = inconnu ; str pour un statut (« positive », « tendue »…)
    unite: str                       # "FCFA", "%", "jours", "pièces", "mois", "statut", "nombre"
    periode: str = ""                # ex. "2026-01-01/2026-11-25"
    sources: list[str] = field(default_factory=list)   # Source.court() + précisions
    formule: str = ""                # formule lisible
    alerte: str = ""                 # "" | "vert" | "orange" | "rouge"
    impact_fcfa: float | None = None
    preuves: list[str] = field(default_factory=list)   # 5 lignes d'exemple au maximum
    outil_flow: str = ""             # "Flow Sell" | "Flow Compta" | "Flow RH" | "Flow Legal" | "Flow Task" | ""
    hypotheses: list[str] = field(default_factory=list)
    estimation: bool = False         # True : valeur estimée (couverture partielle)

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "libelle", "valeur", "unite", "periode", "sources", "formule", "alerte",
                                              "impact_fcfa", "preuves", "outil_flow", "hypotheses", "estimation")}


@dataclass
class Anomalie:
    """Constat de lecture ou de qualité (doublon, ligne sans date, total arithmétique, KPI invraisemblable…)."""
    code: str
    message: str
    montant: float | None = None
    sources: list[str] = field(default_factory=list)
