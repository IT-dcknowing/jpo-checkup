# Mise en ligne — Check-up 360 (Vercel + Supabase)

## Architecture

| Élément | Où | Rôle |
|---|---|---|
| Front statique (`checkup360-front/checkup360-front`) | Vercel (CDN) | Site visiteur et espace conseillers |
| API Python (`api/index.py` → `backend/app`) | Fonction Vercel, 300 s max | Dépôt, traitement IA, calculs, rapport, Mes diagnostics, conseillers |
| Rendu Word (`api/docx.js` → `word/md_vers_word.js`) | Fonction Vercel (Node) | Markdown → .docx, appelée par l'API avec `SECRET_KEY` |
| Comptes visiteurs | Supabase Auth | Code à 6 chiffres par e-mail (sans mot de passe), confirmation de l'adresse incluse |
| Base de données | Supabase Postgres (`supabase/migrations`) | `profiles`, `documents`, `rapports`, `entretiens`, `audit_logs`, RLS partout |
| Fichiers | Supabase Storage | Buckets privés `documents` (20 Mo, PDF/Excel/CSV/images) et `rapports` |
| E-mails | Supabase Auth (SMTP Resend) + Edge Function `envoyer-rapport` (API Resend) | Codes de connexion ; rapport (lien signé 7 j, pièce jointe < 5 Mo), excuse, invitation |
| Purge quotidienne | Vercel Cron → `/api/cron/purge` | Fichiers bruts > 24 h, rapports > 30 j, traitements interrompus |

Le rapport est livré en **Word (.docx)** : LibreOffice (PDF) n'existe pas sur Vercel.

## 1. Base de données (fait)

Migrations appliquées sur le projet `hhzwzoantzhgtsbrtswr` :
`20261125180000_schema_initial.sql`, `20261125180100_fonctions_serveur.sql`, `20261125180200_stockage.sql`.

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
*Edge Functions > Secrets* (ou *Project Settings > Edge Functions*) :
- `RESEND_API_KEY` : clé API Resend (obligatoire)
- `MAIL_FROM` : `DC-KNOWING · Diagnostic JPO <diagnostic@dc-knowing.com>`
- `MAIL_COPY_TO` : copie interne aux conseillers (facultatif, adresses séparées par des virgules)
- `PUBLIC_SITE_URL` : `https://jpo-checkup-itdcknowing-5466s-projects.vercel.app`

## 4. Vercel — variables d'environnement
*Project jpo-checkup > Settings > Environment Variables* (Production et Preview), voir `.env.example` :
`SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SERVICE_ROLE_KEY` (sensible), `SECRET_KEY` (sensible, 48+ caractères aléatoires),
`CRON_SECRET` (sensible), `OPENROUTER_API_KEY` (sensible), `ADMIN_ACCOUNTS` (sensible), `LLM_TIMEOUT=75`, `LLM_BUDGET=170`.

Réglages du projet : *Framework Preset* « Other » ; le reste vient de `vercel.json`.
Préversions protégées par Vercel Authentication : activer *Deployment Protection > Protection Bypass for Automation*
pour que l'API puisse appeler `/api/docx` sur une préversion (la production n'en a pas besoin).

## 5. Développement local
```bash
npm install                      # dépendances du rendu Word (racine)
pip install -r backend/requirements.txt
cp .env.example .env             # puis renseigner les valeurs
cd backend && python -m uvicorn app.main:create_app --factory --port 8000   # front + API sur http://localhost:8000
python -m pytest -q              # 48 tests (Supabase et IA simulés)
```
