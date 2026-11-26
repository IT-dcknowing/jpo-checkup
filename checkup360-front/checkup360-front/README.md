# Check-up 360 — front-end (Journées Portes Ouvertes DC-KNOWING)

Front statique, sans étape de build : HTML + CSS + JavaScript, avec Preact (11 Ko, inclus) pour le rendu.
Il fonctionne en **mode démo** (aucun appel réseau) ou **branché sur la backend FastAPI** décrite dans la spécification v2.0.

## Contenu

```
index.html                 Site visiteur : accueil, connexion par code e-mail, formulaire en 6 étapes,
                           suivi en direct (SSE), résultat, « Mes diagnostics » (rapports, invitation, programme, entretien)
conseiller.html            Espace conseillers (connexion JWT, liste du jour, détail, actions) — non indexé
rapport.html               Modèle visuel du rapport PDF de 3 pages A4 (imprimable)
config.js                  Réglages : mode démo ou adresse de l'API
assets/css/jpo.css         Charte JPO (noir, or, fond millimétré, Poppins, Sora, Plus Jakarta Sans, JetBrains Mono)
assets/css/conseiller.css  Styles de l'espace conseillers
assets/js/api.js           SEUL fichier qui parle à la backend (routes, formats, mode démo)
assets/js/app-visiteur.js  Logique du site visiteur
assets/js/app-conseiller.js Logique de l'espace conseillers
assets/js/dc-runtime.js    Petit moteur de gabarits (<sc-if>, <sc-for>, {{ trous }}) au-dessus de Preact
assets/js/vendor/          Preact 10 (licence MIT incluse)
assets/img/                Logo DC-KNOWING
references/emails.html     Maquettes des 3 e-mails (code de connexion, rapport envoyé, erreur de lecture)
```

Le balisage de chaque page est dans un `<script type="text/x-template">` en bas du fichier HTML :
on y modifie les textes et la mise en page ; la logique est dans `assets/js/app-*.js`.

## Lancer en mode démo

Ouvrez `index.html` dans un navigateur (double-clic), ou servez le dossier :

```bash
python3 -m http.server 8080   # puis http://localhost:8080
```

- Code de connexion de démonstration : **482913**
- `index.html?scenario=erreur` simule un échec de lecture (OCR illisible)
- `conseiller.html` : n'importe quel e-mail valide et un mot de passe non vide
- Les données de démo sont gardées dans le `localStorage` du navigateur (videz-le pour repartir de zéro)

## Brancher la backend

1. Dans `config.js` : `mock: false` et `apiBase` vers l'API (par défaut `/api`).
2. Le plus simple : servir ce dossier par FastAPI, sur la même origine (pas de CORS à gérer) :

```python
from fastapi.staticfiles import StaticFiles
app.mount("/", StaticFiles(directory="front", html=True), name="front")  # après les routes /api
```

