"""
Script de diagnóstico: mostra todos os campos relevantes de preço/campanha
para um item_id específico, ajudando a entender qual campo reflete o
preço visível ao cliente.

Uso:
    python -m scripts.inspecionar_item MLB4422127791
    python -m scripts.inspecionar_item MLB4422127791 MLB2663223853  # múltiplos

Saída inclui:
  - GET /items/{id}        — campos price, original_price, sale_price, deal_ids
  - GET de campanhas       — started e candidate, com price/original_price
  - GET /products/{pid}/items — top 5 concorrentes (preço visível ao cliente)

Nada é persistido no banco. Read-only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import ml_client  # noqa: E402


def _print(d, indent=2):
    print(json.dumps(d, ensure_ascii=False, indent=indent, default=str))


def inspecionar(item_id: str) -> None:
    print("=" * 70)
    print(f"  ITEM: {item_id}")
    print("=" * 70)

    # 1) Detalhe do item via batch
    details = ml_client.get_items_details([item_id])
    if not details:
        print("  ✗ Item não retornado pelo ML")
        return
    item = details[0]

    print("\n[1] /items/{id} — campos de preço:")
    campos_preco = {
        "price":             item.get("price"),
        "original_price":    item.get("original_price"),
        "base_price":        item.get("base_price"),
        "sale_price":        item.get("sale_price"),
        "discounted_price":  item.get("discounted_price"),
        "currency_id":       item.get("currency_id"),
        "status":            item.get("status"),
        "available_quantity": item.get("available_quantity"),
        "listing_type_id":   item.get("listing_type_id"),
        "catalog_product_id": item.get("catalog_product_id"),
        "deal_ids":          item.get("deal_ids"),
        "permalink":         item.get("permalink"),
    }
    _print(campos_preco)

    # Alguns itens trazem o preço promocional dentro de "prices" (estrutura nova)
    if "prices" in item:
        print("\n[1b] item['prices'] (estrutura nova ML):")
        _print(item["prices"])

    # 2) Campanhas — started (ativa) e candidate (disponível)
    print("\n[2] /seller-promotions/items/{id}:")
    try:
        camps = ml_client.get_campaigns_for_item(item_id)
    except Exception as exc:
        print(f"  ✗ Erro: {exc}")
        return

    print(f"  {len(camps['ativas'])} ativa(s) (started):")
    for c in camps["ativas"]:
        _print({
            "id": c.get("id"), "ref_id": c.get("ref_id"),
            "type": c.get("type"), "sub_type": c.get("sub_type"),
            "name": c.get("name"),
            "price": c.get("price"),                   # preço com desconto?
            "original_price": c.get("original_price"),
            "meli_percentage": c.get("meli_percentage"),
            "seller_percentage": c.get("seller_percentage"),
            "rebate_valor": c.get("rebate_valor"),
            "start_date": c.get("start_date"),
            "finish_date": c.get("finish_date"),
        }, indent=4)

    print(f"  {len(camps['disponiveis'])} candidata(s):")
    for c in camps["disponiveis"]:
        _print({
            "id": c.get("id"), "ref_id": c.get("ref_id"),
            "type": c.get("type"),
            "price": c.get("price"),
            "suggested_price": c.get("suggested_price"),
            "meli_percentage": c.get("meli_percentage"),
        }, indent=4)

    # 3) Top vendedores no catálogo público
    pid = item.get("catalog_product_id")
    if not pid:
        print("\n[3] catalog_product_id ausente — sem top 5")
        return

    print(f"\n[3] /products/{pid}/items — top 5 concorrentes (preço visível ao cliente):")
    top = ml_client.get_top_sellers_for_product(pid, limit=5)
    for entry in top:
        _print({
            "item_id": entry.get("item_id"),
            "seller_id": entry.get("seller_id"),
            "price": entry.get("price"),
            "original_price": entry.get("original_price"),
            "shipping": (entry.get("shipping") or {}).get("logistic_type"),
            "free_shipping": (entry.get("shipping") or {}).get("free_shipping"),
            "permalink": entry.get("permalink"),
        }, indent=4)

    # 4) Resumo comparativo
    print("\n[4] RESUMO — qual preço usar?")
    p_item = item.get("price")
    p_orig = item.get("original_price")
    p_camp_started = next(
        (c.get("price") for c in camps["ativas"] if c.get("price")),
        None,
    )
    print(f"  item.price           = R$ {p_item}")
    print(f"  item.original_price  = R$ {p_orig}")
    print(f"  campanha_started.price = R$ {p_camp_started}")
    # Acha o preço do mesmo item no top5 do catálogo
    nosso_no_top = next(
        (e for e in top if e.get("item_id") == item_id),
        None,
    )
    if nosso_no_top:
        print(f"  top5.price (nós, visível ao cliente) = R$ {nosso_no_top.get('price')}")
    else:
        print("  top5: nosso anúncio não está na lista pública")


def main() -> None:
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.inspecionar_item MLB1234 [MLB5678 ...]")
        sys.exit(1)

    for item_id in sys.argv[1:]:
        inspecionar(item_id)
        print()


if __name__ == "__main__":
    main()
