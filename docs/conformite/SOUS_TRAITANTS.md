# Sous-traitants — liste de contrôle avant ouverture

À cocher par le responsable du traitement. Conserver une copie de chaque document dans le dossier conformité du cabinet.

| Sous-traitant | Contrat / DPA | Localisation vérifiée | Réglages de confidentialité | Fait |
|---|---|---|---|---|
| Supabase | DPA : *Organization > Legal Documents* (signature en ligne) | Région du projet (*Settings > General*) | Sauvegardes / PITR activés ; accès au tableau de bord limité, MFA des administrateurs | ☐ |
| Vercel | DPA : vercel.com/legal/dpa | Région des fonctions : `dub1` (`vercel.json`) | Protection des préversions ; MFA de l'équipe ; variables Production séparées des Preview | ☐ |
| OpenRouter | Conditions + politique de données ; DPA si disponible | — | *Settings > Privacy* : refuser l'entraînement et la journalisation des prompts ; plafond de dépense ; modèles payants sans conservation dans `OPENROUTER_MODELS` | ☐ |
| Fournisseur(s) des modèles choisis | Politique de conservation du fournisseur (fiche OpenRouter du modèle) | — | Aucune conservation / aucun entraînement | ☐ |
| Resend | DPA : resend.com/legal/dpa | Région d'envoi | Domaine vérifié (SPF, DKIM, DMARC) ; plafond d'envoi | ☐ |
| Sentry (si activé) | DPA : sentry.io/legal/dpa | Région UE si possible | `send_default_pii=False` (déjà dans le code) ; nettoyage des données | ☐ |
| ClamAV hébergé (si activé) | Contrat d'hébergement | — | Accès réseau restreint à l'API | ☐ |
