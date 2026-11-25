-- Fonctions appelées par le serveur (service_role) uniquement : opérations atomiques.

-- Référence séquentielle par année : JPO-2026-0001, JPO-2026-0002...
create or replace function public.prochaine_reference(p_annee text)
returns text
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_n integer;
begin
  insert into public.compteurs (annee, n) values (p_annee, 1)
  on conflict (annee) do update set n = public.compteurs.n + 1
  returning n into v_n;
  return format('JPO-%s-%s', p_annee, lpad(v_n::text, 4, '0'));
end;
$$;

-- Ajoute un événement au journal ET change d'étape dans la même transaction (le suivi voit toujours un état cohérent).
create or replace function public.journaliser(p_id uuid, p_etape text, p_progression integer, p_evenement jsonb)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
begin
  update public.rapports
     set etape = p_etape,
         progression = greatest(0, least(100, p_progression)),
         statut = case p_etape when 'ERREUR' then 'erreur' when 'ENVOYE' then 'envoye' when 'TERMINE' then 'genere' else 'en_attente' end,
         journal = journal || jsonb_build_array(p_evenement)
   where id = p_id;
end;
$$;

-- Verrou de traitement : un même rapport n'est jamais traité deux fois en parallèle.
-- Retourne true si l'appelant obtient le verrou (libre, ou pris depuis plus de p_bail_secondes).
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
     set traitement_depuis = now()
   where id = p_id
     and etape not in ('TERMINE', 'ENVOYE', 'ERREUR')
     and (traitement_depuis is null or traitement_depuis < now() - make_interval(secs => p_bail_secondes))
  returning true into v_ok;
  return coalesce(v_ok, false);
end;
$$;

-- Réserve (ou déplace) l'entretien d'un rapport ; false si le créneau est complet.
create or replace function public.reserver_entretien(p_rapport uuid, p_user uuid, p_jour date, p_heure text, p_capacite integer)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_n integer;
begin
  perform pg_advisory_xact_lock(hashtext(p_jour::text || ' ' || p_heure));
  select count(*) into v_n from public.entretiens
   where jour = p_jour and heure = p_heure and rapport_id <> p_rapport;
  if v_n >= p_capacite then
    return false;
  end if;
  insert into public.entretiens (rapport_id, user_id, jour, heure)
  values (p_rapport, p_user, p_jour, p_heure)
  on conflict (rapport_id) do update set jour = excluded.jour, heure = excluded.heure;
  return true;
end;
$$;

-- Créneaux complets d'un jour (hors ceux du visiteur lui-même)
create or replace function public.creneaux_pris(p_jour date, p_capacite integer, p_exclure uuid default null)
returns setof text
language sql
security invoker
stable
set search_path = ''
as $$
  select heure from public.entretiens
   where jour = p_jour and (p_exclure is null or user_id <> p_exclure)
   group by heure having count(*) >= p_capacite
   order by heure;
$$;

-- Aucun accès client : ces fonctions ne servent qu'au serveur (service_role)
revoke execute on function public.prochaine_reference(text) from public, anon, authenticated;
revoke execute on function public.journaliser(uuid, text, integer, jsonb) from public, anon, authenticated;
revoke execute on function public.prendre_traitement(uuid, integer) from public, anon, authenticated;
revoke execute on function public.reserver_entretien(uuid, uuid, date, text, integer) from public, anon, authenticated;
revoke execute on function public.creneaux_pris(date, integer, uuid) from public, anon, authenticated;
grant execute on function public.prochaine_reference(text) to service_role;
grant execute on function public.journaliser(uuid, text, integer, jsonb) to service_role;
grant execute on function public.prendre_traitement(uuid, integer) to service_role;
grant execute on function public.reserver_entretien(uuid, uuid, date, text, integer) to service_role;
grant execute on function public.creneaux_pris(date, integer, uuid) to service_role;
