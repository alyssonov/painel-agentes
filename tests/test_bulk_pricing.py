"""Precificação em massa: prévia, propostas (sem enviar) e aprovação em lote."""

from sqlalchemy import select

from app.models import Action, AuditLog, PriceReference, Product
from app.models.enums import ActionStatus, ActionType
from app.services.bulk_pricing import Rule, preview, propose_selected
from app.services.store_settings import set_setting
from tests.conftest import csrf_from, login
from tests.test_permissions import _add
from tests.test_prices import ref


def _setup(db):
    set_setting(db, "tax_rate_pct", 2.5)
    p1 = Product(sku="CAN-01", name="Caneca", cost_cents=3460)
    p2 = Product(sku="SEM-CUSTO", name="Sem custo", cost_cents=0)
    db.add_all([p1, p2])
    db.flush()
    refs = [ref(item_id="MLB1", title="Caneca térmica", product_id=p1.id),
            ref(item_id="MLB2", title="Caneca automática", product_id=p1.id, automated=True),
            ref(item_id="MLB3", title="Caneca promo", product_id=p1.id, current_cents=5990),
            ref(item_id="MLB4", title="Sem custo", product_id=p2.id)]
    db.add_all(refs)
    db.commit()
    return refs


def test_previa_por_margem_com_final_90(db):
    _setup(db)
    rows, floor = preview(db, Rule("margem", 20, True))
    by = {r.ref.item_id: r for r in rows}
    r1 = by["MLB1"]
    assert r1.selectable and r1.new_cents % 100 == 90 and r1.margin_new >= 20
    assert by["MLB2"].blocked.startswith("Automatização") and by["MLB3"].blocked.startswith("Em promoção")
    assert by["MLB4"].blocked == "Sem custo (nem no Onix nem no Olist)" and not by["MLB4"].selectable


def test_reajuste_e_avisos(db):
    _setup(db)
    r = {x.ref.item_id: x for x in preview(db, Rule("reajuste", -40, False))[0]}["MLB1"]
    assert r.new_cents == 4080 and "dá prejuízo" in r.warnings and "muda mais de 30%" in r.warnings
    assert not r.preselected and r.selectable
    up = {x.ref.item_id: x for x in preview(db, Rule("reajuste", 5, False))[0]}["MLB1"]
    assert up.new_cents == 7140 and up.preselected


def test_propor_nao_envia_e_troca_proposta_antiga(db):
    refs = _setup(db)
    first = propose_selected(db, Rule("reajuste", 5, False), {refs[0].id, refs[1].id}, actor="user:1")
    db.commit()
    assert first == 1  # o anúncio com automatização do ML não entra
    again = propose_selected(db, Rule("reajuste", 10, False), {refs[0].id}, actor="user:1")
    db.commit()
    acts = list(db.scalars(select(Action).where(Action.type == ActionType.UPDATE_PRICE).order_by(Action.id)))
    assert again == 1 and [a.status for a in acts] == [ActionStatus.REJECTED, ActionStatus.PENDING]
    assert acts[1].payload["price_cents"] == 7480 and acts[1].topic == "massa"
    db.refresh(refs[0])
    assert refs[0].current_cents == 6800  # nada mudou ainda


def test_tela_propor_e_aprovar_em_lote(logged_client, db):
    refs = _setup(db)
    html = logged_client.get("/precificacao/massa?ver=1&regra=reajuste&valor=5&final90=0").text
    assert "Prévia" in html and "Caneca térmica" in html and 'name="ref"' in html
    token = csrf_from(html)
    r = logged_client.post("/precificacao/massa/propor", data={"csrf_token": token, "regra": "reajuste",
                                                               "valor": "5", "ref": [str(refs[0].id)]})
    assert "1 mudança(s) de preço criada(s)" in r.text and "Caneca térmica" in r.text
    action = db.scalar(select(Action).where(Action.status == ActionStatus.PENDING, Action.topic == "massa"))
    r = logged_client.post("/precificacao/massa/decidir", data={"csrf_token": token, "decisao": "aprovar",
                                                                "acao": [str(action.id)]})
    assert "1 mudança(s) aprovada(s)" in r.text
    db.expire_all()
    assert action.status == ActionStatus.SIMULATED  # modo simulação: nada foi enviado ao ML
    assert db.scalars(select(AuditLog).where(AuditLog.event == "precificacao.massa_proposta")).one()
    approved = db.scalars(select(AuditLog).where(AuditLog.event == "acao.aprovada")).one()
    assert approved.actor.startswith("user:") and approved.target_id == str(refs[0].id)


def test_descartar_em_lote_e_valor_invalido(logged_client, db):
    refs = _setup(db)
    propose_selected(db, Rule("reajuste", 5, False), {refs[0].id}, actor="user:1")
    db.commit()
    token = csrf_from(logged_client.get("/precificacao/massa").text)
    action = db.scalar(select(Action).where(Action.target_id == str(refs[0].id)))
    r = logged_client.post("/precificacao/massa/decidir", data={"csrf_token": token, "decisao": "descartar",
                                                                "acao": [str(action.id)]})
    assert "1 mudança(s) descartada(s)" in r.text
    assert "entre -50% e +100%" in logged_client.get("/precificacao/massa?ver=1&regra=reajuste&valor=500").text


def test_sem_permissao_de_precos(client, db, user):
    _setup(db)
    _add(db, "fin@loja.com", "financeiro")
    login(client, "fin@loja.com")
    assert client.get("/precificacao/massa").status_code == 403
    assert "Preços em massa" not in client.get("/precificacao").text
