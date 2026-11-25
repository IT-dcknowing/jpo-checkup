/*
 * Configuration du front Check-up 360.
 *   mock: true  → mode démo, aucune requête réseau (code de connexion : 482913).
 *   mock: false → comptes et fichiers dans Supabase, traitement par l'API (/api, fonctions Vercel).
 * supabaseKey est la clé « publishable » (publique par nature) : la clé service_role n'est JAMAIS ici.
 */
window.CHECKUP_CONFIG = {
  apiBase: '/api',
  mock: false, // true = mode démo sans backend (code de connexion 482913)
  supabaseUrl: 'https://hhzwzoantzhgtsbrtswr.supabase.co',
  supabaseKey: 'sb_publishable_MT9NhBCETkFD3rXKDUM9Ig_3qFJAHpv'
};
