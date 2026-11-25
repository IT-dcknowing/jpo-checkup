CHECK-UP 360 — JPO DC-KNOWING : API (FastAPI) déployée sur Vercel, données dans Supabase

Mise en ligne, réglages Supabase / Vercel / Resend : voir DEPLOIEMENT.md à la racine du projet.

Démarrer en local (depuis backend/) :
  npm install (à la racine, pour le rendu Word) ; pip install -r requirements.txt ; .env à la racine (voir .env.example)
  python -m uvicorn app.main:create_app --factory --port 8000
  Site visiteur : http://localhost:8000/   Espace conseillers : http://localhost:8000/conseiller.html   API : /api/docs

Tester : python -m pytest -q   (Supabase et IA simulés)

Comptes visiteurs : Supabase Auth (code à 6 chiffres par e-mail). Fichiers : buckets privés Supabase Storage.
Comptes conseillers : ADMIN_ACCOUNTS="a@dc-knowing.com=mdp1;b@dc-knowing.com=mdp2" (ou ADMIN_USER / ADMIN_PASSWORD).
Modèles IA (OpenRouter), repli automatique : OPENROUTER_MODELS. Délai et budget de l'IA : LLM_TIMEOUT, LLM_BUDGET.
Rétention : fichiers bruts supprimés après l'analyse (au plus tard 24 h), rapports 30 jours (tâche /api/cron/purge).
