"""Öğretmen paneli. Yazma işlemleri kullanıcının JWT'siyle (user_db) yapılır; RLS ikinci kilit olarak çalışır."""
from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from db import approved_teacher_required, current_user, teacher_required, user_db
from helpers import clean, grade_label, is_uuid, parse_materials

bp = Blueprint("teacher", __name__)


def _my_context():
    """Öğretmenin üyelikleri, yetkili sınıfları ve (sınıf, ders) seçenekleri."""
    db = user_db()
    uid = current_user()["id"]
    mems = (db.table("school_memberships").select("status,school_id,schools(id,name,city,status)")
            .eq("user_id", uid).execute().data or [])
    gids = [r["grade_id"] for r in db.table("teacher_grades").select("grade_id").eq("user_id", uid)
            .execute().data or []]
    grades, options = [], []
    if gids:
        grades = (db.table("grades").select("id,school_id,grade_number,section,school_stage")
                  .in_("id", gids).order("grade_number").order("section").execute().data or [])
        gs = (db.table("grade_subjects").select("grade_id,subject_id,subjects(name)")
              .in_("grade_id", gids).execute().data or [])
        gmap = {g["id"]: g for g in grades}
        for r in gs:
            g = gmap.get(r["grade_id"])
            if g and r.get("subjects"):
                options.append({"value": f"{g['id']}|{r['subject_id']}",
                                "label": f"{grade_label(g)} · {r['subjects']['name']}",
                                "sort": (g["grade_number"], g["section"], r["subjects"]["name"])})
        options.sort(key=lambda o: o["sort"])
    return mems, grades, options


def _names(db, items):
    gids = list({i["grade_id"] for i in items})
    sids = list({i["subject_id"] for i in items})
    grades = {g["id"]: g for g in (db.table("grades").select("id,grade_number,section")
              .in_("id", gids).execute().data or [])} if gids else {}
    subs = {s["id"]: s["name"] for s in (db.table("subjects").select("id,name")
            .in_("id", sids).execute().data or [])} if sids else {}
    for i in items:
        g = grades.get(i["grade_id"])
        i["grade_text"] = grade_label(g) if g else "?"
        i["subject_text"] = subs.get(i["subject_id"], "?")
    return items


@bp.get("/")
@teacher_required
def dashboard():
    db = user_db()
    mems, grades, options = _my_context()
    items = (db.table("assignments")
             .select("id,title,status,due_date,grade_id,subject_id,updated_at")
             .eq("teacher_id", current_user()["id"]).order("updated_at", desc=True)
             .limit(100).execute().data or [])
    return render_template("teacher/dashboard.html", mems=mems, grades=grades,
                           options=options, items=_names(db, items))


def _read_form(options):
    f = request.form
    title, desc = clean(f.get("title"), 200), (f.get("description") or "").strip()[:20000]
    status = f.get("status") if f.get("status") in ("draft", "published") else "draft"
    due = (f.get("due_date") or "").strip() or None
    combo = f.get("combo", "")
    errors = []
    if len(title) < 3:
        errors.append("Başlık en az 3 karakter olmalı.")
    if status == "published" and not desc and not (f.get("materials") or "").strip():
        errors.append("Yayınlamak için açıklama ya da bağlantı ekleyin.")
    if combo not in {o["value"] for o in options}:
        errors.append("Yetkili olduğunuz sınıf ve dersler arasından seçim yapın.")
    if due:
        try:
            from datetime import date
            date.fromisoformat(due)
        except ValueError:
            errors.append("Teslim tarihi geçersiz.")
    mats, merr = parse_materials(f.get("materials"))
    if merr:
        errors.append(merr)
    values = {"title": title, "description": desc, "status": status, "due_date": due or "",
              "combo": combo, "materials": f.get("materials", "")}
    return values, mats, errors


def _save_materials(db, assignment_id, mats):
    db.table("materials").delete().eq("assignment_id", assignment_id).execute()
    if mats:
        db.table("materials").insert([{**m, "assignment_id": assignment_id} for m in mats]).execute()


def _trusted_school(db, grade_id):
    """school_id tarayıcıdan alınmaz; sınıfın kendi kaydından okunur."""
    r = db.table("grades").select("school_id").eq("id", grade_id).limit(1).execute().data
    if not r:
        abort(400)
    return r[0]["school_id"]


