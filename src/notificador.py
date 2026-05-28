"""
Log estruturado: terminal (colorido) + arquivo diário JSON-lines + e-mail opcional.

Cada evento é gravado como uma linha JSON em logs/YYYY-MM-DD.log.
Se email.enabled=true em settings.yaml, envia resumo ao fim da execução.
"""

from __future__ import annotations

import json
import os
import smtplib
import sys
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path


# Códigos ANSI (desativados automaticamente se não for TTY)
_USE_COLOR = sys.stdout.isatty()
_GREEN  = "\033[92m" if _USE_COLOR else ""
_RED    = "\033[91m" if _USE_COLOR else ""
_YELLOW = "\033[93m" if _USE_COLOR else ""
_CYAN   = "\033[96m" if _USE_COLOR else ""
_BOLD   = "\033[1m"  if _USE_COLOR else ""
_RESET  = "\033[0m"  if _USE_COLOR else ""

_log_file: Path | None = None
_email_cfg: dict = {}
_aceitas:  list[dict] = []
_recusadas: list[dict] = []


def setup(log_dir: str, log_output: str, email_cfg: dict | None = None) -> None:
    """Inicializa o destino do log e configuração de e-mail."""
    global _log_file, _email_cfg, _aceitas, _recusadas
    _aceitas = []
    _recusadas = []
    _email_cfg = email_cfg or {}
    if log_output in ("file", "both"):
        dir_path = Path(log_dir)
        dir_path.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        _log_file = dir_path / f"{date_str}.log"


