-- Un visiteur peut venir plusieurs jours ; un seul entretien par compte, sur l'un de ses jours.
-- Compatible avec l'ancienne API (qui n'envoie que jour_evenement) : peut être appliquée avant le déploiement.

alter table public.rapports add column if not exists jours_visite date[];
update public.rapports set jours_visite = array[jour_evenement] where jours_visite is null;

-- Les jours choisis arrivent dans le formulaire ; à défaut, le jour unique. jour_evenement = premier jour choisi.
create or replace function public.remplir_jours_visite()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  if new.jours_visite is null or cardinality(new.jours_visite) = 0 then
    select array_agg(distinct j::date order by j::date) into new.jours_visite
      from jsonb_array_elements_text(coalesce(new.formulaire -> 'jours_visite', '[]'::jsonb)) as t(j);
  end if;
  if new.jours_visite is null then
    new.jours_visite := array[new.jour_evenement];
  end if;
  return new;
end;
$$;
revoke execute on function public.remplir_jours_visite() from public, anon, authenticated;

drop trigger if exists rapports_jours_visite on public.rapports;
create trigger rapports_jours_visite before insert on public.rapports
  for each row execute function public.remplir_jours_visite();

alter table public.rapports alter column jours_visite set not null;
alter table public.rapports add constraint rapports_jours_visite_non_vide check (cardinality(jours_visite) >= 1);
create index if not exists rapports_jours_visite_idx on public.rapports using gin (jours_visite);
grant select (jours_visite) on public.rapports to authenticated;

-- Un seul entretien par compte.
delete from public.entretiens e using public.entretiens plus_recent
 where e.user_id = plus_recent.user_id
   and (e.created_at, e.rapport_id) < (plus_recent.created_at, plus_recent.rapport_id);
create unique index if not exists entretiens_un_par_compte on public.entretiens (user_id);

-- Réserve (ou déplace) l'entretien du compte ; false si le créneau est complet (l'ancien est alors conservé).
-- Le jour réservé est ajouté aux jours de visite du dossier.
create or replace function public.reserver_entretien(p_rapport uuid, p_user uuid, p_jour date, p_heure text, p_capacite integer)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_n integer;
begin
  perform pg_advisory_xact_lock(hashtext('entretien:' || p_user::text));
  perform pg_advisory_xact_lock(hashtext(p_jour::text || ' ' || p_heure));
  select count(*) into v_n from public.entretiens
   where jour = p_jour and heure = p_heure and user_id <> p_user;
  if v_n >= p_capacite then
    return false;
  end if;
  delete from public.entretiens where user_id = p_user and rapport_id <> p_rapport;
  insert into public.entretiens (rapport_id, user_id, jour, heure)
  values (p_rapport, p_user, p_jour, p_heure)
  on conflict (rapport_id) do update set jour = excluded.jour, heure = excluded.heure;
  update public.rapports
     set jours_visite = (select array_agg(distinct j order by j) from unnest(jours_visite || p_jour) as t(j))
   where id = p_rapport and user_id = p_user and not (p_jour = any(jours_visite));
  return true;
end;
$$;
revoke execute on function public.reserver_entretien(uuid, uuid, date, text, integer) from public, anon, authenticated;
grant execute on function public.reserver_entretien(uuid, uuid, date, text, integer) to service_role;
