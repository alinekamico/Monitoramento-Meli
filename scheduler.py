"""
Scheduler do MVP Buybox — orquestra coleta + alertas em loop horário.

Sem APScheduler: usa apenas time.sleep + cálculo do delta até a próxima
hora cheia. Mantém leve, sem dependência adicional.

CLI:
  python scheduler.py                    Loop contínuo (ciclo a cada hora cheia)
  python scheduler.py --once             Roda um ciclo único e sai
  python scheduler.py --sku WLK004       Filtra ciclo único por SKU
  python scheduler.py --sem-alertas      Coleta sem disparar alertas críticos
  python scheduler.py --intervalo 5      Override do intervalo (em minutos, p/ testes)

Cada ciclo:
  1. Coleta snapshots de todos os SKUs (ou subset)
  2. Avalia regras A1/A2/A3 e dispara alertas críticos (com cooldown)
  3. Se passou da hora_resumo_diario e ainda não rodou hoje, envia resumo

Tratamento de erro:
  - Falha em 1 SKU dentro do coletor não derruba os outros (já tratado lá)
  - Falha no ciclo inteiro é logada e o loop continua
  - Ctrl+C encerra limpo
"""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import yaml

# Garante import absoluto de src.*
sys.path.insert(0, str(Path(__file__).parent))

from src.alertas import avaliador  # noqa: E402
from src.buybox import coletor, persistencia  # noqa: E402


_CONFIG_DIR = Path(__file__).parent / "config"
_LOG_DIR = Path(__file__).parent / "logs"
_DATA_DIR = Path(__file__).parent / "data"
_STAMP_RESUMO = _DATA_DIR / ".ultimo_resumo_diario"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
_log = logging.getLogger("scheduler")

# Sinaliza saída solicitada (Ctrl+C / SIGTERM)
_sair = False


def _sigterm_handler(signum, frame):
    global _sair
    _log.info("sinal recebido (%s) — finalizando após o ciclo atual", signum)
    _sair = True


signal.signal(signal.SIGINT, _sigterm_handler)
if hasattr(signal, "SIGTERM"):
    signal.signal(signal.SIGTERM, _sigterm_handler)


# ============================================================
# Utilidades
# ============================================================


