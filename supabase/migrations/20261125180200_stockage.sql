-- Stockage : deux buckets privés, chaque utilisateur n'accède qu'à son dossier {user_id}/...
-- Téléchargements : uniquement par URL signée à durée limitée (créée côté serveur).

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'documents', 'documents', false, 20971520,  -- 20 Mo
  array[
    'application/pdf',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',  -- .xlsx
    'application/vnd.ms-excel',                                           -- .xls
    'text/csv',
    'image/png', 'image/jpeg', 'image/webp', 'image/heic'
  ]
)
on conflict (id) do update
  set public = excluded.public, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'rapports', 'rapports', false, 10485760,  -- 10 Mo
  array[
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  -- .docx
    'application/pdf',
    'text/markdown'
  ]
)
on conflict (id) do update
  set public = excluded.public, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

-- documents : le visiteur dépose, lit et supprime les fichiers de son dossier
create policy "documents : lecture de son dossier" on storage.objects
  for select to authenticated
  using (bucket_id = 'documents' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "documents : dépôt dans son dossier" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'documents' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "documents : suppression dans son dossier" on storage.objects
  for delete to authenticated
  using (bucket_id = 'documents' and (storage.foldername(name))[1] = (select auth.uid())::text);

-- rapports : lecture seule de son dossier (écrits par le serveur)
create policy "rapports : lecture de son dossier" on storage.objects
  for select to authenticated
  using (bucket_id = 'rapports' and (storage.foldername(name))[1] = (select auth.uid())::text);
