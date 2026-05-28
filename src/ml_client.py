"""
Cliente Mercado Livre — OAuth2 + Central de Promoções.

Autenticação inicial (feita uma única vez no projeto existente):
  Os tokens ML_ACCESS_TOKEN / ML_REFRESH_TOKEN já estão no .env.
  Este módulo só renova o access_token automaticamente via refresh quando
  recebe 401.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

_BASE_URL = "https://api.mercadolibre.com"
_TOKEN_URL = f"{_BASE_URL}/oauth/token"
_ENV_FILE = Path(__file__).parent.parent / ".env"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _persist_tokens(data: dict) -> None:
    if not _ENV_FILE.exists():
        _ENV_FILE.touch()
    for env_key, data_key in [
        ("ML_ACCESS_TOKEN", "access_token"),
        ("ML_REFRESH_TOKEN", "refresh_token"),
        ("ML_SELLER_ID",    "user_id"),
    ]:
        value = str(data.get(data_key, ""))
        if value:
            set_key(str(_ENV_FILE), env_key, value)
            os.environ[env_key] = value


def _refresh_access_token() -> str:
    resp = requests.post(_TOKEN_URL, data={
        "grant_type":    "refresh_token",
        "client_id":     os.getenv("ML_APP_ID"),
        "client_secret": os.getenv("ML_CLIENT_SECRET"),
        "refresh_token": os.getenv("ML_REFRESH_TOKEN"),
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    _persist_tokens(data)
    load_dotenv(override=True)
    return data["access_token"]


def _auth_headers() -> dict:
    token = os.getenv("ML_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError(
            "ML_ACCESS_TOKEN não encontrado. "
            "Copie as credenciais do .env do projeto de automação existente."
        )
    return {"Authorization": f"Bearer {token}"}


def _request(method: str, url: str, **kwargs) -> dict | list:
    """Faz requisição e renova token automaticamente em caso de 401."""
    resp = requests.request(method, url, headers=_auth_headers(), timeout=20, **kwargs)
    if resp.status_code == 401:
        _refresh_access_token()
        resp = requests.request(method, url, headers=_auth_headers(), timeout=20, **kwargs)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Seller
# ---------------------------------------------------------------------------

def get_seller_id() -> str:
    seller_id = os.getenv("ML_SELLER_ID", "")
    if not seller_id:
        data = _request("GET", f"{_BASE_URL}/users/me")
        seller_id = str(data["id"])
        set_key(str(_ENV_FILE), "ML_SELLER_ID", seller_id)
        os.environ["ML_SELLER_ID"] = seller_id
    return seller_id


# ---------------------------------------------------------------------------
# Items por SKU
# ---------------------------------------------------------------------------

def get_item_ids_by_sku(seller_id: str, sku: str) -> list[str]:
    try:
        data = _request(
            "GET",
            f"{_BASE_URL}/users/{seller_id}/items/search",
            params={"seller_sku": sku, "limit": 10},
        )
        return data.get("results", [])
    except Exception:
        return []


def get_items_details(item_ids: list[str]) -> list[dict]:
    result = []
    for i in range(0, len(item_ids), 20):
        chunk = item_ids[i: i + 20]
        try:
            data = _request("GET", f"{_BASE_URL}/items", params={"ids": ",".join(chunk)})
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
    rebate_valor   = round(original_price * meli_pct / 100, 2) if original_price else 0.0

    # A API ML usa nomes diferentes dependendo do tipo de campanha.
    # Checamos os mais comuns em ordem de preferência — campos de nível raiz
    # e, em seguida, objetos aninhados (deal, conditions, schedule).
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
        "id":            raw.get("id") or "",
        "ref_id":        raw.get("ref_id") or "",
        "type":          raw.get("type") or "",
        "sub_type":      raw.get("sub_type") or "",
        "status":        raw.get("status") or "",
        "name":          raw.get("name") or "",
        "price":         price,
        "original_price": original_price,
        "meli_percentage":   meli_pct,
        "seller_percentage": seller_pct,
        "rebate_valor":  rebate_valor,
        "start_date":    start_date,
        "finish_date":   finish_date,
        "suggested_price": float(raw.get("suggested_discounted_price", 0) or 0),
        "min_price":     float(raw.get("min_discounted_price", 0) or 0),
        "max_price":     float(raw.get("max_discounted_price", 0) or 0),
    }


_CAMPAIGN_RETRIES = 3
_CAMPAIGN_RETRY_DELAY = 2.0  # segundos entre tentativas no 500


def get_campaigns_for_item(item_id: str) -> dict:
    """
    Retorna campanhas do item separadas em 'ativas' (started) e
    'disponiveis' (candidate).

    Endpoint: GET /seller-promotions/items/{item_id}?app_version=v2

    Em caso de 500 (erro transitório do ML), tenta até _CAMPAIGN_RETRIES vezes
    antes de relançar a exceção para o runner logar como erro real.
    """
    url = f"{_BASE_URL}/seller-promotions/items/{item_id}"
    params = {"app_version": "v2"}

    raw_list: list[dict] = []
    last_exc: Exception | None = None

    for attempt in range(1, _CAMPAIGN_RETRIES + 1):
        try:
            data = _request("GET", url, params=params)
            if isinstance(data, list):
                raw_list = data
            last_exc = None
            break
        except requests.HTTPError as exc:
            code = getattr(exc.response, "status_code", None)
            if code in (400, 404):
                # Item encerrado/indisponível — sem campanhas, não é erro
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

    return {"ativas": ativas, "disponiveis": disponiveis}


# ---------------------------------------------------------------------------
# Catálogo público (MVP Buybox)
# ---------------------------------------------------------------------------
# A Central de Promoções decide pelo SKU; o monitor de buybox precisa
# enxergar a competição no mesmo product_id (catálogo público do ML).
# Estas funções alimentam src/buybox/catalogo.py.

# Cache em memória de sellers — reputação/nome mudam raramente, o ciclo
# horário pode reusar entre SKUs sem refazer a chamada.
_seller_cache: dict[str, dict] = {}


def get_product_id_from_item(item_id: str, item_detail: dict | None = None) -> str | None:
    """
    Retorna o catalog_product_id do anúncio, ou None se não estiver no catálogo.

    Aceita o detail já buscado para evitar uma chamada extra à API.
    """
    if item_detail is None:
        try:
            item_detail = _request("GET", f"{_BASE_URL}/items/{item_id}")
        except Exception:
            return None
    if not isinstance(item_detail, dict):
        return None
    pid = item_detail.get("catalog_product_id")
    return pid if pid else None


def get_catalog_buybox_winner(product_id: str) -> dict | None:
    """
    Retorna o item vencedor do buybox no catálogo, ou None.

    Endpoint GET /products/{product_id} traz o campo buy_box_winner.
    """
    try:
        data = _request("GET", f"{_BASE_URL}/products/{product_id}")
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data.get("buy_box_winner")


def get_top_sellers_for_product(product_id: str, limit: int = 5) -> list[dict]:
    """
    Retorna os top N anúncios concorrentes no catálogo, ordenados por preço.

    Endpoint GET /products/{product_id}/items?limit=N&offset=0
    A resposta tem chave "results" contendo um item por seller.
    """
    try:
        data = _request(
            "GET",
            f"{_BASE_URL}/products/{product_id}/items",
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

    # Ordena por preço crescente e devolve até `limit` entradas. ML pode
    # devolver fora de ordem dependendo do endpoint; garantimos aqui.
    def _preco(entry: dict) -> float:
        return float(entry.get("price") or 0.0) or float("inf")

    results = sorted(results, key=_preco)
    return results[:limit]


def get_seller_info(seller_id: str) -> dict:
    """
    Retorna informações públicas do seller: nome e nível de reputação.

    Usa cache em memória para o ciclo atual.
    """
    if not seller_id:
        return {"id": "", "nickname": "", "reputacao": ""}
    cached = _seller_cache.get(str(seller_id))
    if cached is not None:
        return cached
    try:
        data = _request("GET", f"{_BASE_URL}/users/{seller_id}")
    except Exception:
        info = {"id": str(seller_id), "nickname": "", "reputacao": ""}
        _seller_cache[str(seller_id)] = info
        return info

    if not isinstance(data, dict):
        info = {"id": str(seller_id), "nickname": "", "reputacao": "",
                "total_vendas": 0, "vendas_completas": 0}
    else:
        rep = data.get("seller_reputation") or {}
        # transactions.total = vendas históricas (todas as categorias).
        # transactions.completed = vendas finalizadas (mais conservador).
        trans = rep.get("transactions") or {}
        info = {
            "id":        str(data.get("id") or seller_id),
            "nickname":  data.get("nickname") or "",
            "reputacao": rep.get("level_id") or rep.get("power_seller_status") or "",
            "total_vendas":     int(trans.get("total") or 0),
            "vendas_completas": int(trans.get("completed") or 0),
        }
    _seller_cache[str(seller_id)] = info
    return info


def limpar_cache_sellers() -> None:
    """Limpa o cache em memória de sellers. Chamado entre ciclos do scheduler."""
    _seller_cache.clear()


def get_orders_for_item(
    item_id: str,
    desde_iso: str,
    ate_iso: str,
    max_pedidos: int = 100,
) -> list[dict]:
    """
    Retorna pedidos do vendedor para o item no período.

    Endpoint: GET /orders/search?seller={id}&item.id={item_id}&...

    Faz paginação automática até `max_pedidos` resultados ou até esgotar.
    Nunca levanta exceção — retorna lista vazia se a chamada falhar.
    Cada pedido contém `date_created`, `order_items[]` e `total_amount`.
    """
    seller_id = get_seller_id()
    resultados: list[dict] = []
    offset = 0
    batch = 50  # máximo por página no ML

    while len(resultados) < max_pedidos:
        try:
            data = _request("GET", f"{_BASE_URL}/orders/search", params={
                "seller":            seller_id,
                "item.id":           item_id,
                "date_created.from": desde_iso,
                "date_created.to":   ate_iso,
                "sort":              "date_created_asc",
                "limit":             min(batch, max_pedidos - len(resultados)),
                "offset":            offset,
            })
        except Exception:
            break

        if not isinstance(data, dict):
            break

        paging = data.get("paging") or {}
        pagina = data.get("results") or []
        resultados.extend(pagina)

        total = int(paging.get("total") or 0)
        offset += len(pagina)
        if offset >= total or len(pagina) == 0:
            break
        time.sleep(0.1)

    return resultados


def get_prazo_entrega_dias(item_id: str, zip_code: str) -> int | None:
    """
    Estimativa de prazo de entrega em DIAS úteis para o anúncio até o CEP.

    Endpoint: GET /items/{item_id}/shipping_options?zip_code=XXXXXXXX
    A resposta tem `options[]` com `estimated_handling_limit` e
    `estimated_delivery_time` em ISO. Pegamos a opção mais barata/rápida.

    Retorna None se a chamada falhar (item pausado, frete não disponível,
    rate-limit etc) — nunca levanta exceção.
    """
    if not item_id or not zip_code:
        return None
    cep = "".join(c for c in zip_code if c.isdigit())
    if len(cep) != 8:
        return None
    try:
        data = _request(
            "GET",
            f"{_BASE_URL}/items/{item_id}/shipping_options",
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
        # `estimated_delivery_time.date` em ISO; fallback p/ `estimated_handling_limit.date`
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
