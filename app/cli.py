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
    from app.core.timeutil import local_day_bounds_utc, local_today, to_local
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
        # Quadro de hoje: de onde vem cada número (para comparar com o que a loja despachou).
        from app.services.dashboard import dispatch_day
        start, end = local_day_bounds_utc(local_today())
        day = dispatch_day(db)
        print(f"\n== Quadro de hoje: enviados {day.shipped} · a enviar {day.to_ship} · atrasados {day.late} ==")
        today_out = [r for r in rows if r.shipped_at and start <= r.shipped_at < end]
        print(f"  saíram hoje (pela data de saída): {len(today_out)}")
        for key, count in Counter(f"{r.status}/{r.substatus} {r.logistic_type}" for r in today_out).most_common():
            print(f"    {key:45} {count}")
        due = [r for r in rows if r.dispatch_deadline and start <= r.dispatch_deadline < end]
        print(f"  prazo de despacho hoje: {len(due)}")
        for key, count in Counter(f"{r.status}/{r.substatus} {r.logistic_type} saída={when(r.shipped_at) if r.shipped_at else '-'}"[:70]
                                  for r in due).most_common(15):
            print(f"    {key:70} {count}")
        no_deadline = [r for r in rows if r.dispatch_deadline is None and r.logistic_type != "fulfillment"]
        print(f"  sem prazo conhecido (fora do quadro): {len(no_deadline)}")
        for key, count in Counter(f"{r.status}/{r.substatus} {r.logistic_type}" for r in no_deadline).most_common(8):
            print(f"    {key:45} {count}")
        sample = late[:3] or [r for r in due if left_the_store(r.status, r.substatus)][:2]
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
                # Só datas (nada de dados do comprador): procuramos onde o ML informa a coleta.
                print(f"  datas: { {k: v for k, v in data.items() if ('date' in k or 'updated' in k) and isinstance(v, str)} }")
                sub = data.get("substatus_history")
                if sub:
                    print(f"  substatus_history: {sub}"[:600])
    return 0


def cmd_diag_recebimentos(_args) -> int:
    """De onde dá para prever os recebimentos? (Mercado Livre/Mercado Pago e Olist; sem dados de clientes)."""
    from datetime import timedelta

    from app.core.db import get_sessionmaker, utcnow
    from app.integrations.errors import IntegrationError
    from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
    from app.integrations.mercadolivre.client import MLClient
    from app.integrations.mercadopago.client import MercadoPagoClient
    from app.integrations.olist.auth import OlistTokenProvider, get_olist_account
    from app.core.money import format_brl
    from app.integrations.olist.client import OlistClient

    sm = get_sessionmaker()
    with sm() as db:
        ml, olist = get_ml_account(db), get_olist_account(db)
        seller = ml.external_id if ml else None
        has_olist = bool(olist and olist.access_token_enc)

    print("== Mercado Livre: pagamentos das vendas ==")
    payment_ids = []
    if not seller:
        print("  Mercado Livre não está conectado.")
    else:
        try:
            with MLClient(MLTokenProvider(sm)) as client:
                for order in client.iter_orders(seller, created_from=utcnow() - timedelta(days=3)):
                    if order.get("status") != "paid":
                        continue
                    for pay in order.get("payments") or []:
                        if len(payment_ids) < 3 and pay.get("id"):
                            payment_ids.append(str(pay["id"]))
                            dates = {k: v for k, v in pay.items() if "date" in k and v}
                            print(f"  pagamento: status={pay.get('status')} campos={sorted(pay.keys())}"[:500])
                            print(f"    datas: {dates}")
                    if len(payment_ids) >= 3:
                        break
        except IntegrationError as exc:
            print(f"  ERRO ao ler vendas: {exc}")

    print("\n== Mercado Pago: data de liberação do dinheiro ==")
    if payment_ids:
        try:
            with MercadoPagoClient(MLTokenProvider(sm)) as mp:
                for pid in payment_ids[:2]:
                    data = mp.get_payment(pid) or {}
                    details = data.get("transaction_details") or {}
                    print(f"  pagamento: status={data.get('status')} liberação={data.get('money_release_date')} "
                          f"situação_liberação={data.get('money_release_status')} "
                          f"valor={data.get('transaction_amount')} líquido={details.get('net_received_amount')}")
        except IntegrationError as exc:
            print(f"  Sem acesso ao Mercado Pago com a conta do Mercado Livre: {exc}")
    else:
        print("  Nenhum pagamento recente para consultar.")

    print("\n== Olist: contas a receber em aberto (próximos 30 dias) ==")
    if not has_olist:
        print("  Olist não está conectado.")
    else:
        try:
            with OlistClient(OlistTokenProvider(sm)) as client:
                now = utcnow()
                rows = list(client.iter_receivables(due_from=now - timedelta(days=1), due_until=now + timedelta(days=30)))
                total = sum(float(r.get("valor") or 0) for r in rows)
                dues = sorted(r.get("dataVencimento") for r in rows if r.get("dataVencimento"))
                print(f"  {len(rows)} conta(s), total {format_brl(round(total * 100))}")
                if dues:
                    print(f"  vencimentos de {dues[0]} até {dues[-1]}")
        except Exception as exc:  # noqa: BLE001 - diagnóstico: mostra e segue
            print(f"  ERRO {type(exc).__name__}: {str(exc)[:200]}")
    return 0


