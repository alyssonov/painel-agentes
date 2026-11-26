"""Financeiro: despesas da empresa e DRE mensal (lucro líquido de verdade)."""

from datetime import date

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.money import format_brl
from app.core.timeutil import local_today
from app.models.expense import CATEGORIES, Expense
from app.models.user import User
from app.services.dre import (
    add_months,
    category_label,
    dre_months,
    expenses_for,
    month_label,
    month_start,
    parse_month,
)
from app.services.pricing_calc import parse_money
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/financeiro")


def _snapshot(e: Expense) -> dict:
    return {"nome": e.name, "categoria": category_label(e.category), "valor": format_brl(e.amount_cents),
            "repete": "todo mês" if e.recurring else "só uma vez", "desde": month_label(e.start_month),
            "ate": month_label(e.end_month) if e.end_month else None, "observacoes": e.notes}


def _month_options(center: date) -> list[tuple[str, str]]:
    return [(f"{m:%Y-%m}", month_label(m)) for m in (add_months(center, i) for i in range(-12, 13))]


@router.get("/dre")
def dre_page(request: Request, mes: str = "", db: Session = Depends(get_db), user: User = Depends(current_user)):
    month = parse_month(mes) or month_start(local_today())
    months = dre_months(db, month, 3)
    categories = sorted({c for m in months for c in m.expenses}, key=lambda c: list(CATEGORIES).index(c)
                        if c in CATEGORIES else 99)
    return render(request, "dre.html", {
        "months": months, "current": months[-1], "categories": categories, "category_label": category_label,
        "month_value": f"{month:%Y-%m}", "options": _month_options(month_start(local_today())),
        "prev": f"{add_months(month, -1):%Y-%m}", "next": f"{add_months(month, 1):%Y-%m}",
    }, db=db)


@router.get("/despesas")
def expenses_page(request: Request, mes: str = "", db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    month = parse_month(mes) or month_start(local_today())
    active = expenses_for(db, month)
    all_rows = list(db.scalars(select(Expense).order_by(Expense.recurring.desc(), Expense.name)))
    return render(request, "expenses.html", {
        "active": active, "rows": all_rows, "total": sum(e.amount_cents for e in active),
        "categories": CATEGORIES, "category_label": category_label, "month_label": month_label(month),
        "month_value": f"{month:%Y-%m}", "options": _month_options(month_start(local_today())),
        "this_month": f"{month_start(local_today()):%Y-%m}",
    }, db=db)


def _read_form(name: str, category: str, amount: str, recurring: str, start: str, end: str, notes: str):
    errors = []
    cents = parse_money(amount)
    if not name.strip():
        errors.append("Dê um nome para a despesa.")
    if cents is None or cents <= 0:
        errors.append("Informe o valor (ex.: 1.500,00).")
    start_m = parse_month(start)
    end_m = parse_month(end) if end else None
    if start_m is None:
        errors.append("Escolha o mês de início.")
    elif end_m and end_m < start_m:
        errors.append("O mês final vem antes do inicial.")
    data = {"name": name.strip()[:120], "category": category if category in CATEGORIES else "outros",
            "amount_cents": cents or 0, "recurring": recurring == "1", "start_month": start_m,
            "end_month": end_m if recurring == "1" else None, "notes": notes.strip()[:1000] or None}
    return data, errors


@router.post("/despesas", dependencies=[Depends(verify_csrf)])
def add_expense(request: Request, name: str = Form(""), category: str = Form("outros"), amount: str = Form(""),
                recurring: str = Form("1"), start: str = Form(""), end: str = Form(""), notes: str = Form(""),
                db: Session = Depends(get_db), user: User = Depends(current_user)):
    data, errors = _read_form(name, category, amount, recurring, start, end, notes)
    if errors:
        flash(request, " ".join(errors), "erro")
        return RedirectResponse("/financeiro/despesas", status_code=303)
    expense = Expense(**data)
    db.add(expense)
    db.flush()
    audit.record(db, actor=f"user:{user.id}", event="financeiro.despesa_criada", target_type="despesa",
                 target_id=expense.id, details={"depois": _snapshot(expense)})
    db.commit()
    flash(request, f"Despesa \"{expense.name}\" lançada ({format_brl(expense.amount_cents)}"
                   f"{' por mês' if expense.recurring else ''}).")
    return RedirectResponse("/financeiro/despesas", status_code=303)


@router.post("/despesas/{expense_id}", dependencies=[Depends(verify_csrf)])
def edit_expense(request: Request, expense_id: int, name: str = Form(""), category: str = Form("outros"),
                 amount: str = Form(""), recurring: str = Form("1"), start: str = Form(""), end: str = Form(""),
                 notes: str = Form(""), db: Session = Depends(get_db), user: User = Depends(current_user)):
    expense = db.get(Expense, expense_id)
    if expense is None:
        flash(request, "Despesa não encontrada.", "erro")
        return RedirectResponse("/financeiro/despesas", status_code=303)
    data, errors = _read_form(name, category, amount, recurring, start, end, notes)
    if errors:
        flash(request, " ".join(errors), "erro")
        return RedirectResponse("/financeiro/despesas", status_code=303)
    before = _snapshot(expense)
    for key, value in data.items():
        setattr(expense, key, value)
    after = _snapshot(expense)
    if before != after:
        audit.record(db, actor=f"user:{user.id}", event="financeiro.despesa_alterada", target_type="despesa",
                     target_id=expense.id, details=audit.changes(before, after))
    db.commit()
    flash(request, "Despesa atualizada." if before != after else "Nada mudou.")
    return RedirectResponse("/financeiro/despesas", status_code=303)


@router.post("/despesas/{expense_id}/excluir", dependencies=[Depends(verify_csrf)])
def delete_expense(request: Request, expense_id: int, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    """Apaga um lançamento feito por engano (para encerrar uma despesa, use o mês final)."""
    expense = db.get(Expense, expense_id)
    if expense is None:
        flash(request, "Despesa não encontrada.", "erro")
        return RedirectResponse("/financeiro/despesas", status_code=303)
    audit.record(db, actor=f"user:{user.id}", event="financeiro.despesa_excluida", target_type="despesa",
                 target_id=expense.id, details={"antes": _snapshot(expense)})
    db.delete(expense)
    db.commit()
    flash(request, f"Despesa \"{expense.name}\" excluída. Fica registrada no Histórico.")
    return RedirectResponse("/financeiro/despesas", status_code=303)
