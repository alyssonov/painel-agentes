"""Login, sessão e proteção CSRF do painel."""

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import csrf_is_valid
from app.models.user import User

SESSION_USER_KEY = "user_id"
SESSION_AUTH_VERSION_KEY = "auth_v"


class LoginRequired(Exception):
    """A página exige login."""


class CsrfFailed(Exception):
    """O formulário não trouxe um token anti-CSRF válido."""


def auth_version(user: User) -> str:
    """Muda quando a senha muda — derruba as sessões antigas automaticamente."""
    return user.password_hash[-16:]


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Dependência: devolve o usuário logado ou manda para a tela de login."""
    user_id = request.session.get(SESSION_USER_KEY)
    user = db.get(User, user_id) if user_id else None
    if (
        user is None
        or not user.is_active
        or request.session.get(SESSION_AUTH_VERSION_KEY) != auth_version(user)
    ):
        request.session.pop(SESSION_USER_KEY, None)
        raise LoginRequired()
    request.state.user = user
    return user


async def verify_csrf(request: Request) -> None:
    """Dependência para toda rota POST: confere o token anti-CSRF."""
    submitted = request.headers.get("X-CSRF-Token")
    if not submitted:
        form = await request.form()
        value = form.get("csrf_token")
        submitted = value if isinstance(value, str) else None
    if not csrf_is_valid(request.session, submitted):
        raise CsrfFailed()


def login_required_handler(request: Request, _exc: LoginRequired) -> Response:
    """Sem login: HTMX recebe um redirecionamento de página inteira; o resto, um 303."""
    if request.headers.get("HX-Request"):
        return Response(status_code=200, headers={"HX-Redirect": "/login"})
    return RedirectResponse("/login", status_code=303)


def csrf_failed_handler(_request: Request, _exc: CsrfFailed) -> Response:
    return Response("Sessão expirada ou formulário inválido. Recarregue a página e tente de novo.",
                    status_code=403, media_type="text/plain; charset=utf-8")