@bp.route("/odev/yeni", methods=["GET", "POST"])
@approved_teacher_required
def create():
    _, _, options = _my_context()
    if request.method == "GET":
        return render_template("teacher/form.html", options=options, v={"status": "draft"}, edit=None)
    v, mats, errors = _read_form(options)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("teacher/form.html", options=options, v=v, edit=None), 400
    grade_id, subject_id = v["combo"].split("|")
    db = user_db()
    try:
        row = {"school_id": _trusted_school(db, grade_id), "grade_id": grade_id,
               "subject_id": subject_id, "teacher_id": current_user()["id"],
               "title": v["title"], "description": v["description"],
               "due_date": v["due_date"] or None, "status": v["status"]}
        res = db.table("assignments").insert(row).execute()
        _save_materials(db, res.data[0]["id"], mats)
    except Exception:
        current_app.logger.exception("create assignment failed")
        flash("Ödev kaydedilemedi. Bu sınıf ve ders için yetkiniz olmayabilir.", "error")
        return render_template("teacher/form.html", options=options, v=v, edit=None), 403
    flash("Ödev yayınlandı." if v["status"] == "published" else "Taslak kaydedildi.", "success")
    return redirect(url_for("teacher.dashboard"))


def _own_assignment(db, aid):
    if not is_uuid(aid):
        abort(404)
    r = (db.table("assignments").select("*").eq("id", aid)
         .eq("teacher_id", current_user()["id"]).limit(1).execute().data)
    if not r:
        abort(404)
    return r[0]


@bp.route("/odev/<aid>/duzenle", methods=["GET", "POST"])
@approved_teacher_required
def edit(aid):
    db = user_db()
    a = _own_assignment(db, aid)
    if a["status"] == "hidden":
        flash("Bu ödev yönetici tarafından kaldırıldı; düzenlenemez.", "error")
        return redirect(url_for("teacher.dashboard"))
    _, _, options = _my_context()
    if request.method == "GET":
        mats = db.table("materials").select("title,url").eq("assignment_id", aid).execute().data or []
        v = {"title": a["title"], "description": a["description"], "status": a["status"],
             "due_date": a["due_date"] or "", "combo": f"{a['grade_id']}|{a['subject_id']}",
             "materials": "\n".join(f"{m['title']} | {m['url']}" for m in mats)}
        return render_template("teacher/form.html", options=options, v=v, edit=a)
    v, mats, errors = _read_form(options)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("teacher/form.html", options=options, v=v, edit=a), 400
    grade_id, subject_id = v["combo"].split("|")
    try:
        res = db.table("assignments").update({
            "school_id": _trusted_school(db, grade_id), "grade_id": grade_id, "subject_id": subject_id,
            "title": v["title"], "description": v["description"],
            "due_date": v["due_date"] or None, "status": v["status"]}).eq("id", aid).execute()
        if not res.data:                      # RLS satırı reddetti
            raise PermissionError("update denied")
        _save_materials(db, aid, mats)
    except Exception:
        current_app.logger.exception("update assignment failed")
        flash("Ödev güncellenemedi.", "error")
        return render_template("teacher/form.html", options=options, v=v, edit=a), 403
    flash("Ödev güncellendi.", "success")
    return redirect(url_for("teacher.dashboard"))


@bp.post("/odev/<aid>/durum")
@approved_teacher_required
def set_status(aid):
    new = request.form.get("status")
    if new not in ("draft", "published"):
        abort(400)
    db = user_db()
    a = _own_assignment(db, aid)
    if a["status"] == "hidden":
        abort(403)
    try:
        res = db.table("assignments").update({"status": new}).eq("id", aid).execute()
        if not res.data:
            raise PermissionError("status change denied")
    except Exception:
        current_app.logger.exception("status change failed")
        flash("Durum değiştirilemedi.", "error")
        return redirect(url_for("teacher.dashboard"))
    flash("Ödev yayınlandı." if new == "published" else "Ödev yayından kaldırıldı (taslak).", "success")
    return redirect(url_for("teacher.dashboard"))


@bp.post("/odev/<aid>/sil")
@approved_teacher_required
def delete(aid):
    db = user_db()
    _own_assignment(db, aid)
    try:
        res = db.table("assignments").delete().eq("id", aid).execute()
        if not res.data:
            raise PermissionError("delete denied")
    except Exception:
        current_app.logger.exception("delete failed")
        flash("Ödev silinemedi.", "error")
        return redirect(url_for("teacher.dashboard"))
    flash("Ödev silindi.", "success")
    return redirect(url_for("teacher.dashboard"))
