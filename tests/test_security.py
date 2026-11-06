"""Senhas, criptografia de tokens e CSRF."""

from cryptography.fernet import Fernet

from app.core.security import (
    csrf_is_valid,
    decrypt_secret,
    encrypt_secret,
    generate_keys,
    get_or_create_csrf_token,
    hash_password,
    mask_email,
    password_problem,
    verify_password,
)


def test_senha_hash_e_verificacao():
    h = hash_password("umaSenha123")
    assert h != "umaSenha123"
    assert h.startswith("$argon2")
    assert verify_password(h, "umaSenha123")
    assert not verify_password(h, "outraSenha123")
    assert not verify_password("hash-invalido", "qualquer")


def test_senha_fraca_e_recusada():
    assert password_problem("curta1") is not None
    assert password_problem("somenteletras") is not None
    assert password_problem("1234567890") is not None
    assert password_problem("letras1234") is None


def test_token_criptografado_ida_e_volta():
    enc = encrypt_secret("APP_USR-token-secreto")
    assert "token-secreto" not in enc
    assert decrypt_secret(enc) == "APP_USR-token-secreto"


def test_chaves_geradas_sao_validas():
    keys = generate_keys()
    assert len(keys["SECRET_KEY"]) >= 32
    Fernet(keys["ENCRYPTION_KEY"].encode())  # não deve dar erro


def test_csrf():
    session: dict = {}
    token = get_or_create_csrf_token(session)
    assert get_or_create_csrf_token(session) == token  # estável na sessão
    assert csrf_is_valid(session, token)
    assert not csrf_is_valid(session, "errado")
    assert not csrf_is_valid(session, None)
    assert not csrf_is_valid({}, token)


def test_email_mascarado_nos_logs():
    assert mask_email("joaquim@loja.com") == "jo***@loja.com"
