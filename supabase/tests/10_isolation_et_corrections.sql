-- Tests d'isolation (MU-03 à MU-09, MU-11) et des fonctions ajoutées par les corrections de l'audit.
-- Chaque test lève une exception en cas d'échec ; le script s'arrête à la première erreur (ON_ERROR_STOP).
\set ON_ERROR_STOP on
create function pg_temp.ok(cond boolean, msg text) returns void language plpgsql as $$
begin if cond is not true then raise exception 'ÉCHEC : %', msg; end if; end $$;
-- exécute une requête qui DOIT échouer (droits insuffisants, contrainte...)
create function pg_temp.refus(q text, msg text) returns void language plpgsql as $$
begin
  begin execute q; exception when others then return; end;
  raise exception 'ÉCHEC (accepté à tort) : %', msg;
end $$;
grant execute on all functions in schema pg_temp to public;

-- ---------------------------------------------------------------- données : deux visiteurs A et B
insert into auth.users (id, email) values
  ('aaaaaaaa-0000-0000-0000-000000000001', 'a@exemple.ci'), ('bbbbbbbb-0000-0000-0000-000000000002', 'b@exemple.ci');
set role service_role;
select pg_temp.ok((select count(*) from public.profiles) = 2, 'profils créés par le trigger');
insert into public.documents (id, user_id, nom_fichier, chemin, taille, type) values
  ('d0000000-0000-0000-0000-00000000000a', 'aaaaaaaa-0000-0000-0000-000000000001', 'a.csv', 'aaaaaaaa-0000-0000-0000-000000000001/a.csv', 10, 'text/csv'),
  ('d0000000-0000-0000-0000-00000000000b', 'bbbbbbbb-0000-0000-0000-000000000002', 'b.csv', 'bbbbbbbb-0000-0000-0000-000000000002/b.csv', 10, 'text/csv'),
  ('d0000000-0000-0000-0000-0000000000a2', 'aaaaaaaa-0000-0000-0000-000000000001', 'a2.csv', 'aaaaaaaa-0000-0000-0000-000000000001/a2.csv', 10, 'text/csv');
insert into storage.objects (bucket_id, name) values ('documents', 'aaaaaaaa-0000-0000-0000-000000000001/a.csv'),
  ('documents', 'bbbbbbbb-0000-0000-0000-000000000002/b.csv');

-- ---------------------------------------------------------------- creer_rapport (service_role)
select pg_temp.ok((select reference from public.creer_rapport('aaaaaaaa-0000-0000-0000-000000000001', 'a@exemple.ci', '2026-12-16',
  'Commerce', '{"raison_sociale":"A"}', 'Société A', array['d0000000-0000-0000-0000-00000000000a']::uuid[],
  '{"d0000000-0000-0000-0000-00000000000a":"balance"}', 5)) = 'JPO-2026-0001', 'création atomique + référence');
select pg_temp.ok((select rapport_id is not null and categorie = 'balance' from public.documents where id = 'd0000000-0000-0000-0000-00000000000a'),
  'document rattaché avec sa catégorie');
-- MU-11 : le même document ne peut pas servir deux fois ; rien n'est créé
select pg_temp.refus($q$select public.creer_rapport('aaaaaaaa-0000-0000-0000-000000000001', 'a@exemple.ci', '2026-12-16', 'Commerce',
  '{}', 'A', array['d0000000-0000-0000-0000-00000000000a']::uuid[], '{}', 5)$q$, 'document déjà rattaché');
-- document d'un autre compte
select pg_temp.refus($q$select public.creer_rapport('aaaaaaaa-0000-0000-0000-000000000001', 'a@exemple.ci', '2026-12-16', 'Commerce',
  '{}', 'A', array['d0000000-0000-0000-0000-00000000000b']::uuid[], '{}', 5)$q$, 'document d''un autre visiteur');
select pg_temp.ok((select count(*) from public.rapports) = 1, 'transactions refusées annulées');
-- quota horaire
select pg_temp.refus($q$select public.creer_rapport('aaaaaaaa-0000-0000-0000-000000000001', 'a@exemple.ci', '2026-12-16', 'Commerce',
  '{}', 'A', array['d0000000-0000-0000-0000-0000000000a2']::uuid[], '{}', 1)$q$, 'quota de dépôts');

-- ---------------------------------------------------------------- fusion JSON atomique
select public.fusionner_donnees((select id from public.rapports limit 1), '{"resume":{"v":1}}');
select public.fusionner_donnees((select id from public.rapports limit 1), '{"alertes":[1]}');
select pg_temp.ok((select donnees ? 'resume' and donnees ? 'alertes' from public.rapports limit 1), 'fusion sans perte de clé');

-- ---------------------------------------------------------------- reprise automatique : verrou avec bail et tentatives
select pg_temp.ok(public.prendre_traitement((select id from public.rapports limit 1), 330), 'première prise du verrou');
select pg_temp.ok(not public.prendre_traitement((select id from public.rapports limit 1), 330), 'verrou déjà pris');
update public.rapports set traitement_depuis = now() - interval '10 minutes';
select pg_temp.ok(public.prendre_traitement((select id from public.rapports limit 1), 330), 'reprise après expiration du bail');
select pg_temp.ok((select tentatives from public.rapports limit 1) = 2, 'deux tentatives comptées');
update public.rapports set traitement_depuis = null;

-- ---------------------------------------------------------------- quotas partagés
select pg_temp.ok(public.consommer_quota('t', 3, 5, 60) and public.consommer_quota('t', 2, 5, 60) and not public.consommer_quota('t', 1, 5, 60),
  'consommer_quota respecte le maximum');
