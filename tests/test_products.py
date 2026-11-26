"""Produtos: cadastro complementar (só no Gerente do Ano), compatibilidade e planilha."""

from sqlalchemy import select

from app.core.db import utcnow
from app.models import AuditLog, Product, StockSnapshot
from app.models.product_extra import ProductExtra
from app.services.products import format_compatibility, list_products, parse_compatibility
from tests.conftest import csrf_from, login
from tests.test_permissions import _add


def _product(db, sku="PAST-CG160", name="Pastilha de freio dianteira", cost=1890, stock=3):
    p = Product(sku=sku, name=name, cost_cents=cost, olist_id=f"o-{sku}")
    db.add(p)
    db.flush()
    db.add(StockSnapshot(product_id=p.id, quantity=stock, reserved=0, captured_at=utcnow()))
    db.commit()
    return p


def test_compatibilidade_em_texto_simples():
    items, bad = parse_compatibility("Honda CG 160 2016-2024\nYamaha Factor 150 2017\nHarley-Davidson Iron 883\n"
                                     "Honda Biz 125 2011 a 2020\nCG\nHonda CG 2030-2010")
    assert items[0] == {"marca": "Honda", "modelo": "CG 160", "de": 2016, "ate": 2024}
    assert items[1] == {"marca": "Yamaha", "modelo": "Factor 150", "de": 2017, "ate": 2017}
    assert items[2] == {"marca": "Harley-Davidson", "modelo": "Iron 883", "de": None, "ate": None}
    assert items[3]["de"] == 2011 and items[3]["ate"] == 2020
    assert bad == ["CG", "Honda CG 2030-2010"]
    assert format_compatibility(items[:2]) == "Honda CG 160 2016-2024\nYamaha Factor 150 2017"


def test_editar_cadastro_nao_mexe_no_olist(logged_client, db):
    p = _product(db)
    html = logged_client.get(f"/produtos/{p.id}").text
    token = csrf_from(html)
    r = logged_client.post(f"/produtos/{p.id}", data={
        "csrf_token": token, "supplier": "Distribuidora X", "onix_cost": "21,50", "min_stock": "5",
        "lead_time_days": "7", "location": "A3-02", "compatibility": "Honda CG 160 2016-2024\nHonda Fan 160 2016-2024"})
    assert "Cadastro salvo" in r.text and "o Olist não foi alterado" in r.text
    extra = db.scalar(select(ProductExtra).where(ProductExtra.product_id == p.id))
    assert extra.onix_cost_cents == 2150 and extra.min_stock == 5 and len(extra.compatibility) == 2
    db.refresh(p)
    assert p.cost_cents == 1890  # custo do Olist intacto
    log = db.scalars(select(AuditLog).where(AuditLog.event == "produto.cadastro_alterado")).one()
    assert log.details["depois"]["custo_onix"] == "R$ 21,50" and log.details["antes"]["fornecedor"] is None
    # filtros: abaixo do mínimo (3 < 5), custo diferente (+13%), busca pela moto
    assert [r.product.sku for r in list_products(db, flt="abaixo_minimo")] == ["PAST-CG160"]
    assert [r.product.sku for r in list_products(db, flt="custo_diferente")] == ["PAST-CG160"]
    assert [r.product.sku for r in list_products(db, q="fan 160")] == ["PAST-CG160"]
    # moto que não dá para entender: nada é salvo
    r = logged_client.post(f"/produtos/{p.id}", data={"csrf_token": token, "compatibility": "XYZ", "min_stock": "9"})
    assert "Não entendi estas motos" in r.text
    db.expire_all()
    assert extra.min_stock == 5


def test_planilha_do_onix(logged_client, db):
    _product(db)
    _product(db, sku="RET-01", name="Retentor", cost=500)
    token = csrf_from(logged_client.get("/produtos").text)
    sheet = ("SKU;Custo;Fornecedor;Estoque mínimo;Compatibilidade;Local\n"
             "past-cg160;19,90;Dist Y;4;Honda CG 160 2016-2024 | Honda Fan 160 2016;B1\n"
             "RET-01;;;2;;\n"
             "NAO-EXISTE;1,00;;;;\n"
             "RET-01;abc;;;;\n").encode("cp1252")
    r = logged_client.post("/produtos/planilha", data={"csrf_token": token},
                           files={"arquivo": ("onix.csv", sheet, "text/csv")})
    assert "2 produto(s) atualizado(s)" in r.text and "NAO-EXISTE" in r.text and "não é um valor" in r.text
    extras = {e.product.sku: e for e in db.scalars(select(ProductExtra))}
    assert extras["PAST-CG160"].onix_cost_cents == 1990 and extras["PAST-CG160"].location == "B1"
    assert len(extras["PAST-CG160"].compatibility) == 2 and extras["RET-01"].min_stock == 2
    assert db.scalars(select(AuditLog).where(AuditLog.event == "produto.planilha_importada")).one()
    csv = logged_client.get("/produtos.csv").text
    assert "Honda CG 160 2016-2024 | Honda Fan 160 2016" in csv and "custo_onix" in csv
    assert "sku;fornecedor" in logged_client.get("/produtos/modelo.csv").text


def test_quem_nao_ve_financeiro_nao_ve_nem_muda_custo(client, db, user):
    p = _product(db)
    _add(db, "op@loja.com", "operador")
    login(client, "op@loja.com")
    html = client.get(f"/produtos/{p.id}").text
    assert "Custo (Olist)" not in html and 'name="onix_cost"' not in html and 'name="supplier"' in html
    token = csrf_from(html)
    client.post(f"/produtos/{p.id}", data={"csrf_token": token, "onix_cost": "1,00", "supplier": "Z"})
    extra = db.scalar(select(ProductExtra).where(ProductExtra.product_id == p.id))
    assert extra.supplier == "Z" and extra.onix_cost_cents is None
    assert "custo_onix" not in client.get("/produtos.csv").text
    r = client.post("/produtos/planilha", data={"csrf_token": token},
                    files={"arquivo": ("c.csv", b"sku;custo_onix\nPAST-CG160;5,00\n", "text/csv")})
    assert "só quem vê o financeiro" in r.text


def test_atendimento_so_olha(client, db, user):
    p = _product(db)
    _add(db, "at@loja.com", "atendimento")
    login(client, "at@loja.com")
    html = client.get(f"/produtos/{p.id}").text
    assert client.get("/produtos").status_code == 200 and 'name="supplier"' not in html
    token = csrf_from(client.get("/perguntas").text)
    assert client.post(f"/produtos/{p.id}", data={"csrf_token": token, "supplier": "X"}).status_code == 403


def test_historico_do_produto(logged_client, db):
    p = _product(db)
    token = csrf_from(logged_client.get(f"/produtos/{p.id}").text)
    logged_client.post(f"/produtos/{p.id}", data={"csrf_token": token, "location": "C9"})
    html = logged_client.get(f"/historico?dias=30&registro=produto:{p.id}").text
    assert "Mudou o cadastro do produto" in html and "Local" in html and "C9" in html
    assert "Produto: Pastilha de freio dianteira" in html
