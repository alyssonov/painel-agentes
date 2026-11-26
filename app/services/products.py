"""Produtos: Olist (só leitura) + cadastro complementar do Gerente do Ano.

Nada aqui escreve no Olist. O complemento (fornecedor, custo do Onix, estoque
mínimo, local, compatibilidade) fica na tabela product_extras.
"""

import csv
import io
import re
from collections import Counter
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import utcnow
from app.core.money import format_brl
from app.models import Listing, Product, StockSnapshot
from app.models.product_extra import ProductExtra
from app.services.pricing_calc import parse_money
from app.services.search import matches, normalize, terms

PAGE = 50
FILTERS = {"": "Todos", "sem_compat": "Sem compatibilidade", "abaixo_minimo": "Abaixo do estoque mínimo",
           "custo_diferente": "Custo do Onix diferente do Olist", "sem_marca": "Sem marca",
           "sem_fornecedor": "Sem fornecedor"}
NO_BRAND = "__sem__"  # valor do filtro de marca para "sem marca"
COST_DIFF_PCT = 5.0  # acima disso, avisa que o custo do Onix e o do Olist não batem

# Marcas mais comuns no Brasil, para separar "marca" de "modelo" no texto.
BRANDS = ["Harley-Davidson", "Royal Enfield", "Honda", "Yamaha", "Suzuki", "Kawasaki", "BMW", "Dafra", "Shineray",
          "Haojue", "Kasinski", "Triumph", "Ducati", "Traxx", "Sundown", "KTM", "Bajaj", "Voltz", "Avelloz", "Mottu",
          "Zontes", "CFMoto", "Benelli", "Kymco", "Piaggio", "Vespa"]
_YEARS = re.compile(r"\s+(\d{4})(?:\s*(?:-|a|até|ate|/)\s*(\d{4}))?\s*$", re.I)


# --- Compatibilidade --------------------------------------------------------------

