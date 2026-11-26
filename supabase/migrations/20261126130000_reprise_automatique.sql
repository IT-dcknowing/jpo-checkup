-- Reprise automatique d'un traitement coupé (fonction Vercel arrêtée à 300 s) : compteur de tentatives.
-- Le verrou peut être repris une fois son bail expiré ; chaque prise compte une tentative. Au-delà du maximum fixé par
-- l'API (MAX_TENTATIVES, 2 par défaut), le dossier passe en erreur et un conseiller rappelle le visiteur.
alter table public.rapports add column if not exists tentatives smallint not null default 0;

create or replace function public.prendre_traitement(p_id uuid, p_bail_secondes integer default 330)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_ok boolean;
begin
  update public.rapports
     set traitement_depuis = now(),
         tentatives = tentatives + 1
   where id = p_id
     and etape not in ('TERMINE', 'ENVOYE', 'ERREUR')
     and (traitement_depuis is null or traitement_depuis < now() - make_interval(secs => p_bail_secondes))
  returning true into v_ok;
  return coalesce(v_ok, false);
end;
$$;
revoke execute on function public.prendre_traitement(uuid, integer) from public, anon, authenticated;
grant execute on function public.prendre_traitement(uuid, integer) to service_role;

-- Les visiteurs peuvent lire le nombre de tentatives de leurs dossiers (suivi)
grant select (tentatives) on public.rapports to authenticated;
create index if not exists rapports_en_cours_idx on public.rapports (created_at) where etape not in ('TERMINE', 'ENVOYE', 'ERREUR');