def _write_file(event: dict) -> None:
    if _log_file is None:
        return
    with open(_log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Eventos públicos
# ---------------------------------------------------------------------------

def inicio_execucao(dry_run: bool, total_skus: int) -> None:
    modo = "DRY-RUN" if dry_run else "ATIVO"
    print(f"\n{_BOLD}{_CYAN}{'='*60}{_RESET}")
    print(f"{_BOLD}{_CYAN}  Central de Promoções ML — {modo}  |  {_now()}{_RESET}")
    print(f"{_BOLD}{_CYAN}  {total_skus} SKU(s) rastreados{_RESET}")
    print(f"{_BOLD}{_CYAN}{'='*60}{_RESET}\n")
    _write_file({"evento": "inicio", "modo": modo, "total_skus": total_skus,
                 "ts": datetime.now().isoformat()})


def sku_sem_mlbs(sku: str) -> None:
    print(f"  {_YELLOW}⚠{_RESET}  {sku}: nenhum anúncio encontrado no ML")
    _write_file({"evento": "sku_sem_mlbs", "sku": sku, "ts": datetime.now().isoformat()})


def _stock_tag(has_stock: bool) -> str:
    return "" if has_stock else f" {_YELLOW}[SEM ESTOQUE]{_RESET}"


def sem_campanhas(sku: str, item_id: str, has_stock: bool = True) -> None:
    print(f"  {_CYAN}–{_RESET}  {sku} [{item_id}]{_stock_tag(has_stock)}: sem campanhas disponíveis")
    _write_file({"evento": "sem_campanhas", "sku": sku, "item_id": item_id,
                 "has_stock": has_stock, "ts": datetime.now().isoformat()})


def sem_rebate(sku: str, item_id: str, has_stock: bool = True) -> None:
    print(f"  {_YELLOW}○{_RESET}  {sku} [{item_id}]{_stock_tag(has_stock)}: campanhas disponíveis, mas nenhuma com rebate do ML")
    _write_file({"evento": "sem_rebate", "sku": sku, "item_id": item_id,
                 "has_stock": has_stock, "ts": datetime.now().isoformat()})


def campanha_ativa(sku: str, item_id: str, campanha: dict, margem: dict, is_full: bool = False, has_stock: bool = True) -> None:
    preco  = margem["preco_campanha"]
    rebate = margem["rebate"]
    rc     = margem["rc_pct"]
    lucro  = margem["lucro_bruto"]
    nome   = campanha.get("name") or campanha.get("type") or "—"
    rebate_str = f"R$ {rebate:.2f}" if rebate > 0 else "—"
    full_tag  = f" {_BOLD}[FULL]{_RESET}" if is_full else ""
    stock_tag = _stock_tag(has_stock)

    print(
        f"  {_CYAN}{_BOLD}★{_RESET} {sku} [{item_id}]{full_tag}{stock_tag}  {_CYAN}{_BOLD}ATIVA{_RESET}\n"
        f"     Campanha : {nome}\n"
        f"     Preço    : R$ {preco:.2f}  |  Rebate ML: {rebate_str}  |  RC: {rc:.1f}%  |  Lucro: R$ {lucro:.2f}"
    )
    _write_file({
        "evento":    "campanha_ativa",
        "ts":        datetime.now().isoformat(),
        "sku":       sku,
        "item_id":   item_id,
        "is_full":   is_full,
        "has_stock": has_stock,
        "campanha":  nome,
        "pdv":       margem,
    })


def decisao(
    sku: str,
    item_id: str,
    campanha: dict,
    margem: dict,
    resultado: dict,
    dry_run: bool,
    is_full: bool = False,
    has_stock: bool = True,
) -> None:
    decisao_str = resultado["decisao"]
    motivo      = resultado["motivo"]
    rc          = margem["rc_pct"]
    preco       = margem["preco_campanha"]
    lucro       = margem["lucro_bruto"]
    rebate      = margem["rebate"]
    nome_camp   = campanha.get("name") or campanha.get("type") or "—"

    if decisao_str == "ACEITAR":
        cor = _GREEN
        icone = "✓"
    else:
        cor = _RED
        icone = "✗"

    rebate_str = f"R$ {rebate:.2f}" if rebate > 0 else "—"
    full_tag  = f" {_BOLD}[FULL]{_RESET}" if is_full else ""
    stock_tag = _stock_tag(has_stock)
    dry_tag   = f"{_YELLOW}[DRY-RUN] {_RESET}" if dry_run else ""
    print(
        f"  {cor}{_BOLD}{icone}{_RESET} {dry_tag}"
        f"{sku} [{item_id}]{full_tag}{stock_tag}  {cor}{decisao_str}{_RESET}\n"
        f"     Campanha : {nome_camp}\n"
        f"     Preço    : R$ {preco:.2f}  |  Rebate ML: {rebate_str}  |  RC: {rc:.1f}%  |  Lucro: R$ {lucro:.2f}\n"
        f"     Motivo   : {motivo}"
    )

    event = {
        "evento":    "decisao",
        "ts":        datetime.now().isoformat(),
        "dry_run":   dry_run,
        "is_full":   is_full,
        "sku":       sku,
        "item_id":   item_id,
        "campanha":  nome_camp,
        "decisao":   decisao_str,
        "motivo":    motivo,
        "has_stock": has_stock,
        "pdv":       margem,
    }
    _write_file(event)

    # Acumula para o e-mail
    registro = {
        "sku": sku, "item_id": item_id, "campanha": nome_camp,
        "preco": preco, "rc": rc, "lucro": lucro, "has_stock": has_stock,
    }
    if decisao_str == "ACEITAR":
        _aceitas.append(registro)
    else:
        _recusadas.append(registro)


def secao_sem_estoque(total: int) -> None:
    print(f"\n{_BOLD}{_YELLOW}{'─'*60}{_RESET}")
    print(f"{_BOLD}{_YELLOW}  ⚠  SEM ESTOQUE — {total} anúncio(s) pausado(s){_RESET}")
    print(f"{_BOLD}{_YELLOW}{'─'*60}{_RESET}\n")
    _write_file({"evento": "secao_sem_estoque", "total": total,
                 "ts": datetime.now().isoformat()})


def fim_execucao(aceitos: int, recusados: int, erros: int) -> None:
    total = aceitos + recusados
    print(f"\n{_BOLD}{'─'*60}{_RESET}")
    print(
        f"{_BOLD}  Resumo: {total} campanha(s) analisadas  —  "
        f"{_GREEN}{aceitos} ACEITAS{_RESET}{_BOLD}  |  "
        f"{_RED}{recusados} RECUSADAS{_RESET}{_BOLD}  |  "
        f"{_YELLOW}{erros} ERRO(S){_RESET}"
    )
    print(f"{_BOLD}{'─'*60}{_RESET}\n")
    _write_file({
        "evento": "fim", "ts": datetime.now().isoformat(),
        "aceitos": aceitos, "recusados": recusados, "erros": erros,
    })

    if _email_cfg.get("enabled"):
        _enviar_email(aceitos, recusados, erros)


def _enviar_email(aceitos: int, recusados: int, erros: int) -> None:
    remetente   = os.getenv("EMAIL_REMETENTE", "")
    senha       = os.getenv("EMAIL_SENHA_APP", "")
    destinatario = os.getenv("EMAIL_DESTINATARIO", "")

    if not all([remetente, senha, destinatario]):
        print(f"  {_YELLOW}⚠{_RESET}  E-mail: EMAIL_REMETENTE, EMAIL_SENHA_APP ou EMAIL_DESTINATARIO não configurados no .env")
        return

    agora  = datetime.now().strftime("%d/%m %H:%M")
    modo   = "[DRY-RUN] " if not _email_cfg.get("live") else ""
    assunto = f"{modo}Central de Promoções ML — {agora} | {aceitos} aceitas / {recusados} recusadas"

    linhas = [f"Central de Promoções ML — {agora}\n"]

    if _aceitas:
        linhas.append(f"✅ ACEITAS ({len(_aceitas)})")
        for r in _aceitas:
            stock = "" if r["has_stock"] else " [sem estoque]"
            linhas.append(f"  • {r['sku']} [{r['item_id']}]{stock}")
            linhas.append(f"    {r['campanha']} | R$ {r['preco']:.2f} | RC {r['rc']:.1f}% | Lucro R$ {r['lucro']:.2f}")
        linhas.append("")

    if _recusadas:
        linhas.append(f"❌ RECUSADAS ({len(_recusadas)})")
        for r in _recusadas:
            stock = "" if r["has_stock"] else " [sem estoque]"
            linhas.append(f"  • {r['sku']} [{r['item_id']}]{stock} — RC {r['rc']:.1f}%")
        linhas.append("")

    if erros:
        linhas.append(f"⚠️  ERROS: {erros}")

    corpo = "\n".join(linhas)
    msg = MIMEText(corpo, "plain", "utf-8")
    msg["Subject"] = assunto
    msg["From"]    = remetente
    msg["To"]      = destinatario

    try:
        with smtplib.SMTP(_email_cfg.get("smtp_host", "smtp.gmail.com"),
                          _email_cfg.get("smtp_port", 587)) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(remetente, senha)
            smtp.sendmail(remetente, destinatario, msg.as_string())
        print(f"  ✉  Relatório enviado para {destinatario}")
    except Exception as exc:
        print(f"  {_YELLOW}⚠{_RESET}  Falha ao enviar e-mail: {exc}")


def erro(sku: str, item_id: str, exc: Exception) -> None:
    print(f"  {_RED}✗{_RESET}  {sku} [{item_id}]: ERRO — {exc}")
    _write_file({"evento": "erro", "sku": sku, "item_id": item_id,
                 "erro": str(exc), "ts": datetime.now().isoformat()})
