# Refonte du diagnostic — Spécification P0 (fiabilité des chiffres)

Branche : `refonte-diagnostic`. Ne rien pousser en production.

## Principes non négociables
1. **Règles génériques uniquement.** Aucun nom de client, produit, vendeur, site ou fichier d'un jeu de test dans le code.
   Mots-clés métier génériques (« banque », « caisse », « wave », « orange money », « cnps »…) autorisés.
2. **Les chiffres sont calculés par le code**, jamais par l'IA.
3. **Traçabilité** : chaque chiffre (`Fait`) porte fichier, feuille, lignes Excel, formule.
4. **Pas de tout-ou-rien** : une donnée manquante est signalée, le reste est calculé.
5. **Tests pytest pour chaque règle** (tableaux construits à la main avec openpyxl dans `tmp_path`, ou listes Python).
6. Structures partagées : `backend/app/gestion/modele.py` (Tableau, TableauType, Role, TypeTableau, Ligne, Fait, Anomalie, Source).
   Ne pas les modifier sans nécessité ; si un champ manque, l'ajouter de façon rétrocompatible (valeur par défaut).

## Contexte métier (PME ivoirienne qui tient ses comptes sur Excel)
Fichiers typiques, un ou plusieurs onglets chacun, souvent avec 1 à 4 lignes de titre au-dessus des en-têtes, des lignes
TOTAL / sous-totaux intercalés, des cellules fusionnées ou vides, des formules (valeurs en cache) :
- journal des ventes (souvent un onglet par mois, ou un onglet par année) : date, n° ticket/facture, client, point de vente
  / canal, vendeur, désignation, qté, PU, remise, montant net, mode de paiement (espèces, Wave, Orange Money, carte,
  virement, chèque, crédit) ;
- journal des achats : date, fournisseur, n° pièce, désignation, qté, PU, montant, parfois fret/douane/transit sur des
  lignes séparées ;
- registre des dépenses : date, catégorie, libellé, bénéficiaire, montant, mode de paiement, site ;
- relevé bancaire (un onglet par année possible) : date opération, date valeur, libellé, débit, crédit, solde ;
- journal de caisse (un onglet par site possible) : date, libellé, entrée, sortie, solde ;
- mobile money (Wave, Orange Money, MTN MoMo, Moov) : date/heure, id transaction, type, contrepartie, montant, frais,
  solde, un onglet par opérateur possible ;
- livre de paie : mois, matricule, nom, brut, CNPS salarié/employeur, ITS, net, parfois statut de paiement ;
- inventaires : date d'inventaire (souvent dans le nom de l'onglet), référence, désignation, qté comptée, PU achat,
  valeur, emplacement ;
- tableau d'emprunt / immobilisations.
Montants en FCFA, entiers, parfois en texte « 1 250 000 » ou « 1.250.000 ».

## Lots P0 (exigences)

### P0-1 Détection des numéros de compte (stricte)
Une colonne est une colonne de comptes SYSCOHADA **seulement si** :
- son en-tête correspond à `compte|n° compte|no compte|code compte|account` (et pas « compte de résultat »,
  pas un nom de compte de trésorerie comme « compte bancaire ») ; **ou**
- plus de 80 % des valeurs non vides sont des codes de 2 à 8 chiffres, **et** une colonne `intitulé|libellé` existe,
  **et** les codes commencent par une classe 1 à 8, **et** les codes se répètent ou sont ordonnés comme un plan de comptes
  (ex. majorité de codes triés croissants, ou forte proportion de préfixes communs).
- Une colonne dont l'en-tête contient `montant|débit|crédit|solde|entrée|sortie|frais|prix|pu|valeur|total|qté|quantité`
  n'est **jamais** une colonne de comptes.
Conséquence : relevés bancaires, caisses et mobile money ne sont **jamais** classés `balance` ni `grand_livre`.