def cmd_diag_precos(_args) -> int:
    """Referências de preço do Mercado Livre para os anúncios da loja (só leitura, sem dados de clientes)."""
    from app.core.db import get_sessionmaker
    from app.core.money import format_brl
    from app.integrations.errors import IntegrationError
    from app.integrations.mercadolivre.auth import MLTokenProvider, get_ml_account
    from app.integrations.mercadolivre.client import MLClient
    from app.integrations.olist.dates import to_cents
    from app.models import Listing, Product
    from sqlalchemy import select

    sm = get_sessionmaker()
    with sm() as db:
        ml = get_ml_account(db)
        seller = ml.external_id if ml else None
    if not seller:
        print("Mercado Livre não está conectado.")
        return 1
    with MLClient(MLTokenProvider(sm)) as client:
        print("== Referências de preço ==")
        try:
            items = client.get_price_suggestion_items(seller)
        except IntegrationError as exc:
            print(f"  ERRO: {exc}")
            items = []
        except Exception as exc:  # noqa: BLE001
            print(f"  ERRO {type(exc).__name__}: {str(exc)[:200]}")
            items = []
        print(f"  {len(items)} anúncio(s) com referência de preço")
        statuses: dict[str, int] = {}
        samples = []
        for n, item_id in enumerate(items[:40], 1):
            try:
                data = client.get_price_suggestion(item_id) or {}
            except Exception as exc:  # noqa: BLE001 - diagnóstico: mostra e segue
                print(f"  {item_id}: ERRO {type(exc).__name__}: {str(exc)[:200]}")
                continue
            if not isinstance(data, dict):
                print(f"  {item_id}: resposta em formato inesperado ({type(data).__name__}): {str(data)[:200]}")
                continue
            if n % 10 == 0:
                print(f"  ... {n} lidos")
            statuses[data.get("status") or "?"] = statuses.get(data.get("status") or "?", 0) + 1
            if len(samples) < 4:
                samples.append((item_id, data))
        print(f"  situações (dos primeiros 40): {statuses}")
        with sm() as db:
            for item_id, d in samples:
                try:
                    listing = db.scalar(select(Listing).where(Listing.item_id == item_id))
                    product = db.get(Product, listing.product_id) if listing and listing.product_id else None
                    def amt(key):
                        return (d.get(key) or {}).get("amount")
                    print(f"\n  Anúncio {item_id}: situação={d.get('status')} aplicável={d.get('applicable_suggestion')}")
                    print(f"    preço atual={amt('current_price')} sugerido={amt('suggested_price')} "
                          f"menor={amt('lowest_price')} interno={amt('internal_price')} diferença={d.get('percent_difference')}%")
                    print(f"    custos informados pelo ML: {d.get('costs')} · comparou {((d.get('metadata') or {}).get('compared_values'))} anúncio(s)")
                    print(f"    no painel: preço {format_brl(listing.price_cents) if listing else '—'} · "
                          f"custo Olist {format_brl(product.cost_cents) if product else 'produto não vinculado'}"
                          f"{' · tem variações' if listing and listing.variation_label else ''}")
                    if d.get("promotion_detail"):
                        print(f"    promoção sugerida: {d.get('promotion_detail', {}).get('campaign_name')}")
                except Exception as exc:  # noqa: BLE001 - diagnóstico: mostra e segue
                    print(f"  {item_id}: não consegui mostrar ({type(exc).__name__}: {str(exc)[:200]})")
        print("\n== Automatização de preços do Mercado Livre ==")
        try:
            automated = list(client.iter_automated_price_items(seller))
            print(f"  {len(automated)} anúncio(s) com automatização (o painel não muda o preço desses)")
            if automated:
                print(f"  exemplo de campos: {sorted(automated[0].keys()) if isinstance(automated[0], dict) else automated[0]}")
        except Exception as exc:  # noqa: BLE001 - diagnóstico: mostra e segue
            print(f"  ERRO: {exc}")
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
    sub.add_parser("diag-recebimentos").set_defaults(func=cmd_diag_recebimentos)
    sub.add_parser("diag-precos").set_defaults(func=cmd_diag_precos)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
