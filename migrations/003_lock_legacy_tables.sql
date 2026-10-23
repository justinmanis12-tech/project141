-- =====================================================================
-- 003_lock_legacy_tables.sql
-- Eski tabloları anon/authenticated anahtarlarla erişime KAPATIR
-- (RLS açık + politika yok). Tablolar silinmez; service_role çalışmaya devam eder.
-- 002'nin sonucunu doğruladıktan SONRA çalıştırın.
-- =====================================================================
alter table if exists public.classes enable row level security;
alter table if exists public.tasks   enable row level security;