### P0-2 Types de tableaux (score + confiance)
Types : `ventes, achats, depenses, banque, caisse, mobile_money, paie, stock, emprunt, immobilisations, creances,
balance, grand_livre, bilan, compte_resultat, inconnu`. Score par mots-clés d'en-têtes (poids fort), nom de fichier et
d'onglet, titre au-dessus des en-têtes (poids moyen). Signaux typiques :
| Type | Signaux |
|---|---|
| ventes | date, n° facture/ticket, client, désignation, qté, PU, remise, montant, mode de paiement, vendeur, point de vente |
| achats | fournisseur, n° pièce/bon, désignation, qté, PU, montant, commande |
| depenses | catégorie/nature, libellé, bénéficiaire, montant, mode de paiement |
| banque | date opération, date valeur, libellé, débit, crédit, solde, « relevé », nom de banque |
| caisse | libellé, entrée/recette, sortie/dépense, solde, « caisse », « brouillard » |
| mobile_money | id/réf transaction, wave, orange money, om, momo, mtn, moov, frais, solde, opérateur |
| paie | matricule, brut, CNPS, ITS, net à payer, salarié |
| stock | inventaire, qté comptée/stock, emplacement, valeur stock |
| emprunt | échéance, capital restant dû, intérêts, mensualité, amortissement du prêt |
| immobilisations | date d'acquisition, valeur d'origine/brute, durée, amortissement cumulé, VNC |
| creances | échéance, reste à payer/solde dû, règlement |
| balance / grand_livre | colonne de comptes au sens P0-1 + débit/crédit/solde (grand livre : comptes répétés + dates) |
| bilan / compte_resultat | libellés de postes (actif, passif, capitaux propres / produits, charges, résultat) + montants N / N-1 |
- Les signaux forts l'emportent : « qté comptée », « inventaire », « emplacement » ⇒ `stock` même si un en-tête contient
  « PU achat ». Un mot isolé ne suffit jamais (ex. « achat » dans « prix d'achat »).
- Rôles de colonnes (enum `Role`) détectés par en-têtes, puis validés par le contenu (une colonne `DATE` doit contenir
  des dates, une colonne `MONTANT` des nombres…).
- Score faible (confiance < 0,5) : possibilité de demander à l'IA (en-têtes + 20 lignes) un type et des rôles (JSON) ;
  le code valide (colonnes existantes, types de valeurs cohérents). Interface : fonction injectable `proposeur_ia(en_tetes,
  exemples) -> dict | None` ; sans proposeur, rester sur `inconnu`.
- Tout tableau non reconnu est listé dans « données non exploitées ».

### P0-3 Table de priorité des sources (plus d'addition entre types)
Un poste = une source principale. Même type sur plusieurs feuilles/fichiers : concaténer les lignes, dédoublonner (P0-4),
additionner. Types différents : prendre la source de plus haute priorité ; les autres servent au rapprochement (écart
signalé), jamais à l'addition.
| Poste | Priorité 1 | Priorité 2 | Priorité 3 |
|---|---|---|---|
| CA | journal des ventes | compte de résultat / balance (70) | encaissements banque + caisse + MoMo (estimation signalée) |
| Achats et coût d'achat | journal des achats | balance (60) | sorties banque/caisse vers fournisseurs |
| Frais généraux | registre des dépenses | balance (61-65) | sorties banque/caisse classées |
| Personnel | livre de paie (brut + charges patronales) | balance (66) | — |
| Trésorerie | dernier solde de chaque relevé (banque, caisse, chaque compte MoMo) | balance (5x) | — |
| Stocks | dernier inventaire | balance (3x) | — |
| Dettes financières | tableau d'emprunt | lignes « déblocage prêt / échéance » de la banque | balance (16-17) |

### P0-4 Doublons
- Fichiers : SHA-256 du contenu ; un fichier identique n'est lu qu'une fois (message).
- Lignes (ventes, achats, dépenses) : même (date, n° pièce, tiers, désignation, montant) plusieurs fois ⇒ doublon
  probable : compté une seule fois, `Anomalie` avec le montant.
- Virements internes : caisse → banque (« versement »), MoMo → banque (« transfert », « retrait vers banque »),
  banque → MoMo ou caisse (« approvisionnement », « rechargement », « alimentation »), reconnus par mots-clés **et**
  appariement de montant à ±3 jours entre deux comptes de trésorerie différents. Exclus des recettes et des charges.

### P0-5 Totaux et dates manquantes
- Ligne de total : une cellule texte contient `total|sous-total|cumul|report|à nouveau|solde initial|solde de départ`,
  **ou** son montant égale (à 1 F près) la somme des lignes depuis le total précédent (au moins 2 lignes).
- Ligne sans date dans un journal trié par date, qui suit une ligne datée avec **le même n° de pièce/ticket** : hérite de
  la date précédente. Sinon mise de côté et comptée dans un indicateur de qualité.
- Feuille sans colonne de date : jamais additionnée dans un KPI de période ; signalée.

### P0-6 Période
- Analyser toute la période fournie.
- Période affichée : dernière année civile, complète ou non (ex. 01/01-25/11/2026), comparée à la même période de
  l'année précédente (01/01-25/11/2025). Donner aussi la dernière année complète (ex. 2025) et son total.
- Évolution du CA dès que deux périodes comparables existent.

### P0-7 Rentabilité calculée avec les postes disponibles
- Poste absent et peu probable (produits financiers, impôt) = 0, hypothèse notée.
- Poste majeur manquant (CA, achats, personnel) ⇒ « estimation partielle » + indicateur de couverture.
- Vraisemblance avant affichage : marge brute entre −20 % et 90 % ; charges totales < 3 × CA ; stock < 2 × CA annuel ;
  trésorerie cohérente avec le dernier solde bancaire. Échec ⇒ KPI retiré du rapport + anomalie journalisée.

### P0-8 Trésorerie réelle
- Solde de fin = dernière valeur de la colonne Solde de chaque compte (banque, caisse par site, chaque MoMo) à la fin de
  la période. Si pas de colonne solde : solde reconstitué = solde initial (ligne « solde initial/report » si présente) +
  cumul des mouvements, signalé comme reconstitué.
- Historique quotidien/mensuel du solde bancaire : nombre de jours en découvert (solde < 0), point bas et sa date, jours
  au-delà du découvert autorisé (paramètre ; formulaire ou fiche entreprise), total agios et commissions (libellés
  `agios`, `commission de dépassement`, `intérêts débiteurs`, `commission`, `frais de tenue`…).
- Un jour compte en découvert si le solde de fin de journée (dernier solde connu, reporté les jours sans opération) est
  négatif. Les jours sont comptés sur les 12 derniers mois de la période.
- Statut unique `positive|tendue|critique`, calculé une seule fois : critique si solde bancaire de fin < 0 ou > 30 jours
  au-delà du découvert autorisé sur 12 mois ; tendue si > 60 jours de découvert ; sinon positive.

### P0-9 Conformité : déclaré contre observé
- « Vous ne savez pas » au lieu de « Non communiqué » pour `NE_SAIT_PAS`.
- Observé dans les fichiers : CNPS (paiements en banque/caisse/MoMo libellés CNPS ; colonne de statut dans la paie ;
  trimestres de paie sans paiement CNPS correspondant ⇒ arriéré estimé = cotisations dues de ces trimestres) ;
  DGI/ITS (paiements `DGI|impôt|ITS|acompte|patente|IS|BIC` ; pénalités `pénalité|majoration|amende`).
- Contradiction formulaire / fichiers ⇒ afficher les deux. `penalites_connues` alimenté par les constats.
- FNE déclarative. Échéances TVA affichées seulement si assujetti (formulaire ou TVA présente dans les fichiers).

### P0-10 Petites corrections
- « Rapport remis le » = date de génération ; jour de visite à part (« Entretien prévu le … »).
- Effectif du formulaire comparé au nombre de salariés distincts du dernier mois de paie.
- Fiche entreprise acceptée en `.md`, `.txt`, `.docx` (champs : activité, sites, banque, découvert autorisé, emprunts,
  investissements) — extraction par l'IA, validation par le code ; sans IA, extraction par expressions régulières.
- Feuille de route : aucun objectif chiffré si le KPI correspondant est inconnu.
- Traçabilité stockée par dossier : types détectés, rôles des colonnes, sous-totaux par source, faits, prompt envoyé et
  réponse brute de l'IA (anonymisés).

## Architecture du code (paquet `backend/app/gestion/`)
| Module | Entrée → sortie | Fonctions publiques attendues |
|---|---|---|
| `tableaux.py` | fichiers → `Tableau` | `empreinte(path)->str`, `lire_tableaux(nom, path) -> list[Tableau]` (en-tête, titre, lignes, n° Excel, lignes vides retirées), `marquer_totaux(tableau, col_montant)`, `heriter_dates(tableau, col_date, col_piece)` |
| `typage.py` | `Tableau` → `TableauType` | `est_colonne_comptes(tableau, j)->bool`, `detecter_roles(tableau)->dict[Role,int]`, `typer(tableau, proposeur_ia=None)->TableauType` |
| `normalisation.py` | `TableauType` → `list[Ligne]` | `normaliser(tt)->list[Ligne]`, `normaliser_tout(liste)->list[Ligne]` ; ventes : montant net (montant, sinon qté×PU−remise), retours (montant négatif ou libellé « retour/avoir ») sens `retour` ; trésorerie : sens entree/sortie depuis débit/crédit, entrée/sortie, ou montant signé + type ; `compte` = identifiant du compte de trésorerie (fichier+onglet, ou colonne opérateur) ; paie : extra brut/net/cnps/its/charges patronales/salarié ; stock : extra qté comptée/valeur, date de l'inventaire lue dans l'onglet ou le titre si absente des lignes |
| `rapprochement.py` | lignes → lignes marquées + anomalies | `marquer_doublons(lignes)->list[Anomalie]`, `marquer_virements_internes(lignes)->list[Anomalie]` |
| `periode.py` | dates → périodes | `Periodes` (debut_total, fin_total, courante=(debut,fin), comparable_n1=(debut,fin) ou None, derniere_annee_complete=(debut,fin) ou None), `determiner_periodes(dates)` |
| `tresorerie.py` | lignes trésorerie → faits | `analyser_tresorerie(lignes, periodes, decouvert_autorise)->(list[Fait], list[Anomalie])` : soldes de fin par compte, total, jours découvert, jours au-delà, point bas, agios, statut unique (`Fait` id `tresorerie_statut`) |
| `conformite.py` | lignes + formulaire → faits | `observer_conformite(lignes, form_statuts, periodes)->(list[Fait], dict)` (dict = statuts déclaré/observé par obligation, `penalites_connues`) |
| `faits.py` | lignes + périodes → faits | `calculer_faits(lignes, periodes, ...)`: CA (période, N-1 comparable, dernière année complète, par canal/site), achats, frais généraux, personnel, marge brute, résultat estimé (P0-7), stocks (dernier inventaire), dettes financières, selon la table de priorité ; contrôles de vraisemblance |

L'intégration (pipeline, moteur, rapport) est faite après les modules, par une étape dédiée.
