"""Öğretmen kaydı, giriş, çıkış, şifre sıfırlama (Supabase Auth)."""
import os

import httpx
from flask import (Blueprint, current_app, flash, redirect, render_template,
                   request, session, url_for)

import db
from helpers import EMAIL_RE, clean, is_uuid

bp = Blueprint("auth", __name__)


def _site_url():
    return (os.environ.get("SITE_URL") or request.url_root).rstrip("/")


def _approved_schools():
    return (db.public_db().table("schools").select("id,name,city").eq("status", "approved")
            .order("name").limit(1000).execute().data or [])


@bp.route("/kayit", methods=["GET", "POST"])
def register():
    schools = _approved_schools()
    if request.method == "GET":
        return render_template("auth/register.html", schools=schools, v={})

    f = request.form
    v = {k: f.get(k, "") for k in ("first_name", "last_name", "email", "school_id",
                                    "new_school_name", "new_school_city")}
    first, last = clean(f.get("first_name"), 60), clean(f.get("last_name"), 60)
    email = (f.get("email") or "").strip().lower()
    pw, pw2 = f.get("password", ""), f.get("password2", "")
    school_id = f.get("school_id", "")
    new_name, new_city = clean(f.get("new_school_name"), 150), clean(f.get("new_school_city"), 80)

    errors = []
    if not first or not last:
        errors.append("Ad ve soyad gerekli.")
    if not EMAIL_RE.match(email):
        errors.append("Geçerli bir e-posta adresi girin.")
    if len(pw) < 8 or len(pw) > 72:
        errors.append("Şifre 8–72 karakter olmalı.")
    if pw != pw2:
        errors.append("Şifreler eşleşmiyor.")
    meta = {"first_name": first, "last_name": last}
    if school_id == "__new__":
        if len(new_name) < 3:
            errors.append("Yeni okulun adını yazın (en az 3 karakter).")
        else:
            meta.update(new_school_name=new_name, new_school_city=new_city)
    elif is_uuid(school_id) and any(s["id"] == school_id.lower() for s in schools):
        meta["school_id"] = school_id.lower()
    else:
        errors.append("Listeden bir okul seçin ya da yeni okul başvurusu yapın.")

    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("auth/register.html", schools=schools, v=v), 400

    try:
        db.fresh().auth.sign_up({
            "email": email, "password": pw,
            "options": {"data": meta, "email_redirect_to": _site_url() + url_for("auth.login")},
        })
    except Exception as e:  # Supabase hata metnini kullanıcıya olduğu gibi göstermiyoruz
        current_app.logger.warning("sign_up failed: %s", e)
        msg = str(e).lower()
        if "password" in msg:
            flash("Şifre çok zayıf. Daha uzun ve karmaşık bir şifre deneyin.", "error")
        elif "rate" in msg or "too many" in msg:
            flash("Çok fazla deneme yapıldı. Biraz bekleyip tekrar deneyin.", "error")
        else:
            flash("Kayıt tamamlanamadı. Bilgilerinizi kontrol edip tekrar deneyin.", "error")
        return render_template("auth/register.html", schools=schools, v=v), 400

    flash("Kaydınız alındı. E-posta onayı açıksa gelen kutunuzdaki bağlantıya tıklayın; "
          "ardından yönetici onayını bekleyin. Onaydan önce ödev yayınlayamazsınız.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/giris", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("auth/login.html")
    email = (request.form.get("email") or "").strip().lower()
    pw = request.form.get("password", "")
    if not EMAIL_RE.match(email) or not pw:
        flash("E-posta ve şifre gerekli.", "error")
        return render_template("auth/login.html"), 400
    try:
        res = db.fresh().auth.sign_in_with_password({"email": email, "password": pw})
        sess = res.session
        if not sess:
            raise ValueError("no session")
    except Exception as e:
        msg = str(e).lower()
        if "confirm" in msg:
            flash("E-posta adresiniz henüz onaylanmadı. Gelen kutunuzdaki bağlantıya tıklayın.", "error")
        elif "rate" in msg or "too many" in msg:
            flash("Çok fazla deneme yapıldı. Biraz bekleyip tekrar deneyin.", "error")
        else:
            flash("E-posta veya şifre hatalı.", "error")
        return render_template("auth/login.html"), 401
    session.clear()                       # oturum sabitlemeyi önle
    session.permanent = True
    session["at"], session["rt"] = sess.access_token, sess.refresh_token
    db.csrf_token()
    prof = db.current_user()
    if prof and prof["profile"]["role"] == "admin":
        return redirect(url_for("admin.home"))
    return redirect(url_for("teacher.dashboard"))


@bp.post("/cikis")
def logout():
    session.clear()
    flash("Çıkış yaptınız.", "success")
    return redirect(url_for("public.home"))


@bp.route("/sifremi-unuttum", methods=["GET", "POST"])
def forgot():
    if request.method == "GET":
        return render_template("auth/forgot.html")
    email = (request.form.get("email") or "").strip().lower()
    if EMAIL_RE.match(email):
        try:
            db.fresh().auth.reset_password_for_email(
                email, {"redirect_to": _site_url() + url_for("auth.reset")})
        except Exception as e:
            current_app.logger.warning("reset mail failed: %s", e)
    # Hesap var mı yok mu belli etmeyen, her zaman aynı mesaj
    flash("Bu adres kayıtlıysa şifre sıfırlama bağlantısı gönderildi.", "success")
    return redirect(url_for("auth.login"))


@bp.route("/sifre-sifirla", methods=["GET", "POST"])
def reset():
    """E-postadaki bağlantı jetonu URL parçasında (#access_token=...) gelir;
    static/reset.js bunu forma taşır, sunucu Supabase'e iletir."""
    if request.method == "GET":
        return render_template("auth/reset.html")
    token = request.form.get("access_token", "")
    pw, pw2 = request.form.get("password", ""), request.form.get("password2", "")
    if not token:
        flash("Sıfırlama bağlantısı geçersiz ya da süresi dolmuş. Yeni bir bağlantı isteyin.", "error")
        return redirect(url_for("auth.forgot"))
    if len(pw) < 8 or len(pw) > 72 or pw != pw2:
        flash("Şifre 8–72 karakter olmalı ve iki alan eşleşmeli.", "error")
        return render_template("auth/reset.html"), 400
    try:
        r = httpx.put(f"{db.URL}/auth/v1/user", timeout=10,
                      headers={"apikey": db.ANON, "Authorization": f"Bearer {token}"},
                      json={"password": pw})
        ok = r.status_code == 200
    except httpx.HTTPError:
        ok = False
    if not ok:
        flash("Şifre değiştirilemedi. Bağlantının süresi dolmuş olabilir; yeni bir bağlantı isteyin.", "error")
        return redirect(url_for("auth.forgot"))
    flash("Şifreniz değiştirildi. Yeni şifrenizle giriş yapabilirsiniz.", "success")
    return redirect(url_for("auth.login"))
