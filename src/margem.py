"""
Cálculo de margem de contribuição e RC (Retorno sobre Custo).

Fórmulas (espelhadas da planilha PDV Campanhas Meli.xlsx):
  comissao     = preco × comissao_pct
  frete        = tabela(preco, peso)
  imposto      = preco × imposto_pct
  insumo       = insumo_fixo  (R$ fixo)
  reversa      = preco × reversa_pct
  lucro_bruto  = preco − custo − comissao − frete − imposto − insumo − reversa + rebate
  margem_pct   = lucro_bruto / preco × 100
  rc_pct       = lucro_bruto / custo × 100   ← métrica principal de decisão
"""

from __future__ import annotations

import bisect
from pathlib import Path

import yaml

_CONFIG_DIR = Path(__file__).parent.parent / "config"
_frete_cache: dict | None = None


def _load_frete() -> dict:
    global _frete_cache
    if _frete_cache is None:
        with open(_CONFIG_DIR / "frete_tabela.yaml", encoding="utf-8") as f:
            _frete_cache = yaml.safe_load(f)
    return _frete_cache


def calcular_frete(preco: float, peso: float) -> float:
    frete_cfg = _load_frete()
    preco_breaks = frete_cfg["preco_breaks"]
    peso_breaks  = frete_cfg["peso_breaks"]
    tabela       = frete_cfg["tabela"]

    p_idx = max(0, bisect.bisect_right(preco_breaks, preco) - 1)
    w_idx = max(0, bisect.bisect_right(peso_breaks,  peso)  - 1)

    p_idx = min(p_idx, len(preco_breaks) - 1)
    w_idx = min(w_idx, len(peso_breaks)  - 1)

    return float(tabela[w_idx][p_idx])


def calcular_margem(
    preco_campanha: float,
    custo: float,
    rebate: float,
    peso: float,
    tipo_anuncio: str,
    cfg: dict,
) -> dict:
    """
    Parâmetros
    ----------
    preco_campanha : preço sugerido pela campanha (R$)
    custo          : CMV do SKU (R$)
    rebate         : subsídio do ML em R$ (meli_percentage × original_price / 100)
    peso           : peso do produto em kg
    tipo_anuncio   : 'Clássico' ou 'Premium'
    cfg            : dicionário carregado de settings.yaml

    Retorna dict com todos os componentes.
    """
    if preco_campanha <= 0 or custo <= 0:
        return _vazio()

    comissao_pct = (
        cfg["comissao_premium"]
        if tipo_anuncio.strip().lower() == "premium"
        else cfg["comissao_classico"]
    )

    comissao = preco_campanha * comissao_pct
    frete    = calcular_frete(preco_campanha, peso)
    imposto  = preco_campanha * cfg["imposto_pct"]
    insumo   = cfg["insumo_fixo"]
    reversa  = preco_campanha * cfg["reversa_pct"]

    lucro_bruto = preco_campanha - custo - comissao - frete - imposto - insumo - reversa + rebate

    margem_pct = (lucro_bruto / preco_campanha * 100) if preco_campanha else 0.0
    rc_pct     = (lucro_bruto / custo * 100)          if custo         else 0.0

    return {
        "preco_campanha": round(preco_campanha, 2),
        "custo":          round(custo, 2),
        "rebate":         round(rebate, 2),
        "comissao":       round(comissao, 2),
        "frete":          round(frete, 2),
        "imposto":        round(imposto, 2),
        "insumo":         round(insumo, 2),
        "reversa":        round(reversa, 2),
        "lucro_bruto":    round(lucro_bruto, 2),
        "margem_pct":     round(margem_pct, 2),
        "rc_pct":         round(rc_pct, 2),
    }


def _vazio() -> dict:
    return {k: 0.0 for k in [
        "preco_campanha", "custo", "rebate", "comissao", "frete",
        "imposto", "insumo", "reversa", "lucro_bruto", "margem_pct", "rc_pct",
    ]}
