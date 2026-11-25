CHECK-UP 360 — JPO DC-KNOWING : BACKEND (FastAPI) + FRONT (servi par le même serveur)

Démarrer (PowerShell, dossier backend/) :
  .env existe déjà (APP_ENV=dev, mot de passe conseiller généré). Renseigner OPENROUTER_API_KEY.
  .venv\Scripts\python.exe -m uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000
  Site visiteur : http://localhost:8000/        Espace conseillers : http://localhost:8000/conseiller.html
  Documentation de l'API : http://localhost:8000/api/docs

Connexion visiteur par code e-mail
  Sans SMTP et avec APP_ENV=dev, le code à 6 chiffres s'affiche dans la console du serveur.
  En production (APP_ENV=production), SMTP est obligatoire : sans lui, aucun code ne peut partir (503).

Tester :
  .venv\Scripts\python.exe -m pytest -q            (62 tests, IA simulée)

Modèles IA (gratuits, OpenRouter), essayés dans cet ordre, repli automatique :
  1. nvidia/nemotron-3.5-lightning:free
  2. baidu/ernie-5.5-flash-preview:free
  3. particle-ai/particle-1:free
  Changer l'ordre ou les modèles : OPENROUTER_MODELS dans .env.

Comptes conseillers : ADMIN_USER / ADMIN_PASSWORD (e-mail + mot de passe), ou plusieurs comptes :
  ADMIN_ACCOUNTS="a@dc-knowing.com=mdp1;b@dc-knowing.com=mdp2"

Mise en ligne (VPS Linux) : Python 3.12, Node 20 (npm install dans word/), LibreOffice (libreoffice-writer),
  nginx en HTTPS devant uvicorn, APP_ENV=production, SMTP, SECRET_KEY et DATA_KEY fixes dans l'environnement
  (sinon générés dans data/ au premier lancement : à sauvegarder), TRUST_PROXY=1 derrière nginx,
  PUBLIC_BASE_URL=https://votre-domaine, ADMIN_PASSWORD solide. Sauvegarder backend/data/.

Le front (checkup360-front/) est servi à la racine par FastAPI : pas de CORS, config.js en mock:false.
Routes : voir checkup360-front/README.md (contrat API) ; en plus GET /api/meta, /api/health, /api/fichiers/{id}/{pdf|docx}?token=.
Données : backend/data/ (SQLite, fichiers déposés supprimés après analyse ou 24 h, rapports 30 jours).
