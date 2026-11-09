"""Envio de e-mail pelo SMTP configurado no .env (Gmail: senha de app).

Usado só para mensagens internas (relatórios para a loja).
"""

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.config import get_settings

EMAIL_RE = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


class EmailError(Exception):
    """Falha ao enviar. A mensagem pode ser mostrada no painel."""


def parse_recipients(text: str | None) -> list[str]:
    """Lista de e-mails a partir de um texto separado por vírgula, ponto e vírgula ou linha."""
    parts = re.split(r"[,;\s]+", text or "")
    return [p.strip().lower() for p in parts if p.strip()]


def invalid_recipients(emails: list[str]) -> list[str]:
    return [e for e in emails if not EMAIL_RE.match(e)]


def send_email(to: list[str], subject: str, text: str, html: str | None = None,
               attachments: list[tuple[str, bytes, str]] | None = None) -> None:
    """Envia o e-mail. attachments: (nome do arquivo, conteúdo, tipo MIME)."""
    settings = get_settings()
    if not settings.email_configured:
        raise EmailError("O e-mail que envia os relatórios não está configurado (SMTP_USER e SMTP_PASSWORD no .env).")
    if not to:
        raise EmailError("Nenhum destinatário cadastrado.")
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr(("Gerente do Ano", settings.email_from or settings.smtp_user))
    message["To"] = ", ".join(to)
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    for filename, content, mime in attachments or []:
        maintype, subtype = mime.split("/", 1)
        message.add_attachment(content, maintype=maintype, subtype=subtype, filename=filename)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        raise EmailError("O Gmail recusou o login. Confira SMTP_USER e a senha de app (SMTP_PASSWORD) no .env.") from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise EmailError("Algum destinatário foi recusado. Confira os e-mails em Relatórios.") from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise EmailError(f"Não foi possível enviar o e-mail agora ({type(exc).__name__}).") from exc
