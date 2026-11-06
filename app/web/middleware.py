"""Cabeçalhos de segurança aplicados a todas as respostas."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.config import get_settings

# Só arquivos do próprio painel: nada de scripts ou estilos de terceiros.
_CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if get_settings().session_cookie_secure:
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        if request.url.path not in ("/saude",) and not request.url.path.startswith("/static"):
            # Páginas com dados da loja não devem ficar em cache do navegador.
            response.headers.setdefault("Cache-Control", "no-store")
        return response