reset role;

-- ---------------------------------------------------------------- visiteur A
set role authenticated;
set request.jwt.claim.sub = 'aaaaaaaa-0000-0000-0000-000000000001';
select pg_temp.ok((select count(*) from public.rapports) = 1, 'A voit son dossier');
select pg_temp.ok((select count(*) from public.documents) = 2, 'A ne voit que ses documents');
select pg_temp.refus('select erreur from public.rapports', 'colonne erreur (détail technique) lisible par le visiteur');
select pg_temp.ok((select count(*) from (select id, reference, erreur_publique from public.rapports) x) = 1, 'colonnes publiques lisibles');
-- MU-03 / MU-04 : plus aucune insertion directe de document
select pg_temp.refus($q$insert into public.documents (user_id, nom_fichier, chemin, taille, type)
  values ('aaaaaaaa-0000-0000-0000-000000000001', 'x.csv', 'aaaaaaaa-0000-0000-0000-000000000001/x.csv', 1, 'text/csv')$q$, 'insertion directe de document');
-- dépôt direct dans Storage : interdit (jeton signé obligatoire)
select pg_temp.refus($q$insert into storage.objects (bucket_id, name) values ('documents', 'aaaaaaaa-0000-0000-0000-000000000001/z.csv')$q$,
  'dépôt direct dans Storage');
-- MU-05 : fichiers de B invisibles
select pg_temp.ok((select count(*) from storage.objects where name like 'bbbbbbbb%') = 0, 'fichiers de B invisibles');
-- MU-08 / MU-09
select pg_temp.refus($q$update public.rapports set user_id = 'bbbbbbbb-0000-0000-0000-000000000002'$q$, 'changement de propriétaire');
select pg_temp.refus($q$update public.documents set rapport_id = null$q$, 'modification d''un document');
update public.rapports set rappel_demande = true;
select pg_temp.ok((select rappel_demande from public.rapports), 'demande de rappel sur son dossier');
-- N-6 : un document rattaché ne peut plus être supprimé ; un document libre, si
delete from public.documents where id = 'd0000000-0000-0000-0000-00000000000a';
select pg_temp.ok((select count(*) from public.documents where id = 'd0000000-0000-0000-0000-00000000000a') = 1, 'document rattaché conservé');
delete from public.documents where id = 'd0000000-0000-0000-0000-0000000000a2';
select pg_temp.ok((select count(*) from public.documents where id = 'd0000000-0000-0000-0000-0000000000a2') = 0, 'document libre supprimé');
-- MU-07 et tables serveur
select pg_temp.refus($q$insert into public.entretiens (rapport_id, user_id, jour, heure)
  select id, user_id, '2026-12-16', '09:00' from public.rapports$q$, 'réservation directe d''entretien');
select pg_temp.refus('select * from public.audit_logs', 'lecture du journal d''audit');
select pg_temp.refus('select * from public.quotas', 'lecture des quotas');
select pg_temp.refus($q$select public.creer_rapport('aaaaaaaa-0000-0000-0000-000000000001', 'a@exemple.ci', '2026-12-16', 'C', '{}', 'A',
  array[]::uuid[], '{}', 99)$q$, 'fonction serveur appelée par un visiteur');
select pg_temp.refus($q$select public.consommer_quota('x', 1, 9, 60)$q$, 'quota manipulé par un visiteur');
reset role;

-- ---------------------------------------------------------------- visiteur B
set role authenticated;
set request.jwt.claim.sub = 'bbbbbbbb-0000-0000-0000-000000000002';
select pg_temp.ok((select count(*) from public.rapports) = 0, 'B ne voit pas le dossier de A');
update public.rapports set rappel_demande = false;
reset role;
select pg_temp.ok((select rappel_demande from public.rapports), 'B ne peut pas modifier le dossier de A');

-- ---------------------------------------------------------------- anonyme
set role anon;
select pg_temp.refus('select * from public.rapports', 'lecture anonyme');
reset role;

-- ---------------------------------------------------------------- changement d'adresse (R-15)
update auth.users set email = 'a.nouveau@exemple.ci' where id = 'aaaaaaaa-0000-0000-0000-000000000001';
select pg_temp.ok((select email from public.rapports) = 'a.nouveau@exemple.ci', 'adresse du dossier mise à jour');

-- ---------------------------------------------------------------- stockage : formats réellement traités (R-25)
select pg_temp.ok((select allowed_mime_types from storage.buckets where id = 'documents') =
  array['application/pdf', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'text/csv'], 'types MIME alignés');

-- ---------------------------------------------------------------- plusieurs jours, un entretien par compte
select pg_temp.ok((select jours_visite = array[jour_evenement] from public.rapports), 'jour unique repris dans jours_visite');
select pg_temp.ok(public.reserver_entretien((select id from public.rapports), 'aaaaaaaa-0000-0000-0000-000000000001', '2026-12-17', '14:15', 1),
  'réservation d''un créneau');
select pg_temp.ok((select '2026-12-17'::date = any(jours_visite) from public.rapports), 'jour réservé ajouté à la visite');
select pg_temp.ok(public.reserver_entretien((select id from public.rapports), 'aaaaaaaa-0000-0000-0000-000000000001', '2026-12-18', '14:00', 1)
  and (select count(*) from public.entretiens) = 1, 'changement de créneau : un seul entretien');
select pg_temp.ok(not public.reserver_entretien((select id from public.rapports), 'bbbbbbbb-0000-0000-0000-000000000002', '2026-12-18', '14:00', 1),
  'créneau complet refusé');

\echo 'Tous les tests SQL sont passés.'
