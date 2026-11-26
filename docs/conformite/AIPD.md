# Analyse d'impact relative à la protection des données (AIPD) — Check-up 360

> **Projet à valider** (juriste / correspondant données). Méthode inspirée des guides AIPD usuels : description,
> nécessité, risques pour les personnes, mesures, risque résiduel.

## 1. Pourquoi une analyse d'impact
Traitement **innovant** (IA), de **données financières** confidentielles, incluant des **données de tiers** (salariés :
rémunérations), avec **transferts hors de Côte d'Ivoire**. Voir le registre (`REGISTRE_DE_TRAITEMENT.md`).

## 2. Nécessité et proportionnalité
| Question | Réponse |
|---|---|
| Les données sont-elles nécessaires ? | Les montants comptables oui ; les noms des tiers non → pseudonymisés avant l'IA, fichiers supprimés après analyse. |
| Pourrait-on faire sans IA ? | Partiellement : le serveur calcule déjà les totaux des journaux et balances ; l'IA sert aux documents non structurés. Si l'IA est indisponible ou non conforme, un rapport partiel est produit sans elle. |
| Durées proportionnées ? | Fichiers : quelques minutes à 24 h ; dossiers : 30 jours (préparation et suivi de l'entretien). |
| Information et consentement | Notice exacte (alignée sur le code), case obligatoire, suppression en libre-service. |

## 3. Risques pour les personnes

Échelle : 1 négligeable · 2 limitée · 3 importante · 4 maximale.

| Risque | Sources | Gravité | Vraisemblance avant mesures | Mesures | Vraisemblance résiduelle |
|---|---|---|---|---|---|
| **Accès illégitime** aux données financières d'une entreprise | Autre visiteur, conseiller compromis, clé serveur volée | 3 | 3 | RLS testée, contrôle de propriété, conseillers : scrypt + TOTP + verrouillage + cookie HttpOnly + rôles ; secrets hors code, rotation | 1 |
| **Divulgation des salaires** ou des clients à un tiers (IA) | Envoi de journaux nominatifs à un fournisseur d'IA | 3 | 4 | Paie jamais transmise ligne à ligne ; tiers pseudonymisés (tableaux, PDF au mieux) ; collecte refusée ; modèles gratuits bloqués | 1 (CSV/XLSX) · 2 (PDF libre) |
| **Conservation excessive** | Purge défaillante | 2 | 3 | Purge par lots testée (> 1 000 lignes), démarrage refusé sans `CRON_SECRET`, supervision du cron | 1 |
| **Modification / perte** (diagnostic faux, dossier perdu) | Injection dans un document, erreur IA, panne | 2 | 2 | Totaux calculés prioritaires sur l'IA (écart > 5 %), sauvegardes, reprise automatique | 1 |
| **Envoi à la mauvaise personne** | Changement d'adresse, préversion branchée sur la production | 2 | 2 | Adresse courante du compte à chaque envoi ; e-mails bloqués sur les préversions ; projet de test séparé | 1 |
| **Indisponibilité** pendant l'événement | Pic de charge, fournisseur IA lent | 1 | 3 | Budgets de temps, reprise automatique, rapport partiel sans IA, quotas | 2 |

## 4. Risques résiduels et actions restantes
1. **PDF en texte libre** : un nom de tiers isolé (sans civilité ni étiquette) peut subsister → la notice demande CSV/XLSX
   pour les journaux nominatifs ; à réévaluer après l'événement (exemples réels anonymisés).
2. **Transferts hors de Côte d'Ivoire** : obtenir les DPA (liste `SOUS_TRAITANTS.md`) et, si requise, l'autorisation ARTCI.
3. **Antivirus** : actif seulement si un démon ClamAV est fourni (`CLAMAV_HOST`).

## 5. Avis et validation
| Rôle | Nom | Date | Avis |
|---|---|---|---|
| Responsable du traitement | | | |
| Correspondant données personnelles | | | |
| Responsable technique | | | |
