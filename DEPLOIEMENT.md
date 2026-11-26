# Mise en ligne — Check-up 360 (Vercel + Supabase)

## Architecture

| Élément | Où | Rôle |
|---|---|---|
| Front statique (`checkup360-front/checkup360-front`) | Vercel (CDN) | Site visiteur et espace conseillers |
| API Python (`api/index.py` → `backend/app`) | Fonction Vercel, 300 s max | Dépôt, traitement IA, calculs, rapport, Mes diagnostics, conseillers |
| Rendu Word (`api/docx.js` → `word/md_vers_word.js`) | Fonction Vercel (Node) | Markdown → .docx, appelée par l'API avec `SECRET_KEY` |
| Comptes visiteurs | Supabase Auth | Code à 6 chiffres par e-mail (sans mot de passe), confirmation de l'adresse incluse |
| Base de données | Supabase Postgres (`supabase/migrations`) | `profiles`, `documents`, `rapports`, `entretiens`, `audit_logs`, RLS partout |
| Fichiers | Supabase Storage | Buckets privés `documents` (20 Mo, PDF/XLSX/CSV ; dépôt uniquement par jeton signé délivré par l'API après contrôle des quotas) et `rapports` |
| E-mails | Supabase Auth (SMTP Resend) + Edge Function `envoyer-rapport` (API Resend) | Codes de connexion ; rapport (lien signé 7 j, pièce jointe < 5 Mo), excuse, invitation |
| Purge quotidienne | Vercel Cron → `/api/cron/purge` | Fichiers bruts et lignes `documents` > 24 h, dossiers entiers > 30 j, journal d'audit > 12 mois, traitements interrompus |

Le rapport est livré en **Word (.docx)** : LibreOffice (PDF) n'existe pas sur Vercel.

## 1. Base de données (fait)

Migrations appliquées sur le projet `hhzwzoantzhgtsbrtswr` :
`20261125180000_schema_initial.sql`, `20261125180100_fonctions_serveur.sql`, `20261125180200_stockage.sql`, `20261125193500_droits_api.sql`.

**À appliquer (corrections de l'audit) :** `20261126120000_corrections_audit.sql` — **avant** de déployer la nouvelle API
(elle appelle les fonctions `creer_rapport`, `consommer_quota`, `fusionner_donnees`, `purger_quotas`). Faire d'abord une
sauvegarde (§6). Cette migration retire aux visiteurs le dépôt direct dans Storage : l'ancien front ne peut plus déposer,
déployer le nouveau front en même temps.

Tests des migrations et de l'isolation (Postgres local ou CI) : `supabase/tests/run.sh`.

## 2. Supabase Auth — réglages du tableau de bord

Ces réglages ne sont pas accessibles par le connecteur (MCP). Tableau de bord : https://supabase.com/dashboard/project/hhzwzoantzhgtsbrtswr

### 2.1 URLs — *Authentication > URL Configuration*
- **Site URL** : `https://jpo-checkup-itdcknowing-5466s-projects.vercel.app`
- **Redirect URLs** (bouton *Add URL*, une par ligne) :
  - `https://jpo-checkup-itdcknowing-5466s-projects.vercel.app/**`
  - `http://localhost:8000/**`
  - `http://localhost:3000/**`
  - plus tard, le domaine définitif : `https://<domaine-definitif>/**` (puis remplacer la Site URL)

### 2.2 Connexion par e-mail — *Authentication > Sign In / Providers > Email*
- **Enable Email provider** : activé
- **Confirm email** : activé (confirmation obligatoire ; le code saisi confirme l'adresse)
- **Secure email change** : activé
- **Email OTP Expiration** : `600` secondes
- **Email OTP Length** : `6`
- *Authentication > Sign In / Providers* (haut de page) : **Allow new users to sign up** activé

### 2.3 SMTP Resend — *Authentication > Emails > SMTP Settings*
Préalable : domaine vérifié dans Resend (*Resend > Domains > Add domain*, puis enregistrements DNS SPF/DKIM chez l'hébergeur du domaine).
- **Enable Custom SMTP** : activé
- **Sender email** : `diagnostic@dc-knowing.com`
- **Sender name** : `DC-KNOWING · Diagnostic JPO`
- **Host** : `smtp.resend.com` — **Port** : `465`
- **Username** : `resend` — **Password** : la clé API Resend
- *Authentication > Rate Limits* : **Rate limit for sending emails** → `30` par heure (au moins)

### 2.4 Modèles d'e-mails — *Authentication > Emails > Templates*
Pour chaque modèle, coller le **Subject** (voir `supabase/templates/SUJETS.txt`) et le contenu HTML du fichier :

| Modèle Supabase | Fichier |
|---|---|
| Confirm signup | `supabase/templates/confirmation.html` |
| Magic Link | `supabase/templates/magic_link.html` |
| Reset Password | `supabase/templates/recovery.html` |
| Change Email Address | `supabase/templates/email_change.html` |

Les modèles *Confirm signup* et *Magic Link* doivent contenir `{{ .Token }}` (code à 6 chiffres saisi dans l'app).

### 2.5 Clé service_role
*Project Settings > API Keys > Legacy API keys* : copier **service_role** (`eyJ...`) pour Vercel (étape 4).
C'est cette clé (et pas une `sb_secret_...`) que l'Edge Function reconnaît pour les renvois faits par un conseiller.

## 3. Edge Function `envoyer-rapport` (déployée) — secrets
Redéployer la fonction (`supabase/functions/envoyer-rapport/index.ts`) après la migration ci-dessus.
*Edge Functions > Secrets* (ou *Project Settings > Edge Functions*) :
- `RESEND_API_KEY` : clé API Resend (obligatoire)
- `MAIL_FROM` : `DC-KNOWING · Diagnostic JPO <diagnostic@dc-knowing.com>`
- `MAIL_COPY_TO` : copie interne aux conseillers (facultatif, adresses séparées par des virgules)
- `PUBLIC_SITE_URL` : `https://jpo-checkup-itdcknowing-5466s-projects.vercel.app` (sert aussi à limiter CORS à ce site)
- `EDGE_INTERNAL_SECRET` : secret aléatoire (48+ caractères), **le même** que dans Vercel. Identifie l'API pour les renvois
  faits par un conseiller, quel que soit le format de la clé serveur (`eyJ...` ou `sb_secret_...`).
- `EMAILS_PAR_HEURE` : facultatif, envois par visiteur et par heure (défaut 10).

## 4. Vercel — variables d'environnement
*Project jpo-checkup > Settings > Environment Variables*, voir `.env.example`.
**Production uniquement** pour les secrets de production ; les préversions (Preview) doivent viser un **projet Supabase
de test** distinct (§7), avec leurs propres clés.

Obligatoires en production (l'API **refuse de démarrer** sans elles et l'indique dans les journaux Vercel) :
`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `SECRET_KEY` (48+ caractères), `CRON_SECRET`
(16+ caractères, identique au secret du Cron Vercel), `OPENROUTER_API_KEY`.

Recommandées : `EDGE_INTERNAL_SECRET` (identique à Supabase), `ADMIN_ACCOUNTS`, `SENTRY_DSN`.

**IA (notice ARTCI : « sans collecte »)** : `OPENROUTER_DATA_COLLECTION=deny` (valeur par défaut) et des **modèles
payants** dans `OPENROUTER_MODELS`. En production, l'API refuse tout modèle `:free` (OpenRouter impose la collecte sur
ces modèles) : l'étape IA est alors sautée et seuls les totaux calculés par le serveur alimentent un rapport partiel.
Choisir des modèles dont la fiche OpenRouter indique un fournisseur sans conservation des données, et vérifier le DPA.

**Comptes conseillers** (`ADMIN_ACCOUNTS`) : mots de passe **hachés** (les mots de passe en clair sont refusés en
production) et code à usage unique recommandé.
```bash
cd backend
python -m app.security hash                              # saisir le mot de passe (12 caractères min.) → scrypt$...
python -m app.security totp prenom.nom@dc-knowing.com    # clé TOTP + lien à scanner dans l'application d'authentification
```
Format : `a@dc-knowing.com=scrypt$...,totp=ABCD...;b@dc-knowing.com=scrypt$...,role=lecture` (rôle `lecture` :
consultation seule, sans export CSV, téléchargement Markdown ni action). Session de 4 h (`ADMIN_TOKEN_HOURS`) dans un
cookie HttpOnly (illisible par les scripts de la page), protégé contre le CSRF (SameSite=Strict + en-tête `X-Checkup`).
Changer le mot de passe ou la clé TOTP d'un compte révoque ses jetons ; changer `ADMIN_TOKEN_VERSION` les révoque tous.

Réglages du projet : *Framework Preset* « Other » ; le reste vient de `vercel.json` (dont la CSP : si l'URL du projet
Supabase change, mettre à jour `connect-src`).
Préversions protégées par Vercel Authentication : activer *Deployment Protection > Protection Bypass for Automation*
pour que l'API puisse appeler `/api/docx` sur une préversion (la production n'en a pas besoin). Sur une préversion, aucun
e-mail n'est envoyé sauf `EMAILS_PREVIEW=1`.

### 4.1 Vérifications après chaque déploiement
1. `https://<domaine>/api/health?profond=1` → `200` et `"ok": true` (sinon le détail est dans les journaux Vercel).
2. *Vercel > Cron Jobs* : lancer `/api/cron/purge` à la main → `200` (un `401` = `CRON_SECRET` différent).
3. Dépôt de test avec un compte interne (fichier `backend/tests/fichiers/balance.csv`) : rapport reçu par e-mail.
4. `curl -sI https://<domaine>/ | grep -i -E "content-security|strict-transport"` : en-têtes présents.

### 4.2 Retour arrière
*Vercel > Deployments* : *Promote to Production* sur le déploiement précédent (instantané). Une migration ne s'annule
pas : restaurer la sauvegarde prise juste avant (§6) si elle doit être défaite.

> **Choix en vigueur (26/11/2026)** : modèles gratuits et mots de passe conseillers actuels conservés. Les paragraphes
> ci-dessus décrivant leur refus ne s'appliquent que si `AUTORISER_MODELES_GRATUITS=0` ou `MOTS_DE_PASSE_CLAIR=0`
> sont définis dans Vercel. La notice du site annonce que les fournisseurs des modèles gratuits peuvent conserver les
> données : la corriger si l'on passe aux modèles payants.

## 5. Développement local
```bash
npm install                      # dépendances du rendu Word (racine)
pip install -r backend/requirements.txt
cp .env.example .env             # puis renseigner les valeurs
cd backend && python -m uvicorn app.main:create_app --factory --port 8000   # front + API sur http://localhost:8000
python -m pytest -q              # 93 tests (Supabase et IA simulés), couverture : --cov
```

Tests des migrations et de la RLS sur un Postgres jetable : `supabase/tests/run.sh` (Postgres 15+ installé ; pas de Docker).
L'intégration continue (`.github/workflows/ci.yml`) lance à chaque push : ruff, pytest (couverture 80 % min.), pip-audit,
tests SQL, `node --check`, npm audit et `deno check` de l'Edge Function.

## 6. Sauvegardes et restauration
- **Plan Supabase** : vérifier *Project Settings > Add-ons / Database > Backups*. Plan Pro : sauvegarde quotidienne
  conservée 7 jours ; activer le **PITR** (restauration à la minute) pour la période de l'événement.
- **Objectifs** : RPO 24 h (sauvegarde quotidienne) ou quelques minutes avec PITR ; RTO 2 h.
- **Avant chaque migration** : `pg_dump` du schéma `public` (*Database > Connect* pour la chaîne de connexion) :
  `pg_dump "$DB_URL" -n public -Fc -f sauvegarde_$(date +%F).dump`, conservé chiffré hors du poste (coffre du cabinet).
- **Exercice de restauration** (une fois avant l'événement, puis chaque trimestre) : restaurer la dernière sauvegarde
  dans le projet de test (§7), lancer `supabase/tests/run.sh` et un dépôt de test, noter la durée.
- Les sauvegardes contiennent des données personnelles : même durée de conservation que la base (30 jours) et accès
  limité aux administrateurs.

## 7. Préproduction (préversions Vercel)
1. Créer un second projet Supabase « jpo-checkup-test », y appliquer toutes les migrations et l'Edge Function.
2. Dans Vercel, variables **Preview** : URL et clés de ce projet de test, `SECRET_KEY`/`CRON_SECRET` différents de la
   production, comptes conseillers de test, `EMAILS_PREVIEW` absent (pas d'e-mail réel).
3. Ne jamais copier de données réelles dans le projet de test.

## 8. Secrets : propriétaires, rotation, incident
| Secret | Où | Propriétaire | Rotation |
|---|---|---|---|
| `SUPABASE_SERVICE_ROLE_KEY` | Vercel | Responsable technique | Après chaque départ ou incident ; *API Keys > Roll* puis mise à jour Vercel |
| `SECRET_KEY` | Vercel (API + /api/docx) | Responsable technique | Trimestrielle ; déconnecte les conseillers |
| `CRON_SECRET` | Vercel | Responsable technique | Trimestrielle |
| `EDGE_INTERNAL_SECRET` | Vercel + Supabase | Responsable technique | Trimestrielle, aux deux endroits en même temps |
| `OPENROUTER_API_KEY`, `RESEND_API_KEY` | Vercel / Supabase | Responsable technique | Semestrielle ; plafond de dépense activé chez le fournisseur |
| `ADMIN_ACCOUNTS` | Vercel | Direction du cabinet | Retirer un compte le jour d'un départ ; mot de passe changé = jetons révoqués |

**En cas de fuite suspectée** : régénérer le secret concerné, redéployer, changer `ADMIN_TOKEN_VERSION`, consulter
`audit_logs` (connexions, exports) et les journaux Vercel/Supabase, puis évaluer la notification à l'ARTCI.

## 9. Supervision
- `SENTRY_DSN` (Vercel) : erreurs de l'API envoyées à Sentry (sans données personnelles : `send_default_pii=False`).
- Moniteur externe (UptimeRobot, Better Stack…) sur `/api/health?profond=1` toutes les 5 min, alerte par e-mail/SMS.
- *Vercel > Logs* : journaux JSON (niveau, module, message) ; filtrer `"niveau":"ERROR"` et `purge :` (résultat quotidien).
- Alerte à configurer dans Vercel (ou Sentry) si le cron renvoie autre chose que 200.

### 9.1 Reprise automatique des traitements (file d'attente en base)
Les dossiers non terminés forment la file (`rapports` dont l'étape n'est pas terminale). Un traitement coupé (limite de
300 s de Vercel) est **repris automatiquement** à la dernière étape enregistrée (le résultat de l'IA est conservé) :
- par le navigateur du visiteur : le suivi détecte la coupure (verrou expiré) et relance `/traiter` ;
- sans navigateur (visiteur parti) : `/api/worker`, appelé chaque minute par Supabase (pg_cron + pg_net), protégé par
  `CRON_SECRET`, reprend un dossier par appel.
Au-delà de `MAX_TENTATIVES` (2 par défaut), le dossier passe en erreur et le visiteur est prévenu.

Planification (Supabase > SQL Editor ; extensions *pg_cron* et *pg_net* activées dans *Database > Extensions*) :
```sql
select vault.create_secret('<valeur de CRON_SECRET>', 'cron_secret');
select cron.schedule('jpo-worker', '* * * * *', $$
  select net.http_post(
    url := 'https://<domaine>/api/worker',
    headers := jsonb_build_object('Authorization', 'Bearer ' ||
      (select decrypted_secret from vault.decrypted_secrets where name = 'cron_secret')),
    timeout_milliseconds := 1000);
$$);
```
(`timeout_milliseconds` court : pg_net n'attend pas la fin du traitement, la fonction Vercel continue seule.)

### 9.2 Antivirus (facultatif)
Toujours actif : refus des PDF avec JavaScript ou action automatique et des classeurs avec macros ou objets incorporés.
Analyse ClamAV en plus : héberger un démon `clamd` joignable depuis Vercel (par exemple l'image Docker
`clamav/clamav` sur un petit serveur, port 3310 ouvert **uniquement** aux adresses de sortie Vercel ou via un tunnel),
puis `CLAMAV_HOST`, `CLAMAV_PORT` et, pour bloquer si l'antivirus ne répond pas, `CLAMAV_OBLIGATOIRE=1`.

## 10. Limites connues
- **Traitement synchrone** : un traitement s'exécute dans une fonction de 300 s au plus (budget IA 170 s, rendu Word
  60 s) ; les coupures sont reprises automatiquement (§9.1). Plusieurs dizaines de traitements simultanés restent
  bornés par les quotas du fournisseur d'IA.
- **Contrôle « 4 pages »** : impossible sans LibreOffice (absent de Vercel) ; le gabarit tient en 4 pages sur les cas types.
- **PDF** : les tiers sont pseudonymisés au mieux (tableaux détectés, civilités, étiquettes « Client : »…, paie réduite
  aux totaux) ; un nom isolé au fil du texte peut subsister (la notice recommande CSV ou XLSX).
- **Conformité** : registre, AIPD et liste des sous-traitants dans `docs/conformite/` (à valider par un juriste).
