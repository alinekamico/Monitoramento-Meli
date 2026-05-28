"""
Envia um e-mail simples para validar credenciais SMTP do MVP Buybox.

Lê `buybox.email` do settings.yaml + variáveis do .env. Não toca em
banco de dados, não busca snapshots — só dispara um HTML básico.

Uso:
    python -m scripts.enviar_email_teste
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml  # noqa: E402
from dotenv import load_dotenv  # noqa: E402

from src.alertas import email as email_mod  # noqa: E402


_CONFIG_PATH = Path(__file__).parent.parent / "config" / "settings.yaml"


def main() -> int:
    load_dotenv()

    with open(_CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    cfg_email = (cfg.get("buybox", {}) or {}).get("email", {}) or {}
    destinatarios = cfg_email.get("destinatarios") or []

    if not cfg_email.get("enabled"):
        print("✗ buybox.email.enabled = false — habilite antes de testar.")
        return 1
    if not destinatarios:
        print("✗ buybox.email.destinatarios está vazia.")
        return 1

    assunto = f"[Buybox] Teste de envio — {datetime.now():%d/%m/%Y %H:%M}"
    corpo = (
        '<div style="font-family:Arial,sans-serif;">'
        '<h2 style="color:#2563eb;">✓ E-mail funcionando</h2>'
        '<p>Se você está vendo isto, o pipeline de alertas do MVP Buybox '
        'está configurado corretamente.</p>'
        f'<p><b>Destinatários:</b> {", ".join(destinatarios)}</p>'
        f'<p><b>Host SMTP:</b> {cfg_email.get("smtp_host")}:{cfg_email.get("smtp_port")}</p>'
        f'<p><b>Carimbo:</b> {datetime.now().isoformat()}</p>'
        '<hr><p style="font-size:11px;color:#6b7280;">'
        'MVP Buybox — Central de Promoções ML</p>'
        '</div>'
    )

    print(f"Enviando para {destinatarios}…")
    try:
        email_mod.enviar_email(assunto, corpo, cfg_email)
        print("✓ Enviado com sucesso. Cheque a caixa de entrada (e o spam).")
        return 0
    except email_mod.EmailDesabilitado:
        print("✗ E-mail desabilitado no settings.")
        return 1
    except email_mod.CredenciaisFaltando as exc:
        print(f"✗ Credenciais: {exc}")
        return 1
    except Exception as exc:
        print(f"✗ Falha no envio: {exc.__class__.__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
