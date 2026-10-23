"""Ortak sabitler ve küçük yardımcılar."""
import re
from urllib.parse import urlparse

# Kademeler (arayüz adları Türkçe; ileride çeviri eklemek için tek yer)
STAGES = [
    ("ilkokul", "İlkokul", range(1, 5)),
    ("ortaokul", "Ortaokul", range(5, 9)),
    ("lise", "Lise", range(9, 13)),
]
STAGE_LABEL = {k: label for k, label, _ in STAGES}
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def stage_for(n):
    for key, _, rng in STAGES:
        if n in rng:
            return key
    return None


def grade_label(g):
    n, s = g["grade_number"], (g.get("section") or "")
    return f"{n}-{s}" if s else f"{n}. Sınıf"


def clean(text, max_len):
    return re.sub(r"[ \t]+", " ", (text or "").strip())[:max_len]


def is_uuid(v):
    return bool(v) and bool(UUID_RE.match(str(v)))


def parse_materials(raw, limit=10):
    """Her satır: 'Başlık | https://...' ya da yalnızca URL. Döner: (liste, hata)."""
    out = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        title, _, url = line.rpartition("|")
        url = url.strip()
        title = title.strip()
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.netloc or len(url) > 500:
            return None, f"Geçersiz bağlantı: «{line[:60]}». Bağlantı http:// veya https:// ile başlamalı."
        out.append({"title": (title or p.netloc)[:120], "url": url, "kind": "link"})
        if len(out) > limit:
            return None, f"En fazla {limit} bağlantı ekleyebilirsiniz."
    return out, None


def is_dup_error(e):
    s = str(e).lower()
    return "23505" in s or "duplicate" in s
