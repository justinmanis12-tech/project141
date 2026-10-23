"""Yönetim paneli. Her görünüm @admin_required arkasında; veritabanı işlemleri service_role ile
(RLS'i atlar) bu yüzden yetki denetimi yalnızca burada, sunucuda yapılır."""
from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from db import admin_db
from helpers import STAGES, STAGE_LABEL, clean, grade_label, is_dup_error, is_uuid, stage_for
from db import admin_required

bp = Blueprint("admin", __name__)

TEACHER_STATUSES = ("pending", "approved", "rejected", "suspended")


def _count(table, **eq):
    q = admin_db().table(table).select("id", count="exact")
    for k, v in eq.items():
        q = q.eq(k, v)
    return q.limit(1).execute().count or 0


@bp.get("/")
@admin_required
def home():
    db = admin_db()
    pending_t = (db.table("profiles").select("id,first_name,last_name,email,created_at")
                 .eq("role", "teacher").eq("status", "pending").order("created_at").limit(20).execute().data or [])
    pending_s = (db.table("schools").select("id,name,city,created_at").eq("status", "pending")
                 .order("created_at").limit(20).execute().data or [])
    stats = {"teachers_pending": _count("profiles", role="teacher", status="pending"),
             "teachers_approved": _count("profiles", role="teacher", status="approved"),
             "schools_pending": _count("schools", status="pending"),
             "schools_approved": _count("schools", status="approved"),
             "published": _count("assignments", status="published")}
    return render_template("admin/home.html", pending_t=pending_t, pending_s=pending_s, stats=stats)


# ------------------------------------------------------------- öğretmenler
@bp.get("/ogretmenler")
@admin_required
def teachers():
    st = request.args.get("durum", "")
    q = (admin_db().table("profiles").select("id,first_name,last_name,email,status,created_at")
         .eq("role", "teacher").order("created_at", desc=True).limit(300))
    if st in TEACHER_STATUSES:
        q = q.eq("status", st)
    rows = q.execute().data or []
    mems = {}
    if rows:
        for m in (admin_db().table("school_memberships").select("user_id,status,schools(name,city,status)")
                  .in_("user_id", [r["id"] for r in rows]).execute().data or []):
            mems.setdefault(m["user_id"], []).append(m)
    return render_template("admin/teachers.html", rows=rows, mems=mems, st=st, statuses=TEACHER_STATUSES)


def _teacher_or_404(uid):
    if not is_uuid(uid):
        abort(404)
    r = admin_db().table("profiles").select("*").eq("id", uid).eq("role", "teacher").limit(1).execute().data
    if not r:
        abort(404)
    return r[0]


@bp.get("/ogretmen/<uid>")
@admin_required
def teacher(uid):
    db = admin_db()
    t = _teacher_or_404(uid)
    mems = db.table("school_memberships").select("id,status,school_id,schools(id,name,city,status)") \
        .eq("user_id", uid).execute().data or []
    school_ids = [m["school_id"] for m in mems]
    grades = (db.table("grades").select("id,school_id,grade_number,section").in_("school_id", school_ids)
              .order("grade_number").order("section").execute().data or []) if school_ids else []
    allowed = {r["grade_id"] for r in db.table("teacher_grades").select("grade_id").eq("user_id", uid)
               .execute().data or []}
    return render_template("admin/teacher.html", t=t, mems=mems, grades=grades, allowed=allowed,
                           statuses=TEACHER_STATUSES)


@bp.post("/ogretmen/<uid>/durum")
@admin_required
def teacher_status(uid):
    db = admin_db()
    _teacher_or_404(uid)
    new = request.form.get("status")
    if new not in TEACHER_STATUSES:
        abort(400)
    mems = db.table("school_memberships").select("id,school_id,schools(status)").eq("user_id", uid) \
        .execute().data or []
    if new == "approved":
        ok = [m for m in mems if (m.get("schools") or {}).get("status") == "approved"]
        if not ok:
            flash("Önce öğretmenin okulunu onaylayın (Okullar sayfası).", "error")
            return redirect(url_for("admin.teacher", uid=uid))
        db.table("profiles").update({"status": "approved"}).eq("id", uid).eq("role", "teacher").execute()
        db.table("school_memberships").update({"status": "approved"}) \
            .in_("id", [m["id"] for m in ok]).execute()
        if request.form.get("grant_all"):
            gids = [g["id"] for g in db.table("grades").select("id")
                    .in_("school_id", [m["school_id"] for m in ok]).execute().data or []]
            if gids:
                db.table("teacher_grades").upsert([{"user_id": uid, "grade_id": g} for g in gids],
                                                  on_conflict="user_id,grade_id", ignore_duplicates=True).execute()
    else:
        db.table("profiles").update({"status": new}).eq("id", uid).eq("role", "teacher").execute()
        db.table("school_memberships").update({"status": new}).eq("user_id", uid).execute()
    flash("Öğretmen durumu güncellendi.", "success")
    return redirect(request.form.get("back") if request.form.get("back") == url_for("admin.home")
                    else url_for("admin.teacher", uid=uid))


