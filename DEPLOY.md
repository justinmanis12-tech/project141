# Ödev Panosu — kurulum ve devreye alma

## 0. Ne değişti (özet)
Mevcut Flask + Supabase + Render mimarisi korundu. `app.py` artık yalnızca giriş noktasıdır; mantık
`views_public.py` (öğrenci), `views_auth.py` (kayıt/giriş), `views_teacher.py`, `views_admin.py`, `db.py` içinde.
Eski `templates/index.html` ve eski `/api/*` uç noktaları **kullanılmıyor** (eski paylaşılan «öğretmen kodu»
varsayılan olarak `ogretmen` idi). Eski `classes` / `tasks` tabloları **silinmez**; veriler yeni tablolara kopyalanır.
Geri dönüş: Render'da önceki commit'i yeniden deploy etmek yeterli.

## 1. Dosyaları repoya koyun
Bu klasörün içeriğini repo köküne kopyalayın (aynı adlı `app.py`, `requirements.txt`, `templates/` üzerine yazılır;
eski `templates/index.html` kalabilir, kullanılmaz). `.gitignore`'a `.env` ve `__pycache__/` eklenmiştir.
Render Start Command: `gunicorn app:app` (değişmedi).

## 2. Render ortam değişkenleri
| Değişken | Açıklama |
|---|---|
| `SUPABASE_URL` | Proje URL'si |
| `SUPABASE_ANON_KEY` | anon/public anahtar (RLS'e tabi) |
| `SUPABASE_SERVICE_ROLE_KEY` | GİZLİ, yalnızca sunucuda (yönetim işlemleri) |
| `SECRET_KEY` | Oturum çerezi imzası. `python -c "import secrets;print(secrets.token_hex(32))"` |
| `SITE_URL` | `https://odev-mab-backend.onrender.com` (e-posta bağlantıları için) |

`TEACHER_CODE` artık kullanılmıyor, silebilirsiniz. **Üç Supabase değişkeninin üçü de zorunlu** (eskiden anon anahtara düşülebiliyordu).
`SECRET_KEY` yoksa uygulama bilerek başlamaz.

## 3. Supabase: SQL migrasyonları (sırayla, SQL Editor)
1. `migrations/001_platform_schema.sql` — yeni tablolar, kısıtlar, tetikleyiciler, RLS.
2. `migrations/002_migrate_legacy_data.sql` — eski sınıf/ödevleri kopyalar. **Önce dosyadaki okul adını düzeltin.**
   Sonunda `NOTICE` ile aktarılan/aktarılamayan sayıları yazar.
3. Siteyi açıp eski ödevlerin göründüğünü doğrulayın, **sonra** `migrations/003_lock_legacy_tables.sql`
   (eski tabloları anon anahtara kapatır; veri silinmez).

## 4. Supabase Auth ayarları
- Authentication → URL Configuration: **Site URL** = `SITE_URL`; **Redirect URLs**'e `https://odev-mab-backend.onrender.com/**` ekleyin
  (şifre sıfırlama ve e-posta onayı bu adrese döner).
- Providers → Email: «Confirm email» açık kalsın (önerilir); minimum şifre uzunluğu 8+.
- Üretimde kendi SMTP'nizi bağlayın (varsayılan Supabase e-postası çok düşük günlük limitlidir).

## 5. İlk yöneticiyi güvenli oluşturma
Yönetici herkese açık formdan atanamaz. Normal öğretmen formuyla kayıt olun, e-postanızı onaylayın, sonra SQL Editor'de:
```sql
update public.profiles set role = 'admin', status = 'approved'
where email = 'sizin@eposta.com';
```
Giriş yapınca `/yonetim` açılır. (Tarayıcıdan `profiles` yazılamaz; rol yalnızca burada/service_role ile değişir.)

## 6. Yayın sonrası kontrol listesi
1. `/api/health` → `supabase_configured: true`.
2. Ana sayfa → okul → sınıf → ders → eski ödevler görünüyor mu?
3. Yönetim → Okullar: sınıflar/dersler tamam mı (yeni okullarda sınıf ve ders ekleyin).
4. Yeni hesapla kayıt → Yönetim'de «Bekleyen» görünür → onayla (sınıf yetkisi ver) → öğretmen ödev oluşturur.
5. `docs/SECURITY_TESTS.sql` senaryolarını çalıştırın (RLS gerçekten çalışıyor mu).
6. Telefonda ve klavyeyle (Tab) gezinmeyi deneyin.
7. Testler: `python -m unittest tests.test_smoke` (sahte istemciyle; gerçek RLS'i test etmez).
