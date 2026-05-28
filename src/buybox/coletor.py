"""
Coletor de snapshots do MVP Buybox.

Para cada SKU rastreado:
  - Resolve item_ids via ml_client.get_item_ids_by_sku
  - Para cada item: busca detail, monta top 5 do catálogo, calcula
    margem atual e preço ótimo, persiste snapshot + concorrentes

CLI:
  python -m src.buybox.coletor              # ciclo completo, todos SKUs
  python -m src.buybox.coletor --sku WLK004 # apenas um SKU
  python -m src.buybox.coletor --once       # ciclo único (alias semântico)
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import yaml

from .. import ml_client, pdv
from . import catalogo, persistencia, pricing
from .modelos import SnapshotDom

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_LOG_DIR = Path(__file__).parent.parent.parent / "logs"


def _carregar_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _log_jsonl(evento: dict) -> None:
    """Mesmo padrão de logs do notificador.py: linha JSON por evento."""
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    arquivo = _LOG_DIR / f"buybox-{datetime.now().strftime('%Y-%m-%d')}.log"
    with open(arquivo, "a", encoding="utf-8") as f:
        f.write(json.dumps(evento, ensure_ascii=False, default=str) + "\n")


def _print(msg: str) -> None:
    """
    Imprime tolerando consoles que não aceitam UTF-8 (Windows cp1252).
    O log estruturado em arquivo continua em UTF-8 — aqui só protegemos
    a saída do terminal.
    """
    encoding = (sys.stdout.encoding or "utf-8").lower()
    if encoding in ("utf-8", "utf8"):
        sys.stdout.write(msg + "\n")
    else:
        sys.stdout.write(msg.encode(encoding, errors="replace").decode(encoding) + "\n")
    sys.stdout.flush()


def _campanha_ativa(item_id: str) -> Optional[dict]:
    """
    Devolve a campanha started que está aplicando desconto agora.

    Aceita campanhas com `price > 0` mesmo quando `meli_percentage = 0`
    (SELLER_CAMPAIGN, FLEXIBLE_PERCENTAGE etc — desconto absorvido pelo
    seller, mas o cliente vê o preço com desconto). Quando há várias
    started, escolhe a que aplica maior desconto absoluto.
    """
    try:
        camps = ml_client.get_campaigns_for_item(item_id)
    except Exception:
        return None

    candidatas = [
        c for c in camps.get("ativas", [])
        if float(c.get("price") or 0) > 0
    ]
    if not candidatas:
        return None

    def desconto_absoluto(c: dict) -> float:
        original = float(c.get("original_price") or 0) or float(c.get("price") or 0)
        return original - float(c.get("price") or 0)

    melhor = max(candidatas, key=desconto_absoluto)
    return {
        "id":              melhor.get("ref_id") or melhor.get("id") or "",
        "name":            melhor.get("name") or melhor.get("type") or "",
        "rebate_pct":      float(melhor.get("meli_percentage", 0.0)),
        "price":           float(melhor.get("price", 0.0) or 0.0),
        "original_price":  float(melhor.get("original_price", 0.0) or 0.0),
        # Faixa de preço válida (campanhas SMART/DEAL geralmente expõem).
        # Quando ausente, usamos `preco_aplicado` como heurística.
        "min_price":       float(melhor.get("min_price", 0.0) or 0.0),
        "max_price":       float(melhor.get("max_price", 0.0) or 0.0),
        # Preço atual onde a campanha está aplicada. Mudar para outro
        # preço significa sair da campanha (e perder o rebate ML).
        "preco_aplicado":  float(melhor.get("price", 0.0) or 0.0),
        # Vigência (strings ISO vindas do _parse_promotion)
        "start_date":      melhor.get("start_date") or "",
        "finish_date":     melhor.get("finish_date") or "",
    }


def _preco_base(
    item_detail: dict,
    concorrentes: list,
    campanha: Optional[dict],
    fonte: str,
) -> float:
    """
    Define o preço de referência do snapshot.

    Ordem de preferência (fonte da verdade do que o cliente vê):
      1. Nossa entrada no top 5 do catálogo (/products/{pid}/items.price)
         — o ML aplica todos os descontos aqui.
      2. price da campanha started (quando estamos visíveis no catálogo
         por outro motivo ou não há nossa entrada no top 5).
      3. item.price — preço cheio, fallback final.

    fonte = "preco_atual" força usar item.price (modo legacy/debug).
    """
    preco_item = float(item_detail.get("price") or 0.0)

    if fonte == "preco_atual":
        return preco_item

    # Fonte da verdade absoluta: top 5 público
    from . import catalogo
    preco_top5 = catalogo.nosso_preco_efetivo(concorrentes)
    if preco_top5 is not None and preco_top5 > 0:
        return preco_top5

    # Fallback: preço da campanha started
    if campanha and campanha.get("price", 0) > 0:
        return float(campanha["price"])

    return preco_item


def _processar_item(
    sku: str,
    sku_data: dict,
    item_detail: dict,
    seller_id_proprio: str,
    settings: dict,
) -> Optional[SnapshotDom]:
    """Monta o SnapshotDom completo para um anúncio."""
    item_id = item_detail.get("id", "")
    if not item_id:
        return None

    listing_id = item_detail.get("listing_type_id", "") or ""
    tipo_anuncio = "Premium" if "gold_pro" in listing_id else sku_data["tipo_anuncio"]
    is_full = (item_detail.get("shipping") or {}).get("logistic_type") == "fulfillment"

    cfg_buybox = settings.get("buybox", {}) or {}
    fonte_preco = cfg_buybox.get("fonte_preco", "suggested_price")

    campanha = _campanha_ativa(item_id)

    # Monta top 5 ANTES de definir preço — é fonte da verdade do
    # preço visível ao cliente.
    concorrentes = catalogo.montar_top5(item_detail, seller_id_proprio)
    preco_atual = _preco_base(item_detail, concorrentes, campanha, fonte_preco)
    preco_cheio = float(item_detail.get("price") or 0.0)
    visivel = catalogo.e_visivel_ao_cliente(item_detail) and any(
        c.e_nos for c in concorrentes
    )

    comp = catalogo.derivar_competicao(concorrentes)
    diffs = catalogo.calcular_diffs(
        preco_atual, comp["preco_1o"], comp["preco_2o"]
    )

    # Margem no preço atual
    margem_atual = pricing.calcular_margem_atual(
        preco_atual=preco_atual,
        custo=float(sku_data["custo"]),
        peso=float(sku_data["peso"]),
        tipo_anuncio=tipo_anuncio,
        settings=settings,
        campanha_ativa=campanha,
        is_full=is_full,
    )

    # Preço ótimo
    resultado_pricing = pricing.calcular_preco_otimo(
        preco_atual=preco_atual,
        preco_1o=comp["preco_1o"],
        preco_2o=comp["preco_2o"],
        nossa_posicao=comp["nossa_posicao"],
        tem_buybox=comp["tem_buybox"],
        custo=float(sku_data["custo"]),
        peso=float(sku_data["peso"]),
        tipo_anuncio=tipo_anuncio,
        settings=settings,
        campanha_ativa=campanha,
        is_full=is_full,
        visivel_no_catalogo=visivel,
    )

    return SnapshotDom(
        sku=sku,
        item_id=item_id,
        coletado_em=datetime.now(timezone.utc).replace(microsecond=0),
        preco_atual=round(preco_atual, 2),
        nossa_posicao=comp["nossa_posicao"],
        tem_buybox=comp["tem_buybox"],
        status_anuncio=item_detail.get("status", "unknown"),
        estoque_proprio=int(item_detail.get("available_quantity") or 0),
        is_full=is_full,
        tipo_anuncio=tipo_anuncio,
        preco_1o=comp["preco_1o"],
        preco_2o=comp["preco_2o"],
        qtd_concorrentes=comp["qtd_concorrentes"],
        diff_para_1o_rs=diffs["diff_para_1o_rs"],
        diff_para_1o_pct=diffs["diff_para_1o_pct"],
        diff_para_2o_rs=diffs["diff_para_2o_rs"],
        diff_para_2o_pct=diffs["diff_para_2o_pct"],
        margem_atual_pct=float(margem_atual.get("margem_pct") or 0.0),
        rc_atual_pct=float(margem_atual.get("rc_pct") or 0.0),
        concorrentes=concorrentes,
        campanha_ativa_id=campanha["id"] if campanha else None,
        campanha_ativa_nome=campanha["name"] if campanha else None,
        rebate_pct=campanha["rebate_pct"] if campanha else None,
        campanha_min_price=campanha.get("min_price") if campanha else None,
        campanha_max_price=campanha.get("max_price") if campanha else None,
        campanha_original_price=campanha.get("original_price") if campanha else None,
        campanha_start_date=campanha.get("start_date") if campanha else None,
        campanha_finish_date=campanha.get("finish_date") if campanha else None,
        custo=float(sku_data["custo"]),
        preco_otimo_sugerido=resultado_pricing.preco_otimo_sugerido,
        rc_no_preco_otimo=resultado_pricing.rc_no_preco_otimo,
        motivo_sugestao=resultado_pricing.motivo,
        titulo=item_detail.get("title"),
        url_anuncio=item_detail.get("permalink"),
        thumbnail_url=(
            item_detail.get("secure_thumbnail")
            or item_detail.get("thumbnail")
        ),
        reviews_rating=(
            float((item_detail.get("reviews") or {}).get("rating_average") or 0)
            or None
        ),
        reviews_total=(
            int((item_detail.get("reviews") or {}).get("total") or 0)
            or None
        ),
        visivel_no_catalogo=visivel,
        preco_cheio=round(preco_cheio, 2) if preco_cheio else None,
    )


def coletar(skus_filtro: Optional[Iterable[str]] = None) -> dict:
    """
    Executa um ciclo de coleta. Devolve estatísticas resumidas.

    Tratamento de erro por item: falha em 1 anúncio é logada mas não
    interrompe a fila.
    """
    settings = _carregar_settings()
    persistencia.init_db()
    ml_client.limpar_cache_sellers()

    todos = pdv.load_skus()
    if skus_filtro:
        wanted = {s.upper() for s in skus_filtro}
        skus = {k: v for k, v in todos.items() if k in wanted}
        if not skus:
            _print(f"Nenhum SKU encontrado para filtro: {sorted(wanted)}")
            return {"skus_processados": 0, "snapshots_salvos": 0, "erros": 0}
    else:
        skus = todos

    seller_id = ml_client.get_seller_id()

    inicio = time.time()
    _print(f"Coleta buybox iniciada — {len(skus)} SKU(s)")
    _log_jsonl({
        "evento": "coleta_inicio",
        "ts": datetime.now().isoformat(),
        "total_skus": len(skus),
    })

    stats = {"skus_processados": 0, "snapshots_salvos": 0,
             "snapshots_ignorados": 0, "erros": 0}

    for sku, sku_data in skus.items():
        try:
            item_ids = ml_client.get_item_ids_by_sku(seller_id, sku)
        except Exception as exc:
            stats["erros"] += 1
            _log_jsonl({"evento": "erro_resolucao_mlbs",
                        "sku": sku, "erro": str(exc),
                        "ts": datetime.now().isoformat()})
            _print(f"  ✗ {sku}: falha ao resolver MLBs — {exc}")
            continue

        if not item_ids:
            _print(f"  – {sku}: sem MLBs")
            continue

        items = ml_client.get_items_details(item_ids)
        time.sleep(0.15)

        for item in items:
            item_id = item.get("id", "")
            try:
                dom = _processar_item(sku, sku_data, item, seller_id, settings)
                if dom is None:
                    continue
                snap_id = persistencia.salvar_snapshot(dom)
                if snap_id is None:
                    stats["snapshots_ignorados"] += 1
                    _print(f"  ○ {sku} [{item_id}]: snapshot duplicado, ignorado")
                else:
                    stats["snapshots_salvos"] += 1
                    posicao = dom.nossa_posicao if dom.nossa_posicao is not None else "-"
                    sugestao = (
                        f"R$ {dom.preco_otimo_sugerido:.2f}"
                        if dom.preco_otimo_sugerido is not None else "—"
                    )
                    tag_vis = "" if dom.visivel_no_catalogo else " [OFF-CATÁLOGO]"
                    tag_camp = ""
                    if dom.preco_cheio and dom.preco_cheio > dom.preco_atual + 0.01:
                        tag_camp = f" (cheio R$ {dom.preco_cheio:.2f})"
                    _print(
                        f"  ✓ {sku} [{item_id}]{tag_vis} pos={posicao} "
                        f"preço=R$ {dom.preco_atual:.2f}{tag_camp} | "
                        f"RC={dom.rc_atual_pct:.1f}% | ótimo={sugestao}"
                    )
                    _log_jsonl({
                        "evento": "snapshot_salvo",
                        "ts": datetime.now().isoformat(),
                        "snapshot_id": snap_id,
                        "sku": sku, "item_id": item_id,
                        "preco_atual": dom.preco_atual,
                        "nossa_posicao": dom.nossa_posicao,
                        "tem_buybox": dom.tem_buybox,
                        "preco_otimo_sugerido": dom.preco_otimo_sugerido,
                        "rc_atual_pct": dom.rc_atual_pct,
                    })
            except Exception as exc:
                stats["erros"] += 1
                _print(f"  ✗ {sku} [{item_id}]: ERRO — {exc}")
                _log_jsonl({"evento": "erro_processamento",
                            "sku": sku, "item_id": item_id,
                            "erro": str(exc),
                            "trace": traceback.format_exc(limit=3),
                            "ts": datetime.now().isoformat()})
            time.sleep(0.1)
        stats["skus_processados"] += 1

    duracao = round(time.time() - inicio, 1)
    _print(
        f"\nResumo: {stats['skus_processados']} SKU(s) | "
        f"{stats['snapshots_salvos']} snapshot(s) | "
        f"{stats['snapshots_ignorados']} duplicado(s) | "
        f"{stats['erros']} erro(s) | {duracao}s"
    )
    _log_jsonl({
        "evento": "coleta_fim",
        "ts": datetime.now().isoformat(),
        "duracao_s": duracao,
        **stats,
    })
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coletor de snapshots do MVP Buybox."
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Executa um ciclo único (default — alias semântico).",
    )
    parser.add_argument(
        "--sku", action="append", metavar="SKU",
        help="Processa apenas o(s) SKU(s) informado(s). Pode repetir a flag.",
    )
    parser.add_argument(
        "--com-alertas", action="store_true",
        help="Após a coleta, avalia regras A1/A2/A3 e dispara alertas críticos.",
    )
    args = parser.parse_args()
    coletar(skus_filtro=args.sku)

    if args.com_alertas:
        # Importação preguiçosa para evitar dependência circular se o
        # módulo de alertas for desativado no futuro.
        from ..alertas import avaliador
        stats = avaliador.avaliar_criticos_pendentes()
        _print(
            f"\nAlertas críticos: {stats['pendentes_detectados']} detectados | "
            f"{stats['enviados']} enviados | "
            f"{stats['suprimidos_cooldown']} cooldown | "
            f"{stats['suprimidos_dryrun']} dry-run | "
            f"{stats['suprimidos_email_off']} e-mail off | "
            f"{stats['erros_smtp']} erros"
        )


if __name__ == "__main__":
    main()
