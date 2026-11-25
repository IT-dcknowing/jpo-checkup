-- Check-up 360 (JPO DC-KNOWING) : schéma initial
-- Tables : profiles, documents, rapports, entretiens, audit_logs, compteurs
-- RLS active partout : un utilisateur ne voit que ses lignes ; le serveur (service_role) fait le reste.

-- ---------------------------------------------------------------- profiles
create table public.profiles (
  id          uuid primary key references auth.users (id) on delete cascade,
  email       text not null,
  nom         text,
  entreprise  text,
  telephone   text,
  created_at  timestamptz not null default now()
);
comment on table public.profiles is 'Profil visiteur, créé automatiquement à l''inscription (trigger on_auth_user_created).';

-- Remplit profiles à chaque inscription (nom / entreprise facultatifs dans les métadonnées)
create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.profiles (id, email, nom, entreprise)
  values (new.id, new.email, new.raw_user_meta_data ->> 'nom', new.raw_user_meta_data ->> 'entreprise')
  on conflict (id) do nothing;
  return new;
end;
$$;
revoke execute on function public.handle_new_user() from public, anon, authenticated;

create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.handle_new_user();

-- Garde l'e-mail du profil à jour après un changement d'adresse
create or replace function public.handle_user_email_change()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  update public.profiles set email = new.email where id = new.id;
  return new;
end;
$$;
revoke execute on function public.handle_user_email_change() from public, anon, authenticated;

create trigger on_auth_user_email_changed
  after update of email on auth.users
  for each row when (old.email is distinct from new.email)
  execute function public.handle_user_email_change();

-- ---------------------------------------------------------------- rapports (un dépôt = un rapport)
create table public.rapports (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references auth.users (id) on delete cascade,
  email             text not null,
  reference         text not null unique,                    -- JPO-2026-0001
  titre             text not null,
  statut            text not null default 'en_attente'
                    check (statut in ('en_attente', 'genere', 'envoye', 'erreur')),
  etape             text not null default 'EN_ATTENTE'        -- étape fine du traitement (suivi en direct)
                    check (etape in ('EN_ATTENTE', 'LECTURE', 'MAPPING_IA', 'CALCUL', 'RAPPORT', 'TERMINE', 'ENVOYE', 'ERREUR')),
  progression       smallint not null default 5 check (progression between 0 and 100),
  jour_evenement    date not null,
  secteur           text not null,
  formulaire        jsonb not null,                           -- réponses du formulaire (section 2.2 de la spec)
  donnees           jsonb not null default '{}'::jsonb,       -- résultat IA, résumé, alertes, plan d'action
  journal           jsonb not null default '[]'::jsonb,       -- événements affichés pendant le suivi
  score_performance smallint,
  grade_performance text,
  score_conformite  smallint,
  grade_conformite  text,
  couverture_pct    smallint,
  chemin_fichier    text,                                     -- bucket rapports : {user_id}/{rapport_id}.docx
  chemin_markdown   text,                                     -- bucket rapports : {user_id}/{rapport_id}.md
  pages             smallint,
  lecture_partielle boolean not null default false,
  modele_ia         text,
  erreur            text,                                     -- détail technique (conseillers)
  erreur_publique   text,                                     -- message affiché au visiteur
  rappel_demande    boolean not null default false,
  rappel_effectue   boolean not null default false,
  entretien_realise boolean not null default false,
  traitement_depuis timestamptz,                              -- verrou : traitement en cours depuis
  created_at        timestamptz not null default now(),
  termine_le        timestamptz,
  envoye_le         timestamptz
);
create index rapports_user_id_idx on public.rapports (user_id);
create index rapports_email_idx on public.rapports (email);
create index rapports_jour_idx on public.rapports (jour_evenement, created_at desc);
create index rapports_created_idx on public.rapports (created_at);