def parse_compatibility(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Uma moto por linha: "Honda CG 160 2016-2024", "Yamaha Fazer 250 2018", "Honda Biz 125".

    Devolve (lista, linhas que não deu para entender).
    """
    items, bad = [], []
    for raw in (text or "").replace(";", "\n").splitlines():
        line = " ".join(raw.split())
        if not line:
            continue
        start = end = None
        m = _YEARS.search(line)
        if m:
            start, end = int(m.group(1)), int(m.group(2) or m.group(1))
            line = line[:m.start()].strip()
            if not 1950 <= start <= 2100 or not 1950 <= end <= 2100 or end < start:
                bad.append(raw.strip())
                continue
        brand = next((b for b in BRANDS if line.lower().startswith(b.lower() + " ")), None)
        if brand:
            model = line[len(brand):].strip()
        else:
            brand, _, model = line.partition(" ")
        if not brand or not model:
            bad.append(raw.strip())
            continue
        items.append({"marca": brand[:40], "modelo": model[:80], "de": start, "ate": end})
    return items, bad


def format_compatibility(items: list[dict[str, Any]] | None) -> str:
    lines = []
    for it in items or []:
        years = ""
        if it.get("de"):
            years = f" {it['de']}" + (f"-{it['ate']}" if it.get("ate") and it["ate"] != it["de"] else "")
        lines.append(f"{it.get('marca', '')} {it.get('modelo', '')}{years}".strip())
    return "\n".join(lines)


# --- Lista ------------------------------------------------------------------------

@dataclass
class ProductRow:
    product: Product
    extra: ProductExtra | None
    stock: int | None
    listing_brand: str | None = None  # marca mais comum nos anúncios do ML deste produto

    @property
    def brand(self) -> str | None:
        """Marca: a escrita no painel > a do Olist > a dos anúncios do Mercado Livre."""
        return (self.extra.brand if self.extra and self.extra.brand else None) or self.product.brand \
            or self.listing_brand

    @property
    def brand_source(self) -> str | None:
        if self.extra and self.extra.brand:
            return "escrita no painel"
        if self.product.brand:
            return "Olist"
        return "anúncio do Mercado Livre" if self.listing_brand else None

    @property
    def below_min(self) -> bool:
        return bool(self.extra and self.extra.min_stock is not None and self.stock is not None
                    and self.stock < self.extra.min_stock)

    @property
    def cost_diff_pct(self) -> float | None:
        if not (self.extra and self.extra.onix_cost_cents and self.product.cost_cents):
            return None
        return (self.extra.onix_cost_cents - self.product.cost_cents) / self.product.cost_cents * 100

    @property
    def cost_differs(self) -> bool:
        diff = self.cost_diff_pct
        return diff is not None and abs(diff) > COST_DIFF_PCT


def latest_stock(db: Session) -> dict[int, int]:
    latest = select(func.max(StockSnapshot.id)).group_by(StockSnapshot.product_id)
    return {s.product_id: s.quantity - (s.reserved or 0)
            for s in db.scalars(select(StockSnapshot).where(StockSnapshot.id.in_(latest)))}


def listing_brands(db: Session) -> dict[int, str]:
    """Marca mais comum nos anúncios de cada produto (atributo "Marca" do Mercado Livre)."""
    counts: dict[int, Counter] = {}
    for product_id, brand in db.execute(select(Listing.product_id, Listing.brand).where(
            Listing.product_id.is_not(None), Listing.brand.is_not(None))):
        counts.setdefault(product_id, Counter())[brand] += 1
    return {pid: c.most_common(1)[0][0] for pid, c in counts.items()}


def list_products(db: Session, q: str = "", flt: str = "", brand: str = "") -> list[ProductRow]:
    """Produtos ativos; a busca acha pelas palavras em qualquer ordem (nome, SKU, marca, moto, fornecedor, local)."""
    stmt = select(Product).where(Product.is_active.is_(True)).order_by(Product.name)
    extras = {e.product_id: e for e in db.scalars(select(ProductExtra))}
    stock = latest_stock(db)
    by_listing = listing_brands(db)
    rows = [ProductRow(p, extras.get(p.id), stock.get(p.id), by_listing.get(p.id)) for p in db.scalars(stmt)]
    wanted = terms(q)
    if wanted:
        rows = [r for r in rows if matches(wanted, r.product.name, r.product.sku, r.brand, *(
            (format_compatibility(r.extra.compatibility), r.extra.supplier, r.extra.location) if r.extra else ()))]
    if brand == NO_BRAND:
        rows = [r for r in rows if not r.brand]
    elif brand:
        rows = [r for r in rows if r.brand and normalize(r.brand) == normalize(brand)]
    if flt == "sem_compat":
        rows = [r for r in rows if not (r.extra and r.extra.compatibility)]
    elif flt == "abaixo_minimo":
        rows = [r for r in rows if r.below_min]
    elif flt == "custo_diferente":
        rows = [r for r in rows if r.cost_differs]
    elif flt == "sem_fornecedor":
        rows = [r for r in rows if not (r.extra and r.extra.supplier)]
    elif flt == "sem_marca":
        rows = [r for r in rows if not r.brand]
    return rows


def brand_options(rows: list[ProductRow]) -> list[tuple[str, int]]:
    """Marcas para o filtro, com quantos produtos cada uma tem (mais produtos primeiro)."""
    names: dict[str, str] = {}
    counts: Counter = Counter()
    for r in rows:
        if r.brand:
            key = normalize(r.brand)
            names.setdefault(key, r.brand)
            counts[key] += 1
    return sorted(((names[k], n) for k, n in counts.items()), key=lambda x: (-x[1], x[0].lower()))


def extra_for(db: Session, product: Product) -> ProductExtra:
    extra = db.scalar(select(ProductExtra).where(ProductExtra.product_id == product.id))
    if extra is None:
        extra = ProductExtra(product_id=product.id, compatibility=[])
        db.add(extra)
    return extra


def snapshot(extra: ProductExtra) -> dict[str, Any]:
    """Para o histórico (antes → depois)."""
    return {"marca": extra.brand, "fornecedor": extra.supplier, "codigo_fornecedor": extra.supplier_code,
            "custo_onix": format_brl(extra.onix_cost_cents) if extra.onix_cost_cents is not None else None,
            "estoque_minimo": extra.min_stock, "prazo_dias": extra.lead_time_days, "localizacao": extra.location,
            "compatibilidade": format_compatibility(extra.compatibility).replace("\n", "; ") or None,
            "observacoes": extra.notes}


def set_cost(extra: ProductExtra, cents: int | None) -> None:
    if cents != extra.onix_cost_cents:
        extra.onix_cost_cents = cents
        extra.onix_cost_at = utcnow() if cents is not None else None


def to_int(value: Any, lo: int = 0, hi: int = 1_000_000) -> int | None:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return number if lo <= number <= hi else None


# --- Planilha ---------------------------------------------------------------------

COLUMNS = ["sku", "marca", "fornecedor", "codigo_fornecedor", "custo_onix", "estoque_minimo", "prazo_dias", "localizacao",
           "compatibilidade", "observacoes"]
_ALIASES = {"custo": "custo_onix", "custo onix": "custo_onix", "estoque minimo": "estoque_minimo",
            "estoque mínimo": "estoque_minimo", "minimo": "estoque_minimo", "prazo": "prazo_dias",
            "local": "localizacao", "localização": "localizacao", "compatível": "compatibilidade",
            "compativel": "compatibilidade", "codigo": "codigo_fornecedor", "código": "codigo_fornecedor",
            "observações": "observacoes", "obs": "observacoes", "código do fornecedor": "codigo_fornecedor"}
MAX_IMPORT_BYTES = 2_000_000


@dataclass
class ImportResult:
    updated: int
    unchanged: int
    unknown_skus: list[str]
    errors: list[str]


def template_csv() -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    w.writerow(COLUMNS)
    w.writerow(["PAST-CG160", "Cobreq", "Distribuidora Exemplo", "DX-123", "18,90", "5", "7", "A3-02",
                "Honda CG 160 2016-2024 | Honda Fan 160 2016-2024", "pastilha dianteira"])
    return "﻿" + out.getvalue()


def _norm(header: str) -> str:
    key = header.strip().lower().lstrip("﻿").replace("_", " ")
    key = _ALIASES.get(key, key).replace(" ", "_")
    return key


def import_csv(db: Session, raw: bytes) -> tuple[ImportResult, list[tuple[ProductExtra, dict, dict]]]:
    """Lê a planilha (separador ; ou ,). Só preenche as colunas que vierem com valor.

    Devolve o resultado e as mudanças (complemento, antes, depois) para o histórico.
    """
    if len(raw) > MAX_IMPORT_BYTES:
        return ImportResult(0, 0, [], ["Arquivo grande demais (máximo 2 MB)."]), []
    text = None
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if not text:
        return ImportResult(0, 0, [], ["Não consegui ler o arquivo. Salve como CSV."]), []
    dialect = csv.Sniffer().sniff(text.splitlines()[0] if text else ";", delimiters=";,\t")
    reader = csv.reader(io.StringIO(text), dialect)
    header = [_norm(h) for h in next(reader, [])]
    if "sku" not in header:
        return ImportResult(0, 0, [], ["A planilha precisa de uma coluna 'sku'. Baixe o modelo."]), []
    products = {p.sku.lower(): p for p in db.scalars(select(Product))}
    result, changes = ImportResult(0, 0, [], []), []
    for n, row in enumerate(reader, start=2):
        data = {header[i]: (row[i].strip() if i < len(row) else "") for i in range(len(header))}
        sku = data.get("sku", "")
        if not sku:
            continue
        product = products.get(sku.lower())
        if product is None:
            result.unknown_skus.append(sku)
            continue
        extra = extra_for(db, product)
        before = snapshot(extra)
        if data.get("marca"):
            extra.brand = data["marca"][:80]
        if data.get("fornecedor"):
            extra.supplier = data["fornecedor"][:120]
        if data.get("codigo_fornecedor"):
            extra.supplier_code = data["codigo_fornecedor"][:64]
        if data.get("custo_onix"):
            cents = parse_money(data["custo_onix"])
            if cents is None:
                result.errors.append(f"Linha {n} ({sku}): custo '{data['custo_onix']}' não é um valor.")
            else:
                set_cost(extra, cents)
        for col, attr, hi in (("estoque_minimo", "min_stock", 1_000_000), ("prazo_dias", "lead_time_days", 365)):
            if data.get(col):
                value = to_int(data[col], 0, hi)
                if value is None:
                    result.errors.append(f"Linha {n} ({sku}): {col.replace('_', ' ')} '{data[col]}' inválido.")
                else:
                    setattr(extra, attr, value)
        if data.get("localizacao"):
            extra.location = data["localizacao"][:60]
        if data.get("compatibilidade"):
            items, bad = parse_compatibility(data["compatibilidade"].replace("|", "\n"))
            if bad:
                result.errors.append(f"Linha {n} ({sku}): não entendi a moto '{bad[0]}'.")
            extra.compatibility = items
        if data.get("observacoes"):
            extra.notes = data["observacoes"][:2000]
        after = snapshot(extra)
        if after != before:
            result.updated += 1
            changes.append((extra, before, after))
        else:
            result.unchanged += 1
    return result, changes


def export_csv(rows: list[ProductRow], *, with_costs: bool) -> str:
    out = io.StringIO()
    w = csv.writer(out, delimiter=";")
    cols = [c for c in COLUMNS if with_costs or c != "custo_onix"]
    w.writerow(["nome", *cols, "estoque_olist", *(["custo_olist"] if with_costs else [])])
    for r in rows:
        e = r.extra
        values = {"sku": r.product.sku, "marca": r.brand or "", "fornecedor": e.supplier if e else "", "codigo_fornecedor": e.supplier_code if e else "",
                  "custo_onix": format_brl(e.onix_cost_cents).replace("R$", "").strip() if e and e.onix_cost_cents else "",
                  "estoque_minimo": e.min_stock if e and e.min_stock is not None else "",
                  "prazo_dias": e.lead_time_days if e and e.lead_time_days is not None else "",
                  "localizacao": e.location if e else "",
                  "compatibilidade": format_compatibility(e.compatibility).replace("\n", " | ") if e else "",
                  "observacoes": e.notes if e else ""}
        w.writerow([r.product.name, *[values[c] for c in cols], r.stock if r.stock is not None else "",
                    *([format_brl(r.product.cost_cents).replace("R$", "").strip()] if with_costs else [])])
    return "﻿" + out.getvalue()
