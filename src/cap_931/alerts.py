import logging
import smtplib
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage

from .config import get_settings
from .research.search import search

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    keywords TEXT NOT NULL,
    email TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen (
    watch_id INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
    url TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (watch_id, url)
);
"""


@dataclass
class Watch:
    id: int
    company: str
    keywords: list[str]
    email: str
    created_at: str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(get_settings().alerts_db)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def add_watch(company: str, keywords: list[str], email: str) -> int:
    with closing(_connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO watches (company, keywords, email, created_at) VALUES (?, ?, ?, ?)",
            (company.strip(), ",".join(k.strip() for k in keywords if k.strip()), email.strip(),
             datetime.now().isoformat(timespec="seconds")),
        )
        return cur.lastrowid


def list_watches() -> list[Watch]:
    with closing(_connect()) as conn:
        rows = conn.execute("SELECT id, company, keywords, email, created_at FROM watches ORDER BY id").fetchall()
    return [Watch(r[0], r[1], r[2].split(","), r[3], r[4]) for r in rows]


def delete_watch(watch_id: int) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("DELETE FROM watches WHERE id = ?", (watch_id,))


def check_watch(watch: Watch) -> list[dict]:
    """Search for each keyword and return results not seen before (and mark them seen)."""
    company = watch.company.lower()
    hits: dict[str, dict] = {}
    for keyword in watch.keywords:
        queries = [(f'"{watch.company}" {keyword}', True), (f'"{watch.company}" {keyword} jobs', False)]
        for query, news in queries:
            for r in search(query, max_results=8, news=news, use_cache=False):
                blob = f"{r['title']} {r['snippet']}".lower()
                if r["url"] and company in blob and keyword.lower().split()[0] in blob:
                    hits.setdefault(r["url"], {**r, "keyword": keyword})
    with closing(_connect()) as conn, conn:
        seen = {row[0] for row in conn.execute("SELECT url FROM seen WHERE watch_id = ?", (watch.id,))}
        new = [h for url, h in hits.items() if url not in seen]
        conn.executemany("INSERT OR IGNORE INTO seen (watch_id, url, seen_at) VALUES (?, ?, ?)",
                         [(watch.id, h["url"], datetime.now().isoformat(timespec="seconds")) for h in new])
    return new


def format_digest(watch: Watch, items: list[dict]) -> str:
    lines = [f"{len(items)} new item(s) for {watch.company} matching: {', '.join(watch.keywords)}", ""]
    for item in items:
        date = f" ({item['published']})" if item.get("published") else ""
        lines += [f"- [{item['keyword']}] {item['title']}{date}", f"  {item['url']}", f"  {item['snippet'][:240]}", ""]
    return "\n".join(lines)


def send_email(to: str, subject: str, body: str) -> bool:
    """Send via SMTP (STARTTLS). Returns False if SMTP isn't configured."""
    settings = get_settings()
    if not (settings.smtp_user and settings.smtp_password):
        log.warning("SMTP_USER/SMTP_PASSWORD not set; skipping email to %s", to)
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_user, to, subject
    msg.set_content(body)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
        smtp.starttls()
        smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(msg)
    return True


def run_all() -> None:
    """Check every watch and email digests. Entry point for the scheduled job."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for watch in list_watches():
        items = check_watch(watch)
        log.info("Watch %d (%s): %d new item(s)", watch.id, watch.company, len(items))
        if items:
            sent = send_email(watch.email, f"[Sales alert] {len(items)} new item(s) for {watch.company}",
                              format_digest(watch, items))
            if not sent:
                print(format_digest(watch, items))
