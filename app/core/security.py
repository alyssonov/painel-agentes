"""Segurança: senhas, criptografia de tokens e proteção CSRF."""

import hmac
import secrets

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_hasher = PasswordHasher()

# Senha mínima para usuários do painel.
MIN_PASSWORD_LENGTH = 10


# --- Senhas ------------------------------------------------------------------

def hash_password(password: str) -> str:
    """Gera o hash Argon2 da senha (é só ele que vai para o banco)."""
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Confere a senha digitada com o hash guardado."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def password_problem(password: str) -> str | None:
    """Retorna uma mensagem se a senha for fraca, ou None se estiver boa."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"A senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres."
    if password.isdigit() or password.isalpha():
        return "Use letras e números (ou símbolos) na senha."
    return None


# --- Criptografia de tokens OAuth -------------------------------------------

def _fernet() -> Fernet:
    key = get_settings().encryption_key
    if not key:
        raise RuntimeError("ENCRYPTION_KEY não configurada no .env.")
    return Fernet(key.encode())


def encrypt_secret(value: str) -> str:
    """Criptografa um token antes de gravar no banco."""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    """Descriptografa um token lido do banco."""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except InvalidToken as exc:
        raise RuntimeError(
            "Não foi possível ler o token: a ENCRYPTION_KEY mudou? Reconecte a conta."
        ) from exc


def generate_keys() -> dict[str, str]:
    """Gera chaves novas para SECRET_KEY e ENCRYPTION_KEY."""
    return {
        "SECRET_KEY": secrets.token_urlsafe(48),
        "ENCRYPTION_KEY": Fernet.generate_key().decode(),
    }


# --- CSRF --------------------------------------------------------------------

CSRF_SESSION_KEY = "csrf_token"


def get_or_create_csrf_token(session: dict) -> str:
    """Token anti-CSRF guardado na sessão e repetido em cada formulário."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def csrf_is_valid(session: dict, submitted: str | None) -> bool:
    expected = session.get(CSRF_SESSION_KEY)
    if not expected or not submitted:
        return False
    return hmac.compare_digest(expected, submitted)


def mask_email(email: str) -> str:
    """Esconde parte do e-mail para gravar em logs (ex.: 'jo***@loja.com')."""
    name, _, domain = email.partition("@")
    return f"{name[:2]}***@{domain}" if domain else "***"
