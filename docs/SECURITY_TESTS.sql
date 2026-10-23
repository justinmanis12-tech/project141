-- =====================================================================
-- RLS doğrulama senaryoları (Supabase SQL Editor). Her blok ROLLBACK ile biter, veri değişmez.
-- Önce <...> yerlerini gerçek id'lerle değiştirin:
--   TEACHER_A = onaylı öğretmen, TEACHER_B = başka bir onaylı öğretmen (ya da pending olan),
--   A_ASSIGNMENT = A'nın bir ödevi, B_ASSIGNMENT = B'nin ödevi, GRADE_OTHER = A'nın yetkisi olmayan sınıf.
-- Beklenen sonuç her satırın yanında yazıyor.
-- =====================================================================

-- 1) Ziyaretçi yalnızca YAYINDAKİ ödevleri görür
begin;
set local role anon;
select count(*) as should_be_zero from public.assignments where status <> 'published';  -- 0
select count(*) as should_be_zero from public.schools where status <> 'approved';        -- 0
select count(*) as should_be_zero from public.profiles;                                  -- 0
rollback;

-- 2) Ziyaretçi yazamaz
begin;
set local role anon;
insert into public.schools(name, city, status) values ('Hack Okulu','X','approved');     -- HATA: permission denied
rollback;

-- 3) Öğretmen kendi profilinde rol/statü değiştiremez
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<TEACHER_A>","role":"authenticated"}';
update public.profiles set role = 'admin', status = 'approved' where id = '<TEACHER_A>'; -- HATA: permission denied
rollback;

-- 4) Öğretmen başkasının ödevini değiştiremez / silemez (0 satır etkilenir)
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<TEACHER_A>","role":"authenticated"}';
update public.assignments set title = 'ele geçirildi' where id = '<B_ASSIGNMENT>' returning id;  -- 0 satır
delete from public.assignments where id = '<B_ASSIGNMENT>' returning id;                          -- 0 satır
rollback;

-- 5) Yetkisi olmayan sınıfa / başka okula ödev açamaz
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<TEACHER_A>","role":"authenticated"}';
insert into public.assignments(school_id, grade_id, subject_id, teacher_id, title, status)
select g.school_id, g.id, gs.subject_id, '<TEACHER_A>', 'izinsiz', 'published'
from public.grades g join public.grade_subjects gs on gs.grade_id = g.id
where g.id = '<GRADE_OTHER>' limit 1;                                                     -- HATA: row-level security policy
rollback;

-- 6) teacher_id'yi başkası olarak vermek (taklit) engellenir
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<TEACHER_A>","role":"authenticated"}';
insert into public.assignments(school_id, grade_id, subject_id, teacher_id, title, status)
select g.school_id, g.id, gs.subject_id, '<TEACHER_B>', 'taklit', 'draft'
from public.grades g join public.grade_subjects gs on gs.grade_id = g.id limit 1;         -- HATA: row-level security policy
rollback;

-- 7) Onaysız (pending/suspended) öğretmen ödev açamaz
--    Aynı insert'i TEACHER_PENDING için deneyin                                           -- HATA: row-level security policy

-- 8) Öğretmen kendine sınıf yetkisi / üyelik ekleyemez
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<TEACHER_A>","role":"authenticated"}';
insert into public.teacher_grades(user_id, grade_id) values ('<TEACHER_A>', '<GRADE_OTHER>');   -- HATA: permission denied
update public.school_memberships set status = 'approved' where user_id = '<TEACHER_A>';         -- HATA: permission denied
rollback;

-- 9) Yönetici tarafından kaldırılan (hidden) ödev ne herkese görünür ne öğretmence düzenlenir
--    Bir ödevi service_role ile status='hidden' yapın; anon select -> 0 satır; sahibi update -> 0 satır.

-- 10) Sahte meta veri ile admin olunamaz: kayıt sırasında raw_user_meta_data içine
--     {"role":"admin","status":"approved"} koyulsa bile profil teacher/pending doğar
--     (handle_new_user bu alanları hiç okumaz). Test hesabı oluşturup profiles satırına bakın.
