-- =====================================================================
-- 001_platform_schema.sql
-- Okul bazlı ödev platformu: şema, kısıtlar, tetikleyiciler, RLS.
-- Yıkıcı işlem YOK: mevcut `classes` ve `tasks` tablolarına dokunmaz.
-- Supabase Dashboard -> SQL Editor içinde bir kez çalıştırın.
-- Tekrar çalıştırmak güvenlidir (idempotent).
-- =====================================================================

-- ---------- Yardımcı: sınıf numarasından kademe ----------------------
create or replace function public.stage_for_grade(n int)
returns text language sql immutable as $$
  select case when n between 1 and 4 then 'ilkokul'
              when n between 5 and 8 then 'ortaokul'
              when n between 9 and 12 then 'lise' end
$$;

-- ---------- schools --------------------------------------------------
create table if not exists public.schools (
  id           uuid primary key default gen_random_uuid(),
  name         text not null check (char_length(btrim(name)) between 3 and 150),
  city         text not null default '' check (char_length(city) <= 80),
  district     text check (district is null or char_length(district) <= 80),
  address      text check (address is null or char_length(address) <= 300),
  status       text not null default 'pending'
               check (status in ('pending','approved','rejected')),
  submitted_by uuid references auth.users(id) on delete set null,
  created_at   timestamptz not null default now()
);
-- Aynı okulun tekrar eklenmesini engeller (ad + şehir, büyük/küçük harf duyarsız)
create unique index if not exists schools_unique_name_city
  on public.schools (lower(btrim(name)), lower(btrim(city)));
create index if not exists schools_status_idx on public.schools (status);

-- ---------- profiles -------------------------------------------------
create table if not exists public.profiles (
  id         uuid primary key references auth.users(id) on delete cascade,
  email      text,
  first_name text not null default '' check (char_length(first_name) <= 60),
  last_name  text not null default '' check (char_length(last_name) <= 60),
  role       text not null default 'teacher' check (role in ('teacher','admin')),
  status     text not null default 'pending'
             check (status in ('pending','approved','rejected','suspended')),
  created_at timestamptz not null default now()
);

-- ---------- school_memberships ---------------------------------------
create table if not exists public.school_memberships (
  id         uuid primary key default gen_random_uuid(),
  school_id  uuid not null references public.schools(id) on delete cascade,
  user_id    uuid not null references auth.users(id) on delete cascade,
  role       text not null default 'teacher' check (role in ('teacher','school_admin')),
  status     text not null default 'pending'
             check (status in ('pending','approved','rejected','suspended')),
  created_at timestamptz not null default now(),
  unique (school_id, user_id)
);
create index if not exists memberships_user_idx on public.school_memberships (user_id);

-- ---------- grades ---------------------------------------------------
-- section: "A", "B", "ATP", "Özel Sınıf" ... ('' = şubesiz). Mevcut sitedeki 9-A gibi sınıflar korunur.
create table if not exists public.grades (
  id           uuid primary key default gen_random_uuid(),
  school_id    uuid not null references public.schools(id) on delete cascade,
  grade_number smallint not null check (grade_number between 1 and 12),
  section      text not null default '' check (char_length(section) <= 20),
  school_stage text not null check (school_stage in ('ilkokul','ortaokul','lise')),
  created_at   timestamptz not null default now(),
  unique (school_id, grade_number, section),
  unique (id, school_id),
  check (school_stage = public.stage_for_grade(grade_number))
);
create index if not exists grades_school_idx on public.grades (school_id);

-- ---------- subjects (genel katalog) + grade_subjects ----------------
create table if not exists public.subjects (
  id         uuid primary key default gen_random_uuid(),
  name       text not null check (char_length(btrim(name)) between 2 and 80),
  created_at timestamptz not null default now()
);
create unique index if not exists subjects_unique_name on public.subjects (lower(btrim(name)));

