"""Assistente (chat de IA): tela cheia em /assistente e o botão flutuante em todas as páginas."""

import csv
import io
import logging

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai.assistant import ask, preview_tools_used
from app.ai.client import AIError
from app.core import audit
from app.core.db import get_db
from app.core.timeutil import to_local
from app.models.assistant import AssistantConversation, AssistantMessage
from app.models.user import User
from app.web.auth import current_user, verify_csrf
from app.web.markdown_lite import parse_tables, render
from app.web.templating import flash
from app.web.templating import render as render_page

logger = logging.getLogger("painel.assistente")
router = APIRouter(prefix="/assistente")
MAX_QUESTION = 2000
SUGGESTIONS = [
    "Quanto vendi esta semana por marketplace?",
    "Quais produtos preciso comprar?",
    "O que está esperando por mim hoje?",
    "Onde vejo o lucro de uma venda?",
    "Monte um relatório dos 10 produtos que mais venderam no mês",
    "Como cadastro a compatibilidade das motos?",
]


def _conversation(db: Session, user: User, conv_id: int | None) -> AssistantConversation | None:
    if not conv_id:
        return None
    conv = db.get(AssistantConversation, conv_id)
    return conv if conv and conv.user_id == user.id else None  # cada pessoa só vê as suas


def message_view(m: AssistantMessage) -> dict:
    if m.role == "user":
        html = render(m.text)
    else:
        html = render(m.text, table_url=f"/assistente/mensagem/{m.id}/tabela")
    return {"id": m.id, "role": m.role, "html": html, "tools": preview_tools_used(m.tools_used or []),
            "at": to_local(m.created_at).strftime("%d/%m %H:%M")}


@router.get("")
def assistant_page(request: Request, c: int | None = None, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    conversations = list(db.scalars(select(AssistantConversation).where(AssistantConversation.user_id == user.id)
                                    .order_by(AssistantConversation.updated_at.desc()).limit(30)))
    current = _conversation(db, user, c)
    return render_page(request, "assistant.html", {
        "conversations": conversations, "current": current,
        "messages": [message_view(m) for m in current.messages] if current else [],
        "suggestions": SUGGESTIONS,
    }, db=db)


@router.post("/perguntar", dependencies=[Depends(verify_csrf)])
def ask_route(request: Request, pergunta: str = Form(""), conversa: str = Form(""), db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    """Recebe a pergunta, responde e guarda. Com JavaScript devolve JSON; sem, volta para a página."""
    wants_json = request.headers.get("X-Requested-With") == "fetch"
    question = pergunta.strip()[:MAX_QUESTION]
    conv = _conversation(db, user, int(conversa) if conversa.isdigit() else None)

    def fail(message: str, status: int = 400):
        if wants_json:
            return JSONResponse({"erro": message, "conversa": conv.id if conv else None}, status_code=status)
        flash(request, message, "erro")
        return RedirectResponse(f"/assistente{f'?c={conv.id}' if conv else ''}", status_code=303)

    if not question:
        return fail("Escreva uma pergunta.")
    history = [(m.role, m.text) for m in conv.messages] if conv else []
    try:
        answer = ask(db, user, history, question)
    except AIError as exc:
        db.commit()  # guarda o custo das chamadas que já aconteceram
        return fail(str(exc), 503)
    except Exception:  # noqa: BLE001 - nunca derrubar a página por causa do chat
        logger.exception("Erro no assistente")
        db.rollback()
        return fail("Algo deu errado ao responder. Tente de novo em instantes.", 500)

    if conv is None:
        conv = AssistantConversation(user_id=user.id, title=question[:110])
        db.add(conv)
        db.flush()
    db.add(AssistantMessage(conversation_id=conv.id, role="user", text=question))
    reply = AssistantMessage(conversation_id=conv.id, role="assistant", text=answer.text,
                             tables=parse_tables(answer.text), tools_used=answer.tools_used,
                             cost_brl_millicents=answer.cost_brl_millicents)
    db.add(reply)
    conv.title = conv.title or question[:110]
    db.flush()
    # Histórico: só que houve uma pergunta e quais consultas a IA fez (o texto fica na conversa da pessoa).
    audit.record(db, actor=f"user:{user.id}", event="assistente.pergunta", target_type="conversa_ia",
                 target_id=conv.id, details={"consultas": answer.tools_used})
    db.commit()
    if wants_json:
        return JSONResponse({"conversa": conv.id, "resposta": message_view(reply)})
    return RedirectResponse(f"/assistente?c={conv.id}#fim", status_code=303)


@router.post("/conversa/{conv_id}/apagar", dependencies=[Depends(verify_csrf)])
def delete_conversation(request: Request, conv_id: int, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    conv = _conversation(db, user, conv_id)
    if conv:
        db.delete(conv)
        db.commit()
        flash(request, "Conversa apagada.")
    return RedirectResponse("/assistente", status_code=303)


@router.get("/mensagem/{message_id}/tabela/{n}.csv")
def table_csv(message_id: int, n: int, db: Session = Depends(get_db), user: User = Depends(current_user)):
    message = db.get(AssistantMessage, message_id)
    if message is None or message.conversation.user_id != user.id or not (0 <= n < len(message.tables or [])):
        return Response("Tabela não encontrada.", status_code=404)
    table = message.tables[n]
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow([c.replace("**", "") for c in table["header"]])
    for row in table["rows"]:
        writer.writerow([c.replace("**", "") for c in row])
    return Response("﻿" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="relatorio-assistente-{message_id}-{n + 1}.csv"'})
