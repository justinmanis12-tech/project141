"""Duman testi: gerçek Supabase OLMADAN, sahte istemciyle yönlendirme, şablonlar ve yetki kapıları.
(RLS politikalarını test ETMEZ — onlar için docs/SECURITY_TESTS.md'deki SQL senaryolarını kullanın.)"""
import os
import sys
import types
import unittest

os.environ.update(SUPABASE_URL="http://x", SUPABASE_ANON_KEY="a", SUPABASE_SERVICE_ROLE_KEY="s", SECRET_KEY="t")

SID, GID, SUB, AID, UID, AUID = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                                 "33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444",
                                 "55555555-5555-4555-8555-555555555555", "66666666-6666-4666-8666-666666666666")
STORE = {}


class Res:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class Q:
    def __init__(self, table):
        self.t, self.f, self.op, self.payload = table, [], "select", None

    def select(self, *a, **k): return self
    def eq(self, k, v): self.f.append(lambda r: str(r.get(k)) == str(v)); return self
    def in_(self, k, vs): self.f.append(lambda r: r.get(k) in vs); return self
    def ilike(self, k, v): return self
    def order(self, *a, **k): return self
    def limit(self, *a): return self
    def insert(self, p): self.op, self.payload = "insert", p; return self
    def update(self, p): self.op, self.payload = "update", p; return self
    def delete(self): self.op = "delete"; return self
    def upsert(self, p, **k): self.op, self.payload = "upsert", p; return self

    def execute(self):
        rows = [r for r in STORE.get(self.t, []) if all(f(r) for f in self.f)]
        if self.op == "insert":
            p = self.payload if isinstance(self.payload, list) else [self.payload]
            return Res([{**x, "id": x.get("id", AID)} for x in p])
        if self.op in ("update", "delete"):
            return Res(rows)
        if self.op == "upsert":
            return Res([])
        return Res(rows, count=len(rows))


class Client:
    def __init__(self): self.postgrest = types.SimpleNamespace(auth=lambda t: None); self.auth = Auth()
    def table(self, n): return Q(n)


class Auth:
    def get_user(self, tok):
        who = {"teacher-tok": UID, "admin-tok": AUID}.get(tok)
        return types.SimpleNamespace(user=types.SimpleNamespace(id=who, email="x@y.com")) if who else None
    def sign_up(self, *a, **k): return types.SimpleNamespace()
    def reset_password_for_email(self, *a, **k): return None
    def sign_in_with_password(self, c):
        if c["password"] != "correct-pass":
            raise Exception("Invalid login credentials")
        return types.SimpleNamespace(session=types.SimpleNamespace(access_token="teacher-tok", refresh_token="r"))


fake = types.ModuleType("supabase")
fake.create_client = lambda url, key: Client()
fake.Client = Client
sys.modules["supabase"] = fake
try:
    import httpx  # noqa: F401
except ModuleNotFoundError:  # sandbox without httpx
    hx = types.ModuleType("httpx")
    hx.HTTPError = Exception
    hx.put = lambda *a, **k: types.SimpleNamespace(status_code=200)
    sys.modules["httpx"] = hx
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as appmod  # noqa: E402


def reset_store(teacher_status="approved"):
    STORE.clear()
    STORE.update({
        "schools": [{"id": SID, "name": "Test Lisesi", "city": "Ankara", "district": None, "address": None,
                     "status": "approved", "created_at": "2026-10-01T00:00:00+00:00"}],
        "grades": [{"id": GID, "school_id": SID, "grade_number": 9, "section": "A", "school_stage": "lise"}],
        "grade_subjects": [{"grade_id": GID, "subject_id": SUB, "subjects": {"id": SUB, "name": "Matematik"}}],
        "subjects": [{"id": SUB, "name": "Matematik"}],
        "assignments": [{"id": AID, "school_id": SID, "grade_id": GID, "subject_id": SUB, "teacher_id": UID,
                         "title": "Türev", "description": "<script>alert(1)</script> Sayfa 5", "status": "published",
                         "due_date": "2026-11-01", "published_at": "2026-10-20T10:00:00+00:00",
                         "created_at": "2026-10-20T10:00:00+00:00", "updated_at": "2026-10-20T10:00:00+00:00",
                         "teacher_name": "Ayşe K.", "materials": [{"id": "m", "title": "Video", "url": "https://e.com", "kind": "link"}]}],
        "profiles": [
            {"id": UID, "role": "teacher", "status": teacher_status, "first_name": "Ayşe", "last_name": "Kaya",
             "email": "a@k.com", "created_at": "2026-10-01T00:00:00+00:00"},
            {"id": AUID, "role": "admin", "status": "approved", "first_name": "Ad", "last_name": "Min",
             "email": "ad@m.in", "created_at": "2026-10-01T00:00:00+00:00"}],
        "school_memberships": [{"user_id": UID, "status": "approved", "school_id": SID, "id": "m1",
                                "schools": {"id": SID, "name": "Test Lisesi", "city": "Ankara", "status": "approved"}}],
        "teacher_grades": [{"user_id": UID, "grade_id": GID}],
    })


