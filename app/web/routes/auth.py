"""Telas de entrar e sair."""

from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core import audit
from app.core.db import get_db, utcnow
from app.core.security import get_or_create_csrf_token, mask_email, verify_password
from app.models.user import User
from app.web.auth import SESSION_AUTH_VERSION_KEY, SESSION_USER_KEY, auth_version, verify_csrf
from app.web.templating import render

router = APIRouter()

_GENERIC_ERROR = "E-mail ou senha incorretos."


@router.get("/login")
def login_page(request: Request):
    if request.session.get(SESSION_USER_KEY):
        return RedirectResponse("/", status_code=303)
    return render(request, "login.html")


@router.post("/login", dependencies=[Depends(verify_csrf)])
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    email = email.strip().lower()
    user = db.scalar(select(User).where(func.lower(User.email) == email))
    now = utcnow()

    if user and user.locked_until and user.locked_until > now:
        minutes = max(1, int((user.locked_until - now).total_seconds() // 60) + 1)
        audit.record(db, actor="system", event="login.bloqueado", details={"email": mask_email(email)})
        db.commit()
        return render(request, "login.html",
                      {"error": f"Muitas tentativas. Tente de novo em {minutes} minuto(s).", "email": email},
                      status_code=429)

    if not user or not user.is_active or not verify_password(user.password_hash, password):
        if user:
            user.failed_logins += 1
            if user.failed_logins >= settings.login_max_attempts:
                user.locked_until = now + timedelta(minutes=settings.login_lock_minutes)
                user.failed_logins = 0
        audit.record(db, actor="system", event="login.falhou", details={"email": mask_email(email)})
        db.commit()
        return render(request, "login.html", {"error": _GENERIC_ERROR, "email": email}, status_code=401)

    user.failed_logins = 0
    user.locked_until = None
    user.last_login_at = now
    audit.record(db, actor=f"user:{user.id}", event="login.ok", target_type="user", target_id=user.id)
    db.commit()

    # Sessão nova a cada login (evita reaproveitar uma sessão antiga).
    request.session.clear()
    request.session[SESSION_USER_KEY] = user.id
    request.session[SESSION_AUTH_VERSION_KEY] = auth_version(user)
    get_or_create_csrf_token(request.session)
    return RedirectResponse("/", status_code=303)


@router.post("/logout", dependencies=[Depends(verify_csrf)])
def logout(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get(SESSION_USER_KEY)
    if user_id:
        audit.record(db, actor=f"user:{user_id}", event="logout", target_type="user", target_id=user_id)
        db.commit()
    request.session.clear()
    return RedirectResponse("/login", status_code=303)
