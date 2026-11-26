"""Papéis, permissões (conferidas no servidor) e a tela Equipe."""

import re

import pytest
from fastapi.routing import APIRoute
from sqlalchemy import select

from app.core.permissions import ALL, FINANCE, ROUTE_RULES, SUPPORT, TEAM, VIEW, permissions_of, required_permission
from app.core.security import hash_password
from app.models import AuditLog, Company, User
from tests.conftest import TEST_PASSWORD, csrf_from, login


def _add(db, email, role, perms=None, company_id=1, active=True):
    u = User(email=email, name=email.split("@")[0].title(), role=role, permissions=perms, company_id=company_id,
             is_active=active, password_hash=hash_password(TEST_PASSWORD))
    db.add(u)
    db.commit()
    return u


def test_regras_de_permissao():
    assert required_permission("GET", "/margem/venda/3") == FINANCE
    assert required_permission("POST", "/perguntas/acao/1/enviar") == SUPPORT
    assert required_permission("GET", "/perguntas") == VIEW
    assert required_permission("POST", "/equipe/2") == TEAM
    assert required_permission("POST", "/tema") is None
    assert required_permission("POST", "/rota-nova-sem-regra") == VIEW  # padrão: no mínimo ver o painel
    assert permissions_of("admin", []) == ALL  # administrador sempre tem tudo
    assert permissions_of("atendimento", None) == {VIEW, SUPPORT, "usar_assistente"}
    assert permissions_of("atendimento", [FINANCE, "inventada"]) == {FINANCE}


def test_toda_rota_tem_regra(client):
    """Nenhuma rota do painel fica sem permissão por engano (exceto as públicas)."""
    public = {"/login", "/saude", "/health", "/static"}
    for route in client.app.routes:
        if not isinstance(route, APIRoute) or any(route.path.startswith(p) for p in public):
            continue
        for method in route.methods - {"HEAD"}:
            path = re.sub(r"\{[^}]+\}", "1", route.path)
            perm = required_permission(method, path)
            assert perm is None or perm in ALL, (method, route.path)
            if perm is None:
                assert path in ("/tema", "/logout"), route.path
    assert ROUTE_RULES[-1][2] == VIEW


def test_atendimento_nao_ve_financeiro_nem_equipe(client, db, user):
    _add(db, "atende@loja.com", "atendimento")
    assert login(client, "atende@loja.com").status_code == 303
    home = client.get("/").text
    assert "Lucro real do mês" not in home and "Margem média real" not in home and 'href="/equipe"' not in home
    assert 'href="/perguntas"' in home and 'href="/margem"' not in home
    r = client.get("/margem")
    assert r.status_code == 403 and "Ver o financeiro" in r.text
    assert client.get("/equipe").status_code == 403
    assert client.get("/integracoes").status_code == 403
    # Ação também é barrada no servidor (não basta esconder o botão)
    token = csrf_from(home)
    assert client.post("/agentes/perguntas/modo", data={"csrf_token": token, "mode": "automatico"}).status_code == 403
    assert client.get("/perguntas").status_code == 200


def test_permissao_personalizada(client, db, user):
    _add(db, "fin@loja.com", "atendimento", perms=[VIEW, FINANCE])
    login(client, "fin@loja.com")
    assert client.get("/margem").status_code == 200
    token = csrf_from(client.get("/perguntas").text)
    assert client.post("/perguntas/acao/1/ignorar", data={"csrf_token": token}).status_code == 403


