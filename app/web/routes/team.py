"""Equipe: quem acessa o painel, com qual papel e quais permissões.

Regras de segurança (conferidas aqui, no servidor):
- só mexe em pessoas da MESMA empresa de quem está editando;
- ninguém desativa a si mesmo nem tira de si a permissão de gerenciar a equipe;
- a empresa sempre fica com pelo menos uma pessoa ativa que gerencia a equipe;
- senha nova derruba as sessões abertas daquela pessoa (ver auth_version);
- toda mudança vai para o histórico com o valor anterior e o novo.
"""

import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.db import get_db
from app.core.permissions import LABELS, PERMISSIONS, ROLES, TEAM, permissions_of, role_label
from app.core.security import hash_password, mask_email, password_problem
from app.core.tenant import company_of
from app.core.timeutil import to_local
from app.models.user import User
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/equipe")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _team(db: Session) -> list[User]:
    # A tabela de usuários não tem o filtro automático (o login é global): filtra aqui.
    return list(db.scalars(select(User).where(User.company_id == company_of(db)).order_by(User.name)))


def _member(db: Session, user_id: int) -> User | None:
    member = db.get(User, user_id)
    return member if member and member.company_id == company_of(db) else None


def _snapshot(member: User) -> dict:
    """Como a pessoa está agora (para o histórico "antes → depois")."""
    return {"nome": member.name, "papel": role_label(member.role), "ativo": member.is_active,
            "permissoes": sorted(LABELS[p] for p in member.effective_permissions)}


def _managers_left(db: Session, without: User, after: frozenset[str] | None = None, active: bool = True) -> int:
    """Quantas pessoas ativas continuariam gerenciando a equipe depois da mudança."""
    count = 0
    for m in _team(db):
        perms, is_active = m.effective_permissions, m.is_active
        if m.id == without.id:
            perms, is_active = (after if after is not None else perms), active
        count += int(is_active and TEAM in perms)
    return count


@router.get("")
def team_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    members = [{"m": m, "role": role_label(m.role), "custom": m.permissions is not None and m.role != "admin",
                "perms": m.effective_permissions,
                "last_login": to_local(m.last_login_at).strftime("%d/%m/%Y %H:%M") if m.last_login_at else "nunca"}
               for m in _team(db)]
    return render(request, "team.html", {"members": members, "roles": ROLES, "permissions": PERMISSIONS}, db=db)


@router.post("/novo", dependencies=[Depends(verify_csrf)])
def add_member(request: Request, name: str = Form(""), email: str = Form(""), role: str = Form("operador"),
               password: str = Form(""), db: Session = Depends(get_db), user: User = Depends(current_user)):
    name, email = name.strip(), email.strip().lower()
    error = None
    if not name or not _EMAIL.match(email):
        error = "Informe o nome e um e-mail válido."
    elif role not in ROLES:
        error = "Escolha um papel da lista."
    elif problem := password_problem(password):
        error = problem
    elif db.scalar(select(User).where(func.lower(User.email) == email)):
        error = "Esse e-mail já tem acesso ao painel."
    if error:
        flash(request, error, "erro")
        return RedirectResponse("/equipe", status_code=303)
    member = User(name=name, email=email, role=role, company_id=company_of(db), password_hash=hash_password(password))
    db.add(member)
    db.flush()
    audit.record(db, actor=f"user:{user.id}", event="equipe.pessoa_adicionada", target_type="user",
                 target_id=member.id, details={"email": mask_email(email), "depois": _snapshot(member)})
    db.commit()
    flash(request, f"{name} foi adicionado(a) como {role_label(role)}. Passe a senha provisória pessoalmente.")
    return RedirectResponse("/equipe", status_code=303)


@router.post("/{user_id}", dependencies=[Depends(verify_csrf)])
async def update_member(user_id: int, request: Request, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    form = await request.form()
    member = _member(db, user_id)
    if member is None:
        flash(request, "Pessoa não encontrada.", "erro")
        return RedirectResponse("/equipe", status_code=303)
    role = str(form.get("role") or member.role)
    if role not in ROLES:
        flash(request, "Escolha um papel da lista.", "erro")
        return RedirectResponse("/equipe", status_code=303)
    custom = None
    if form.get("personalizar") and role != "admin":
        custom = sorted({str(p) for p in form.getlist("perm")} & set(LABELS))
    active = form.get("ativo") == "1"
    new_perms = permissions_of(role, custom)

    if member.id == user.id and (not active or TEAM not in new_perms):
        flash(request, "Você não pode se desativar nem tirar de si a permissão de gerenciar a equipe.", "erro")
        return RedirectResponse("/equipe", status_code=303)
    if _managers_left(db, member, new_perms, active) == 0:
        flash(request, "A loja precisa de pelo menos uma pessoa ativa que gerencia a equipe.", "erro")
        return RedirectResponse("/equipe", status_code=303)

    before = _snapshot(member)
    member.name = str(form.get("name") or member.name).strip()[:120] or member.name
    member.role, member.permissions, member.is_active = role, custom, active
    after = _snapshot(member)
    if before != after:
        audit.record(db, actor=f"user:{user.id}", event="equipe.pessoa_alterada", target_type="user",
                     target_id=member.id, details={"antes": before, "depois": after})
    db.commit()
    flash(request, f"Alterações de {member.name} salvas." if before != after else "Nada mudou.")
    return RedirectResponse("/equipe", status_code=303)


@router.post("/{user_id}/senha", dependencies=[Depends(verify_csrf)])
def reset_password(user_id: int, request: Request, password: str = Form(""), db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    member = _member(db, user_id)
    if member is None:
        flash(request, "Pessoa não encontrada.", "erro")
        return RedirectResponse("/equipe", status_code=303)
    if problem := password_problem(password):
        flash(request, problem, "erro")
        return RedirectResponse("/equipe", status_code=303)
    member.password_hash = hash_password(password)  # muda o auth_version: derruba as sessões abertas
    member.failed_logins, member.locked_until = 0, None
    audit.record(db, actor=f"user:{user.id}", event="equipe.senha_redefinida", target_type="user",
                 target_id=member.id)  # a senha nunca vai para o histórico
    db.commit()
    if member.id == user.id:
        flash(request, "Sua senha foi trocada. Entre de novo com a senha nova.")
        return RedirectResponse("/login", status_code=303)
    flash(request, f"Senha de {member.name} redefinida. Ela precisa entrar de novo.")
    return RedirectResponse("/equipe", status_code=303)