3. Les jetons sont envoyés en `Authorization: Bearer <token>`. Pour le flux SSE (EventSource ne permet pas
   d'en-tête), le jeton est ajouté en `?token=…` (désactivable avec `sseWithToken: false` si vous utilisez un cookie).

## Contrat API attendu par le front

Toutes les routes sont appelées depuis `assets/js/api.js`. Si la backend utilise d'autres noms de champs,
adaptez la fonction `normalize()` (visiteur) ou `fromApi()` dans `app-conseiller.js` plutôt que les écrans.

### Connexion visiteur par code e-mail (nouveau, absent de la spec v2.0)

| Méthode | Route | Corps | Réponse |
|---|---|---|---|
| POST | `/api/auth/code` | `{ "email" }` | `{ "expires_in": 600, "resend_after": 30 }` · 429 si trop de demandes |
| POST | `/api/auth/verify` | `{ "email", "code" }` | `{ "token", "email" }` · 401 `{ "detail", "essais_restants" }` · 410 si expiré |
| POST | `/api/auth/logout` | — | `{}` |

Règles affichées à l'écran : code à 6 chiffres, valable 10 minutes, 3 essais, renvoi après 30 s.

### Diagnostics (visiteur connecté)

| Méthode | Route | Détail |
|---|---|---|
| POST | `/api/fichiers` | `{ "fichiers": [{ "nom", "taille", "categorie" }] }` (1 à 10, CSV/XLSX/PDF, 20 Mo par fichier, 50 Mo au total). Quotas vérifiés AVANT l'envoi. Réponse `{ "fichiers": [{ "id", "chemin", "jeton", "type" }] }` ; chaque fichier part ensuite dans Supabase Storage par `storage.from('documents').uploadToSignedUrl(chemin, jeton, fichier)` (seule voie de dépôt). Erreurs : 401, 413, 422, 429 |
| POST | `/api/diagnostics` | JSON : champs ci-dessous + `documents: [{ "id", "type" }]` (identifiants reçus de `/api/fichiers`). Réponse `{ "job_id", "reference" }`. Erreurs gérées : 401, 422, 429 |
| GET | `/api/diagnostics/{id}/sse` | flux d'avancement (voir plus bas) |
| GET | `/api/diagnostics/{id}` | résumé du diagnostic (champs ci-dessous) |
| GET | `/api/diagnostics/{id}/pdf` | `{ "url" }` — lien signé valable 7 jours |
| GET | `/api/me/diagnostics` | liste des diagnostics de l'e-mail connecté (tableau ou `{ "items": [...] }`) |
| POST | `/api/diagnostics/{id}/rappel` | demande de rappel après un échec de lecture |
| GET | `/api/entretiens/creneaux?jour=2026-12-16` | `{ "pris": ["09:30", "10:30"] }` |
| POST | `/api/diagnostics/{id}/entretien` | `{ "jour", "heure" }` |
| POST | `/api/me/invitation/envoi` | renvoie l'invitation par e-mail |
| GET | `/api/me/invitation.ics` | fichier agenda |
| DELETE | `/api/me` | suppression des données (droits ARTCI) |

**Champs envoyés par le formulaire** (section 2.2 de la spec) :
`raison_sociale`, `ncc` (facultatif), `secteur`, `effectif`, `contact_nom`, `contact_email` (celui de la connexion),
`contact_telephone`, `type_client` (`ACTUEL` | `REACTIVER` | `PROSPECT`), `jour_evenement` (`2026-12-16` | `-17` | `-18`),
`regime_fiscal` (`TEE` | `RME` | `REEL_SIMPLIFIE` | `REEL_NORMAL` | `NE_SAIT_PAS`),
`fne` (`ACTIVE` | `EN_COURS_ACTIVATION` | `NON_ACTIVE` | `NON_APPLICABLE` | `NON_DISPONIBLE`),
`statut_dgi`, `statut_cnps`, `statut_its`, `statut_cmu`, `statut_disa` (`A_JOUR` | `EN_RETARD` | `NON_DISPONIBLE`),
`tva_exoneree` (`true` | `false`), `consentement_artci` (`true`).
« Je ne sais pas » est transmis comme `NON_DISPONIBLE`, sauf pour le régime fiscal (`NE_SAIT_PAS`, comme dans la spec).
Pour les secteurs exemptés de FNE, la question n'est pas posée et `fne = NON_APPLICABLE`.

**Flux SSE** : chaque message `data:` est un JSON
`{ "etape": "LECTURE" | "MAPPING_IA" | "CALCUL" | "INTERPRETATION" | "PDF" | "ENVOYE" | "ERREUR", "progression": 0-100, "message": "texte affiché", "niveau": "" | "ok" | "erreur" }`.
Le front ferme le flux sur `ENVOYE` ou `ERREUR`, puis appelle `GET /api/diagnostics/{id}`.

**Résumé d'un diagnostic** (utilisé par l'écran Résultat et « Mes diagnostics ») :
`job_id`, `reference`, `entreprise`, `statut`, `progression`, `created_at`, `score_performance`, `grade_performance`,
`score_conformite`, `grade_conformite`, `couverture_pct`, `indicatif`, `verdict`, `points_forts[]`, `points_a_traiter[]`,
`alerte_principale`, `plan_action[] { priorite, action, delai, module }`, `entretien` (heure réservée ou null).

### Espace conseillers (JWT)

| Méthode | Route | Détail |
|---|---|---|
| POST | `/api/admin/login` | `{ "email", "password", "code"? }` → `{ "token", "role" }` (code TOTP si activé pour le compte ; jeton conservé en sessionStorage, 4 h) |
| GET | `/api/admin/diagnostics?jour=2026-12-16` | liste du jour, rechargée toutes les 10 s |
| GET | `/api/admin/diagnostics/{id}/pdf` | `{ "url" }` |
| POST | `/api/admin/diagnostics/{id}/renvoyer` | renvoi de l'e-mail |
| POST | `/api/admin/diagnostics/{id}/relancer` | relance d'un traitement en erreur |
| POST | `/api/admin/diagnostics/{id}/entretien-realise` | |
| POST | `/api/admin/diagnostics/{id}/rappel-effectue` | |
| GET | `/api/admin/diagnostics.csv?jour=…` | export |

Champs lus pour chaque ligne : `id`, `reference`, `nom_reel`, `type_client`, `secteur`, `effectif`, `created_at`, `statut`,
`score_performance`, `score_conformite`, `couverture_pct`, `indicatif`, `entretien`, `entretien_realise`, `rappel_effectue`,
`alertes[] { niveau, texte }`, `plan_action[] { priorite, action, module }`, `erreur`.

## À compléter avant l'événement

- **Programme** : créneaux indicatifs dans `app-visiteur.js` (`K.PROG`), à remplacer par le programme validé.
- **Code QR de l'invitation** : dessin décoratif ; à remplacer par un vrai QR fourni par la backend.
- **Police « Journées »** : la charte utilise Amsterdam (police commerciale). En attendant, « Mrs Saint Delafield »
  est utilisée. Pour l'intégrer : ajoutez le fichier dans `assets/fonts/` et un `@font-face { font-family: Amsterdam; src: url(../fonts/amsterdam.woff2) }` en tête de `jpo.css`.
- **Créneaux d'entretien** : liste dans `K.SLOTS` (le jeudi commence à 10h30).
- **rapport.html** sert de modèle visuel pour `templates/rapport.html.j2`. WeasyPrint ne gère pas `conic-gradient` :
  les anneaux de score doivent y être générés en SVG (comme prévu dans la spec, section 11).

## Navigateurs

Chrome, Edge, Firefox, Safari récents (2022+). Mise en page adaptée au mobile.
Les animations respectent le réglage « réduire les animations » du système.