create table if not exists public.grade_subjects (
  grade_id   uuid not null references public.grades(id) on delete cascade,
  subject_id uuid not null references public.subjects(id) on delete cascade,
  primary key (grade_id, subject_id)
);

-- ---------- teacher_grades (öğretmenin yetkili olduğu sınıflar) ------
create table if not exists public.teacher_grades (
  user_id  uuid not null references auth.users(id) on delete cascade,
  grade_id uuid not null references public.grades(id) on delete cascade,
  primary key (user_id, grade_id)
);

-- ---------- assignments ----------------------------------------------
create table if not exists public.assignments (
  id           uuid primary key default gen_random_uuid(),
  school_id    uuid not null,
  grade_id     uuid not null,
  subject_id   uuid not null references public.subjects(id),
  teacher_id   uuid references auth.users(id) on delete set null,
  teacher_name text check (teacher_name is null or char_length(teacher_name) <= 100),
  title        text not null check (char_length(btrim(title)) between 3 and 200),
  description  text not null default '' check (char_length(description) <= 400000),
  due_date     date,
  status       text not null default 'draft' check (status in ('draft','published','hidden')),
  published_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  legacy_id    text unique,
  -- sınıf kesinlikle aynı okula ait olmalı (okullar karışamaz)
  constraint assignments_grade_school_fk
    foreign key (grade_id, school_id) references public.grades (id, school_id) on delete restrict,
  -- ders bu sınıfta tanımlı olmalı
  constraint assignments_grade_subject_fk
    foreign key (grade_id, subject_id) references public.grade_subjects (grade_id, subject_id) on delete restrict
);
create index if not exists assignments_browse_idx
  on public.assignments (school_id, grade_id, subject_id, status, published_at desc);
create index if not exists assignments_teacher_idx on public.assignments (teacher_id);

-- ---------- materials (bağlantılar) ----------------------------------
create table if not exists public.materials (
  id            uuid primary key default gen_random_uuid(),
  assignment_id uuid not null references public.assignments(id) on delete cascade,
  title         text not null check (char_length(btrim(title)) between 1 and 120),
  url           text not null check (url ~* '^https?://' and char_length(url) <= 500),
  kind          text not null default 'link' check (kind in ('link','file')),
  created_at    timestamptz not null default now()
);
create index if not exists materials_assignment_idx on public.materials (assignment_id);

-- =====================================================================
-- Tetikleyiciler
-- =====================================================================

-- Yeni Auth kullanıcısı -> profil (HER ZAMAN teacher/pending) + okul üyeliği (pending).
-- raw_user_meta_data kullanıcı tarafından değiştirilebilir; bu yüzden rol/statü
-- oradan ASLA okunmaz, yalnızca ad ve okul seçimi alınır.
create or replace function public.handle_new_user()
returns trigger language plpgsql security definer set search_path = public as $$
declare
  m          jsonb := coalesce(new.raw_user_meta_data, '{}'::jsonb);
  v_sid      text  := nullif(btrim(m->>'school_id'), '');
  v_new_name text  := left(nullif(btrim(m->>'new_school_name'), ''), 150);
  v_new_city text  := left(coalesce(btrim(m->>'new_school_city'), ''), 80);
  v_school   uuid;
begin
  insert into public.profiles (id, email, first_name, last_name)
  values (new.id, new.email,
          left(btrim(coalesce(m->>'first_name', '')), 60),
          left(btrim(coalesce(m->>'last_name', '')), 60));

  if v_sid is not null and v_sid ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' then
    select id into v_school from public.schools where id = v_sid::uuid and status = 'approved';
  elsif v_new_name is not null and char_length(v_new_name) >= 3 then
    insert into public.schools (name, city, status, submitted_by)
    values (v_new_name, v_new_city, 'pending', new.id)
    on conflict do nothing
    returning id into v_school;
    if v_school is null then   -- aynı okul zaten var (tekrar ekleme yok)
      select id into v_school from public.schools
      where lower(btrim(name)) = lower(btrim(v_new_name))
        and lower(btrim(city)) = lower(btrim(v_new_city));
    end if;
  end if;

  if v_school is not null then
    insert into public.school_memberships (school_id, user_id) values (v_school, new.id)
    on conflict do nothing;
  end if;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- Ödev: öğretmen adı profilden alınır (tarayıcıdan gelen değere güvenilmez).
