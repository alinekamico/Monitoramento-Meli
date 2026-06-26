"""
Cliente Mercado Livre — OAuth2 + Central de Promoções.

Suporte a múltiplas contas (The Best Hair / Hair Pro).
Cada conta tem seu próprio conjunto de credenciais no .env, identificado
pelo sufixo definido em config/contas.yaml (ex: _BESTHAIR, _HAIRPRO).

Backward compat: se ML_APP_ID_BESTHAIR não existir mas ML_APP_ID existir,
usa o valor sem sufixo para a conta best_hair — migração transparente.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

import requests
import yaml
from dotenv import load_dotenv, set_key

load_dotenv()

_log = logging.getLogger(__name__)

_BASE_URL  = "https://api.mercadolibre.com"
_TOKEN_URL = f"{_BASE_URL}/oauth/token"
_ENV_FILE  = Path(__file__).parent.parent / ".env"
_CONTAS_FILE = Path(__file__).parent.parent / "config" / "contas.yaml"


# ---------------------------------------------------------------------------
# Carregamento de contas
# ---------------------------------------------------------------------------

def _load_contas() -> dict:
    """Lê config/contas.yaml e retorna o dict completo."""
    with open(_CONTAS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _sufixo(conta: str) -> str:
    """Retorna o sufixo de env para a conta (ex: 'BESTHAIR', 'HAIRPRO')."""
    cfg = _load_contas()
    contas = cfg.get("contas", {})
    return contas.get(conta, {}).get("env_sufixo", conta.upper())


def _get_env(conta: str, campo: str) -> str:
    """
    Lê variável de ambiente para a conta.

    Tenta ML_{CAMPO}_{SUFIXO} primeiro (novo padrão multi-conta).
    Fallback para ML_{CAMPO} sem sufixo (backward compat para best_hair).

    Se a variável não for encontrada, recarrega o .env (tolerância a servidores
    que foram iniciados antes do .env ser preenchido, ex.: antes de gerar tokens).
    """
    sufixo = _sufixo(conta)
    val = os.getenv(f"ML_{campo}_{sufixo}", "").strip()
    if not val and conta == "best_hair":
        val = os.getenv(f"ML_{campo}", "").strip()
    # Se ainda vazio, recarrega o .env e tenta novamente (servidor iniciado antes
    # de o .env ser atualizado com os tokens da conta)
    if not val:
        load_dotenv(_ENV_FILE, override=True)
        val = os.getenv(f"ML_{campo}_{sufixo}", "").strip()
        if not val and conta == "best_hair":
            val = os.getenv(f"ML_{campo}", "").strip()
    return val


def _set_env(conta: str, campo: str, valor: str) -> None:
    """Persiste variável de ambiente para a conta no .env e em os.environ."""
    sufixo = _sufixo(conta)
    key = f"ML_{campo}_{sufixo}"
    if not _ENV_FILE.exists():
        _ENV_FILE.touch()
    set_key(str(_ENV_FILE), key, valor)
    os.environ[key] = valor


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _persist_tokens(data: dict, conta: str = "best_hair") -> None:
    for campo, data_key in [
        ("ACCESS_TOKEN",  "access_token"),
        ("REFRESH_TOKEN", "refresh_token"),
        ("SELLER_ID",     "user_id"),
    ]:
        value = str(data.get(data_key, ""))
        if value:
            _set_env(conta, campo, value)


def _refresh_access_token(conta: str = "best_hair") -> str:
    resp = requests.post(_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "client_id":     _get_env(conta, "APP_ID"),
        "client_secret": _get_env(conta, "CLIENT_SECRET"),
        "refresh_token": _get_env(conta, "REFRESH_TOKEN"),
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    _persist_tokens(data, conta)
    load_dotenv(override=True)
    return data["access_token"]


def _auth_headers(conta: str = "best_hair") -> dict:
    token = _get_env(conta, "ACCESS_TOKEN")
    if not token:
        raise RuntimeError(
            f"ML_ACCESS_TOKEN_{_sufixo(conta)} não encontrado. "
            f"Execute: python scripts/gerar_tokens.py --conta {conta}"
        )
    return {"Authorization": f"Bearer {token}"}


def _request(method: str, url: str, conta: str = "best_hair", **kwargs) -> dict | list:
    """Faz requisição e renova token automaticamente em caso de 401."""
    resp = requests.request(method, url, headers=_auth_headers(conta), timeout=20, **kwargs)
    if resp.status_code == 401:
        _refresh_access_token(conta)
        resp = requests.request(method, url, headers=_auth_headers(conta), timeout=20, **kwargs)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Seller
# ---------------------------------------------------------------------------

def get_seller_id(conta: str = "best_hair") -> str:
    seller_id = _get_env(conta, "SELLER_ID")
    if not seller_id:
        data = _request("GET", f"{_BASE_URL}/users/me", conta=conta)
        seller_id = str(data["id"])
        _set_env(conta, "SELLER_ID", seller_id)
    return seller_id


# ---------------------------------------------------------------------------
# Items por SKU
# ---------------------------------------------------------------------------

def get_item_ids_by_sku(seller_id: str, sku: str, conta: str = "best_hair") -> list[str]:
    try:
        data = _request(
            "GET",
            f"{_BASE_URL}/users/{seller_id}/items/search",
            conta=conta,
            params={"seller_sku": sku, "limit": 10},
        )
        return data.get("results", [])
    except Exception as exc:
        _log.warning("get_item_ids_by_sku sku=%s: %s", sku, exc)
        return []


def get_items_details(item_ids: list[str], conta: str = "best_hair") -> list[dict]:
    result = []
    for i in range(0, len(item_ids), 20):
        chunk = item_ids[i: i + 20]
        try:
            data = _request("GET", f"{_BASE_URL}/items", conta=conta,
                            params={"ids": ",".join(chunk)})
            for entry in data:
                if entry.get("code") == 200:
                    result.append(entry["body"])
        except Exception:
            pass
        time.sleep(0.1)
    return result


# ---------------------------------------------------------------------------
# Campanhas (Central de Promoções)
# ---------------------------------------------------------------------------

def _normalize_date(val: object) -> str:
    """Normaliza data para ISO 8601. Converte espaço em 'T' se necessário."""
    if not val:
        return ""
    s = str(val).strip()
    return s.replace(" ", "T") if " " in s else s


def _parse_promotion(raw: dict) -> dict:
    original_price = float(raw.get("original_price", 0) or 0)
    price          = float(raw.get("price", 0) or 0)
    meli_pct       = float(raw.get("meli_percentage", 0) or 0)
    seller_pct     = float(raw.get("seller_percentage", 0) or 0)
    _base_rebate   = original_price or price  # fallback: price quando original_price ausente
    rebate_valor   = round(_base_rebate * meli_pct / 100, 2) if (_base_rebate and meli_pct) else 0.0

    _nested = {
        **{k: v for sub in ["deal", "conditions", "schedule", "promotion", "validity"]
           for k, v in (raw.get(sub) or {}).items()},
    }

    def _pick(*fields: str) -> str:
        for f in fields:
            v = raw.get(f) or _nested.get(f)
            if v:
                return v
        return ""

    start_date = _normalize_date(
        _pick("start_date", "starts_at", "date_from", "date_start",
              "promotion_start_date", "effective_start", "valid_from", "from_date")
    )
    finish_date = _normalize_date(
        _pick("finish_date", "ends_at", "finishes_at", "date_to", "date_end",
              "promotion_end_date", "effective_end", "valid_until", "to_date")
    )

    return {
        "id":             raw.get("id") or "",
        "ref_id":         raw.get("ref_id") or "",
        "type":           raw.get("type") or "",
        "sub_type":       raw.get("sub_type") or "",
        "status":         raw.get("status") or "",
        "name":           raw.get("name") or "",
        "price":          price,
        "original_price": original_price,
        "meli_percentage":   meli_pct,
        "seller_percentage": seller_pct,
        "rebate_valor":   rebate_valor,
        "start_date":     start_date,
        "finish_date":    finish_date,
        "suggested_price": float(raw.get("suggested_discounted_price", 0) or 0),
        "min_price":      float(raw.get("min_discounted_price", 0) or 0),
        "max_price":      float(raw.get("max_discounted_price", 0) or 0),
    }


_CAMPAIGN_RETRIES    = 3
_CAMPAIGN_RETRY_DELAY = 2.0


def get_campaigns_for_item(item_id: str, conta: str = "best_hair") -> dict:
    """
    Retorna campanhas do item separadas em 'ativas' (started) e
    'disponiveis' (candidate).
    """
    url    = f"{_BASE_URL}/seller-promotions/items/{item_id}"
    params = {"app_version": "v2"}

    raw_list: list[dict] = []
    last_exc: Exception | None = None

    for attempt in range(1, _CAMPAIGN_RETRIES + 1):
        try:
            data = _request("GET", url, conta=conta, params=params)
            if isinstance(data, list):
                raw_list = data
            elif isinstance(data, dict):
                raw_list = data.get("results") or data.get("promotions") or []
                if not raw_list:
                    _log.warning("get_campaigns_for_item %s: dict inesperado da API: chaves=%s", item_id, list(data.keys()))
            last_exc = None
            break
        except requests.HTTPError as exc:
            code = getattr(exc.response, "status_code", None)
            if code in (400, 404):
                last_exc = None
                break
            if code == 500 and attempt < _CAMPAIGN_RETRIES:
                time.sleep(_CAMPAIGN_RETRY_DELAY)
                continue
            last_exc = exc
            break
        except Exception as exc:
            last_exc = exc
            break

    if last_exc is not None:
        raise last_exc

    ativas: list[dict] = []
    disponiveis: list[dict] = []
    for raw in raw_list:
        entry = _parse_promotion(raw)
        if entry["status"] == "started":
            ativas.append(entry)
        elif entry["status"] == "candidate":
            disponiveis.append(entry)
        else:
            _log.debug("get_campaigns_for_item %s: status ignorado '%s' (id=%s)", item_id, entry["status"], entry.get("id"))

    return {"ativas": ativas, "disponiveis": disponiveis}


# ---------------------------------------------------------------------------
# Catálogo público (MVP Buybox)
# ---------------------------------------------------------------------------

# Cache de sellers keyed por (conta, seller_id)
_seller_cache: dict[tuple[str, str], dict] = {}


def get_product_id_from_item(item_id: str, item_detail: dict | None = None,
                              conta: str = "best_hair") -> str | None:
    if item_detail is None:
        try:
            item_detail = _request("GET", f"{_BASE_URL}/items/{item_id}", conta=conta)
        except Exception:
            return None
    if not isinstance(item_detail, dict):
        return None
    pid = item_detail.get("catalog_product_id")
    return pid if pid else None


def get_catalog_buybox_winner(product_id: str, conta: str = "best_hair") -> dict | None:
    try:
        data = _request("GET", f"{_BASE_URL}/products/{product_id}", conta=conta)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data.get("buy_box_winner")


def get_top_sellers_for_product(product_id: str, limit: int = 5,
                                 conta: str = "best_hair") -> list[dict]:
    try:
        data = _request(
            "GET",
            f"{_BASE_URL}/products/{product_id}/items",
            conta=conta,
            params={"limit": max(limit, 1), "offset": 0},
        )
    except Exception:
        return []

    if isinstance(data, dict):
        results = data.get("results") or []
    elif isinstance(data, list):
        results = data
    else:
        results = []

    def _preco(entry: dict) -> float:
        return float(entry.get("price") or 0.0) or float("inf")

    results = sorted(results, key=_preco)
    return results[:limit]


def get_seller_info(seller_id: str, conta: str = "best_hair") -> dict:
    """Retorna informações públicas do seller, com cache por (conta, seller_id)."""
    if not seller_id:
        return {"id": "", "nickname": "", "reputacao": ""}
    cache_key = (conta, str(seller_id))
    cached = _seller_cache.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = _request("GET", f"{_BASE_URL}/users/{seller_id}", conta=conta)
    except Exception:
        info = {"id": str(seller_id), "nickname": "", "reputacao": ""}
        _seller_cache[cache_key] = info
        return info

    if not isinstance(data, dict):
        info = {"id": str(seller_id), "nickname": "", "reputacao": "",
                "total_vendas": 0, "vendas_completas": 0}
    else:
        rep   = data.get("seller_reputation") or {}
        trans = rep.get("transactions") or {}
        info  = {
            "id":               str(data.get("id") or seller_id),
            "nickname":         data.get("nickname") or "",
            "reputacao":        rep.get("level_id") or rep.get("power_seller_status") or "",
            "total_vendas":     int(trans.get("total") or 0),
            "vendas_completas": int(trans.get("completed") or 0),
        }
    _seller_cache[cache_key] = info
    return info


def limpar_cache_sellers() -> None:
    """Limpa o cache em memória de sellers. Chamado entre ciclos do scheduler."""
    _seller_cache.clear()


def get_orders_for_item(
    item_id: str,
    desde_iso: str,
    ate_iso: str,
    max_pedidos: int = 100,
    conta: str = "best_hair",
) -> list[dict]:
    """Retorna pedidos do vendedor para o item no período, com paginação."""
    seller_id  = get_seller_id(conta)
    resultados: list[dict] = []
    offset = 0
    batch  = 50

    while len(resultados) < max_pedidos:
        try:
            params: dict = {
                "seller": seller_id,
                "q":      item_id,
                "sort":   "date_desc",
                "limit":  min(batch, max_pedidos - len(resultados)),
                "offset": offset,
            }
            if desde_iso:
                params["date_created.from"] = desde_iso
            if ate_iso:
                params["date_created.to"] = ate_iso
            data = _request("GET", f"{_BASE_URL}/orders/search", conta=conta, params=params)
        except Exception as exc:
            _log.warning("get_orders_for_item item=%s offset=%d: %s", item_id, offset, exc)
            break

        if not isinstance(data, dict):
            _log.warning("get_orders_for_item item=%s: resposta inesperada tipo %s", item_id, type(data).__name__)
            break

        paging  = data.get("paging") or {}
        pagina  = data.get("results") or []
        resultados.extend(pagina)

        total  = int(paging.get("total") or 0)
        offset += len(pagina)
        if offset >= total or len(pagina) == 0:
            break
        time.sleep(0.1)

    return resultados


def get_prazo_entrega_dias(item_id: str, zip_code: str,
                            conta: str = "best_hair") -> int | None:
    """Estimativa de prazo de entrega em dias para o CEP informado."""
    if not item_id or not zip_code:
        return None
    cep = "".join(c for c in zip_code if c.isdigit())
    if len(cep) != 8:
        return None
    try:
        data = _request(
            "GET",
            f"{_BASE_URL}/items/{item_id}/shipping_options",
            conta=conta,
            params={"zip_code": cep},
        )
    except Exception:
        return None
    if not isinstance(data, dict):
        return None

    options = data.get("options") or []
    if not options:
        return None

    from datetime import datetime as _dt

    melhor_dias = None
    agora = _dt.now()
    for opt in options:
        delivery = ((opt.get("estimated_delivery_time") or {}).get("date")
                    or (opt.get("estimated_handling_limit") or {}).get("date"))
        if not delivery:
            continue
        try:
            d = _dt.fromisoformat(delivery.replace("Z", "+00:00"))
        except ValueError:
            continue
        dias = max(0, (d.replace(tzinfo=None) - agora).days)
        if melhor_dias is None or dias < melhor_dias:
            melhor_dias = dias
    return melhor_dias


# ---------------------------------------------------------------------------
# Utilitários de contas
# ---------------------------------------------------------------------------

def listar_contas() -> list[dict]:
    """Retorna lista de contas configuradas com nome e conta_id."""
    cfg = _load_contas()
    contas = cfg.get("contas", {})
    padrao = cfg.get("conta_padrao", "best_hair")
    return [
        {"id": k, "nome": v.get("nome", k), "padrao": (k == padrao)}
        for k, v in contas.items()
    ]
