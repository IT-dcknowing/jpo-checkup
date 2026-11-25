/*
 * Configuration du front Check-up 360.
 *   mock: true  → mode démo, aucune requête réseau (code de connexion : 482913).
 *   mock: false → branché sur la backend FastAPI, à l'adresse apiBase.
 */
window.CHECKUP_CONFIG = {
  apiBase: '/api',
  mock: false, // true = mode démo sans backend (code de connexion 482913)
  sseWithToken: true // ajoute ?token=… à l'URL SSE (EventSource n'envoie pas d'en-tête Authorization)
};
