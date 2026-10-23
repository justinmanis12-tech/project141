"""Odev platformu — Flask giriş noktası (Render: `gunicorn app:app`)."""
import os
from datetime import datetime, timedelta

from flask import Flask, jsonify, render_template, request, session
from markupsafe import Markup, escape

import db
from helpers import STAGE_LABEL, grade_label

DEBUG = os.environ.get("FLASK_DEBUG") == "1"

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or ("dev-only-key" if DEBUG else None)
if not app.secret_key:
    raise RuntimeError("SECRET_KEY ortam değişkeni ayarlanmalı (Render > Environment).")

app.config.update(
    MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not DEBUG,
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

from views_public import bp as public_bp      # noqa: E402
from views_auth import bp as auth_bp          # noqa: E402
from views_teacher import bp as teacher_bp    # noqa: E402
from views_admin import bp as admin_bp        # noqa: E402

app.register_blueprint(public_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(teacher_bp, url_prefix="/ogretmen")
app.register_blueprint(admin_bp, url_prefix="/yonetim")


@app.before_request
def csrf_protect():
    if request.method == "POST" and request.endpoint != "static":
        db.check_csrf()


@app.after_request
def security_headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
        "img-src 'self' data:; frame-ancestors 'none'; form-action 'self'"
    )
    if request.endpoint not in ("static", "public.home", "public.school",
                                "public.grade", "public.subject", "public.assignment"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.context_processor
def inject():
    return {
        "me": db.current_user,
        "grade_label": grade_label,
        "STAGE_LABEL": STAGE_LABEL,
        "csrf_input": lambda: Markup(
            f'<input type="hidden" name="_csrf" value="{escape(db.csrf_token())}">'),
    }


@app.template_filter("tr_date")
def tr_date(value):
    if not value:
        return ""
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return d.strftime("%d.%m.%Y")
    except ValueError:
        return str(value)


@app.template_filter("excerpt")
def excerpt(text, n=160):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n].rstrip() + "…"


@app.get("/api/health")
def health():
    return jsonify(version="v4-platform", supabase_configured=db.configured())


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def client_error(e):
    code = getattr(e, "code", 400)
    msg = {403: "Bu sayfaya erişim izniniz yok.", 404: "Sayfa bulunamadı.",
           413: "Gönderilen veri çok büyük."}.get(code, getattr(e, "description", "Geçersiz istek."))
    return render_template("error.html", code=code, message=msg), code


@app.errorhandler(500)
def server_error(e):
    return render_template("error.html", code=500,
                           message="Beklenmeyen bir hata oluştu. Lütfen daha sonra tekrar deneyin."), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=DEBUG)