@bp.post("/ogretmen/<uid>/siniflar")
@admin_required
def teacher_grades(uid):
    db = admin_db()
    _teacher_or_404(uid)
    # yalnızca öğretmenin (onaylı/bekleyen) okullarındaki sınıflar kabul edilir
    school_ids = [m["school_id"] for m in db.table("school_memberships").select("school_id")
                  .eq("user_id", uid).execute().data or []]
    valid = {g["id"] for g in db.table("grades").select("id").in_("school_id", school_ids).execute().data or []} \
        if school_ids else set()
    chosen = [g for g in request.form.getlist("grade_ids") if g in valid]
    db.table("teacher_grades").delete().eq("user_id", uid).execute()
    if chosen:
        db.table("teacher_grades").insert([{"user_id": uid, "grade_id": g} for g in chosen]).execute()
    flash("Sınıf yetkileri kaydedildi.", "success")
    return redirect(url_for("admin.teacher", uid=uid))


# ------------------------------------------------------------------ okullar
def _create_grades(school_id, stage_keys):
    rows = [{"school_id": school_id, "grade_number": n, "section": "", "school_stage": key}
            for key, _, rng in STAGES if key in stage_keys for n in rng]
    if rows:
        admin_db().table("grades").upsert(rows, on_conflict="school_id,grade_number,section",
                                          ignore_duplicates=True).execute()


@bp.get("/okullar")
@admin_required
def schools():
    rows = admin_db().table("schools").select("*").order("created_at", desc=True).limit(500).execute().data or []
    order = {"pending": 0, "approved": 1, "rejected": 2}
    rows.sort(key=lambda r: order.get(r["status"], 9))
    return render_template("admin/schools.html", rows=rows, stages=STAGES)


@bp.post("/okullar/ekle")
@admin_required
def school_add():
    name, city = clean(request.form.get("name"), 150), clean(request.form.get("city"), 80)
    if len(name) < 3:
        flash("Okul adı en az 3 karakter olmalı.", "error")
        return redirect(url_for("admin.schools"))
    try:
        res = admin_db().table("schools").insert({
            "name": name, "city": city, "district": clean(request.form.get("district"), 80) or None,
            "status": "approved"}).execute()
        _create_grades(res.data[0]["id"], request.form.getlist("stages"))
    except Exception as e:
        flash("Bu okul zaten kayıtlı." if is_dup_error(e) else "Okul eklenemedi.", "error")
        return redirect(url_for("admin.schools"))
    flash("Okul eklendi.", "success")
    return redirect(url_for("admin.school", sid=res.data[0]["id"]))


def _school_or_404(sid):
    if not is_uuid(sid):
        abort(404)
    r = admin_db().table("schools").select("*").eq("id", sid).limit(1).execute().data
    if not r:
        abort(404)
    return r[0]


@bp.get("/okul/<sid>")
@admin_required
def school(sid):
    db = admin_db()
    s = _school_or_404(sid)
    grades = db.table("grades").select("id,grade_number,section,school_stage").eq("school_id", sid) \
        .order("grade_number").order("section").execute().data or []
    links = {}
    if grades:
        for r in db.table("grade_subjects").select("grade_id,subject_id,subjects(name)") \
                .in_("grade_id", [g["id"] for g in grades]).execute().data or []:
            links.setdefault(r["grade_id"], []).append(r)
    return render_template("admin/school.html", s=s, grades=grades, links=links, stages=STAGES)


@bp.post("/okul/<sid>/duzenle")
@admin_required
def school_edit(sid):
    _school_or_404(sid)
    name, city = clean(request.form.get("name"), 150), clean(request.form.get("city"), 80)
    if len(name) < 3:
        flash("Okul adı en az 3 karakter olmalı.", "error")
        return redirect(url_for("admin.school", sid=sid))
    try:
        admin_db().table("schools").update({
            "name": name, "city": city, "district": clean(request.form.get("district"), 80) or None,
            "address": clean(request.form.get("address"), 300) or None}).eq("id", sid).execute()
    except Exception as e:
        flash("Aynı ad ve şehirde başka bir okul var." if is_dup_error(e) else "Kaydedilemedi.", "error")
        return redirect(url_for("admin.school", sid=sid))
    flash("Okul güncellendi.", "success")
    return redirect(url_for("admin.school", sid=sid))


@bp.post("/okul/<sid>/durum")
@admin_required
def school_status(sid):
    _school_or_404(sid)
    new = request.form.get("status")
    if new not in ("approved", "rejected", "pending"):
        abort(400)
    admin_db().table("schools").update({"status": new}).eq("id", sid).execute()
    if new == "approved":
        _create_grades(sid, request.form.getlist("stages"))
    flash("Okul durumu güncellendi.", "success")
    return redirect(request.form.get("back") if request.form.get("back") == url_for("admin.home")
                    else url_for("admin.school", sid=sid))


