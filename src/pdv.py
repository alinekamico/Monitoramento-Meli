"""
Carrega dados de custo/peso dos SKUs a partir de config/skus.yaml.

Futuro: substituir o YAML por chamada à API do Tiny ERP para custo em tempo real.
"""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import yaml

_CONFIG_DIR = Path(__file__).parent.parent / "config"


class SkuData(TypedDict):
    custo: float
    peso: float
    tipo_anuncio: str


def load_skus() -> dict[str, SkuData]:
    """Retorna {SKU: {custo, peso, tipo_anuncio}} do YAML."""
    path = _CONFIG_DIR / "skus.yaml"
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return {sku.upper(): data for sku, data in raw["skus"].items()}


def get_sku_data(sku: str, skus: dict[str, SkuData]) -> SkuData | None:
    return skus.get(sku.upper())