create or replace function public.assignments_before()
returns trigger language plpgsql security definer set search_path = public as $$
begin
  if tg_op = 'INSERT' then
    if new.teacher_id is not null then
      select left(first_name, 60) ||
             case when last_name <> '' then ' ' || upper(left(last_name, 1)) || '.' else '' end
        into new.teacher_name
        from public.profiles where id = new.teacher_id;
    end if;
    if auth.uid() is not null then          -- tarayıcı/API üzerinden gelen kayıt
      new.created_at   := now();
      new.updated_at   := now();
      new.published_at := case when new.status = 'published' then now() end;
      new.legacy_id    := null;
    end if;
  else
    new.updated_at   := now();
    new.created_at   := old.created_at;
    new.teacher_name := old.teacher_name;
    new.legacy_id    := old.legacy_id;
    if new.status = 'published' and old.status <> 'published' then
      new.published_at := now();
    elsif new.status <> 'published' and old.status = 'published' then
      new.published_at := old.published_at;
    end if;
  end if;
  return new;
end $$;

drop trigger if exists assignments_before_trg on public.assignments;
create trigger assignments_before_trg before insert or update on public.assignments
  for each row execute function public.assignments_before();

-- =====================================================================
-- Yetki yardımcı fonksiyonları (RLS içinde kullanılır)
-- =====================================================================
create or replace function public.is_admin()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles
                 where id = auth.uid() and role = 'admin' and status = 'approved')
$$;

create or replace function public.is_approved_teacher()
returns boolean language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.profiles
                 where id = auth.uid() and role = 'teacher' and status = 'approved')
$$;

-- Kullanıcı: onaylı öğretmen + onaylı üyelik + onaylı okul + bu sınıfa yetkili
create or replace function public.can_manage_grade(p_grade uuid)
returns boolean language sql stable security definer set search_path = public as $$
  select exists (
    select 1
    from public.profiles p
    join public.school_memberships m on m.user_id = p.id and m.status = 'approved'
    join public.grades g on g.school_id = m.school_id and g.id = p_grade
    join public.schools s on s.id = g.school_id and s.status = 'approved'
    join public.teacher_grades tg on tg.user_id = p.id and tg.grade_id = g.id
    where p.id = auth.uid() and p.role = 'teacher' and p.status = 'approved'
  )
$$;

-- =====================================================================
-- Yetkiler (GRANT) + RLS
-- =====================================================================
alter table public.schools            enable row level security;
alter table public.profiles           enable row level security;
alter table public.school_memberships enable row level security;
alter table public.grades             enable row level security;
alter table public.subjects           enable row level security;
alter table public.grade_subjects     enable row level security;
alter table public.teacher_grades     enable row level security;
alter table public.assignments        enable row level security;
alter table public.materials          enable row level security;

-- Yönetim tabloları: tarayıcıdan YAZMA yok (yalnızca sunucu, service_role)
revoke insert, update, delete on
  public.schools, public.profiles, public.school_memberships, public.grades,
  public.subjects, public.grade_subjects, public.teacher_grades
from anon, authenticated;
-- Ziyaretçi (anon) hiçbir şey yazamaz
revoke insert, update, delete on public.assignments, public.materials from anon;

-- ---- schools
drop policy if exists schools_read on public.schools;
create policy schools_read on public.schools for select to anon, authenticated
  using (status = 'approved' or exists (
    select 1 from public.school_memberships m
    where m.school_id = schools.id and m.user_id = auth.uid()));

