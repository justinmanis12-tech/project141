"""Öğrenci tarafı: hesap gerektirmez. Yalnızca anon anahtar + RLS (yayında olanlar) kullanılır."""
from flask import Blueprint, abort, render_template, request

from db import public_db
from helpers import STAGES

bp = Blueprint("public", __name__)


def _one(query):
    rows = query.limit(1).execute().data
    return rows[0] if rows else None


def _school_or_404(school_id):
    s = _one(public_db().table("schools").select("id,name,city,district,address")
             .eq("id", str(school_id)).eq("status", "approved"))
    if not s:
        abort(404)
    return s


def _grade_or_404(school_id, grade_id):
    g = _one(public_db().table("grades").select("id,grade_number,section,school_stage")
             .eq("id", str(grade_id)).eq("school_id", str(school_id)))
    if not g:
        abort(404)
    return g


@bp.get("/")
def home():
    q = request.args.get("q", "").strip()[:80]
    qb = public_db().table("schools").select("id,name,city,district").eq("status", "approved")
    if q:
        qb = qb.ilike("name", f"%{q}%")
    schools = qb.order("name").limit(60).execute().data or []
    return render_template("public/home.html", schools=schools, q=q)


@bp.get("/okul/<uuid:school_id>")
def school(school_id):
    s = _school_or_404(school_id)
    grades = (public_db().table("grades").select("id,grade_number,section,school_stage")
              .eq("school_id", s["id"]).order("grade_number").order("section").execute().data or [])
    by_stage = [(key, label, [g for g in grades if g["school_stage"] == key])
                for key, label, _ in STAGES]
    return render_template("public/school.html", school=s, by_stage=[x for x in by_stage if x[2]])


@bp.get("/okul/<uuid:school_id>/sinif/<uuid:grade_id>")
def grade(school_id, grade_id):
    s = _school_or_404(school_id)
    g = _grade_or_404(s["id"], grade_id)
    rows = (public_db().table("grade_subjects").select("subject_id,subjects(id,name)")
            .eq("grade_id", g["id"]).execute().data or [])
    subjects = sorted((r["subjects"] for r in rows if r.get("subjects")), key=lambda x: x["name"])
    return render_template("public/grade.html", school=s, grade=g, subjects=subjects)


@bp.get("/okul/<uuid:school_id>/sinif/<uuid:grade_id>/ders/<uuid:subject_id>")
def subject(school_id, grade_id, subject_id):
    s = _school_or_404(school_id)
    g = _grade_or_404(s["id"], grade_id)
    # Ders bu sınıfa bağlı olmalı
    link = _one(public_db().table("grade_subjects").select("subjects(id,name)")
                .eq("grade_id", g["id"]).eq("subject_id", str(subject_id)))
    if not link or not link.get("subjects"):
        abort(404)
    items = (public_db().table("assignments")
             .select("id,title,description,due_date,published_at,teacher_name,materials(id)")
             .eq("school_id", s["id"]).eq("grade_id", g["id"]).eq("subject_id", str(subject_id))
             .eq("status", "published").order("published_at", desc=True).limit(50).execute().data or [])
    return render_template("public/subject.html", school=s, grade=g,
                           subject=link["subjects"], items=items)


@bp.get("/odev/<uuid:assignment_id>")
def assignment(assignment_id):
    a = _one(public_db().table("assignments")
             .select("*,materials(id,title,url,kind)")
             .eq("id", str(assignment_id)).eq("status", "published"))
    if not a:
        abort(404)
    s = _school_or_404(a["school_id"])
    g = _grade_or_404(s["id"], a["grade_id"])
    sub = _one(public_db().table("subjects").select("id,name").eq("id", a["subject_id"]))
    return render_template("public/assignment.html", a=a, school=s, grade=g, subject=sub)
