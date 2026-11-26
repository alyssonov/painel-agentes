"""Compras (Operação): fornecedores e pedidos de compra — só no Gerente do Ano.

Receber um pedido NÃO mexe no estoque (a entrada continua no Olist). Custos de
compra só aparecem para quem vê o financeiro.
"""

import csv
import io
import re
import unicodedata
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.config import has_real_data
from app.core import audit
from app.core.db import get_db
from app.core.money import format_brl
from app.core.permissions import FINANCE
from app.core.timeutil import local_today
from app.models import Product
from app.models.product_extra import ProductExtra
from app.models.purchase import PO_STATUS, PurchaseOrder, PurchaseOrderItem, Supplier
from app.models.user import User
from app.services.pricing_calc import parse_money
from app.services.purchase import purchase_suggestions
from app.services.stock import stock_rows
from app.web.auth import current_user, verify_csrf
from app.web.routes.reports import _purchase_rules, _rules
from app.web.templating import flash, render

router = APIRouter(prefix="/compras")


def _ascii(text: str) -> str:
    """Nome de arquivo para download: sem acentos nem espaços (cabeçalho HTTP só aceita ASCII)."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "-", plain).strip("-").lower() or "fornecedor"


def _supplier_snapshot(s: Supplier) -> dict:
    return {"nome": s.name, "contato": s.contact, "telefone": s.phone, "email": s.email,
            "prazo_dias": s.lead_time_days, "observacoes": s.notes}


def parse_lines(db: Session, text: str) -> tuple[list[PurchaseOrderItem], list[str]]:
    """Uma linha por item: "SKU; quantidade; custo unitário (opcional)". Separador ; , ou tab."""
    products = {p.sku.lower(): p for p in db.scalars(select(Product))}
    items, errors = [], []
    for n, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.replace("\t", ";").split(";")]
        if len(parts) == 1:
            parts = [p.strip() for p in line.split(",")]
        sku = parts[0]
        qty = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        cost = parse_money(parts[2]) if len(parts) > 2 and parts[2] else None
        product = products.get(sku.lower())
        if product is None:
            errors.append(f"Linha {n}: SKU \"{sku}\" não está no Olist.")
            continue
        if not qty or qty <= 0:
            errors.append(f"Linha {n} ({sku}): informe a quantidade.")
            continue
        items.append(PurchaseOrderItem(product_id=product.id, sku=product.sku, name=product.name, quantity=qty,
                                       unit_cost_cents=cost if cost is not None else (product.cost_cents or None)))
    return items, errors


def _suggestion_text(db: Session, supplier: Supplier | None) -> str:
    rules = _rules(db, "estoque")
    lead, coverage = _purchase_rules(rules)
    rows = stock_rows(db, window_days=int(rules.get("janela_vendas_dias", 30)), demo=not has_real_data(db))
    items = purchase_suggestions(db, rows, lead_days=lead, coverage_days=coverage)
    if supplier is not None:
        items = [i for i in items if (i.supplier or "").lower() == supplier.name.lower()] or items
    return "\n".join(f"{i.sku}; {i.quantity}" for i in items)


@router.get("")
def purchases_page(request: Request, situacao: str = "aberto", db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    stmt = select(PurchaseOrder).options(selectinload(PurchaseOrder.items), selectinload(PurchaseOrder.supplier))
    if situacao in PO_STATUS:
        stmt = stmt.where(PurchaseOrder.status == situacao)
    orders = list(db.scalars(stmt.order_by(PurchaseOrder.number.desc()).limit(200)))
    counts = dict(db.execute(select(PurchaseOrder.status, func.count()).group_by(PurchaseOrder.status)).all())
    late = sum(1 for o in orders if o.status == "aberto" and o.expected_on and o.expected_on < local_today())
    return render(request, "purchases.html", {"orders": orders, "situacao": situacao, "statuses": PO_STATUS,
                                              "counts": counts, "late": late, "today": local_today()}, db=db)


@router.get("/fornecedores")
def suppliers_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    suppliers = list(db.scalars(select(Supplier).order_by(Supplier.name)))
    products = {}
    for extra in db.scalars(select(ProductExtra).where(ProductExtra.supplier.is_not(None))):
        key = (extra.supplier or "").lower()
        products[key] = products.get(key, 0) + 1
    return render(request, "suppliers.html", {"suppliers": suppliers, "products": products}, db=db)


@router.post("/fornecedores", dependencies=[Depends(verify_csrf)])
def save_supplier(request: Request, supplier_id: str = Form(""), name: str = Form(""), contact: str = Form(""),
                  phone: str = Form(""), email: str = Form(""), lead_time_days: str = Form(""),
                  notes: str = Form(""), db: Session = Depends(get_db), user: User = Depends(current_user)):
    name = name.strip()[:120]
    if not name:
        flash(request, "Informe o nome do fornecedor.", "erro")
        return RedirectResponse("/compras/fornecedores", status_code=303)
    lead = int(lead_time_days) if lead_time_days.strip().isdigit() and int(lead_time_days) <= 365 else None
    supplier = db.get(Supplier, int(supplier_id)) if supplier_id.isdigit() else None
    same_name = db.scalar(select(Supplier).where(func.lower(Supplier.name) == name.lower()))
    if same_name and (supplier is None or same_name.id != supplier.id):
        flash(request, "Já existe um fornecedor com esse nome.", "erro")
        return RedirectResponse("/compras/fornecedores", status_code=303)
    before = _supplier_snapshot(supplier) if supplier else None
    if supplier is None:
        supplier = Supplier(name=name)
        db.add(supplier)
    supplier.name, supplier.contact, supplier.phone = name, contact.strip()[:120] or None, phone.strip()[:40] or None
    supplier.email, supplier.notes = email.strip()[:160] or None, notes.strip()[:1000] or None
    supplier.lead_time_days = lead
    db.flush()
    after = _supplier_snapshot(supplier)
    if before != after:
        audit.record(db, actor=f"user:{user.id}", event="compras.fornecedor_salvo", target_type="fornecedor",
                     target_id=supplier.id, details=audit.changes(before or {}, after))
    db.commit()
    flash(request, f"Fornecedor {supplier.name} salvo.")
    return RedirectResponse("/compras/fornecedores", status_code=303)


@router.get("/novo")
def new_order_page(request: Request, fornecedor: str = "", sugestao: str = "", db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    suppliers = list(db.scalars(select(Supplier).order_by(Supplier.name)))
    chosen = db.get(Supplier, int(fornecedor)) if fornecedor.isdigit() else None
    return render(request, "purchase_new.html", {
        "suppliers": suppliers, "chosen": chosen, "lines": _suggestion_text(db, chosen) if sugestao else "",
        "today": local_today(),
    }, db=db)


@router.post("/novo", dependencies=[Depends(verify_csrf)])
def create_order(request: Request, supplier_id: str = Form(""), lines: str = Form(""), expected: str = Form(""),
                 notes: str = Form(""), db: Session = Depends(get_db), user: User = Depends(current_user)):
    supplier = db.get(Supplier, int(supplier_id)) if supplier_id.isdigit() else None
    items, errors = parse_lines(db, lines)
    if supplier is None:
        errors.insert(0, "Escolha o fornecedor (cadastre em Fornecedores, se precisar).")
    if not items and not errors:
        errors.append("Informe pelo menos um item (SKU; quantidade).")
    if errors:
        flash(request, " ".join(errors[:5]), "erro")
        return RedirectResponse("/compras/novo", status_code=303)
    if not user.can(FINANCE):
        for item in items:  # sem acesso ao financeiro: não define custos (fica o do Olist, invisível)
            item.unit_cost_cents = None
    today = local_today()
    expected_on = None
    if expected.strip().isdigit():
        expected_on = today + timedelta(days=int(expected))
    elif supplier.lead_time_days:
        expected_on = today + timedelta(days=supplier.lead_time_days)
    number = (db.scalar(select(func.max(PurchaseOrder.number))) or 0) + 1
    order = PurchaseOrder(number=number, supplier_id=supplier.id, status="aberto", ordered_on=today,
                          expected_on=expected_on, notes=notes.strip()[:1000] or None, items=items)
    db.add(order)
    db.flush()
    audit.record(db, actor=f"user:{user.id}", event="compras.pedido_criado", target_type="compra",
                 target_id=order.id, details={"numero": number, "fornecedor": supplier.name,
                                              "itens": len(items), "unidades": order.units})
    db.commit()
    flash(request, f"Pedido de compra nº {number} registrado ({order.units} unidade(s)). A sugestão de compra "
                   "já desconta esses itens.")
    return RedirectResponse(f"/compras/{order.id}", status_code=303)


def _order(db: Session, order_id: int) -> PurchaseOrder | None:
    return db.scalar(select(PurchaseOrder).where(PurchaseOrder.id == order_id)
                     .options(selectinload(PurchaseOrder.items), selectinload(PurchaseOrder.supplier)))


@router.get("/{order_id}.csv")
def order_csv(order_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    order = _order(db, order_id)
    if order is None:
        return Response("Pedido não encontrado.", status_code=404)
    with_costs = user.can(FINANCE)
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(["SKU", "Produto", "Quantidade", *(["Custo unitário", "Total"] if with_costs else [])])
    for i in order.items:
        cost = i.unit_cost_cents or 0
        w.writerow([i.sku, i.name, i.quantity, *([format_brl(cost), format_brl(cost * i.quantity)] if with_costs else [])])
    name = f"pedido-compra-{order.number}-{_ascii(order.supplier.name)[:30]}.csv"
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{order_id}")
def order_page(request: Request, order_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    order = _order(db, order_id)
    if order is None:
        flash(request, "Pedido de compra não encontrado.", "erro")
        return RedirectResponse("/compras", status_code=303)
    return render(request, "purchase_order.html", {"o": order, "statuses": PO_STATUS, "today": local_today()}, db=db)


@router.post("/{order_id}/situacao", dependencies=[Depends(verify_csrf)])
def set_status(request: Request, order_id: int, status: str = Form(""), db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    order = _order(db, order_id)
    if order is None or status not in PO_STATUS:
        flash(request, "Pedido de compra não encontrado.", "erro")
        return RedirectResponse("/compras", status_code=303)
    before = PO_STATUS[order.status]
    order.status = status
    order.received_on = local_today() if status == "recebido" else None
    audit.record(db, actor=f"user:{user.id}", event="compras.pedido_situacao", target_type="compra",
                 target_id=order.id, details={"numero": order.number, **audit.changes(
                     {"situacao": before}, {"situacao": PO_STATUS[status]})})
    db.commit()
    msg = f"Pedido nº {order.number}: {PO_STATUS[status].lower()}."
    if status == "recebido":
        msg += " Lembre de dar a entrada da mercadoria no Olist (o painel não mexe no estoque)."
    flash(request, msg)
    return RedirectResponse(f"/compras/{order.id}", status_code=303)