@bp.post("/okul/<sid>/sinif")
@admin_required
def grade_add(sid):
    _school_or_404(sid)
    try:
        n = int(request.form.get("grade_number", ""))
    except ValueError:
        n = 0
    stage = stage_for(n)
    if not stage:
        flash("Sınıf numarası 1–12 arasında olmalı.", "error")
        return redirect(url_for("admin.school", sid=sid))
    try:
        admin_db().table("grades").insert({"school_id": sid, "grade_number": n,
                                           "section": clean(request.form.get("section"), 20),
                                           "school_stage": stage}).execute()
        flash("Sınıf eklendi.", "success")
    except Exception as e:
        flash("Bu sınıf zaten var." if is_dup_error(e) else "Sınıf eklenemedi.", "error")
    return redirect(url_for("admin.school", sid=sid))


@bp.post("/okul/<sid>/sinif/<gid>/sil")
@admin_required
def grade_delete(sid, gid):
    if not is_uuid(gid):
        abort(404)
    try:
        admin_db().table("grades").delete().eq("id", gid).eq("school_id", sid).execute()
        flash("Sınıf silindi.", "success")
    except Exception:
        flash("Bu sınıfta ödevler olduğu için silinemez.", "error")
    return redirect(url_for("admin.school", sid=sid))


@bp.post("/okul/<sid>/ders")
@admin_required
def subject_add(sid):
    db = admin_db()
    _school_or_404(sid)
    name = clean(request.form.get("name"), 80)
    if len(name) < 2:
        flash("Ders adı en az 2 karakter olmalı.", "error")
        return redirect(url_for("admin.school", sid=sid))
    valid = {g["id"] for g in db.table("grades").select("id").eq("school_id", sid).execute().data or []}
    chosen = [g for g in request.form.getlist("grade_ids") if g in valid]
    if not chosen:
        flash("En az bir sınıf seçin.", "error")
        return redirect(url_for("admin.school", sid=sid))
    found = db.table("subjects").select("id").eq("name", name).limit(1).execute().data
    if not found:
        try:
            found = db.table("subjects").insert({"name": name}).execute().data
        except Exception:  # büyük/küçük harf farkıyla zaten var
            found = db.table("subjects").select("id").ilike("name", name.replace("%", "").replace("_", r"\_")) \
                .limit(1).execute().data
    if not found:
        flash("Ders eklenemedi.", "error")
        return redirect(url_for("admin.school", sid=sid))
    db.table("grade_subjects").upsert([{"grade_id": g, "subject_id": found[0]["id"]} for g in chosen],
                                      on_conflict="grade_id,subject_id", ignore_duplicates=True).execute()
    flash("Ders sınıflara eklendi.", "success")
    return redirect(url_for("admin.school", sid=sid))


@bp.post("/okul/<sid>/ders/kaldir")
@admin_required
def subject_unlink(sid):
    gid, subj = request.form.get("grade_id", ""), request.form.get("subject_id", "")
    if not (is_uuid(gid) and is_uuid(subj)):
        abort(400)
    try:
        admin_db().table("grade_subjects").delete().eq("grade_id", gid).eq("subject_id", subj).execute()
        flash("Ders sınıftan kaldırıldı.", "success")
    except Exception:
        flash("Bu derste ödevler olduğu için kaldırılamaz.", "error")
    return redirect(url_for("admin.school", sid=sid))


# ------------------------------------------------------------------ ödevler
@bp.get("/odevler")
@admin_required
def assignments():
    st = request.args.get("durum", "")
    q = (admin_db().table("assignments")
         .select("id,title,status,school_id,grade_id,subject_id,teacher_name,created_at")
         .order("created_at", desc=True).limit(100))
    if st in ("draft", "published", "hidden"):
        q = q.eq("status", st)
    rows = q.execute().data or []
    db = admin_db()
    sch = {s["id"]: s["name"] for s in db.table("schools").select("id,name")
           .in_("id", list({r["school_id"] for r in rows})).execute().data or []} if rows else {}
    grd = {g["id"]: g for g in db.table("grades").select("id,grade_number,section")
           .in_("id", list({r["grade_id"] for r in rows})).execute().data or []} if rows else {}
    sub = {s["id"]: s["name"] for s in db.table("subjects").select("id,name")
           .in_("id", list({r["subject_id"] for r in rows})).execute().data or []} if rows else {}
    for r in rows:
        r["school"] = sch.get(r["school_id"], "?")
        r["grade_text"] = grade_label(grd[r["grade_id"]]) if r["grade_id"] in grd else "?"
        r["subject_text"] = sub.get(r["subject_id"], "?")
    return render_template("admin/assignments.html", rows=rows, st=st)


@bp.post("/odev/<aid>/moderasyon")
@admin_required
def assignment_moderate(aid):
    if not is_uuid(aid):
        abort(404)
    action = request.form.get("action")
    if action == "hide":
        new = "hidden"
    elif action == "restore":
        new = "draft"      # öğretmen tekrar kontrol edip yayınlar
    else:
        abort(400)
    admin_db().table("assignments").update({"status": new}).eq("id", aid).execute()
    flash("Ödev yayından kaldırıldı." if new == "hidden" else "Ödev taslağa alındı.", "success")
    return redirect(url_for("admin.assignments"))