-- ---------------------------------------------------------------- documents déposés
create table public.documents (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null default auth.uid() references auth.users (id) on delete cascade,
  rapport_id  uuid references public.rapports (id) on delete cascade,
  nom_fichier text not null check (char_length(nom_fichier) between 1 and 200),
  chemin      text not null unique,                           -- bucket documents : {user_id}/{fichier}
  taille      bigint not null check (taille > 0 and taille <= 20971520),
  type        text not null,                                  -- type MIME
  categorie   text not null default 'autre' check (categorie in ('balance', 'cr', 'bilan', 'autre')),
  created_at  timestamptz not null default now(),
  constraint documents_chemin_proprietaire check (split_part(chemin, '/', 1) = user_id::text)
);
create index documents_user_id_idx on public.documents (user_id);
create index documents_rapport_id_idx on public.documents (rapport_id);

-- ---------------------------------------------------------------- entretiens individuels
create table public.entretiens (
  rapport_id uuid primary key references public.rapports (id) on delete cascade,
  user_id    uuid not null references auth.users (id) on delete cascade,
  jour       date not null,
  heure      text not null check (heure ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'),
  created_at timestamptz not null default now()
);
create index entretiens_user_id_idx on public.entretiens (user_id);
create index entretiens_creneau_idx on public.entretiens (jour, heure);

-- ---------------------------------------------------------------- journal d'audit (serveur uniquement)
create table public.audit_logs (
  id         bigint generated always as identity primary key,
  rapport_id uuid references public.rapports (id) on delete set null,
  acteur     text not null,
  action     text not null,
  ip         text,
  horodatage timestamptz not null default now()
);
create index audit_logs_rapport_id_idx on public.audit_logs (rapport_id);

-- ---------------------------------------------------------------- compteur des références (serveur uniquement)
create table public.compteurs (
  annee text primary key,
  n     integer not null
);

-- ================================================================ RLS
alter table public.profiles   enable row level security;
alter table public.rapports   enable row level security;
alter table public.documents  enable row level security;
alter table public.entretiens enable row level security;
alter table public.audit_logs enable row level security;
alter table public.compteurs  enable row level security;

-- profiles : lecture et modification de son propre profil
create policy "profil : lecture du sien" on public.profiles
  for select to authenticated using ((select auth.uid()) = id);
create policy "profil : modification du sien" on public.profiles
  for update to authenticated using ((select auth.uid()) = id) with check ((select auth.uid()) = id);
revoke insert, delete on public.profiles from anon, authenticated;
revoke update on public.profiles from anon, authenticated;
grant update (nom, entreprise, telephone) on public.profiles to authenticated;

-- rapports : lecture des siens ; seule la demande de rappel est modifiable par le visiteur.
-- Statut, scores, envoi (« envoye ») : écrits uniquement par le serveur (service_role).
create policy "rapport : lecture des siens" on public.rapports
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "rapport : demande de rappel sur les siens" on public.rapports
  for update to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
revoke insert, update, delete on public.rapports from anon, authenticated;
grant update (rappel_demande) on public.rapports to authenticated;

-- documents : le visiteur dépose, lit et supprime les siens (chemin {user_id}/...)
create policy "document : lecture des siens" on public.documents
  for select to authenticated using ((select auth.uid()) = user_id);
create policy "document : ajout des siens" on public.documents
  for insert to authenticated with check ((select auth.uid()) = user_id and rapport_id is null);
create policy "document : suppression des siens" on public.documents
  for delete to authenticated using ((select auth.uid()) = user_id);
revoke update on public.documents from anon, authenticated;
revoke all on public.documents from anon;

-- entretiens : lecture des siens (réservation par le serveur, qui contrôle la capacité des créneaux)
create policy "entretien : lecture des siens" on public.entretiens
  for select to authenticated using ((select auth.uid()) = user_id);
revoke insert, update, delete on public.entretiens from anon, authenticated;

-- audit_logs, compteurs : aucune policy = aucun accès hors service_role
revoke all on public.audit_logs, public.compteurs from anon, authenticated;
revoke all on public.profiles, public.rapports, public.entretiens from anon;
