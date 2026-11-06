"""Comandos de administração. Uso: python -m app.cli <comando>

Comandos:
  init-env     cria o .env a partir do .env.example, com chaves novas
  gen-keys     mostra chaves novas (SECRET_KEY e ENCRYPTION_KEY)
  create-user  cria um usuário do painel (ou redefine a senha)
  seed-demo    recria os dados de simulação (só com DRY_RUN=true)
"""

import argparse
import getpass
import sys
from pathlib import Path

from sqlalchemy import func, select

from app.core.security import generate_keys, hash_password, password_problem


def cmd_gen_keys(_args) -> int:
    for name, value in generate_keys().items():
        print(f"{name}={value}")
    return 0


def cmd_init_env(_args) -> int:
    env, example = Path(".env"), Path(".env.example")
    if env.exists():
        print(".env já existe — nada foi alterado. (Para chaves novas: make keys)")
        return 0
    keys = generate_keys()
    lines = []
    for line in example.read_text(encoding="utf-8").splitlines():
        name = line.split("=", 1)[0]
        lines.append(f"{name}={keys[name]}" if name in keys else line)
    env.write_text("\n".join(lines) + "\n", encoding="utf-8")
    env.chmod(0o600)  # só o dono do arquivo pode ler
    print(".env criado com chaves novas. Guarde uma cópia segura da ENCRYPTION_KEY.")
    return 0


def cmd_create_user(args) -> int:
    from app.core import audit
    from app.core.db import get_sessionmaker
    from app.models.user import User

    email = (args.email or input("E-mail: ")).strip().lower()
    name = (args.name or input("Nome: ")).strip()
    if "@" not in email or not name:
        print("E-mail e nome são obrigatórios.")
        return 1
    password = getpass.getpass("Senha (mínimo 10 caracteres): ")
    if problem := password_problem(password):
        print(problem)
        return 1
    if getpass.getpass("Repita a senha: ") != password:
        print("As senhas não conferem.")
        return 1

    with get_sessionmaker()() as db:
        user = db.scalar(select(User).where(func.lower(User.email) == email))
        if user:
            user.password_hash = hash_password(password)
            user.name = name
            user.failed_logins = 0
            user.locked_until = None
            event = "usuario.senha_redefinida"
        else:
            user = User(email=email, name=name, password_hash=hash_password(password))
            db.add(user)
            event = "usuario.criado"
        db.flush()
        audit.record(db, actor="system", event=event, target_type="user", target_id=user.id)
        db.commit()
    print("Usuário salvo." if event == "usuario.criado" else "Senha redefinida.")
    return 0


def cmd_seed_demo(_args) -> int:
    from app.core.db import get_sessionmaker
    from app.fake_data.seed import SeedRefused, seed_demo

    with get_sessionmaker()() as db:
        try:
            result = seed_demo(db)
        except SeedRefused as exc:
            print(f"Não carregado: {exc}")
            return 1
    print("Simulação carregada: " + ", ".join(f"{v} {k}" for k, v in result.items()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli", description="Administração do painel")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-env").set_defaults(func=cmd_init_env)
    sub.add_parser("gen-keys").set_defaults(func=cmd_gen_keys)
    p_user = sub.add_parser("create-user")
    p_user.add_argument("--email")
    p_user.add_argument("--name")
    p_user.set_defaults(func=cmd_create_user)
    sub.add_parser("seed-demo").set_defaults(func=cmd_seed_demo)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
