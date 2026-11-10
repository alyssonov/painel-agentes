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


def cmd_diag_envios(_args) -> int:
    """Diagnóstico dos envios do Mercado Livre (sem dados de clientes: só situações e datas)."""
    from collections import Counter

    from sqlalchemy import select

    from app.core.db import get_sessionmaker, utcnow
    from app.core.timeutil import to_local
    from app.integrations.errors import IntegrationError
    from app.integrations.mercadolivre.mapping import left_the_store
    from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
    from app.integrations.mercadolivre.client import MLClient
    from app.models import Order, Shipment, SyncRun
    from app.models.enums import Channel

    def when(dt):
        return to_local(dt).strftime("%d/%m %H:%M") if dt else "—"

    sm = get_sessionmaker()
    with sm() as db:
        if get_ml_account(db) is None:
            print("Mercado Livre não está conectado.")
            return 1
        print("== Últimas leituras do Mercado Livre ==")
        for run in db.scalars(select(SyncRun).where(SyncRun.provider == "ml").order_by(SyncRun.id.desc()).limit(5)):
            print(f"  {when(run.started_at)}  {run.status:8}  {run.counts}  {run.error or ''}")
        rows = list(db.scalars(select(Shipment).where(Shipment.channel == Channel.ML)))
        print(f"\n== Envios guardados: {len(rows)} ==")
        for status, count in Counter(r.status for r in rows).most_common():
            print(f"  {status:16} {count}")
        out = sum(1 for r in rows if r.status == "ready_to_ship" and left_the_store(r.status, r.substatus))
        print(f"  (dos ready_to_ship, {out} já foram coletados / estão no centro do Mercado Livre)")
        now = utcnow()
        late = sorted((r for r in rows if r.status in ("pending", "handling", "ready_to_ship")
                       and not left_the_store(r.status, r.substatus)
                       and r.dispatch_deadline and r.dispatch_deadline <= now), key=lambda r: r.checked_at)
        print(f"\nAguardando despacho com prazo vencido: {len(late)}")
        if late:
            print(f"  conferido há mais tempo: {when(late[0].checked_at)} · mais recente: {when(late[-1].checked_at)}")
        sample = late[:3]
        if not sample:
            return 0
        print("\n== Amostra (o que o Mercado Livre responde AGORA) ==")
        with MLClient(MLTokenProvider(sm)) as client:
            for r in sample:
                order = db.scalar(select(Order).where(Order.channel == Channel.ML, Order.external_id == r.order_ref))
                print(f"\nEnvio {r.shipment_id} (pedido {r.order_ref})")
                print(f"  no painel: {r.status}/{r.substatus} · prazo {when(r.dispatch_deadline)} · conferido {when(r.checked_at)}")
                print(f"  no Olist:  {order.olist_status if order else 'pedido não encontrado'}")
                try:
                    data = client.get_shipment(r.shipment_id) or {}
                except IntegrationError as exc:
                    print(f"  Mercado Livre: ERRO — {exc}")
                    continue
                history = data.get("status_history") or {}
                logistic = data.get("logistic_type") or (data.get("logistic") or {}).get("type")
                print(f"  Mercado Livre: status={data.get('status')} substatus={data.get('substatus')} logistica={logistic}")
                print(f"  campos recebidos: {', '.join(sorted(k for k in data.keys()))[:400]}")
                dates = {k: v for k, v in history.items() if v} if isinstance(history, dict) else history
                print(f"  status_history: {dates}")
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
    sub.add_parser("diag-envios").set_defaults(func=cmd_diag_envios)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
