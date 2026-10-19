from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import smtplib
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

from flask import (
    abort, current_app, jsonify, make_response, redirect, render_template_string,
    request, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash

UTC = timezone.utc
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LENGTH = 12
INVITE_LIFETIME_DAYS = 7
RESET_LIFETIME_HOURS = 1
TRIAL_DAYS = 30
MILESTONES = (5, 3, 1)
PUBLIC_PATHS = {"/healthz", "/api/health", "/login", "/logout", "/trial-expired"}
_thread_lock = threading.Lock()
_thread_started = False


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).astimezone(UTC).isoformat(timespec="seconds")


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _email(value: str | None) -> str:
    return (value or "").strip().casefold()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _connect(db_path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _audit(con: sqlite3.Connection, actor: str | None, action: str,
           target: str | None = None, details: str = "") -> None:
    con.execute(
        "INSERT INTO audit_log(id,actor_user_id,action,target_user_id,details,created_at) VALUES(?,?,?,?,?,?)",
        (uuid.uuid4().hex, actor, action, target, details[:500], _stamp()),
    )


def _schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','tester')),
            status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
            created_at TEXT NOT NULL,
            activated_at TEXT,
            trial_started_at TEXT,
            trial_ends_at TEXT,
            last_login_at TEXT,
            disabled_at TEXT
        );
        CREATE TABLE IF NOT EXISTS invitations(
            id TEXT PRIMARY KEY,
            email TEXT,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            revoked_at TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(created_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS password_resets(
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            expires_at TEXT NOT NULL,
            used_at TEXT,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id),
            FOREIGN KEY(created_by) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS trial_notifications(
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            email TEXT NOT NULL,
            milestone_days INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','sent')),
            created_at TEXT NOT NULL,
            sent_at TEXT,
            last_error TEXT,
            UNIQUE(user_id,milestone_days),
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS audit_log(
            id TEXT PRIMARY KEY,
            actor_user_id TEXT,
            action TEXT NOT NULL,
            target_user_id TEXT,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS login_attempts(
            id TEXT PRIMARY KEY,
            key_hash TEXT NOT NULL,
            failed_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_users_trial ON users(role,status,trial_ends_at);
        CREATE INDEX IF NOT EXISTS idx_invites_active ON invitations(used_at,revoked_at,expires_at);
        CREATE INDEX IF NOT EXISTS idx_notifications_pending ON trial_notifications(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts(key_hash,failed_at);
        """)


def _user_by_id(db_path: Path, user_id: str | None):
    if not user_id:
        return None
    with _connect(db_path) as con:
        row = con.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def _public_user(user: dict | None) -> dict | None:
    if not user:
        return None
    ends = _parse_stamp(user.get("trial_ends_at"))
    remaining = None
    if user.get("role") == "tester" and ends:
        remaining = max(0, int((ends - _now()).total_seconds() // 86400 + (1 if (ends - _now()).total_seconds() % 86400 else 0)))
    return {
        "id": user["id"], "email": user["email"], "role": user["role"],
        "status": user["status"], "trial_ends_at": user.get("trial_ends_at"),
        "trial_days_remaining": remaining, "last_login_at": user.get("last_login_at"),
    }


def _is_trial_expired(user: dict) -> bool:
    if user.get("role") != "tester":
        return False
    ends = _parse_stamp(user.get("trial_ends_at"))
    return not ends or _now() >= ends


def _send_email(recipient: str, subject: str, body: str) -> tuple[bool, str]:
    host = os.environ.get("DDS_SMTP_HOST", "").strip()
    if not host:
        return False, "SMTP não configurado"
    try:
        port = int(os.environ.get("DDS_SMTP_PORT", "587"))
        username = os.environ.get("DDS_SMTP_USER", "")
        password = os.environ.get("DDS_SMTP_PASSWORD", "")
        sender = os.environ.get("DDS_SMTP_FROM", username).strip()
        if not sender:
            return False, "DDS_SMTP_FROM não configurado"
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = sender
        message["To"] = recipient
        message.set_content(body)
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as smtp:
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as smtp:
                smtp.ehlo()
                if port in (587, 25):
                    smtp.starttls()
                    smtp.ehlo()
                if username:
                    smtp.login(username, password)
                smtp.send_message(message)
        return True, ""
    except Exception as exc:
        # Never log message bodies, passwords, or one-time links.
        current_app.logger.warning("Falha no envio de aviso de teste (%s).", type(exc).__name__)
        return False, type(exc).__name__


def _process_trial_notifications(db_path: Path) -> None:
    """Create due notices and attempt delivery; unique user/milestone keys avoid duplicates."""
    now = _now()
    with _connect(db_path) as con:
        rows = con.execute(
            "SELECT id,email,trial_ends_at FROM users WHERE role='tester' AND status='active' AND trial_ends_at IS NOT NULL"
        ).fetchall()
        for row in rows:
            end = _parse_stamp(row["trial_ends_at"])
            if not end:
                continue
            seconds = (end - now).total_seconds()
            remaining = int((seconds + 86399) // 86400)
            if remaining in MILESTONES and seconds > 0:
                con.execute(
                    "INSERT OR IGNORE INTO trial_notifications(id,user_id,email,milestone_days,status,created_at) VALUES(?,?,?,?,?,?)",
                    (uuid.uuid4().hex, row["id"], row["email"], remaining, "pending", _stamp(now)),
                )
        pending = con.execute(
            "SELECT n.id,n.email,n.milestone_days,u.trial_ends_at FROM trial_notifications n "
            "JOIN users u ON u.id=n.user_id WHERE n.status='pending' AND u.status='active' ORDER BY n.created_at LIMIT 100"
        ).fetchall()
    for row in pending:
        ends = _parse_stamp(row["trial_ends_at"])
        when = ends.strftime("%d/%m/%Y %H:%M UTC") if ends else "indisponível"
        days = int(row["milestone_days"])
        subject = f"Gerador de DDS: seu teste termina em {days} dia(s)"
        body = (
            f"Olá!\n\nO período de teste do Gerador de DDS termina em aproximadamente {days} dia(s).\n"
            f"Data prevista de término: {when}.\n\n"
            "Entre em contato com o administrador para regularizar o acesso. Seus dados não são apagados quando o teste termina.\n"
        )
        sent, error = _send_email(row["email"], subject, body)
        if sent:
            with _connect(db_path) as con:
                con.execute(
                    "UPDATE trial_notifications SET status='sent',sent_at=?,last_error=NULL WHERE id=? AND status='pending'",
                    (_stamp(), row["id"]),
                )
        else:
            with _connect(db_path) as con:
                con.execute(
                    "UPDATE trial_notifications SET last_error=? WHERE id=? AND status='pending'",
                    (error[:120], row["id"]),
                )


def _notification_worker(db_path: Path) -> None:
    # Single Gunicorn worker is configured in render.yaml; durable unique rows protect normal retries.
    while True:
        try:
            _process_trial_notifications(db_path)
        except Exception as exc:
            current_app.logger.error("Falha na rotina de avisos de teste (%s).", type(exc).__name__)
        time.sleep(3600)


def _start_worker(app, db_path: Path) -> None:
    global _thread_started
    if not app.config.get("DDS_ACCOUNT_AUTH_ENABLED") or app.testing or os.environ.get("DDS_DISABLE_NOTIFICATION_THREAD") == "1":
        return
    with _thread_lock:
        if _thread_started:
            return
        _thread_started = True

    def run():
        with app.app_context():
            _notification_worker(db_path)

    threading.Thread(target=run, name="dds-trial-notifications", daemon=True).start()


def _login_key(email: str, remote_addr: str) -> str:
    pepper = os.environ.get("DDS_SESSION_SECRET", "local-rate-limit-key")
    return hmac.new(pepper.encode(), f"{email}|{remote_addr}".encode(), hashlib.sha256).hexdigest()


def _login_limited(db_path: Path, email: str, remote: str) -> bool:
    key = _login_key(email, remote)
    cutoff = _stamp(_now() - timedelta(minutes=15))
    with _connect(db_path) as con:
        con.execute("DELETE FROM login_attempts WHERE failed_at < ?", (cutoff,))
        count = con.execute(
            "SELECT COUNT(*) FROM login_attempts WHERE key_hash=? AND failed_at>=?", (key, cutoff)
        ).fetchone()[0]
    return count >= 8


def _record_login_failure(db_path: Path, email: str, remote: str) -> None:
    with _connect(db_path) as con:
        con.execute("INSERT INTO login_attempts(id,key_hash,failed_at) VALUES(?,?,?)",
                    (uuid.uuid4().hex, _login_key(email, remote), _stamp()))


def _base_page(title: str, inner: str, csrf: str) -> str:
    return render_template_string("""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#123d76"><meta name="csrf-token" content="{{ csrf }}">
<title>{{ title }} · Gerador de DDS</title>
<style>
:root{color-scheme:light;--blue:#123d76;--line:#dbe3ef;--muted:#667085}*{box-sizing:border-box}
body{margin:0;background:#f3f6fb;color:#1d2939;font:16px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:960px;margin:40px auto;padding:0 16px}.card{background:#fff;border:1px solid var(--line);border-radius:16px;padding:24px;box-shadow:0 8px 28px #17345b0d}
.narrow{max-width:440px;margin:6vh auto}.brand{color:var(--blue);font-size:13px;font-weight:800;letter-spacing:.08em}
h1{font-size:26px;line-height:1.2;margin:.4em 0}h2{font-size:20px;margin:0 0 12px}p{color:#475467}
label{display:block;font-weight:650;margin:14px 0 6px}input,select,textarea{width:100%;padding:12px;border:1px solid #cbd5e1;border-radius:9px;font:inherit;background:#fff}
button,.button{display:inline-block;background:var(--blue);color:#fff;border:0;border-radius:9px;padding:11px 16px;font:inherit;font-weight:700;cursor:pointer;text-decoration:none}
.secondary{background:#eef3fa;color:#193b69}.danger{background:#9b2020}.muted{color:var(--muted);font-size:14px}.error{background:#fff0f0;border:1px solid #facaca;padding:10px;border-radius:8px;color:#8c1e1e}
.success{background:#ecfdf3;border:1px solid #abefc6;padding:10px;border-radius:8px;color:#067647}
.toolbar{display:flex;gap:10px;justify-content:space-between;align-items:center;flex-wrap:wrap;margin-bottom:18px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:10px;border-bottom:1px solid var(--line);vertical-align:top}
small{color:var(--muted)}.actions{display:flex;gap:7px;flex-wrap:wrap}.actions button{font-size:13px;padding:7px 9px}
a{color:var(--blue)}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f9fc;padding:12px;border-radius:8px}
@media(max-width:600px){main{margin:18px auto}.card{padding:18px}h1{font-size:23px}table{display:block;overflow-x:auto;white-space:nowrap}}
</style>
<script>
(function(){const nativeFetch=window.fetch.bind(window);window.fetch=async function(input,init){init=init||{};const method=String(init.method||(input&&input.method)||'GET').toUpperCase();const headers=new Headers(init.headers||(input&&input.headers)||{});const meta=document.querySelector('meta[name="csrf-token"]');if(!['GET','HEAD','OPTIONS'].includes(method)&&meta)headers.set('X-CSRF-Token',meta.content);const res=await nativeFetch(input,{...init,headers});const gate=res.headers.get('X-DDS-Access');if(gate==='login'){location.assign('/login');}else if(gate==='trial-expired'){location.assign('/trial-expired');}return res;};})();
</script></head><body><main>{{ inner|safe }}</main></body></html>""", title=title, inner=inner, csrf=csrf)


def install_account_access(app, data_dir: Path) -> None:
    """Install optional individual accounts. Enable only after secrets are configured."""
    enabled = os.environ.get("DDS_ACCOUNT_AUTH", "").strip() == "1"
    db_path = Path(data_dir) / "accounts.sqlite3"
    app.config["DDS_ACCOUNT_AUTH_ENABLED"] = enabled
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
    app.config["SESSION_COOKIE_NAME"] = "dds_session"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = os.environ.get("DDS_HOSTED", "").strip() == "1"

    if enabled:
        session_secret = os.environ.get("DDS_SESSION_SECRET", "")
        admin_email = _email(os.environ.get("DDS_ADMIN_EMAIL", ""))
        admin_password = os.environ.get("DDS_ADMIN_PASSWORD", "")
        if len(session_secret) < 32:
            raise RuntimeError("Acesso individual bloqueado: DDS_SESSION_SECRET deve ter pelo menos 32 caracteres aleatórios.")
        if not EMAIL_RE.match(admin_email):
            raise RuntimeError("Acesso individual bloqueado: configure DDS_ADMIN_EMAIL com o e-mail inicial do administrador.")
        if len(admin_password) < 16:
            raise RuntimeError("Acesso individual bloqueado: DDS_ADMIN_PASSWORD deve ter pelo menos 16 caracteres.")
        app.secret_key = session_secret
        _schema(db_path)
        with _connect(db_path) as con:
            row = con.execute("SELECT id FROM users WHERE email=?", (admin_email,)).fetchone()
            if not row:
                admin_id = uuid.uuid4().hex
                con.execute(
                    "INSERT INTO users(id,email,password_hash,role,status,created_at,activated_at) VALUES(?,?,?,?,?,?,?)",
                    (admin_id, admin_email, generate_password_hash(admin_password, method="scrypt"),
                     "admin", "active", _stamp(), _stamp()),
                )
                _audit(con, admin_id, "bootstrap_admin_created", admin_id, "Conta inicial criada por variável de ambiente.")
            else:
                # The configured secret is the administrator recovery/rotation mechanism.
                con.execute("UPDATE users SET role='admin',status='active',disabled_at=NULL,password_hash=? WHERE email=?",
                            (generate_password_hash(admin_password, method="scrypt"), admin_email))
        _start_worker(app, db_path)

    def before_request_account_gate():
        if not enabled:
            if request.path == "/admin" or request.path.startswith(("/api/admin/", "/api/account/", "/register/", "/reset/")) or request.path in {"/login", "/logout", "/trial-expired"}:
                abort(404)
            return None

        if not session.get("csrf_token"):
            session["csrf_token"] = secrets.token_urlsafe(32)
        unsafe = request.method not in {"GET", "HEAD", "OPTIONS"}
        if unsafe:
            supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
            expected = session.get("csrf_token", "")
            if not supplied or not expected or not hmac.compare_digest(str(supplied), str(expected)):
                return jsonify({"error": "Solicitação expirada ou inválida. Atualize a página e tente novamente."}), 400

        path = request.path
        is_public = (
            path in PUBLIC_PATHS
            or path.startswith("/register/")
            or path.startswith("/reset/")
            or path.startswith("/static/")
            or path in {"/", "/app.js", "/styles.css", "/favicon.ico"}
            and request.method == "GET"
        )
        if is_public:
            return None

        user = _user_by_id(db_path, session.get("user_id"))
        if not user or user["status"] != "active":
            session.pop("user_id", None)
            if path.startswith("/api/"):
                response = jsonify({"error": "Faça login para continuar.", "code": "LOGIN_REQUIRED"})
                response.status_code = 401
                response.headers["X-DDS-Access"] = "login"
                return response
            return redirect(url_for("dds_login", next=path))

        if user["role"] == "tester" and _is_trial_expired(user):
            if path.startswith("/api/"):
                response = jsonify({"error": "O período de teste terminou. Seus dados foram preservados.", "code": "TRIAL_EXPIRED"})
                response.status_code = 403
                response.headers["X-DDS-Access"] = "trial-expired"
                return response
            if path != "/trial-expired":
                return redirect(url_for("dds_trial_expired"))

        if path == "/admin" or path.startswith("/api/admin/"):
            if user["role"] != "admin":
                if path.startswith("/api/"):
                    return jsonify({"error": "Acesso exclusivo do administrador."}), 403
                abort(403)
        return None

    app.before_request(before_request_account_gate)

    @app.get("/login", endpoint="dds_login")
    def login_page():
        if session.get("user_id"):
            user = _user_by_id(db_path, session.get("user_id"))
            if user and user["status"] == "active":
                return redirect("/admin" if user["role"] == "admin" else "/")
        csrf = session["csrf_token"]
        error = request.args.get("error", "")
        message = "Conta desativada ou período indisponível. Procure o administrador." if error == "disabled" else ""
        inner = """<section class="card narrow"><div class="brand">SEGURANÇA · ROTINA OPERACIONAL</div>
<h1>Entrar no Gerador de DDS</h1><p>Use sua conta individual para acessar a plataforma.</p>
{% if message %}<div class="error">{{ message }}</div>{% endif %}
{% if error and error != 'disabled' %}<div class="error">E-mail ou senha inválidos. Confira os dados e tente novamente.</div>{% endif %}
<form method="post"><input type="hidden" name="csrf_token" value="{{ csrf }}">
<label for="email">E-mail</label><input id="email" name="email" type="email" autocomplete="username" required maxlength="254">
<label for="password">Senha</label><input id="password" name="password" type="password" autocomplete="current-password" required>
<p class="muted">O acesso é individual. Não compartilhe sua senha.</p><button type="submit">Entrar</button></form>
</section>"""
        page = render_template_string(inner, csrf=csrf, error=error, message=message)
        if request.method == "POST":
            # Handled by the separate POST route below.
            abort(405)
        return _base_page("Entrar", page, csrf)

    @app.post("/login", endpoint="dds_login_submit")
    def login_submit():
        email = _email(request.form.get("email"))
        password = request.form.get("password", "")
        remote = request.remote_addr or "unknown"
        if not EMAIL_RE.match(email) or _login_limited(db_path, email, remote):
            _record_login_failure(db_path, email, remote)
            return redirect(url_for("dds_login", error="invalid"))
        with _connect(db_path) as con:
            row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            user = dict(row) if row else None
        # Equalize unknown-account and known-account handling as much as possible.
        valid = bool(user and check_password_hash(user["password_hash"], password))
        if not valid:
            _record_login_failure(db_path, email, remote)
            return redirect(url_for("dds_login", error="invalid"))
        if user["status"] != "active":
            return redirect(url_for("dds_login", error="disabled"))
        session.clear()
        session["csrf_token"] = secrets.token_urlsafe(32)
        session["user_id"] = user["id"]
        session.permanent = True
        with _connect(db_path) as con:
            con.execute("UPDATE users SET last_login_at=? WHERE id=?", (_stamp(), user["id"]))
            con.execute("DELETE FROM login_attempts WHERE key_hash=?", (_login_key(email, remote),))
            _audit(con, user["id"], "login", user["id"])
        destination = request.args.get("next", "/")
        if not destination.startswith("/") or destination.startswith("//") or urlparse(destination).netloc:
            destination = "/"
        if user["role"] == "tester" and _is_trial_expired(user):
            destination = "/trial-expired"
        elif user["role"] == "admin":
            destination = "/admin"
        return redirect(destination)

    @app.post("/logout", endpoint="dds_logout")
    def logout():
        user_id = session.get("user_id")
        if user_id:
            with _connect(db_path) as con:
                _audit(con, user_id, "logout", user_id)
        session.clear()
        return redirect(url_for("dds_login"))

    @app.get("/trial-expired", endpoint="dds_trial_expired")
    def trial_expired_page():
        user = _user_by_id(db_path, session.get("user_id"))
        if not user or user.get("role") != "tester" or not _is_trial_expired(user):
            return redirect("/login")
        inner = """<section class="card narrow"><div class="brand">GERADOR DE DDS</div><h1>Período de teste encerrado</h1>
<p>O seu acesso de teste de 30 dias terminou. Os dados e documentos existentes foram preservados.</p>
<p>Para regularizar o acesso, entre em contato com o administrador da plataforma.</p>
<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="secondary" type="submit">Sair da conta</button></form></section>"""
        return _base_page("Teste encerrado", render_template_string(inner, csrf=session["csrf_token"]), session["csrf_token"])

    @app.get("/register/<token>", endpoint="dds_register_invitation")
    def register_invitation(token: str):
        digest = _token_hash(token)
        with _connect(db_path) as con:
            invite = con.execute("SELECT * FROM invitations WHERE token_hash=?", (digest,)).fetchone()
        if not invite or invite["used_at"] or invite["revoked_at"] or (_parse_stamp(invite["expires_at"]) or _now()) <= _now():
            return _base_page("Convite inválido", '<section class="card narrow"><h1>Convite inválido ou vencido</h1><p>Solicite um novo link ao administrador.</p><a class="button" href="/login">Ir para entrar</a></section>', session["csrf_token"])
        prefilled = invite["email"] or ""
        inner = """<section class="card narrow"><div class="brand">CADASTRO INDIVIDUAL</div><h1>Ativar sua conta</h1>
<p>Seu teste terá duração de 30 dias a partir da ativação. Use uma senha única com pelo menos 12 caracteres.</p>
<form method="post"><input type="hidden" name="csrf_token" value="{{ csrf }}"><input type="hidden" name="token" value="{{ token }}">
<label for="email">E-mail</label><input id="email" name="email" type="email" value="{{ email }}" {% if email %}readonly{% else %}required{% endif %} maxlength="254">
<label for="password">Criar senha</label><input id="password" name="password" type="password" autocomplete="new-password" minlength="12" required>
<label for="confirm">Confirmar senha</label><input id="confirm" name="confirm" type="password" autocomplete="new-password" minlength="12" required>
<button type="submit">Criar conta e iniciar teste</button></form></section>"""
        if request.method == "POST":
            abort(405)
        return _base_page("Criar conta", render_template_string(inner, csrf=session["csrf_token"], token=token, email=prefilled), session["csrf_token"])

    @app.post("/register/<token>", endpoint="dds_register_submit")
    def register_submit(token: str):
        email = _email(request.form.get("email"))
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        now = _now()
        digest = _token_hash(token)
        if not EMAIL_RE.match(email) or len(password) < MIN_PASSWORD_LENGTH or password != confirm:
            return _base_page("Dados inválidos", '<section class="card narrow"><h1>Não foi possível cadastrar</h1><p>Confira o e-mail, use uma senha de pelo menos 12 caracteres e confirme-a corretamente.</p><a class="button" href="javascript:history.back()">Voltar</a></section>', session["csrf_token"]), 400
        with _connect(db_path) as con:
            invite = con.execute("SELECT * FROM invitations WHERE token_hash=?", (digest,)).fetchone()
            if not invite or invite["used_at"] or invite["revoked_at"] or (_parse_stamp(invite["expires_at"]) or now) <= now:
                return _base_page("Convite inválido", '<section class="card narrow"><h1>Convite inválido ou vencido</h1><p>Solicite um novo link ao administrador.</p><a class="button" href="/login">Ir para entrar</a></section>', session["csrf_token"]), 400
            if invite["email"] and invite["email"] != email:
                return _base_page("E-mail diferente", '<section class="card narrow"><h1>O e-mail não corresponde ao convite</h1><p>Use o e-mail informado pelo administrador.</p></section>', session["csrf_token"]), 400
            if con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                return _base_page("Conta existente", '<section class="card narrow"><h1>Este e-mail já tem cadastro</h1><p>Entre na conta existente ou solicite ao administrador um link de redefinição de senha.</p><a class="button" href="/login">Entrar</a></section>', session["csrf_token"]), 409
            user_id = uuid.uuid4().hex
            start = _stamp(now)
            end = _stamp(now + timedelta(days=TRIAL_DAYS))
            con.execute("INSERT INTO users(id,email,password_hash,role,status,created_at,activated_at,trial_started_at,trial_ends_at) VALUES(?,?,?,?,?,?,?,?,?)",
                        (user_id, email, generate_password_hash(password, method="scrypt"), "tester", "active", start, start, start, end))
            con.execute("UPDATE invitations SET used_at=? WHERE id=? AND used_at IS NULL", (start, invite["id"]))
            _audit(con, user_id, "tester_registered", user_id, "Conta criada por convite.")
        session.clear()
        session["user_id"] = user_id
        session["csrf_token"] = secrets.token_urlsafe(32)
        session.permanent = True
        return redirect("/")

    @app.get("/reset/<token>", endpoint="dds_password_reset")
    def password_reset_page(token: str):
        with _connect(db_path) as con:
            reset = con.execute("SELECT * FROM password_resets WHERE token_hash=?", (_token_hash(token),)).fetchone()
        if not reset or reset["used_at"] or (_parse_stamp(reset["expires_at"]) or _now()) <= _now():
            return _base_page("Link inválido", '<section class="card narrow"><h1>Link inválido ou vencido</h1><p>Solicite outro link ao administrador.</p><a class="button" href="/login">Entrar</a></section>', session["csrf_token"])
        inner = """<section class="card narrow"><div class="brand">GERADOR DE DDS</div><h1>Definir nova senha</h1>
<p>Use pelo menos 12 caracteres e não reutilize uma senha de outro serviço.</p>
<form method="post"><input type="hidden" name="csrf_token" value="{{ csrf }}"><input type="hidden" name="token" value="{{ token }}">
<label for="password">Nova senha</label><input id="password" name="password" type="password" minlength="12" required>
<label for="confirm">Confirmar nova senha</label><input id="confirm" name="confirm" type="password" minlength="12" required>
<button type="submit">Salvar nova senha</button></form></section>"""
        if request.method == "POST":
            abort(405)
        return _base_page("Redefinir senha", render_template_string(inner, csrf=session["csrf_token"], token=token), session["csrf_token"])

    @app.post("/reset/<token>", endpoint="dds_password_reset_submit")
    def password_reset_submit(token: str):
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if len(password) < MIN_PASSWORD_LENGTH or password != confirm:
            return _base_page("Dados inválidos", '<section class="card narrow"><h1>Senhas inválidas</h1><p>Use pelo menos 12 caracteres e confirme exatamente a mesma senha.</p></section>', session["csrf_token"]), 400
        now = _stamp()
        with _connect(db_path) as con:
            reset = con.execute("SELECT * FROM password_resets WHERE token_hash=?", (_token_hash(token),)).fetchone()
            if not reset or reset["used_at"] or (_parse_stamp(reset["expires_at"]) or _now()) <= _now():
                return _base_page("Link inválido", '<section class="card narrow"><h1>Link inválido ou vencido</h1><p>Solicite outro ao administrador.</p></section>', session["csrf_token"]), 400
            con.execute("UPDATE users SET password_hash=? WHERE id=?",
                        (generate_password_hash(password, method="scrypt"), reset["user_id"]))
            con.execute("UPDATE password_resets SET used_at=? WHERE id=?", (now, reset["id"]))
            con.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL", (now, reset["user_id"]))
            _audit(con, reset["created_by"], "password_reset_completed", reset["user_id"])
        session.clear()
        session["csrf_token"] = secrets.token_urlsafe(32)
        return redirect("/login")

    @app.get("/api/account/me", endpoint="dds_account_me")
    def account_me():
        user = _user_by_id(db_path, session.get("user_id"))
        if not user:
            return jsonify({"error": "Sessão encerrada."}), 401
        return jsonify({"user": _public_user(user)})

    @app.get("/admin", endpoint="dds_admin_page")
    def admin_page():
        csrf = session["csrf_token"]
        inner = """<div class="toolbar"><div><div class="brand">PAINEL ADMINISTRATIVO</div><h1>Gerador de DDS · Acessos</h1><p class="muted">Contas, convites e período de teste.</p></div>
<form method="post" action="/logout"><input type="hidden" name="csrf_token" value="{{ csrf }}"><button class="secondary" type="submit">Sair</button></form></div>
<section class="card"><div class="toolbar"><div><h2>Convidar testador</h2><p class="muted">O convite vale por 7 dias e inicia os 30 dias quando a pessoa concluir o cadastro.</p></div></div>
<form id="invite-form"><label for="invite-email">E-mail (opcional)</label><input id="invite-email" type="email" maxlength="254" placeholder="pessoa@exemplo.com"><p><button type="submit">Gerar convite individual</button></p></form>
<div id="invite-result" hidden class="success"></div><h2 style="margin-top:18px">Convites pendentes</h2><div id="invitations"><p>Carregando…</p></div><div id="admin-message" aria-live="polite"></div></section>
<section class="card" style="margin-top:18px"><div class="toolbar"><div><h2>Usuários</h2><p class="muted">A expiração bloqueia o uso operacional, sem apagar os dados.</p></div><button id="refresh" class="secondary" type="button">Atualizar</button></div>
<div id="users"><p>Carregando…</p></div></section>
<section class="card" style="margin-top:18px"><h2>Avisos de vencimento</h2><p class="muted">E-mails automáticos dependem de configurar SMTP. Sem SMTP, os avisos ficam pendentes; o painel não informa como enviados.</p><div id="notifications">Carregando…</div></section>
<script>
const csrf=document.querySelector('meta[name="csrf-token"]').content;
async function api(url,options={}){const r=await fetch(url,{...options,headers:{'Content-Type':'application/json',...(options.headers||{})}});let d={};try{d=await r.json()}catch{}if(!r.ok)throw new Error(d.error||'Falha na solicitação.');return d}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function loadUsers(){const box=document.querySelector('#users');try{const d=await api('/api/admin/users');if(!d.users.length){box.textContent='Nenhum testador cadastrado ainda.';return}box.innerHTML='<table><thead><tr><th>Conta</th><th>Status</th><th>Teste termina</th><th>Ações</th></tr></thead><tbody>'+d.users.map(u=>'<tr><td><b>'+esc(u.email)+'</b><br><small>'+esc(u.role)+'</small></td><td>'+esc(u.status_label)+'</td><td>'+esc(u.trial_ends_label)+'</td><td><div class="actions">'+(u.role==='tester'?'<button type="button" data-action="extend" data-id="'+esc(u.id)+'">Prorrogar 30 dias</button><button type="button" class="secondary" data-action="reset" data-id="'+esc(u.id)+'">Link de senha</button><button type="button" class="'+(u.status==='active'?'danger':'secondary')+'" data-action="toggle" data-status="'+esc(u.status)+'" data-id="'+esc(u.id)+'">'+(u.status==='active'?'Desativar':'Reativar')+'</button>':'Administrador')+'</div></td></tr>').join('')+'</tbody></table>'}catch(e){box.innerHTML='<div class="error">'+esc(e.message)+'</div>'}}
async function loadNotifications(){const box=document.querySelector('#notifications');try{const d=await api('/api/admin/notifications');box.innerHTML=d.notifications.length?'<table><thead><tr><th>Conta</th><th>Aviso</th><th>Entrega</th></tr></thead><tbody>'+d.notifications.map(n=>'<tr><td>'+esc(n.email)+'</td><td>'+esc(n.milestone_days)+' dia(s)</td><td>'+esc(n.status_label)+(n.last_error?' · '+esc(n.last_error):'')+'</td></tr>').join('')+'</tbody></table>':'Nenhum aviso de vencimento registrado ainda.'}catch(e){box.textContent=e.message}}
async function loadInvitations(){const box=document.querySelector('#invitations');try{const d=await api('/api/admin/invitations');box.innerHTML=d.invitations.length?'<table><thead><tr><th>E-mail</th><th>Validade / status</th><th>Ação</th></tr></thead><tbody>'+d.invitations.map(i=>'<tr><td>'+esc(i.email||'Sem restrição de e-mail')+'</td><td>'+esc(i.status_label)+'<br><small>'+esc(i.expires_label)+'</small></td><td>'+(i.status==='pending'?'<button class="danger" type="button" data-revoke="'+esc(i.id)+'">Revogar</button>':'—')+'</td></tr>').join('')+'</tbody></table>':'Nenhum convite registrado.'}catch(e){box.textContent=e.message}}
document.querySelector('#invitations').addEventListener('click',async e=>{const b=e.target.closest('button[data-revoke]');if(!b)return;const reason=prompt('Motivo para revogar este convite:');if(!reason||!reason.trim())return;try{await api('/api/admin/invitations/'+encodeURIComponent(b.dataset.revoke)+'/revoke',{method:'POST',body:JSON.stringify({reason:reason.trim()})});await loadInvitations()}catch(err){alert(err.message)}})
document.querySelector('#invite-form').addEventListener('submit',async e=>{e.preventDefault();const box=document.querySelector('#invite-result');const msg=document.querySelector('#admin-message');try{const d=await api('/api/admin/invitations',{method:'POST',body:JSON.stringify({email:document.querySelector('#invite-email').value.trim()})});box.hidden=false;box.innerHTML='<b>Convite criado. Copie e envie à pessoa:</b><pre>'+esc(d.invite_url)+'</pre><button id="copy-invite" type="button" class="secondary">Copiar link</button>';document.querySelector('#copy-invite').onclick=async()=>{try{await navigator.clipboard.writeText(d.invite_url);msg.innerHTML='<p class="success">Link copiado.</p>'}catch{msg.textContent='Selecione e copie o link acima.'}};document.querySelector('#invite-email').value='';await loadNotifications();await loadInvitations()}catch(err){msg.innerHTML='<div class="error">'+esc(err.message)+'</div>'}});
document.querySelector('#users').addEventListener('click',async e=>{const b=e.target.closest('button[data-action]');if(!b)return;const id=b.dataset.id;try{if(b.dataset.action==='extend'){const reason=prompt('Motivo da prorrogação (obrigatório):');if(!reason||!reason.trim())return;await api('/api/admin/users/'+encodeURIComponent(id)+'/extend',{method:'POST',body:JSON.stringify({days:30,reason:reason.trim()})})}else if(b.dataset.action==='toggle'){const active=b.dataset.status==='active';const reason=prompt(active?'Motivo da desativação:':'Motivo da reativação:');if(!reason||!reason.trim())return;await api('/api/admin/users/'+encodeURIComponent(id)+'/status',{method:'POST',body:JSON.stringify({status:active?'disabled':'active',reason:reason.trim()})})}else if(b.dataset.action==='reset'){const d=await api('/api/admin/users/'+encodeURIComponent(id)+'/reset-link',{method:'POST',body:JSON.stringify({})});document.querySelector('#admin-message').innerHTML='<div class="success">Link de redefinição (válido por 1 hora):<pre>'+esc(d.reset_url)+'</pre><button id="copy-reset" type="button" class="secondary">Copiar link</button></div>';document.querySelector('#copy-reset').onclick=async()=>{try{await navigator.clipboard.writeText(d.reset_url)}catch{}}}await loadUsers();await loadNotifications()}catch(err){alert(err.message)}})
document.querySelector('#refresh').addEventListener('click',()=>{loadUsers();loadNotifications();loadInvitations()});loadUsers();loadNotifications();loadInvitations();
</script>"""
        return _base_page("Painel administrativo", render_template_string(inner, csrf=csrf), csrf)

    @app.get("/api/admin/users", endpoint="dds_admin_users")
    def admin_users():
        now = _now()
        with _connect(db_path) as con:
            rows = con.execute("SELECT id,email,role,status,created_at,trial_started_at,trial_ends_at,last_login_at,disabled_at FROM users ORDER BY role,email").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            if item["role"] == "admin":
                status_label, end_label = "Administrador", "Sem vencimento"
            elif item["status"] == "disabled":
                status_label, end_label = "Desativado", "—"
            else:
                ends = _parse_stamp(item["trial_ends_at"])
                if not ends:
                    status_label, end_label = "Revisão necessária", "Não definido"
                elif now >= ends:
                    status_label, end_label = "Teste vencido", ends.strftime("%d/%m/%Y %H:%M UTC")
                else:
                    status_label, end_label = "Em teste", ends.strftime("%d/%m/%Y %H:%M UTC")
            item["status_label"] = status_label
            item["trial_ends_label"] = end_label
            result.append(item)
        return jsonify({"users": result})

    @app.post("/api/admin/invitations", endpoint="dds_admin_create_invitation")
    def admin_create_invitation():
        body = request.get_json(silent=True) or {}
        invite_email = _email(body.get("email"))
        if invite_email and not EMAIL_RE.match(invite_email):
            return jsonify({"error": "Informe um e-mail válido ou deixe o campo vazio."}), 400
        token = secrets.token_urlsafe(32)
        invite_id = uuid.uuid4().hex
        actor = session["user_id"]
        now = _now()
        with _connect(db_path) as con:
            if invite_email and con.execute("SELECT 1 FROM users WHERE email=?", (invite_email,)).fetchone():
                return jsonify({"error": "Já existe uma conta com esse e-mail."}), 409
            con.execute("INSERT INTO invitations(id,email,token_hash,expires_at,created_by,created_at) VALUES(?,?,?,?,?,?)",
                        (invite_id, invite_email or None, _token_hash(token), _stamp(now + timedelta(days=INVITE_LIFETIME_DAYS)), actor, _stamp(now)))
            _audit(con, actor, "invitation_created", None, f"email={invite_email or 'livre'};expires={_stamp(now + timedelta(days=INVITE_LIFETIME_DAYS))}")
        return jsonify({"ok": True, "invite_url": url_for("dds_register_invitation", token=token, _external=True),
                        "expires_at": _stamp(now + timedelta(days=INVITE_LIFETIME_DAYS))})

    @app.get("/api/admin/invitations", endpoint="dds_admin_invitations")
    def admin_invitations():
        now = _now()
        with _connect(db_path) as con:
            rows = con.execute(
                "SELECT id,email,expires_at,used_at,revoked_at,created_at FROM invitations ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            exp = _parse_stamp(item["expires_at"])
            if item["used_at"]:
                item["status"], item["status_label"] = "used", "Utilizado"
            elif item["revoked_at"]:
                item["status"], item["status_label"] = "revoked", "Revogado"
            elif not exp or exp <= now:
                item["status"], item["status_label"] = "expired", "Vencido"
            else:
                item["status"], item["status_label"] = "pending", "Pendente"
            item["expires_label"] = exp.strftime("%d/%m/%Y %H:%M UTC") if exp else "—"
            result.append(item)
        return jsonify({"invitations": result})

    @app.post("/api/admin/invitations/<invite_id>/revoke", endpoint="dds_admin_revoke_invitation")
    def admin_revoke_invitation(invite_id: str):
        body = request.get_json(silent=True) or {}
        reason = str(body.get("reason", "")).strip()
        if not reason:
            return jsonify({"error": "Informe o motivo da revogação."}), 400
        now = _stamp()
        actor = session["user_id"]
        with _connect(db_path) as con:
            row = con.execute("SELECT id,used_at,revoked_at FROM invitations WHERE id=?", (invite_id,)).fetchone()
            if not row:
                return jsonify({"error": "Convite não encontrado."}), 404
            if row["used_at"]:
                return jsonify({"error": "Esse convite já foi utilizado e não pode ser revogado."}), 409
            if row["revoked_at"]:
                return jsonify({"ok": True})
            con.execute("UPDATE invitations SET revoked_at=? WHERE id=?", (now, invite_id))
            _audit(con, actor, "invitation_revoked", None, f"invitation_id={invite_id};reason={reason}")
        return jsonify({"ok": True})

    @app.post("/api/admin/users/<user_id>/extend", endpoint="dds_admin_extend_trial")
    def admin_extend_trial(user_id: str):
        body = request.get_json(silent=True) or {}
        reason = str(body.get("reason", "")).strip()
        days = body.get("days", 30)
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= 180 or not reason:
            return jsonify({"error": "Informe um motivo e um prazo entre 1 e 180 dias."}), 400
        actor = session["user_id"]
        with _connect(db_path) as con:
            user = con.execute("SELECT * FROM users WHERE id=? AND role='tester'", (user_id,)).fetchone()
            if not user:
                return jsonify({"error": "Conta de teste não encontrada."}), 404
            now = _now()
            old = _parse_stamp(user["trial_ends_at"]) or now
            new_end = max(old, now) + timedelta(days=days)
            con.execute("UPDATE users SET trial_ends_at=?,status='active',disabled_at=NULL WHERE id=?",
                        (_stamp(new_end), user_id))
            _audit(con, actor, "trial_extended", user_id, f"days={days};reason={reason};old_end={user['trial_ends_at']};new_end={_stamp(new_end)}")
        return jsonify({"ok": True, "trial_ends_at": _stamp(new_end)})

    @app.post("/api/admin/users/<user_id>/status", endpoint="dds_admin_user_status")
    def admin_user_status(user_id: str):
        body = request.get_json(silent=True) or {}
        status = body.get("status")
        reason = str(body.get("reason", "")).strip()
        if status not in {"active", "disabled"} or not reason:
            return jsonify({"error": "Informe um status válido e o motivo da alteração."}), 400
        actor = session["user_id"]
        with _connect(db_path) as con:
            row = con.execute("SELECT id,role,status,trial_ends_at FROM users WHERE id=?", (user_id,)).fetchone()
            if not row:
                return jsonify({"error": "Usuário não encontrado."}), 404
            if row["role"] == "admin":
                return jsonify({"error": "A conta administrativa não pode ser desativada por este painel."}), 400
            if status == "active" and row["role"] == "tester" and _is_trial_expired(dict(row)):
                return jsonify({"error": "O teste está vencido. Prorrogue o prazo antes de reativar."}), 400
            disabled_at = None if status == "active" else _stamp()
            con.execute("UPDATE users SET status=?,disabled_at=? WHERE id=?", (status, disabled_at, user_id))
            _audit(con, actor, "user_status_changed", user_id, f"status={status};reason={reason}")
        return jsonify({"ok": True})

    @app.post("/api/admin/users/<user_id>/reset-link", endpoint="dds_admin_reset_link")
    def admin_reset_link(user_id: str):
        actor = session["user_id"]
        with _connect(db_path) as con:
            user = con.execute("SELECT id,email,status FROM users WHERE id=?", (user_id,)).fetchone()
            if not user:
                return jsonify({"error": "Usuário não encontrado."}), 404
            if user["status"] != "active":
                return jsonify({"error": "Reative a conta antes de gerar um link de redefinição."}), 400
            token = secrets.token_urlsafe(32)
            reset_id = uuid.uuid4().hex
            now = _now()
            con.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL", (_stamp(now), user_id))
            con.execute("INSERT INTO password_resets(id,user_id,token_hash,expires_at,created_by,created_at) VALUES(?,?,?,?,?,?)",
                        (reset_id, user_id, _token_hash(token), _stamp(now + timedelta(hours=RESET_LIFETIME_HOURS)), actor, _stamp(now)))
            _audit(con, actor, "password_reset_link_created", user_id, "Link de uso único válido por uma hora.")
            return jsonify({"ok": True, "reset_url": url_for("dds_password_reset", token=token, _external=True),
                            "expires_at": _stamp(now + timedelta(hours=RESET_LIFETIME_HOURS))})

    @app.get("/api/admin/notifications", endpoint="dds_admin_notifications")
    def admin_notifications():
        with _connect(db_path) as con:
            rows = con.execute(
                "SELECT n.email,n.milestone_days,n.status,n.created_at,n.sent_at,n.last_error "
                "FROM trial_notifications n JOIN users u ON u.id=n.user_id ORDER BY n.created_at DESC LIMIT 200"
            ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["status_label"] = "Enviado" if item["status"] == "sent" else "Pendente"
            result.append(item)
        return jsonify({"notifications": result, "smtp_configured": bool(os.environ.get("DDS_SMTP_HOST", "").strip())})

    app.extensions["dds_account_db_path"] = db_path

    def decorate_index_response(response):
        if not enabled:
            return response
        user = _user_by_id(db_path, session.get("user_id"))
        if not user or user["status"] != "active" or (user["role"] == "tester" and _is_trial_expired(user)):
            return redirect("/trial-expired" if user and user.get("role") == "tester" else "/login")
        csrf = session["csrf_token"]
        days = _public_user(user).get("trial_days_remaining")
        import html as html_lib
        email = html_lib.escape(user["email"], quote=True)
        admin_link = '<a href="/admin" style="color:#123d76;font-weight:700">Painel de administração</a> · ' if user["role"] == "admin" else ""
        warning = ""
        if user["role"] == "tester" and days is not None and days <= 5:
            warning = f'<span style="color:#9a3412;font-weight:700">Atenção: restam {days} dia(s) de teste.</span>'
        bar = (
            '<div style="display:flex;gap:12px;justify-content:space-between;align-items:center;flex-wrap:wrap;'
            'padding:10px 16px;background:#fff;border-bottom:1px solid #dbe3ef;font:14px system-ui">'
            f'<span>Conta: <b>{email}</b> · {warning}</span>'
            f'<span>{admin_link}<form method="post" action="/logout" style="display:inline;margin:0">'
            f'<input type="hidden" name="csrf_token" value="{html_lib.escape(csrf, quote=True)}">'
            '<button style="padding:5px 10px;border:0;border-radius:6px;background:#123d76;color:#fff;font-weight:700" type="submit">Sair</button></form></span></div>'
        )
        script = """<meta name="csrf-token" content="__CSRF__"><script>
(function(){const nativeFetch=window.fetch.bind(window);window.fetch=async function(input,init){init=init||{};const method=String(init.method||(input&&input.method)||'GET').toUpperCase();const headers=new Headers(init.headers||(input&&input.headers)||{});const meta=document.querySelector('meta[name="csrf-token"]');if(!['GET','HEAD','OPTIONS'].includes(method)&&meta)headers.set('X-CSRF-Token',meta.content);const res=await nativeFetch(input,{...init,headers});const gate=res.headers.get('X-DDS-Access');if(gate==='login')location.assign('/login');else if(gate==='trial-expired')location.assign('/trial-expired');return res;};})();
</script>""".replace("__CSRF__", html_lib.escape(csrf, quote=True))
        text = response.get_data(as_text=True)
        text = text.replace("</head>", script + "</head>", 1)
        text = text.replace("<body>", "<body>" + bar, 1)
        response.set_data(text)
        response.headers["Cache-Control"] = "no-store"
        return response

    app.extensions["dds_decorate_index_response"] = decorate_index_response

    # Create reminders promptly on process start, then retry pending email delivery hourly.
    # The persistent rows and milestone uniqueness prevent routine duplicate sends.
