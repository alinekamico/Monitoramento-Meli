"""
Diagnóstico detalhado de uma campanha started do ML — mostra TODOS os
campos brutos da API, incluindo limites de preço que possam existir.

Uso:
    python -m scripts.diagnosticar_campanha MLB4232704563
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv

from src import ml_client  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.diagnosticar_campanha MLB1234")
        return 1
    item_id = sys.argv[1]
    load_dotenv()

    url = f"{ml_client._BASE_URL}/seller-promotions/items/{item_id}"
    resp = requests.get(url, params={"app_version": "v2"},
                        headers=ml_client._auth_headers(), timeout=20)
    if resp.status_code == 401:
        ml_client._refresh_access_token()
        resp = requests.get(url, params={"app_version": "v2"},
                            headers=ml_client._auth_headers(), timeout=20)
    resp.raise_for_status()
    raw = resp.json()

    if not isinstance(raw, list):
        print("Resposta inesperada:", raw)
        return 1

    started = [c for c in raw if c.get("status") == "started"]
    print(f"=== {item_id}: {len(started)} campanha(s) started ===\n")
    for i, c in enumerate(started, 1):
        print(f"--- Campanha {i} (status=started) ---")
        print(json.dumps(c, ensure_ascii=False, indent=2, default=str))
        print()
        # Destaque dos campos relevantes para o pricing
        print(f"  > price:                  {c.get('price')}")
        print(f"  > original_price:         {c.get('original_price')}")
        print(f"  > meli_percentage:        {c.get('meli_percentage')}")
        print(f"  > seller_percentage:      {c.get('seller_percentage')}")
        print(f"  > min_discounted_price:   {c.get('min_discounted_price')}  <-- FAIXA INFERIOR")
        print(f"  > max_discounted_price:   {c.get('max_discounted_price')}  <-- FAIXA SUPERIOR")
        print(f"  > suggested_discounted_price: {c.get('suggested_discounted_price')}")
        print()

    if not started:
        print("Sem campanhas started — não há rebate a se aplicar.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
