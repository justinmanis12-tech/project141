"""Supabase istemcileri, oturum ve yetki denetimi."""
import os
import secrets
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from supabase import create_client

URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
ANON = os.environ.get("SUPABASE_ANON_KEY", "")
SERVICE = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

_public = None
_admin = None


def configured():
    return bool(URL and ANON and SERVICE)


def fresh(key=None):
    """Her çağrıda yeni istemci (oturum durumu istekler arasında paylaşılmasın diye)."""
    return create_client(URL, key or ANON)


def public_db():
    """Anon anahtar: yalnızca RLS'in izin verdiği (yayında) verileri okur."""
    global _public
    if _public is None:
        _public = create_client(URL, ANON)
    return _public


def admin_db():
    """Service role: RLS'i atlar. Yalnızca @admin_required arkasında ve güvenilir sunucu işlemlerinde."""
    global _admin
    if _admin is None:
        _admin = create_client(URL, SERVICE)
    return _admin


def user_db():
    """Giriş yapmış kullanıcının JWT'siyle çalışan istemci: RLS gerçekten uygulanır."""
    c = create_client(URL, ANON)
    c.postgrest.auth(g.token)
    return c


# ----------------------------------------------------------------- oturum
def _verify(token):
    try:
        res = fresh().auth.get_user(token)
        return res.user if res else None
    except Exception:
        return None


def current_user():
    """{'id','email','profile'} ya da None. Profil doğrudan veritabanından okunur."""
    if "user" in g:
        return g.user
    g.user = None
    g.token = None
    token = session.get("at")
    if not token:
        return None
    user = _verify(token)
    if not user and session.get("rt"):
        try:
            res = fresh().auth.refresh_session(session["rt"])
            if res and res.session:
                session["at"] = token = res.session.access_token
                session["rt"] = res.session.refresh_token
                user = res.user or _verify(token)
        except Exception:
            user = None
    if not user:
        session.pop("at", None)
        session.pop("rt", None)
        return None
    try:
        rows = admin_db().table("profiles").select("*").eq("id", user.id).limit(1).execute().data
    except Exception:
        rows = []
    if not rows:
        return None
    g.token = token
    g.user = {"id": str(user.id), "email": user.email, "profile": rows[0]}
    return g.user


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    sent = request.form.get("_csrf", "")
    real = session.get("csrf", "")
    if not real or not secrets.compare_digest(sent.encode(), real.encode()):
        abort(400, "Oturum doğrulanamadı. Sayfayı yenileyip tekrar deneyin.")


# ------------------------------------------------------------- dekoratörler
def login_required(fn):
    @wraps(fn)
    def w(*a, **kw):
        if not current_user():
            flash("Devam etmek için giriş yapın.", "error")
            return redirect(url_for("auth.login"))
        return fn(*a, **kw)
    return w


def teacher_required(fn):
    """Öğretmen hesabı (onay bekleyen de olabilir)."""
    @wraps(fn)
    @login_required
    def w(*a, **kw):
        if current_user()["profile"]["role"] != "teacher":
            abort(403)
        return fn(*a, **kw)
    return w


def approved_teacher_required(fn):
    """Yalnızca onaylanmış öğretmen: pending/rejected/suspended yazamaz."""
    @wraps(fn)
    @teacher_required
    def w(*a, **kw):
        if current_user()["profile"]["status"] != "approved":
            flash("Hesabınız henüz onaylanmadı; ödev yayınlayamazsınız.", "error")
            return redirect(url_for("teacher.dashboard"))
        return fn(*a, **kw)
    return w


def admin_required(fn):
    @wraps(fn)
    @login_required
    def w(*a, **kw):
        p = current_user()["profile"]
        if p["role"] != "admin" or p["status"] != "approved":
            abort(404)  # yönetim panelinin varlığını da gizle
        return fn(*a, **kw)
    return w
