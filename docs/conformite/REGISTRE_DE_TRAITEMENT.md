# Registre de traitement — Check-up 360 (Journées Portes Ouvertes DC-KNOWING)

> **Projet à valider** par le responsable juridique / le correspondant à la protection des données du cabinet.
> Rédigé à partir du code (commit de référence : branche `claude/friendly-allen-m9dyw6`). Cadre : loi n°2013-450 du
> 19 juin 2013 relative à la protection des données à caractère personnel (Côte d'Ivoire), autorité : ARTCI.

## 1. Identification

| Rubrique | Contenu |
|---|---|
| Responsable du traitement | Cabinet DC-KNOWING, Cocody Riviera 2, Abidjan — infos@dc-knowing.com · 27 22 42 14 43 |
| Représentant | *(nom et fonction du dirigeant — à compléter)* |
| Correspondant données personnelles | *(à désigner — à compléter)* |
| Nom du traitement | Check-up 360 : diagnostic financier et de conformité automatisé, préparation des entretiens |
| Date de mise en œuvre | Décembre 2026 (Journées Portes Ouvertes des 16, 17 et 18 décembre) |
| Formalité ARTCI | *(déclaration ou demande d'autorisation : numéro et date — à compléter avant ouverture)* |

## 2. Finalités et base légale

| Finalité | Base légale |
|---|---|
| Produire un diagnostic financier et de conformité à partir des documents déposés | Consentement exprès (case obligatoire, notice affichée avant le dépôt) |
| Envoyer le rapport par e-mail, l'invitation et le rappel d'entretien | Consentement / exécution de la demande du visiteur |
| Préparer et tenir l'entretien individuel avec un conseiller | Consentement |
| Sécurité, traçabilité des accès des conseillers | Intérêt légitime du cabinet (obligation de sécurité) |

Exclu : prospection commerciale sans nouveau consentement, revente, profilage hors du diagnostic.

## 3. Personnes concernées et données

| Catégorie de personnes | Données | Source |
|---|---|---|
| Visiteur (dirigeant, contact de l'entreprise) | Nom, e-mail, téléphone, entreprise, NCC, secteur, effectif, régime fiscal, statuts déclaratifs (DGI, CNPS, ITS, CMU, DISA, FNE) | Formulaire |
| Entreprise du visiteur | Données comptables et financières (CA, charges, bilan, trésorerie), scores, alertes, plan d'action | Documents déposés, calcul |
| Tiers figurant dans les documents (clients, fournisseurs, salariés) | Noms, montants ; **salaires** dans les journaux de paie | Documents déposés |
| Conseillers DC-KNOWING | E-mail professionnel, journal de leurs actions (horodatage, adresse IP) | Espace conseiller |
| Tous | Adresse IP, horodatages (journal d'audit, limitation des tentatives) | Technique |

**Données sensibles** au sens de la loi : aucune collectée volontairement. Les journaux de paie contiennent des
rémunérations nominatives (données à caractère personnel de salariés) : leur détail n'est **jamais** transmis à l'IA
(seuls les totaux) et les fichiers sont supprimés après l'analyse.

## 4. Destinataires

| Destinataire | Données | Motif |
|---|---|---|
| Conseillers DC-KNOWING habilités (comptes nominatifs, mot de passe haché, code à usage unique) | Dossiers, rapports, coordonnées | Entretien, rappel |
| Sous-traitants techniques (§6) | Selon leur rôle | Hébergement, IA, e-mail |

## 5. Durées de conservation (appliquées par le code, purge quotidienne)

| Donnée | Durée | Mécanisme |
|---|---|---|
| Fichiers déposés (bruts) | Supprimés dès l'analyse terminée, 24 h au plus | `pipeline._purge_uploads`, `purge()` |
| Dossier (formulaire, résultats, journal de suivi, rapport Word, entretien) | 30 jours après le dépôt | `purge()` |
| Journal d'audit (actions, IP) | 12 mois | `purge()` (`RETENTION_AUDIT_DAYS`) |
| Compteurs de limitation (tentatives, envois) | 2 jours | `purger_quotas()` |
| Compte visiteur (e-mail) | Jusqu'à suppression par le visiteur (« Supprimer mes données ») | `DELETE /api/me` |
| Sauvegardes Supabase | Selon le plan (7 jours en Pro) | Supabase |

## 6. Sous-traitants et transferts hors de Côte d'Ivoire

| Sous-traitant | Rôle | Données | Localisation | Garanties à obtenir |
|---|---|---|---|---|
| Supabase Inc. | Base de données, fichiers, comptes, envoi des codes | Toutes | *(région du projet — à vérifier : Project Settings > General)* | DPA signé, chiffrement au repos |
| Vercel Inc. | Exécution de l'application | Transitoires (traitement) | Région `dub1` (Dublin, UE) | DPA |
| OpenRouter + fournisseur du modèle | Extraction des chiffres par IA | Texte pseudonymisé des documents (sans identifiants du visiteur ni noms des tiers des tableaux) | États-Unis / selon fournisseur | **Situation actuelle : modèles gratuits, collecte autorisée (`data_collection=allow`)**, annoncée dans la notice ; à valider par un juriste. Cible : modèles payants sans conservation, `deny`, DPA |
| Resend | Envoi des e-mails | E-mail, nom, scores, lien du rapport, pièce jointe Word | États-Unis / UE | DPA |
| Sentry (si activé) | Erreurs techniques | Traces sans données personnelles (`send_default_pii=False`) | UE / États-Unis | DPA |

Transferts hors de Côte d'Ivoire : **oui** (tous les sous-traitants). Autorisation ARTCI à obtenir si requise *(à
confirmer par le conseil juridique)*.

## 7. Mesures de sécurité (techniques et organisationnelles)

- Comptes visiteurs par code à usage unique ; isolation de chaque visiteur (RLS Postgres testée en CI, dossiers Storage
  par compte, contrôle de propriété côté API).
- Conseillers : mots de passe hachés (scrypt), code TOTP, verrouillage après 5 échecs, sessions de 4 h en cookie
  HttpOnly révocables, rôle « lecture seule », journalisation des consultations, exports et actions.
- Pseudonymisation avant l'IA : identifiants du déclarant, noms des tiers (tableaux et PDF au mieux), paie réduite aux totaux.
- Fichiers : contrôle de signature, taille, archives anormales, contenu actif (macros, JavaScript), antivirus ClamAV
  si configuré ; jamais servis à d'autres utilisateurs.
- Chiffrement en transit (HTTPS, HSTS) et au repos (Supabase) ; CSP stricte ; secrets hors du code, rotation (DEPLOIEMENT.md §8).
- Sauvegardes et exercice de restauration (DEPLOIEMENT.md §6) ; supervision (§9).

## 8. Droits des personnes

Information : notice affichée avant le consentement. Accès et effacement : « Mes diagnostics » et « Supprimer mes
données » (effacement immédiat des fichiers, dossiers, entretiens et du compte). Rectification, opposition, autres
demandes : infos@dc-knowing.com, réponse sous *(délai — à compléter)*. Tiers (salariés, clients) : droits exercés
auprès du cabinet ; leurs données ne sont conservées que le temps de l'analyse.
