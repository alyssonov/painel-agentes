"""Produtos (Operação): dados do Olist (só leitura) + cadastro complementar do Gerente do Ano."""

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.permissions import FINANCE
from app.core.timeutil import local_today, to_local
from app.models import Product
from app.models.product_extra import ProductExtra
from app.models.user import User
from app.services.pricing_calc import parse_money
from app.services.products import (
    COST_DIFF_PCT,
    FILTERS,
    NO_BRAND,
    PAGE,
    brand_options,
    export_csv,
    extra_for,
    format_compatibility,
    import_csv,
    latest_stock,
    list_products,
    listing_brands,
    parse_compatibility,
    set_cost,
    snapshot,
    template_csv,
    to_int,
)
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/produtos")


def _csv(content: str, name: str) -> Response:
    return Response(content, media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("")
def products_page(request: Request, busca: str = "", filtro: str = "", marca: str = "", pagina: int = 1,
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    filtro = filtro if filtro in FILTERS else ""
    marca = marca.strip()[:80]
    everything = list_products(db)
    rows = list_products(db, busca.strip()[:80], filtro, marca)
    pages = max(1, -(-len(rows) // PAGE))
    pagina = min(max(1, pagina), pages)
    counts = {"sem_compat": sum(1 for r in everything if not (r.extra and r.extra.compatibility)),
              "sem_marca": sum(1 for r in everything if not r.brand)}
    return render(request, "products.html", {
        "rows": rows[(pagina - 1) * PAGE: pagina * PAGE], "total": len(rows), "page": pagina, "pages": pages,
        "busca": busca, "filtro": filtro, "filters": FILTERS, "counts": counts, "diff_pct": COST_DIFF_PCT,
        "marca": marca, "brands": brand_options(everything), "no_brand": NO_BRAND,
    }, db=db)


@router.get(".csv")
def products_csv(busca: str = "", filtro: str = "", marca: str = "", db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    rows = list_products(db, busca.strip()[:80], filtro if filtro in FILTERS else "", marca.strip()[:80])
    return _csv(export_csv(rows, with_costs=user.can(FINANCE)), f"produtos-{local_today():%Y-%m-%d}.csv")


@router.get("/modelo.csv")
def template(user: User = Depends(current_user)):
    return _csv(template_csv(), "modelo-cadastro-produtos.csv")


@router.post("/planilha", dependencies=[Depends(verify_csrf)])
async def upload(request: Request, arquivo: UploadFile = File(...), db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    raw = await arquivo.read(2_000_001)
    result, changes = import_csv(db, raw)
    if not user.can(FINANCE):
        # Sem acesso ao financeiro, a planilha não muda custos (só o resto).
        for extra, before, _after in changes:
            if before["custo_onix"] != snapshot(extra)["custo_onix"]:
                db.rollback()
                flash(request, "A planilha tem a coluna de custo, que só quem vê o financeiro pode enviar. "
                               "Tire a coluna custo_onix e envie de novo.", "erro")
                return RedirectResponse("/produtos", status_code=303)
    for extra, before, after in changes:
        audit.record(db, actor=f"user:{user.id}", event="produto.cadastro_alterado", target_type="produto",
                     target_id=extra.product_id, details={**audit.changes(before, after), "origem": "planilha"})
    audit.record(db, actor=f"user:{user.id}", event="produto.planilha_importada",
                 details={"atualizados": result.updated, "sem_mudanca": result.unchanged,
                          "sku_desconhecido": len(result.unknown_skus), "erros": len(result.errors)})
    db.commit()
    msg = f"Planilha lida: {result.updated} produto(s) atualizado(s), {result.unchanged} sem mudança."
    if result.unknown_skus:
        msg += f" {len(result.unknown_skus)} SKU(s) não encontrado(s) no Olist: " + ", ".join(result.unknown_skus[:8])
    if result.errors:
        msg += " Atenção: " + " ".join(result.errors[:4]) + (" (e outros)" if len(result.errors) > 4 else "")
    flash(request, msg, "erro" if result.errors else "ok")
    return RedirectResponse("/produtos", status_code=303)


@router.get("/{product_id}")
def product_page(request: Request, product_id: int, db: Session = Depends(get_db),
                 user: User = Depends(current_user)):
    product = db.get(Product, product_id)
    if product is None:
        flash(request, "Produto não encontrado.", "erro")
        return RedirectResponse("/produtos", status_code=303)
    extra = db.scalar(select(ProductExtra).where(ProductExtra.product_id == product.id))
    return render(request, "product.html", {
        "olist_brand": product.brand, "listing_brand": listing_brands(db).get(product.id),
        "p": product, "e": extra, "stock": latest_stock(db).get(product.id),
        "compat_text": format_compatibility(extra.compatibility if extra else []),
        "cost_at": to_local(extra.onix_cost_at).strftime("%d/%m/%Y") if extra and extra.onix_cost_at else None,
    }, db=db)


@router.post("/{product_id}", dependencies=[Depends(verify_csrf)])
async def save_product(request: Request, product_id: int, db: Session = Depends(get_db),
                       user: User = Depends(current_user)):
    product = db.get(Product, product_id)
    if product is None:
        flash(request, "Produto não encontrado.", "erro")
        return RedirectResponse("/produtos", status_code=303)
    form = await request.form()
    extra = extra_for(db, product)
    before = snapshot(extra)
    errors = []
    extra.brand = str(form.get("brand", "")).strip()[:80] or None
    extra.supplier = str(form.get("supplier", "")).strip()[:120] or None
    extra.supplier_code = str(form.get("supplier_code", "")).strip()[:64] or None
    extra.location = str(form.get("location", "")).strip()[:60] or None
    extra.notes = str(form.get("notes", "")).strip()[:2000] or None
    for field, attr, hi, label in (("min_stock", "min_stock", 1_000_000, "Estoque mínimo"),
                                   ("lead_time_days", "lead_time_days", 365, "Prazo do fornecedor")):
        raw = str(form.get(field, "")).strip()
        value = to_int(raw, 0, hi) if raw else None
        if raw and value is None:
            errors.append(f"{label}: use um número inteiro.")
        else:
            setattr(extra, attr, value)
    if user.can(FINANCE) and "onix_cost" in form:
        raw = str(form.get("onix_cost", "")).strip()
        cents = parse_money(raw) if raw else None
        if raw and cents is None:
            errors.append("Custo do Onix: use um valor como 18,90.")
        else:
            set_cost(extra, cents)
    items, bad = parse_compatibility(str(form.get("compatibility", "")))
    if bad:
        errors.append("Não entendi estas motos: " + "; ".join(bad[:3]) + ". Use: marca modelo ano-ano.")
    else:
        extra.compatibility = items
    if errors:
        db.rollback()
        flash(request, " ".join(errors), "erro")
        return RedirectResponse(f"/produtos/{product_id}", status_code=303)
    after = snapshot(extra)
    if after != before:
        audit.record(db, actor=f"user:{user.id}", event="produto.cadastro_alterado", target_type="produto",
                     target_id=product.id, details=audit.changes(before, after))
    db.commit()
    flash(request, "Cadastro salvo (só no Gerente do Ano; o Olist não foi alterado)." if after != before
          else "Nada mudou.")
    return RedirectResponse(f"/produtos/{product_id}", status_code=303)
