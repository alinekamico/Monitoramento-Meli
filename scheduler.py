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

Cada ciclo processa TODAS as contas configuradas em config/contas.yaml:
  1. Para cada conta: coleta snapshots de todos os SKUs (ou subset)
  2. Para cada conta: avalia regras A1/A2/A3 e dispara alertas críticos
  3. Para cada conta: se passou da hora_resumo_diario, envia resumo diário

Tratamento de erro:
  - Falha em 1 SKU dentro do coletor não derruba os outros (já tratado lá)
  - Falha em 1 conta não interrompe as demais contas do ciclo
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


def _carregar_contas() -> list[str]:
    """Retorna lista de IDs de contas configuradas em config/contas.yaml."""
    with open(_CONFIG_DIR / "contas.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return list(cfg.get("contas", {}).keys())


def _log_jsonl(evento: dict) -> None:
    """Log estruturado em JSON-lines, mesmo padrão do coletor."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    arquivo = _LOG_DIR / f"buybox-{datetime.now():%Y-%m-%d}.log"
    with open(arquivo, "a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")


def _hora_local_agora() -> datetime:
    """datetime local (não UTC) — usado para decidir se é hora do resumo."""
    return datetime.now()


def _stamp_path(conta: str) -> Path:
    """Arquivo de stamp do resumo diário por conta."""
    return _DATA_DIR / f".ultimo_resumo_diario_{conta}"


def _ja_rodou_resumo_hoje(conta: str) -> bool:
    """Stamp em arquivo por conta para detectar envio duplicado."""
    stamp = _stamp_path(conta)
    if not stamp.exists():
        return False
    try:
        conteudo = stamp.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return conteudo == _hora_local_agora().strftime("%Y-%m-%d")


def _marcar_resumo_enviado_hoje(conta: str) -> None:
    stamp = _stamp_path(conta)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(_hora_local_agora().strftime("%Y-%m-%d"), encoding="utf-8")


def _eh_hora_resumo(hora_resumo: int, conta: str) -> bool:
    return _hora_local_agora().hour >= hora_resumo and not _ja_rodou_resumo_hoje(conta)


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
    contas: Optional[list[str]] = None,
) -> dict:
    """
    Executa um ciclo completo para todas as contas e devolve estatísticas.

    Não levanta exceção: erros são logados mas o ciclo continua para as
    demais contas e o `dict` de saída ainda é válido.
    """
    inicio = time.time()
    cfg_buybox = cfg.get("buybox", {}) or {}
    hora_resumo = int(cfg_buybox.get("hora_resumo_diario", 8))

    if contas is None:
        contas = _carregar_contas()

    stats: dict = {
        "iniciado_em": datetime.now().isoformat(),
        "contas":      {},
        "erros":       [],
    }

    _log_jsonl({"evento": "ciclo_inicio", "ts": stats["iniciado_em"],
                "contas": contas})

    for conta in contas:
        _log.info("iniciando ciclo para conta: %s", conta)
        conta_stats: dict = {"coleta": None, "alertas": None, "resumo_diario": None}

        # ---- 1) Coleta ----
        try:
            conta_stats["coleta"] = coletor.coletar(
                skus_filtro=skus_filtro, conta=conta,
            )
        except Exception as exc:
            msg = f"coleta [{conta}]: {exc.__class__.__name__}: {exc}"
            _log.exception("falha na coleta para conta %s", conta)
            stats["erros"].append(msg)
            _log_jsonl({"evento": "erro_coleta", "conta": conta, "erro": msg,
                        "trace": traceback.format_exc(limit=5),
                        "ts": datetime.now().isoformat()})

        # ---- 2) Alertas críticos ----
        if avaliar_alertas:
            try:
                conta_stats["alertas"] = avaliador.avaliar_criticos_pendentes(
                    cfg=cfg, conta=conta,
                )
            except Exception as exc:
                msg = f"alertas [{conta}]: {exc.__class__.__name__}: {exc}"
                _log.exception("falha nos alertas para conta %s", conta)
                stats["erros"].append(msg)
                _log_jsonl({"evento": "erro_alertas", "conta": conta, "erro": msg,
                            "trace": traceback.format_exc(limit=5),
                            "ts": datetime.now().isoformat()})

        # ---- 3) Resumo diário (por conta) ----
        deve_resumo = avaliar_alertas and (
            forcar_resumo or _eh_hora_resumo(hora_resumo, conta)
        )
        if deve_resumo:
            try:
                r = avaliador.enviar_resumo_diario(cfg=cfg, conta=conta)
                conta_stats["resumo_diario"] = r
                _marcar_resumo_enviado_hoje(conta)
            except Exception as exc:
                msg = f"resumo [{conta}]: {exc.__class__.__name__}: {exc}"
                _log.exception("falha no resumo para conta %s", conta)
                stats["erros"].append(msg)
                _log_jsonl({"evento": "erro_resumo", "conta": conta, "erro": msg,
                            "trace": traceback.format_exc(limit=5),
                            "ts": datetime.now().isoformat()})

        stats["contas"][conta] = conta_stats

    # Stats agregados para compatibilidade com _resumir_ciclo
    stats["coleta"] = {
        "snapshots_salvos": sum(
            (s.get("coleta") or {}).get("snapshots_salvos", 0)
            for s in stats["contas"].values()
        ),
        "erros": sum(
            (s.get("coleta") or {}).get("erros", 0)
            for s in stats["contas"].values()
        ),
    }
    stats["alertas"] = {
        "enviados": sum(
            (s.get("alertas") or {}).get("enviados", 0)
            for s in stats["contas"].values()
        ),
    }

    stats["duracao_s"] = round(time.time() - inicio, 1)
    _log_jsonl({"evento": "ciclo_fim", "ts": datetime.now().isoformat(),
                **{k: v for k, v in stats.items() if k != "iniciado_em"}})
    return stats


# ============================================================
# Loop principal
# ============================================================


def loop_continuo(cfg: dict, avaliar_alertas: bool,
                  contas: list[str]) -> None:
    cfg_buybox = cfg.get("buybox", {}) or {}
    intervalo = int(cfg_buybox.get("intervalo_coleta_minutos", 60))
    _log.info("scheduler iniciado — %d conta(s) — intervalo de %s min",
              len(contas), intervalo)
    _log_jsonl({"evento": "scheduler_inicio",
                "contas": contas,
                "intervalo_min": intervalo,
                "ts": datetime.now().isoformat()})

    while not _sair:
        stats = executar_ciclo(cfg, avaliar_alertas=avaliar_alertas,
                               contas=contas)
        _resumir_ciclo(stats)

        if _sair:
            break

        sleep_s = _segundos_ate_proxima_execucao(intervalo)
        prox = datetime.now() + timedelta(seconds=sleep_s)
        _log.info("proximo ciclo em %.0fs (aprox. %s)", sleep_s,
                  prox.strftime("%H:%M:%S"))

        # Sleep em chunks curtos para responder rapidamente ao Ctrl+C
        restante = sleep_s
        while restante > 0 and not _sair:
            time.sleep(min(restante, 1.0))
            restante -= 1.0

    _log.info("scheduler encerrado")
    _log_jsonl({"evento": "scheduler_fim", "ts": datetime.now().isoformat()})


def _resumir_ciclo(stats: dict) -> None:
    """Imprime resumo do ciclo no log do terminal."""
    coleta  = stats.get("coleta") or {}
    alertas = stats.get("alertas") or {}
    erros   = stats.get("erros") or []
    contas  = stats.get("contas") or {}

    _log.info(
        "ciclo concluido em %ss | contas=%s | snapshots=%s | "
        "erros_coleta=%s | alertas_enviados=%s | erros_ciclo=%s",
        stats.get("duracao_s"),
        len(contas),
        coleta.get("snapshots_salvos", 0),
        coleta.get("erros", 0),
        alertas.get("enviados", 0),
        len(erros),
    )
    for conta, cs in contas.items():
        resumo = cs.get("resumo_diario")
        if resumo:
            _log.info(
                "resumo diario [%s]: B1=%s B2=%s B3=%s enviado=%s",
                conta, resumo.get("b1"), resumo.get("b2"),
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
        help="Executa um ciclo único e sai (util para debug).",
    )
    parser.add_argument(
        "--sku", action="append", metavar="SKU",
        help="Filtra coleta por SKU(s). Pode repetir a flag.",
    )
    parser.add_argument(
        "--sem-alertas", action="store_true",
        help="Roda so a coleta (nao avalia A1/A2/A3 nem resumo).",
    )
    parser.add_argument(
        "--forcar-resumo", action="store_true",
        help="Força envio do resumo diario neste ciclo (ignora horario/stamp).",
    )
    parser.add_argument(
        "--intervalo", type=int, metavar="MIN",
        help="Override do intervalo entre ciclos (em minutos).",
    )
    args = parser.parse_args()

    # Inicializa bancos para todas as contas
    contas = _carregar_contas()
    for conta in contas:
        persistencia.init_db(conta)

    cfg = _carregar_settings()
    if args.intervalo is not None:
        cfg.setdefault("buybox", {})["intervalo_coleta_minutos"] = args.intervalo

    avaliar = not args.sem_alertas

    if args.once:
        _log.info("modo --once — executando 1 ciclo para %d conta(s): %s",
                  len(contas), contas)
        stats = executar_ciclo(
            cfg, skus_filtro=args.sku,
            avaliar_alertas=avaliar,
            forcar_resumo=args.forcar_resumo,
            contas=contas,
        )
        _resumir_ciclo(stats)
        return 0 if not stats.get("erros") else 1

    # Loop contínuo ignora --sku (faria sentido filtrar mas deixaríamos
    # outros SKUs sem snapshot — um arrasta o outro). Loop = "todos".
    if args.sku:
        _log.warning("--sku ignorado em modo loop. Use --once --sku para filtrar.")

    try:
        loop_continuo(cfg, avaliar_alertas=avaliar, contas=contas)
    except Exception:
        _log.exception("scheduler abortado por excecao inesperada")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
