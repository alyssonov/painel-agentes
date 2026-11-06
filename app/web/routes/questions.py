"""Seção Perguntas: fila de aprovação das respostas sugeridas pelo agente."""

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import actions
from app.core.db import get_db
from app.core.timeutil import local_day_bounds_utc, local_today, to_local
from app.models import Action, Question
from app.models.enums import CHANNEL_LABELS, ActionStatus, ActionType, Channel, QuestionStatus
from app.models.user import User
from app.web.auth import current_user, verify_csrf
from app.web.templating import flash, render

router = APIRouter(prefix="/perguntas")

MAX_CHARS = 2000


def _channel_label(channel: str) -> str:
    try:
        return CHANNEL_LABELS[Channel(channel)]
    except ValueError:
        return channel


@router.get("")
def questions_page(request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)):
    queue = []
    for action in db.scalars(select(Action).where(
            Action.type == ActionType.ANSWER_QUESTION,
            Action.status.in_([ActionStatus.PENDING, ActionStatus.FAILED])).order_by(Action.created_at)):
        question = db.get(Question, int(action.target_id))
        if question is not None:
            queue.append({"action": action, "q": question, "asked": to_local(question.asked_at).strftime("%d/%m %H:%M"),
                          "channel_label": _channel_label(question.channel)})

    handled_ids = {int(item["q"].id) for item in queue}
    unhandled = [
        {"q": q, "asked": to_local(q.asked_at).strftime("%d/%m %H:%M"), "channel_label": _channel_label(q.channel)}
        for q in db.scalars(select(Question).where(
            Question.status.in_([QuestionStatus.NEW, QuestionStatus.HUMAN, QuestionStatus.PROPOSED]))
            .order_by(Question.asked_at))
        if q.id not in handled_ids
    ]

    start, end = local_day_bounds_utc(local_today())
    answered = [
        {"q": q, "at": to_local(q.answered_at).strftime("%H:%M"), "channel_label": _channel_label(q.channel)}
        for q in db.scalars(select(Question).where(
            Question.answered_at >= start, Question.answered_at < end,
            Question.status.in_([QuestionStatus.ANSWERED, QuestionStatus.SIMULATED]))
            .order_by(Question.answered_at.desc()).limit(50))
    ]
    return render(request, "questions.html", {
        "queue": queue, "unhandled": unhandled, "answered": answered, "max_chars": MAX_CHARS,
    }, db=db)


def _clean_text(text: str) -> str | None:
    text = (text or "").strip()
    return text if 0 < len(text) <= MAX_CHARS else None


def _result_flash(request: Request, action: Action) -> None:
    if action.status == ActionStatus.SIMULATED:
        flash(request, "Resposta aprovada. Modo simulação: nada foi enviado ao cliente.")
    elif action.status == ActionStatus.EXECUTED:
        flash(request, "Resposta enviada ao cliente.")
    else:
        flash(request, f"A resposta não foi enviada: {action.result}", "erro")


@router.post("/acao/{action_id}/enviar", dependencies=[Depends(verify_csrf)])
def send_answer(request: Request, action_id: int, text: str = Form(""), db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    """Aprovar (texto igual à sugestão) ou enviar o texto editado/escrito por você."""
    action = db.get(Action, action_id)
    clean = _clean_text(text)
    if action is None or action.type != ActionType.ANSWER_QUESTION:
        flash(request, "Pergunta não encontrada.", "erro")
    elif clean is None:
        flash(request, f"Escreva uma resposta (até {MAX_CHARS} caracteres).", "erro")
    else:
        try:
            actions.approve(db, action, actor=f"user:{user.id}", edited_payload={"text": clean})
            db.commit()
            _result_flash(request, action)
        except actions.ActionNotAllowed as exc:
            flash(request, str(exc), "erro")
    return RedirectResponse("/perguntas", status_code=303)


@router.post("/acao/{action_id}/ignorar", dependencies=[Depends(verify_csrf)])
def ignore_answer(request: Request, action_id: int, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Tira da fila sem responder (ex.: vou responder direto no marketplace)."""
    action = db.get(Action, action_id)
    if action is not None:
        try:
            actions.reject(db, action, actor=f"user:{user.id}", reason="Ignorada no painel")
            question = db.get(Question, int(action.target_id))
            if question is not None and question.status != QuestionStatus.ANSWERED:
                question.status = QuestionStatus.CLOSED
            db.commit()
            flash(request, "Pergunta tirada da fila. Nada foi enviado.")
        except actions.ActionNotAllowed as exc:
            flash(request, str(exc), "erro")
    return RedirectResponse("/perguntas", status_code=303)


@router.post("/{question_id}/responder", dependencies=[Depends(verify_csrf)])
def answer_myself(request: Request, question_id: int, text: str = Form(""), db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    """Responder uma pergunta que ainda não tem sugestão do agente."""
    question = db.get(Question, question_id)
    clean = _clean_text(text)
    if question is None:
        flash(request, "Pergunta não encontrada.", "erro")
    elif clean is None:
        flash(request, f"Escreva uma resposta (até {MAX_CHARS} caracteres).", "erro")
    else:
        action = actions.propose(db, agent_slug=None, type=ActionType.ANSWER_QUESTION, channel=question.channel,
                                 target_type="pergunta", target_id=question.id, payload={"text": clean},
                                 reason="Escrita por uma pessoa no painel")
        actions.approve(db, action, actor=f"user:{user.id}")
        db.commit()
        _result_flash(request, action)
    return RedirectResponse("/perguntas", status_code=303)