def test_equipe_adicionar_editar_desativar(logged_client, db, user):
    html = logged_client.get("/equipe").text
    assert "Adicionar pessoa" in html and "O que cada papel pode fazer" in html
    token = csrf_from(html)
    r = logged_client.post("/equipe/novo", data={"csrf_token": token, "name": "Ana", "email": "Ana@Loja.com",
                                                 "role": "atendimento", "password": "senhaAna12345"})
    assert "Ana foi adicionado(a) como Atendimento" in r.text
    ana = db.scalar(select(User).where(User.email == "ana@loja.com"))
    assert ana.role == "atendimento" and ana.company_id == user.company_id and ana.password_hash != "senhaAna12345"
    # e-mail repetido e senha fraca são recusados
    assert "já tem acesso" in logged_client.post("/equipe/novo", data={
        "csrf_token": token, "name": "X", "email": "ana@loja.com", "role": "atendimento",
        "password": "senhaAna12345"}).text
    assert "pelo menos 10" in logged_client.post("/equipe/novo", data={
        "csrf_token": token, "name": "X", "email": "x@loja.com", "role": "atendimento", "password": "curta1"}).text
    # personalizar e desativar
    logged_client.post(f"/equipe/{ana.id}", data={"csrf_token": token, "name": "Ana Souza", "role": "atendimento",
                                                  "ativo": "0", "personalizar": "1", "perm": [VIEW, FINANCE]})
    db.expire_all()
    assert ana.name == "Ana Souza" and not ana.is_active and ana.permissions == sorted([VIEW, FINANCE])
    log = db.scalars(select(AuditLog).where(AuditLog.event == "equipe.pessoa_alterada")).one()
    assert log.details["antes"]["ativo"] is True and log.details["depois"]["ativo"] is False
    assert "Ver o financeiro" in log.details["depois"]["permissoes"]
    # Desativada não entra
    logged_client.post("/logout", data={"csrf_token": token})
    assert login(logged_client, "ana@loja.com", "senhaAna12345").status_code == 401


def test_travas_de_seguranca_da_equipe(logged_client, db, user):
    token = csrf_from(logged_client.get("/equipe").text)
    r = logged_client.post(f"/equipe/{user.id}", data={"csrf_token": token, "role": "admin", "ativo": "0"})
    assert "não pode se desativar" in r.text
    r = logged_client.post(f"/equipe/{user.id}", data={"csrf_token": token, "role": "gerente", "ativo": "1"})
    assert "não pode se desativar nem tirar de si" in r.text
    db.expire_all()
    assert user.role == "admin" and user.is_active
    # Pessoa de outra empresa: nem aparece, nem pode ser alterada
    db.add(Company(id=2, name="Outra"))
    db.commit()
    other = _add(db, "outro@outra.com", "admin", company_id=2)
    assert "outro@outra.com" not in logged_client.get("/equipe").text
    r = logged_client.post(f"/equipe/{other.id}", data={"csrf_token": token, "role": "operador", "ativo": "0"})
    assert "não encontrada" in r.text
    db.expire_all()
    assert other.is_active and other.role == "admin"


def test_redefinir_senha_derruba_sessao(client, db, user):
    ana = _add(db, "ana@loja.com", "atendimento")
    login(client, "ana@loja.com")
    assert client.get("/perguntas").status_code == 200
    from fastapi.testclient import TestClient
    with TestClient(client.app) as admin:
        login(admin)
        token = csrf_from(admin.get("/equipe").text)
        r = admin.post(f"/equipe/{ana.id}/senha", data={"csrf_token": token, "password": "novaSenha2026x"})
        assert "Senha de Ana redefinida" in r.text
    assert client.get("/perguntas", follow_redirects=False).status_code == 303  # sessão antiga caiu
    log = db.scalars(select(AuditLog).where(AuditLog.event == "equipe.senha_redefinida")).one()
    assert "novaSenha2026x" not in str(log.details)


@pytest.mark.parametrize("role", ["gerente", "financeiro", "operador", "atendimento"])
def test_menu_so_mostra_o_permitido(client, db, user, role):
    _add(db, f"{role}@loja.com", role)
    login(client, f"{role}@loja.com")
    html = client.get("/").text
    for href in re.findall(r'class="nav-item[^"]*" href="([^"]+)"|href="([^"]+)" class="nav-item', html):
        target = href[0] or href[1]
        assert client.get(target).status_code == 200, (role, target)
