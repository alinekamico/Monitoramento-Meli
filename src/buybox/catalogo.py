"""
Montagem do top 5 do catálogo para um item nosso.

Fluxo:
  1. Recebe um item detail (já buscado pelo runner/coletor)
  2. Resolve catalog_product_id
  3. Busca top 5 itens no catálogo via /products/{id}/items
  4. Enriquece cada concorrente com nome+reputação do seller
  5. Identifica nossa posição e flag e_nos
  6. Devolve lista de ConcorrenteDom já ordenada por preço

Tratamento de exceção:
  - Item sem catalog_product_id → retorna top 5 contendo só você
  - Endpoint público fora do ar → idem (logado no caller)
"""

from __future__ import annotations

import os
from typing import Optional

from .. import ml_client
from .modelos import ConcorrenteDom


def _tipo_envio(shipping: dict | None) -> str:
    """Mapeia o dict shipping do ML para os rótulos full/flex/normal."""
    if not isinstance(shipping, dict):
        return "normal"
    logistic = shipping.get("logistic_type") or ""
    if logistic == "fulfillment":
        return "full"
    if logistic in ("self_service", "cross_docking", "drop_off", "xd_drop_off"):
        return "flex"
    return "normal"


def _frete_gratis(shipping: dict | None) -> bool:
    if not isinstance(shipping, dict):
        return False
    return bool(shipping.get("free_shipping"))


def _url_publica(item_id: str, permalink: Optional[str] = None) -> str:
    """Devolve a URL canônica do anúncio. Prefere permalink quando disponível."""
    if permalink:
        return permalink
    return f"https://produto.mercadolivre.com.br/{item_id}" if item_id else ""


def e_visivel_ao_cliente(item_detail: dict) -> bool:
    """
    Anúncios pausados ou sem estoque NÃO aparecem na competição do
    catálogo público. Inserir-los artificialmente no top 5 falsifica
    posição/buybox/preço ótimo.
    """
    if not isinstance(item_detail, dict):
        return False
    if item_detail.get("status") != "active":
        return False
    if int(item_detail.get("available_quantity") or 0) <= 0:
        return False
    return True


def _entrada_propria(
    item_detail: dict,
    posicao: int,
    seller_id_proprio: str,
) -> ConcorrenteDom:
    """Cria um ConcorrenteDom a partir do nosso próprio item detail."""
    seller = item_detail.get("seller") or {}
    seller_nome = seller.get("nickname") or "Nós"
    return ConcorrenteDom(
        posicao=posicao,
        seller_id=str(seller_id_proprio or seller.get("id") or ""),
        seller_nome=seller_nome,
        preco=float(item_detail.get("price") or 0.0),
        tipo_envio=_tipo_envio(item_detail.get("shipping")),
        frete_gratis=_frete_gratis(item_detail.get("shipping")),
        reputacao="",
        url_anuncio=_url_publica(item_detail.get("id", ""), item_detail.get("permalink")),
        e_nos=True,
    )


