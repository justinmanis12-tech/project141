-- =====================================================================
-- 002_migrate_legacy_data.sql
-- Eski `classes` / `tasks` verilerini yeni yapıya KOPYALAR (silmez).
-- Önce 001 çalıştırılmış olmalı. Tekrar çalıştırmak güvenlidir.
-- ÖNEMLİ: aşağıdaki okul adını kendi okulunuzun doğru adıyla değiştirin
-- (sonradan yönetim panelinden de düzenlenebilir).
-- =====================================================================
do $$
declare
  v_school  uuid;
  v_classes int := 0;
  v_tasks   int := 0;
  v_skipped int := 0;
begin
  if to_regclass('public.classes') is null or to_regclass('public.tasks') is null then
    raise notice 'Eski classes/tasks tabloları bulunamadı; aktarılacak veri yok.';
    return;
  end if;

  insert into public.schools (name, city, status)
  values ('Mehmet Ali Büyükhali Lisesi', '', 'approved')
  on conflict do nothing;
  select id into v_school from public.schools
   where lower(btrim(name)) = lower('Mehmet Ali Büyükhali Lisesi') and city = '';

  -- Sınıflar: "9-A", "12-Özel Sınıf" ...
  insert into public.grades (school_id, grade_number, section, school_stage)
  select distinct v_school,
         substring(btrim(name) from '^(\d{1,2})-')::int,
         left(btrim(substring(btrim(name) from '^\d{1,2}-(.+)$')), 20),
         public.stage_for_grade(substring(btrim(name) from '^(\d{1,2})-')::int)
  from public.classes
  where btrim(name) ~ '^\d{1,2}-.+$'
    and substring(btrim(name) from '^(\d{1,2})-')::int between 1 and 12
  on conflict do nothing;
  get diagnostics v_classes = row_count;

  -- Dersler (boş ders adı -> "Genel")
  insert into public.subjects (name)
  select distinct left(coalesce(nullif(btrim(subject), ''), 'Genel'), 80) from public.tasks
  on conflict do nothing;

  -- Sınıf-ders bağlantıları
  insert into public.grade_subjects (grade_id, subject_id)
  select distinct g.id, s.id
  from public.tasks t
  join public.grades g
    on g.school_id = v_school
   and btrim(t.cls) = g.grade_number::text || '-' || g.section
  join public.subjects s
    on lower(btrim(s.name)) = lower(left(coalesce(nullif(btrim(t.subject), ''), 'Genel'), 80))
  on conflict do nothing;

  -- Ödevler (yayında olarak)
  insert into public.assignments
    (school_id, grade_id, subject_id, teacher_id, teacher_name, title, description,
     due_date, status, published_at, created_at, legacy_id)
  select v_school, g.id, s.id, null,
         nullif(left(btrim(coalesce(t.teacher, '')), 100), ''),
         left(coalesce(nullif(btrim(t.subject), ''), 'Ödev') || ' - ' ||
              to_char((t."date")::timestamptz at time zone 'Europe/Istanbul', 'DD.MM.YYYY'), 200),
         left(coalesce(t.task_text, ''), 400000),
         null, 'published', (t."date")::timestamptz, (t."date")::timestamptz, t.id::text
  from public.tasks t
  join public.grades g
    on g.school_id = v_school
   and btrim(t.cls) = g.grade_number::text || '-' || g.section
  join public.subjects s
    on lower(btrim(s.name)) = lower(left(coalesce(nullif(btrim(t.subject), ''), 'Genel'), 80))
  on conflict (legacy_id) do nothing;
  get diagnostics v_tasks = row_count;

  select count(*) into v_skipped from public.tasks t
   where not exists (select 1 from public.assignments a where a.legacy_id = t.id::text);

  raise notice 'Aktarılan yeni sınıf: %, yeni ödev: %, aktarılamayan eski ödev: %',
               v_classes, v_tasks, v_skipped;
end $$;