-- ---- grades / subjects / grade_subjects
drop policy if exists grades_read on public.grades;
create policy grades_read on public.grades for select to anon, authenticated
  using (exists (select 1 from public.schools s where s.id = grades.school_id and s.status = 'approved'));

drop policy if exists subjects_read on public.subjects;
create policy subjects_read on public.subjects for select to anon, authenticated using (true);

drop policy if exists grade_subjects_read on public.grade_subjects;
create policy grade_subjects_read on public.grade_subjects for select to anon, authenticated
  using (exists (select 1 from public.grades g where g.id = grade_subjects.grade_id));

-- ---- profiles / memberships / teacher_grades: yalnızca kendi kaydı
drop policy if exists profiles_read_own on public.profiles;
create policy profiles_read_own on public.profiles for select to authenticated
  using (id = auth.uid());

drop policy if exists memberships_read_own on public.school_memberships;
create policy memberships_read_own on public.school_memberships for select to authenticated
  using (user_id = auth.uid());

drop policy if exists teacher_grades_read_own on public.teacher_grades;
create policy teacher_grades_read_own on public.teacher_grades for select to authenticated
  using (user_id = auth.uid());

-- ---- assignments
drop policy if exists assignments_read_public on public.assignments;
create policy assignments_read_public on public.assignments for select to anon, authenticated
  using (status = 'published' and exists (
    select 1 from public.schools s where s.id = assignments.school_id and s.status = 'approved'));

drop policy if exists assignments_read_own on public.assignments;
create policy assignments_read_own on public.assignments for select to authenticated
  using (teacher_id = auth.uid());

drop policy if exists assignments_insert on public.assignments;
create policy assignments_insert on public.assignments for insert to authenticated
  with check (teacher_id = auth.uid()
              and status in ('draft','published')
              and public.can_manage_grade(grade_id));

drop policy if exists assignments_update on public.assignments;
create policy assignments_update on public.assignments for update to authenticated
  using (teacher_id = auth.uid() and status in ('draft','published') and public.can_manage_grade(grade_id))
  with check (teacher_id = auth.uid() and status in ('draft','published') and public.can_manage_grade(grade_id));

drop policy if exists assignments_delete on public.assignments;
create policy assignments_delete on public.assignments for delete to authenticated
  using (teacher_id = auth.uid() and status in ('draft','published') and public.is_approved_teacher());

-- ---- materials (ödevin görünürlüğünü izler)
drop policy if exists materials_read on public.materials;
create policy materials_read on public.materials for select to anon, authenticated
  using (exists (select 1 from public.assignments a where a.id = materials.assignment_id));

drop policy if exists materials_insert on public.materials;
create policy materials_insert on public.materials for insert to authenticated
  with check (exists (select 1 from public.assignments a
    where a.id = materials.assignment_id and a.teacher_id = auth.uid()
      and a.status in ('draft','published') and public.can_manage_grade(a.grade_id)));

drop policy if exists materials_update on public.materials;
create policy materials_update on public.materials for update to authenticated
  using (exists (select 1 from public.assignments a
    where a.id = materials.assignment_id and a.teacher_id = auth.uid()
      and a.status in ('draft','published') and public.can_manage_grade(a.grade_id)))
  with check (exists (select 1 from public.assignments a
    where a.id = materials.assignment_id and a.teacher_id = auth.uid()
      and a.status in ('draft','published') and public.can_manage_grade(a.grade_id)));

drop policy if exists materials_delete on public.materials;
create policy materials_delete on public.materials for delete to authenticated
  using (exists (select 1 from public.assignments a
    where a.id = materials.assignment_id and a.teacher_id = auth.uid()
      and a.status in ('draft','published') and public.can_manage_grade(a.grade_id)));

-- NOT: Yönetici işlemleri sunucuda service_role anahtarıyla yapılır
-- (RLS'i atlar); sunucu önce profiles.role='admin' ve status='approved' doğrular.
