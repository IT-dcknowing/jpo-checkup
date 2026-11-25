-- Droits explicites de l'API (le projet n'accorde pas de droits par défaut sur les nouvelles tables).
-- La RLS reste le filtre ligne par ligne ; ces GRANT ouvrent seulement les opérations utiles à chaque rôle.

-- Serveur (clé service_role : API Vercel, Edge Function)
grant usage on schema public to service_role;
grant select, insert, update, delete on public.profiles, public.rapports, public.documents,
  public.entretiens, public.audit_logs, public.compteurs to service_role;
grant usage, select on all sequences in schema public to service_role;

-- Visiteur connecté (navigateur, clé publishable + jeton de session)
grant usage on schema public to authenticated;
grant select on public.profiles, public.rapports, public.documents, public.entretiens to authenticated;
grant update (nom, entreprise, telephone) on public.profiles to authenticated;
grant update (rappel_demande) on public.rapports to authenticated;
grant insert, delete on public.documents to authenticated;

-- Anonyme : aucun accès aux tables
revoke all on public.profiles, public.rapports, public.documents, public.entretiens, public.audit_logs, public.compteurs from anon;