def _carregar_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _log_jsonl(evento: dict) -> None:
    """Log estruturado em JSON-lines, mesmo padrão do coletor."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    arquivo = _LOG_DIR / f"buybox-{datetime.now():%Y-%m-%d}.log"
    with open(arquivo, "a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")


def _hora_local_agora() -> datetime:
    """datetime local (não UTC) — usado para decidir se é hora do resumo."""
    return datetime.now()


def _ja_rodou_resumo_hoje() -> bool:
    """Stamp em arquivo para detectar envio duplicado entre ciclos."""
    if not _STAMP_RESUMO.exists():
        return False
    try:
        conteudo = _STAMP_RESUMO.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return conteudo == _hora_local_agora().strftime("%Y-%m-%d")


def _marcar_resumo_enviado_hoje() -> None:
    _STAMP_RESUMO.parent.mkdir(parents=True, exist_ok=True)
    _STAMP_RESUMO.write_text(_hora_local_agora().strftime("%Y-%m-%d"), encoding="utf-8")


def _eh_hora_resumo(hora_resumo: int) -> bool:
    return _hora_local_agora().hour >= hora_resumo and not _ja_rodou_resumo_hoje()


def _segundos_ate_proxima_execucao(intervalo_min: int) -> float:
    """
    Calcula tempo até a próxima execução alinhada.

    Para intervalo 60min: dorme até a próxima hora cheia (HH+1:00:05).
    Para intervalos menores: dorme até o próximo múltiplo do intervalo
    a partir do minuto 00.

    O +5s de offset evita disparar exatamente no limite e perder dados
    que demoram a chegar (ex: snapshots do ML ainda sendo gravados).
    """
    agora = datetime.now()
    if intervalo_min >= 60:
        proxima = (agora + timedelta(hours=1)).replace(
            minute=0, second=5, microsecond=0,
        )
    else:
        # alinha no múltiplo do intervalo
        prox_min = (agora.minute // intervalo_min + 1) * intervalo_min
        if prox_min >= 60:
            proxima = (agora + timedelta(hours=1)).replace(
                minute=0, second=5, microsecond=0,
            )
        else:
            proxima = agora.replace(
                minute=prox_min, second=5, microsecond=0,
            )
    delta = (proxima - agora).total_seconds()
    return max(delta, 5.0)


# ============================================================
# Ciclo
# ============================================================


def executar_ciclo(
    cfg: dict,
    skus_filtro: Optional[Iterable[str]] = None,
    avaliar_alertas: bool = True,
    forcar_resumo: bool = False,
) -> dict:
    """
    Executa um ciclo completo e devolve estatísticas agregadas.

    Não levanta exceção: erros aqui dentro são logados mas o `dict` de
    saída ainda é válido para o loop continuar.
    """
    inicio = time.time()
    cfg_buybox = cfg.get("buybox", {}) or {}
    hora_resumo = int(cfg_buybox.get("hora_resumo_diario", 8))

    stats = {
        "iniciado_em":    datetime.now().isoformat(),
        "coleta":         None,
        "alertas":        None,
        "resumo_diario":  None,
        "erros":          [],
    }

    _log_jsonl({"evento": "ciclo_inicio", "ts": stats["iniciado_em"]})

    # ---- 1) Coleta ----
    try:
        stats["coleta"] = coletor.coletar(skus_filtro=skus_filtro)
    except Exception as exc:
        msg = f"coleta: {exc.__class__.__name__}: {exc}"
        _log.exception("falha na coleta")
        stats["erros"].append(msg)
        _log_jsonl({"evento": "erro_coleta", "erro": msg,
                    "trace": traceback.format_exc(limit=5),
                    "ts": datetime.now().isoformat()})

    # ---- 2) Alertas críticos ----
    if avaliar_alertas:
        try:
            stats["alertas"] = avaliador.avaliar_criticos_pendentes(cfg=cfg)
        except Exception as exc:
            msg = f"alertas: {exc.__class__.__name__}: {exc}"
            _log.exception("falha na avaliação de alertas críticos")
            stats["erros"].append(msg)
            _log_jsonl({"evento": "erro_alertas", "erro": msg,
                        "trace": traceback.format_exc(limit=5),
                        "ts": datetime.now().isoformat()})

    # ---- 3) Resumo diário ----
    # Só roda se: avaliação de alertas está ligada E (foi forçado OU é a hora certa)
    deve_rodar_resumo = avaliar_alertas and (
        forcar_resumo or _eh_hora_resumo(hora_resumo)
    )
    if deve_rodar_resumo:
        try:
            r = avaliador.enviar_resumo_diario(cfg=cfg)
            stats["resumo_diario"] = r
            _marcar_resumo_enviado_hoje()
        except Exception as exc:
            msg = f"resumo: {exc.__class__.__name__}: {exc}"
            _log.exception("falha no resumo diário")
            stats["erros"].append(msg)
            _log_jsonl({"evento": "erro_resumo", "erro": msg,
                        "trace": traceback.format_exc(limit=5),
                        "ts": datetime.now().isoformat()})

    stats["duracao_s"] = round(time.time() - inicio, 1)
    _log_jsonl({"evento": "ciclo_fim",
                "ts": datetime.now().isoformat(),
                **{k: v for k, v in stats.items() if k != "iniciado_em"}})
    return stats


# ============================================================
# Loop principal
# ============================================================


def loop_continuo(cfg: dict, avaliar_alertas: bool) -> None:
    cfg_buybox = cfg.get("buybox", {}) or {}
    intervalo = int(cfg_buybox.get("intervalo_coleta_minutos", 60))
    _log.info("scheduler iniciado — intervalo de %s min", intervalo)
    _log_jsonl({"evento": "scheduler_inicio",
                "intervalo_min": intervalo,
                "ts": datetime.now().isoformat()})

    # Roda um ciclo imediato pra não esperar a próxima hora cheia
    while not _sair:
        stats = executar_ciclo(cfg, avaliar_alertas=avaliar_alertas)
        _resumir_ciclo(stats)

        if _sair:
            break

        sleep_s = _segundos_ate_proxima_execucao(intervalo)
        prox = datetime.now() + timedelta(seconds=sleep_s)
        _log.info("próximo ciclo em %.0fs (≈ %s)", sleep_s, prox.strftime("%H:%M:%S"))

        # Sleep em chunks curtos para responder rapidamente ao Ctrl+C
        restante = sleep_s
        while restante > 0 and not _sair:
            time.sleep(min(restante, 1.0))
            restante -= 1.0

    _log.info("scheduler encerrado")
    _log_jsonl({"evento": "scheduler_fim", "ts": datetime.now().isoformat()})


def _resumir_ciclo(stats: dict) -> None:
    """Imprime resumo do ciclo no log do terminal."""
    coleta = stats.get("coleta") or {}
    alertas = stats.get("alertas") or {}
    resumo = stats.get("resumo_diario")
    erros = stats.get("erros") or []

    _log.info(
        "ciclo concluído em %ss | snapshots=%s | erros_coleta=%s | alertas_enviados=%s | erros=%s",
        stats.get("duracao_s"),
        coleta.get("snapshots_salvos", 0),
        coleta.get("erros", 0),
        alertas.get("enviados", 0),
        len(erros),
    )
    if resumo:
        _log.info(
            "resumo diário: B1=%s B2=%s B3=%s enviado=%s",
            resumo.get("b1"), resumo.get("b2"),
            resumo.get("b3"), resumo.get("enviado"),
        )
    for msg in erros:
        _log.error("ciclo erro: %s", msg)


# ============================================================
# CLI
# ============================================================


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scheduler do MVP Buybox — coleta + alertas em loop."
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Executa um ciclo único e sai (útil para debug).",
    )
    parser.add_argument(
        "--sku", action="append", metavar="SKU",
        help="Filtra coleta por SKU(s). Pode repetir a flag.",
    )
    parser.add_argument(
        "--sem-alertas", action="store_true",
        help="Roda só a coleta (não avalia A1/A2/A3 nem resumo).",
    )
    parser.add_argument(
        "--forcar-resumo", action="store_true",
        help="Força envio do resumo diário neste ciclo (ignora horário/stamp).",
    )
    parser.add_argument(
        "--intervalo", type=int, metavar="MIN",
        help="Override do intervalo entre ciclos (em minutos).",
    )
    args = parser.parse_args()

    persistencia.init_db()
    cfg = _carregar_settings()
    if args.intervalo is not None:
        cfg.setdefault("buybox", {})["intervalo_coleta_minutos"] = args.intervalo

    avaliar = not args.sem_alertas

    if args.once:
        _log.info("modo --once — executando 1 ciclo")
        stats = executar_ciclo(
            cfg, skus_filtro=args.sku,
            avaliar_alertas=avaliar,
            forcar_resumo=args.forcar_resumo,
        )
        _resumir_ciclo(stats)
        return 0 if not stats.get("erros") else 1

    # Loop contínuo ignora --sku (faria sentido filtrar mas deixaríamos
    # outros SKUs sem snapshot — um arrasta o outro). Loop = "todos".
    if args.sku:
        _log.warning("--sku é ignorado em modo loop. Use --once --sku para filtrar.")

    try:
        loop_continuo(cfg, avaliar_alertas=avaliar)
    except Exception:
        _log.exception("scheduler abortado por exceção inesperada")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