def montar_top5(
    item_detail: dict,
    seller_id_proprio: str,
    limit: int = 5,
) -> list[ConcorrenteDom]:
    """
    Devolve a lista de até `limit` concorrentes ordenada por preço.

    Regras:
      - Anúncios pausados ou sem estoque NÃO entram no top 5 (não são
        visíveis ao cliente no catálogo público).
      - Quando estamos visíveis e o endpoint público omite nosso item,
        inserimos manualmente — mas isso só acontece para anúncios ativos.
      - O `price` retornado pelo /products/{id}/items é a fonte de verdade
        do preço visível ao cliente (já reflete descontos de campanha,
        smart, deal, seller_campaign etc).
    """
    nosso_visivel = e_visivel_ao_cliente(item_detail)

    product_id = ml_client.get_product_id_from_item(
        item_detail.get("id", ""), item_detail=item_detail
    )

    if not product_id:
        # Item fora de catálogo — sem competição comparável
        if nosso_visivel:
            return [_entrada_propria(
                item_detail, posicao=1, seller_id_proprio=seller_id_proprio
            )]
        return []

    raw_top = ml_client.get_top_sellers_for_product(product_id, limit=limit * 2)

    concorrentes: list[ConcorrenteDom] = []
    nos_aparecemos = False
    seller_id_proprio_str = str(seller_id_proprio or "")
    nosso_item_id = item_detail.get("id", "")

    for raw in raw_top:
        seller_id = str(raw.get("seller_id") or "")
        e_nos = (
            seller_id == seller_id_proprio_str
            or raw.get("item_id") == nosso_item_id
        )
        if e_nos:
            nos_aparecemos = True
            # Para nossa entrada, evitamos chamar /users/me (cache fica vazio)
            seller_info = {"nickname": "Nós", "reputacao": ""}
        else:
            seller_info = ml_client.get_seller_info(seller_id)
        concorrentes.append(
            ConcorrenteDom(
                posicao=0,  # preenchido após ordenação
                seller_id=seller_id,
                seller_nome=seller_info.get("nickname", "") or ("Nós" if e_nos else ""),
                preco=float(raw.get("price") or 0.0),
                tipo_envio=_tipo_envio(raw.get("shipping")),
                frete_gratis=_frete_gratis(raw.get("shipping")),
                reputacao=seller_info.get("reputacao", ""),
                url_anuncio=_url_publica(
                    raw.get("item_id", ""), raw.get("permalink"),
                ),
                e_nos=e_nos,
                total_vendas=int(seller_info.get("total_vendas") or 0),
            )
        )

    # Inserção manual: só se estamos visíveis E o endpoint público nos omitiu
    if not nos_aparecemos and nosso_visivel:
        concorrentes.append(_entrada_propria(
            item_detail, posicao=0, seller_id_proprio=seller_id_proprio,
        ))

    concorrentes.sort(key=lambda c: c.preco)
    for idx, c in enumerate(concorrentes[:limit], start=1):
        c.posicao = idx

    return concorrentes[:limit]


def nosso_preco_efetivo(concorrentes: list[ConcorrenteDom]) -> float | None:
    """
    Preço visível ao cliente no nosso anúncio.

    Retorna o preço da nossa entrada no top 5 — é a verdade absoluta,
    pois reflete descontos de qualquer campanha. None se não estamos
    visíveis no catálogo.
    """
    for c in concorrentes:
        if c.e_nos:
            return c.preco
    return None


def derivar_competicao(concorrentes: list[ConcorrenteDom]) -> dict:
    """
    A partir do top 5, devolve preço_1o, preço_2o (do 2º distinto de nós),
    nossa posição e tem_buybox.

    Regra do tem_buybox: é o primeiro da lista, e ele somos nós (e_nos=True).
    Empate em 1º conta como "ninguém tem buybox" (ML não dá buybox por empate).
    """
    if not concorrentes:
        return {
            "preco_1o": None, "preco_2o": None,
            "nossa_posicao": None, "tem_buybox": False,
            "qtd_concorrentes": 0,
        }

    primeiro = concorrentes[0]
    segundo = concorrentes[1] if len(concorrentes) >= 2 else None

    nossa_posicao: Optional[int] = next(
        (c.posicao for c in concorrentes if c.e_nos), None
    )

    # Empate em 1º com mais alguém invalida o buybox
    empate_no_topo = (
        segundo is not None and primeiro.preco == segundo.preco
    )
    tem_buybox = bool(primeiro.e_nos) and not empate_no_topo

    return {
        "preco_1o": primeiro.preco,
        "preco_2o": segundo.preco if segundo is not None else None,
        "nossa_posicao": nossa_posicao,
        "tem_buybox": tem_buybox,
        "qtd_concorrentes": len(concorrentes),
    }


def calcular_diffs(preco_atual: float, preco_1o: Optional[float],
                   preco_2o: Optional[float]) -> dict:
    """Diferenças em R$ e %. Positivo = você é mais caro que o concorrente."""
    out: dict = {
        "diff_para_1o_rs": None, "diff_para_1o_pct": None,
        "diff_para_2o_rs": None, "diff_para_2o_pct": None,
    }
    if preco_1o is not None and preco_1o > 0:
        out["diff_para_1o_rs"] = round(preco_atual - preco_1o, 2)
        out["diff_para_1o_pct"] = round((preco_atual - preco_1o) / preco_1o * 100, 2)
    if preco_2o is not None and preco_2o > 0:
        out["diff_para_2o_rs"] = round(preco_atual - preco_2o, 2)
        out["diff_para_2o_pct"] = round((preco_atual - preco_2o) / preco_2o * 100, 2)
    return out
