"""
Diagnóstico completo de campanhas de um MLB — mostra TODOS os status
retornados pela API (started, candidate, paused, pending, etc.).

Uso:
    python -m scripts.diagnosticar_campanha MLB4232704563
    python -m scripts.diagnosticar_campanha MLB4232704563 --conta hair_pro
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from dotenv import load_dotenv

from src import ml_client  # noqa: E402

_CAMPOS_PRICING = [
    "price", "original_price", "meli_percentage", "seller_percentage",
    "min_discounted_price", "max_discounted_price", "suggested_discounted_price",
]


def _print_campanha(i: int, c: dict) -> None:
    status = c.get("status", "?")
    nome   = c.get("name") or c.get("type") or "—"
    print(f"--- [{i}] status={status} | {nome} ---")
    print(json.dumps(c, ensure_ascii=False, indent=2, default=str))
    print()
    for campo in _CAMPOS_PRICING:
        print(f"  > {campo:<32} {c.get(campo)}")
    print()


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print("Uso: python -m scripts.diagnosticar_campanha MLB1234 [--conta hair_pro]")
        return 1

    item_id = args[0]
    conta   = "best_hair"
    if "--conta" in args:
        idx = args.index("--conta")
        if idx + 1 < len(args):
            conta = args[idx + 1]

    load_dotenv()

    url  = f"{ml_client._BASE_URL}/seller-promotions/items/{item_id}"
    resp = requests.get(url, params={"app_version": "v2"},
                        headers=ml_client._auth_headers(conta), timeout=20)
    if resp.status_code == 401:
        ml_client._refresh_access_token(conta)
        resp = requests.get(url, params={"app_version": "v2"},
                            headers=ml_client._auth_headers(conta), timeout=20)
    resp.raise_for_status()
    raw = resp.json()

    if not isinstance(raw, list):
        print(f"Resposta inesperada (não é lista): {type(raw).__name__}")
        print(json.dumps(raw, ensure_ascii=False, indent=2, default=str))
        return 1

    # Agrupa por status para facilitar leitura
    from collections import defaultdict
    por_status: dict[str, list] = defaultdict(list)
    for c in raw:
        por_status[c.get("status", "?")].append(c)

    total = len(raw)
    resumo = ", ".join(f"{s}={len(v)}" for s, v in sorted(por_status.items()))
    print(f"=== {item_id} ({conta}): {total} campanha(s) — {resumo} ===\n")

    for status in sorted(por_status):
        campanhas = por_status[status]
        print(f"{'='*60}")
        print(f"  STATUS: {status.upper()} ({len(campanhas)} campanha(s))")
        print(f"{'='*60}\n")
        for i, c in enumerate(campanhas, 1):
            _print_campanha(i, c)

    if not raw:
        print("Nenhuma campanha retornada pela API para este item.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