class Smoke(unittest.TestCase):
    def setUp(self):
        reset_store()
        self.c = appmod.app.test_client()

    def login(self, tok):
        with self.c.session_transaction() as s:
            s["at"], s["csrf"] = tok, "csrf"

    def test_public_flow(self):
        for url, text in [("/", "Test Lisesi"), (f"/okul/{SID}", "Lise"), (f"/okul/{SID}/sinif/{GID}", "Matematik"),
                          (f"/okul/{SID}/sinif/{GID}/ders/{SUB}", "Türev"), (f"/odev/{AID}", "Sayfa 5")]:
            r = self.c.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertIn(text, r.get_data(as_text=True), url)

    def test_xss_escaped(self):
        html = self.c.get(f"/odev/{AID}").get_data(as_text=True)
        self.assertNotIn("<script>alert(1)", html)
        self.assertIn("&lt;script&gt;", html)

    def test_draft_not_public(self):
        STORE["assignments"][0]["status"] = "draft"
        self.assertEqual(self.c.get(f"/odev/{AID}").status_code, 404)

    def test_wrong_school_grade_404(self):
        other = "99999999-9999-4999-8999-999999999999"
        self.assertEqual(self.c.get(f"/okul/{other}/sinif/{GID}").status_code, 404)

    def test_auth_pages_render(self):
        for u in ("/giris", "/kayit", "/sifremi-unuttum", "/sifre-sifirla"):
            self.assertEqual(self.c.get(u).status_code, 200, u)

    def test_protected_pages_redirect(self):
        for u in ("/ogretmen/", "/ogretmen/odev/yeni", "/yonetim/"):
            r = self.c.get(u)
            self.assertEqual(r.status_code, 302, u)
            self.assertIn("/giris", r.headers["Location"])

    def test_csrf_required(self):
        self.assertEqual(self.c.post("/giris", data={"email": "a@b.co", "password": "x"}).status_code, 400)

    def test_login_wrong_password(self):
        with self.c.session_transaction() as s:
            s["csrf"] = "csrf"
        r = self.c.post("/giris", data={"_csrf": "csrf", "email": "a@b.co", "password": "bad"})
        self.assertEqual(r.status_code, 401)

    def test_login_ok_redirects_to_panel(self):
        with self.c.session_transaction() as s:
            s["csrf"] = "csrf"
        r = self.c.post("/giris", data={"_csrf": "csrf", "email": "a@b.co", "password": "correct-pass"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/ogretmen", r.headers["Location"])

    def test_teacher_dashboard_and_form(self):
        self.login("teacher-tok")
        self.assertIn("Türev", self.c.get("/ogretmen/").get_data(as_text=True))
        self.assertIn("9-A · Matematik", self.c.get("/ogretmen/odev/yeni").get_data(as_text=True))
        self.assertEqual(self.c.get(f"/ogretmen/odev/{AID}/duzenle").status_code, 200)

    def test_pending_teacher_cannot_publish(self):
        reset_store("pending")
        self.login("teacher-tok")
        r = self.c.get("/ogretmen/odev/yeni")
        self.assertEqual(r.status_code, 302)
        r = self.c.post("/ogretmen/odev/yeni", data={"_csrf": "csrf", "title": "x" * 5,
                        "combo": f"{GID}|{SUB}", "status": "published", "description": "d"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.c.get("/ogretmen/").status_code, 200)

    def test_teacher_cannot_use_unauthorised_grade(self):
        self.login("teacher-tok")
        other = "77777777-7777-4777-8777-777777777777"
        r = self.c.post("/ogretmen/odev/yeni", data={"_csrf": "csrf", "title": "Başlık",
                        "combo": f"{other}|{SUB}", "status": "draft", "description": "d"})
        self.assertEqual(r.status_code, 400)

    def test_teacher_blocked_from_admin(self):
        self.login("teacher-tok")
        for u in ("/yonetim/", "/yonetim/ogretmenler", "/yonetim/okullar", "/yonetim/odevler"):
            self.assertEqual(self.c.get(u).status_code, 404, u)
        r = self.c.post(f"/yonetim/ogretmen/{UID}/durum", data={"_csrf": "csrf", "status": "approved"})
        self.assertEqual(r.status_code, 404)

    def test_admin_pages_render(self):
        self.login("admin-tok")
        for u in ("/yonetim/", "/yonetim/ogretmenler", f"/yonetim/ogretmen/{UID}", "/yonetim/okullar",
                  f"/yonetim/okul/{SID}", "/yonetim/odevler"):
            r = self.c.get(u)
            self.assertEqual(r.status_code, 200, u)

    def test_admin_cannot_target_non_teacher(self):
        self.login("admin-tok")
        self.assertEqual(self.c.get(f"/yonetim/ogretmen/{AUID}").status_code, 404)

    def test_security_headers(self):
        r = self.c.get("/")
        self.assertEqual(r.headers["X-Frame-Options"], "DENY")
        self.assertIn("script-src 'self'", r.headers["Content-Security-Policy"])

    def test_health(self):
        self.assertTrue(self.c.get("/api/health").get_json()["supabase_configured"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
