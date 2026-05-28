"""
Envio SMTP dos alertas do MVP Buybox.

Suporta dois modos:
  - `enabled=true` em settings.buybox.email → envio real
  - `enabled=false` OU `dry_run=true` → não envia, retorna False (o
    avaliador registra o alerta como "suprimido" no banco mesmo assim).

Credenciais lidas de variáveis de ambiente indicadas em
`buybox.email.remetente_env` / `buybox.email.senha_env`. Não logamos
senha em nenhuma circunstância.
"""

from __future__ import annotations

import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Iterable


class EmailDesabilitado(Exception):
    """Levantada quando email.enabled=false (não é erro, é configuração)."""


class CredenciaisFaltando(Exception):
    """Levantada quando enabled=true mas faltam env vars."""


def _resolver_credenciais(cfg_email: dict) -> tuple[str, str]:
    """Lê remetente/senha das env vars definidas em settings."""
    remetente_env = cfg_email.get("remetente_env", "EMAIL_REMETENTE")
    senha_env = cfg_email.get("senha_env", "EMAIL_SENHA_APP")
    remetente = os.getenv(remetente_env, "")
    senha = os.getenv(senha_env, "")
    if not remetente or not senha:
        raise CredenciaisFaltando(
            f"Defina {remetente_env} e {senha_env} no .env antes de habilitar."
        )
    return remetente, senha


def enviar_email(
    assunto: str,
    corpo_html: str,
    cfg_email: dict,
    destinatarios_override: Iterable[str] | None = None,
) -> bool:
    """
    Envia um e-mail HTML via SMTP. Devolve True em sucesso.

    Parâmetros
    ----------
    cfg_email : seção `buybox.email` do settings.yaml
    destinatarios_override : usa essa lista em vez de cfg_email['destinatarios']
                             (útil para testes ou alertas direcionados)
    """
    if not cfg_email.get("enabled"):
        raise EmailDesabilitado()

    destinatarios = list(destinatarios_override or cfg_email.get("destinatarios") or [])
    if not destinatarios:
        raise CredenciaisFaltando(
            "Lista buybox.email.destinatarios está vazia."
        )

    remetente, senha = _resolver_credenciais(cfg_email)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = assunto
    msg["From"] = remetente
    msg["To"] = ", ".join(destinatarios)
    msg.attach(MIMEText(corpo_html, "html", "utf-8"))

    host = cfg_email.get("smtp_host", "smtp.gmail.com")
    port = int(cfg_email.get("smtp_port", 587))

    with smtplib.SMTP(host, port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(remetente, senha)
        smtp.sendmail(remetente, destinatarios, msg.as_string())
    return True
