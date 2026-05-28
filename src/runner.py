"""
Orquestrador principal.

Fluxo por execução:
  1. Carrega config/settings.yaml e config/skus.yaml
  2. Resolve todos os MLBs de todos os SKUs via API
  3. Separa MLBs em duas seções: com estoque e sem estoque
  4. Processa seção "com estoque" primeiro, depois "sem estoque"
  5. Para cada MLB: busca campanhas, calcula RC, decide ACEITAR/RECUSAR
  6. Em dry_run=False, ACEITARIA via API (stub — implementar quando sair do dry-run)
  7. Loga tudo no terminal + arquivo
"""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from . import decisor, margem, ml_client, notificador, pdv

_CONFIG_DIR = Path(__file__).parent.parent / "config"


def _load_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run(dry_run_override: bool | None = None, filter_sku: str | None = None) -> None:
    cfg = _load_settings()
    dry_run = dry_run_override if dry_run_override is not None else cfg["dry_run"]
    rc_min  = float(cfg["rc_minimo"])

    notificador.setup(cfg.get("log_dir", "logs"), cfg.get("log_output", "both"))

    skus = pdv.load_skus()
    if filter_sku:
        upper = filter_sku.upper()
        if upper not in skus:
            print(f"SKU '{upper}' não encontrado em config/skus.yaml")
            return
        skus = {upper: skus[upper]}

    notificador.inicio_execucao(dry_run, len(skus))

    seller_id = ml_client.get_seller_id()

    # -----------------------------------------------------------------------
    # Fase 1 — Resolve todos os MLBs (item_ids + detalhes) de todos os SKUs
    # -----------------------------------------------------------------------
    # Lista de (sku, sku_data, item_dict)
    todos: list[tuple[str, dict, dict]] = []

    for sku, sku_data in skus.items():
        item_ids = ml_client.get_item_ids_by_sku(seller_id, sku)
        if not item_ids:
            notificador.sku_sem_mlbs(sku)
            continue
        items = ml_client.get_items_details(item_ids)
        time.sleep(0.15)
        for item in items:
            todos.append((sku, sku_data, item))

    # -----------------------------------------------------------------------
    # Fase 2 — Separa por estoque
    # -----------------------------------------------------------------------
    com_estoque = [(s, sd, it) for s, sd, it in todos if it.get("available_quantity", 0) > 0]
    sem_estoque = [(s, sd, it) for s, sd, it in todos if it.get("available_quantity", 0) == 0]

    # -----------------------------------------------------------------------
    # Fase 3 — Processa e exibe
    # -----------------------------------------------------------------------
    stats = {"aceitos": 0, "recusados": 0, "erros": 0}

    for sku, sku_data, item in com_estoque:
        _process_item(sku, sku_data, item, has_stock=True,
                      cfg=cfg, rc_min=rc_min, dry_run=dry_run, stats=stats)

    if sem_estoque:
        notificador.secao_sem_estoque(len(sem_estoque))
        for sku, sku_data, item in sem_estoque:
            _process_item(sku, sku_data, item, has_stock=False,
                          cfg=cfg, rc_min=rc_min, dry_run=dry_run, stats=stats)

    notificador.fim_execucao(stats["aceitos"], stats["recusados"], stats["erros"])


def _process_item(
    sku: str,
    sku_data: dict,
    item: dict,
    has_stock: bool,
    cfg: dict,
    rc_min: float,
    dry_run: bool,
    stats: dict,
) -> None:
    item_id      = item.get("id", "")
    listing_id   = item.get("listing_type_id", "")
    tipo_anuncio = "Premium" if "gold_pro" in listing_id else sku_data["tipo_anuncio"]
    is_full      = item.get("shipping", {}).get("logistic_type") == "fulfillment"
    item_cfg     = {**cfg, "insumo_fixo": 0.0} if is_full else cfg

    try:
        campaigns = ml_client.get_campaigns_for_item(item_id)

        # Campanhas já ativas (participando)
        for campanha in campaigns["ativas"]:
            preco_ativo = campanha.get("price") or 0.0
            resultado_margem = margem.calcular_margem(
                preco_campanha=preco_ativo,
                custo=sku_data["custo"],
                rebate=campanha.get("rebate_valor", 0.0),
                peso=sku_data["peso"],
                tipo_anuncio=tipo_anuncio,
                cfg=item_cfg,
            )
            notificador.campanha_ativa(sku, item_id, campanha, resultado_margem, is_full, has_stock)

        # Campanhas candidatas com rebate do ML
        candidatas = [c for c in campaigns["disponiveis"] if c.get("rebate_valor", 0) > 0]

        if not campaigns["ativas"] and not campaigns["disponiveis"]:
            notificador.sem_campanhas(sku, item_id, has_stock)
            return

        if not candidatas:
            if not campaigns["ativas"]:
                notificador.sem_rebate(sku, item_id, has_stock)
            return

        for campanha in candidatas:
            # PRICE_MATCHING: preço fixado pelo ML — usar price, não suggested_price
            if campanha.get("type") == "PRICE_MATCHING":
                preco_sugerido = campanha.get("price") or 0.0
            else:
                preco_sugerido = campanha.get("suggested_price") or campanha.get("price") or 0.0
            resultado_margem = margem.calcular_margem(
                preco_campanha=preco_sugerido,
                custo=sku_data["custo"],
                rebate=campanha.get("rebate_valor", 0.0),
                peso=sku_data["peso"],
                tipo_anuncio=tipo_anuncio,
                cfg=item_cfg,
            )
            resultado_decisao = decisor.decidir(resultado_margem, rc_min)
            notificador.decisao(
                sku, item_id, campanha, resultado_margem,
                resultado_decisao, dry_run, is_full, has_stock,
            )
            if resultado_decisao["decisao"] == "ACEITAR":
                stats["aceitos"] += 1
                if not dry_run:
                    _aceitar_campanha(item_id, campanha, preco_sugerido)
            else:
                stats["recusados"] += 1

    except Exception as exc:
        notificador.erro(sku, item_id, exc)
        stats["erros"] += 1

    time.sleep(0.1)


def _aceitar_campanha(item_id: str, campanha: dict, preco: float) -> None:
    """
    Stub para aceitar uma campanha via API.
    Implementar quando sair do modo dry_run.

    Endpoint esperado:
      POST /seller-promotions/items/{item_id}
      body: {"promotion_id": campanha["ref_id"], "price": preco}
    """
    raise NotImplementedError(
        "Aceite automático ainda não implementado. "
        "Ative somente após validar o endpoint correto da API ML."
    )
