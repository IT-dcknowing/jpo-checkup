-- Corrections issues de la contre-vérification de l'audit (audit/RAPPORT_DE_CORRECTION.md).
-- Idempotente autant que possible : rejouable sur un projet déjà migré.

-- ================================================================ quotas et limitation de débit partagés
-- Une ligne par « consommation » (tentative de connexion, envoi d'e-mail, fichiers autorisés...).
-- Partagé entre toutes les instances Vercel, contrairement au limiteur en mémoire.
create table if not exists public.quotas (
  id         bigint generated always as identity primary key,
  cle        text not null,
  poids      bigint not null check (poids > 0),
  created_at timestamptz not null default now()
);
create index if not exists quotas_cle_idx on public.quotas (cle, created_at);
alter table public.quotas enable row level security;
revoke all on public.quotas from anon, authenticated;
grant select, insert, delete on public.quotas to service_role;
grant usage, select on all sequences in schema public to service_role;

-- Consomme p_poids sur la clé si le total de la fenêtre reste <= p_max ; atomique (verrou consultatif par clé).
create or replace function public.consommer_quota(p_cle text, p_poids bigint, p_max bigint, p_fenetre_s integer)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_total bigint;
begin
  perform pg_advisory_xact_lock(hashtext('quota:' || p_cle));
  select coalesce(sum(poids), 0) into v_total from public.quotas
   where cle = p_cle and created_at > now() - make_interval(secs => p_fenetre_s);
  if v_total + p_poids > p_max then
    return false;
  end if;
  insert into public.quotas (cle, poids) values (p_cle, p_poids);
  return true;
end;
$$;

create or replace function public.purger_quotas()
returns void
language sql
security invoker
set search_path = ''
as $$
  delete from public.quotas where created_at < now() - interval '2 days';
$$;

-- ================================================================ création atomique d'un dossier
-- Quota horaire vérifié sous verrou (plus de course entre deux dépôts simultanés) et documents rattachés
-- seulement s'ils appartiennent au visiteur et sont encore libres ; sinon tout est annulé.
create or replace function public.creer_rapport(
  p_user uuid, p_email text, p_jour date, p_secteur text, p_formulaire jsonb, p_titre_entreprise text,
  p_documents uuid[], p_categories jsonb, p_limite_heure integer)
returns setof public.rapports
language plpgsql
security invoker
set search_path = ''
as $$
declare
  v_n integer;
  v_ref text;
  v_id uuid;
begin
  perform pg_advisory_xact_lock(hashtext('depot:' || p_user::text));
  select count(*) into v_n from public.rapports
   where user_id = p_user and created_at > now() - interval '1 hour';
  if v_n >= p_limite_heure then
    raise exception 'QUOTA_DEPOTS';
  end if;
  v_ref := public.prochaine_reference(extract(year from p_jour)::text);
  insert into public.rapports (user_id, email, reference, titre, jour_evenement, secteur, formulaire)
  values (p_user, p_email, v_ref, left('Diagnostic ' || v_ref || ' — ' || p_titre_entreprise, 200), p_jour, p_secteur, p_formulaire)
  returning id into v_id;
  update public.documents d
     set rapport_id = v_id,
         categorie = coalesce(p_categories ->> d.id::text, d.categorie)
   where d.id = any(p_documents) and d.user_id = p_user and d.rapport_id is null;
  get diagnostics v_n = row_count;
  if v_n <> cardinality(p_documents) then
    raise exception 'DOCUMENTS_INDISPONIBLES';
  end if;
  return query select * from public.rapports where id = v_id;
end;
$$;

-- ================================================================ fusion atomique de la colonne JSON « donnees »
create or replace function public.fusionner_donnees(p_id uuid, p_patch jsonb)
returns void
language sql
security invoker
set search_path = ''
as $$
  update public.rapports set donnees = donnees || p_patch where id = p_id;
$$;

revoke execute on function public.consommer_quota(text, bigint, bigint, integer) from public, anon, authenticated;
revoke execute on function public.purger_quotas() from public, anon, authenticated;
revoke execute on function public.creer_rapport(uuid, text, date, text, jsonb, text, uuid[], jsonb, integer) from public, anon, authenticated;
revoke execute on function public.fusionner_donnees(uuid, jsonb) from public, anon, authenticated;
grant execute on function public.consommer_quota(text, bigint, bigint, integer) to service_role;
grant execute on function public.purger_quotas() to service_role;
grant execute on function public.creer_rapport(uuid, text, date, text, jsonb, text, uuid[], jsonb, integer) to service_role;
grant execute on function public.fusionner_donnees(uuid, jsonb) to service_role;

-- ================================================================ changement d'adresse e-mail
-- L'adresse des dossiers suit celle du compte (les rapports ne partent plus à l'ancienne adresse).
create or replace function public.handle_user_email_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.profiles set email = new.email where id = new.id;
  update public.rapports set email = new.email where user_id = new.id;
  return new;
end;
$$;
revoke execute on function public.handle_user_email_change() from public, anon, authenticated;

-- ================================================================ droits des visiteurs
-- Dépôt de fichiers : uniquement par jeton d'envoi signé délivré par l'API (quota vérifié avant).
-- Plus d'insertion directe dans Storage ni dans public.documents.
drop policy if exists "documents : dépôt dans son dossier" on storage.objects;
drop policy if exists "document : ajout des siens" on public.documents;
revoke insert on public.documents from authenticated;

-- Suppression d'un document : seulement s'il n'est pas encore rattaché à un dossier.
drop policy if exists "document : suppression des siens" on public.documents;
create policy "document : suppression des siens" on public.documents
  for delete to authenticated using ((select auth.uid()) = user_id and rapport_id is null);

-- Dossiers : lecture de toutes les colonnes sauf « erreur » (détail technique réservé aux conseillers).
revoke select on public.rapports from authenticated;
grant select (id, user_id, email, reference, titre, statut, etape, progression, jour_evenement, secteur, formulaire, donnees,
  journal, score_performance, grade_performance, score_conformite, grade_conformite, couverture_pct, chemin_fichier,
  chemin_markdown, pages, lecture_partielle, modele_ia, erreur_publique, rappel_demande, rappel_effectue, entretien_realise,
  traitement_depuis, created_at, termine_le, envoye_le) on public.rapports to authenticated;

-- ================================================================ stockage : formats réellement traités
update storage.buckets
   set allowed_mime_types = array[
     'application/pdf',
     'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
     'text/csv'
   ]
 where id = 'documents';

-- Purge : index pour les lectures par lots (plus anciens d'abord)
create index if not exists documents_created_idx on public.documents (created_at);
create index if not exists audit_logs_horodatage_idx on public.audit_logs (horodatage);
