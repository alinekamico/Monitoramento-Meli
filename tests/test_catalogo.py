"""
Testes do módulo de catálogo (montagem do top 5 e fonte de preço).

Cobre os bugs encontrados na 1ª coleta real:
  - Anúncio pausado/sem estoque não deve aparecer artificialmente no top 5
  - O preço do top 5 do catálogo é a fonte da verdade do preço visível
    ao cliente, não o item.price (que pode estar cheio quando há campanha
    started com seller_percentage > 0).
"""

from __future__ import annotations

from unittest.mock import patch

from src.buybox import catalogo


# ============================================================
# Fixtures auxiliares
# ============================================================


def _item(price=300.0, status="active", qtd=10, pid="MLB-CATALOGO-001",
          item_id="MLB100", seller_id=111111):
    return {
        "id": item_id,
        "price": price,
        "original_price": None,
        "status": status,
        "available_quantity": qtd,
        "listing_type_id": "gold_special",
        "catalog_product_id": pid,
        "permalink": f"https://produto.mercadolivre.com.br/{item_id}",
        "shipping": {"logistic_type": "cross_docking", "free_shipping": True},
        "seller": {"id": seller_id, "nickname": "NossaLoja"},
    }


def _top5(*entries):
    """Helper para criar resposta de /products/{id}/items."""
    return list(entries)


def _fake_seller(seller_id):
    return {"id": str(seller_id),
            "nickname": f"Vendedor-{seller_id}",
            "reputacao": "gold"}


# ============================================================
# Tests
# ============================================================


def test_e_visivel_ao_cliente_active_com_estoque():
    assert catalogo.e_visivel_ao_cliente(_item()) is True


def test_e_visivel_ao_cliente_paused():
    assert catalogo.e_visivel_ao_cliente(_item(status="paused")) is False


def test_e_visivel_ao_cliente_sem_estoque():
    assert catalogo.e_visivel_ao_cliente(_item(qtd=0)) is False


def test_anuncio_pausado_nao_entra_no_top5():
    """
    Bug real: MLB2663223853 estava pausado e foi inserido manualmente em
    1º a R$ 318,90, dando falso buybox. Não pode mais acontecer.
    """
    item = _item(status="paused", qtd=0, price=318.90)

    raw_top5 = _top5(
        {"item_id": "MLB-A", "seller_id": "222", "price": 338.60,
         "shipping": {"logistic_type": "cross_docking"}},
        {"item_id": "MLB-B", "seller_id": "333", "price": 341.99,
         "shipping": {"logistic_type": "cross_docking"}},
    )

    with patch("src.ml_client.get_product_id_from_item", return_value="PID"), \
         patch("src.ml_client.get_top_sellers_for_product", return_value=raw_top5), \
         patch("src.ml_client.get_seller_info", side_effect=_fake_seller):
        concorrentes = catalogo.montar_top5(item, seller_id_proprio="111111")

    # Nossa entrada NÃO deve aparecer (pausado)
    assert all(not c.e_nos for c in concorrentes)
    # Mas os concorrentes reais continuam visíveis
    assert len(concorrentes) == 2
    assert concorrentes[0].preco == 338.60
    assert concorrentes[1].preco == 341.99


def test_anuncio_ativo_inserido_se_omitido_pelo_endpoint_publico():
    """
    Caso oposto: anúncio ativo mas o endpoint público não retornou
    nossa entrada. Inserimos manualmente para manter visibilidade.
    """
    item = _item(status="active", qtd=10, price=310.0, item_id="MLB-NOS",
                 seller_id=111111)
    raw_top5 = _top5(
        {"item_id": "MLB-A", "seller_id": "222", "price": 305.0,
         "shipping": {"logistic_type": "fulfillment"}},
        {"item_id": "MLB-B", "seller_id": "333", "price": 320.0,
         "shipping": {"logistic_type": "cross_docking"}},
    )

    with patch("src.ml_client.get_product_id_from_item", return_value="PID"), \
         patch("src.ml_client.get_top_sellers_for_product", return_value=raw_top5), \
         patch("src.ml_client.get_seller_info", side_effect=_fake_seller):
        concorrentes = catalogo.montar_top5(item, seller_id_proprio="111111")

    # Nossa entrada deve estar presente (inserida manualmente)
    assert any(c.e_nos for c in concorrentes)
    nossa = next(c for c in concorrentes if c.e_nos)
    assert nossa.preco == 310.0
    # Ordenado por preço: 305, 310, 320
    assert [c.preco for c in concorrentes] == [305.0, 310.0, 320.0]
    assert [c.posicao for c in concorrentes] == [1, 2, 3]


def test_nosso_preco_efetivo_vem_do_top5():
    """
    Quando estamos no top 5, o preço efetivo deve ser o do catálogo,
    NÃO o item.price. Caso real: SELLER_CAMPAIGN aplica desconto sem
    aparecer em meli_percentage — item.price=459.90, top5.price=317.33.
    """
    item = _item(item_id="MLB-NOS", seller_id=111111, price=459.90)
    raw_top5 = _top5(
        {"item_id": "MLB-NOS", "seller_id": "111111", "price": 317.33,
         "shipping": {"logistic_type": "fulfillment"}},
        {"item_id": "MLB-X", "seller_id": "222", "price": 333.0,
         "shipping": {"logistic_type": "cross_docking"}},
    )
    with patch("src.ml_client.get_product_id_from_item", return_value="PID"), \
         patch("src.ml_client.get_top_sellers_for_product", return_value=raw_top5), \
         patch("src.ml_client.get_seller_info", side_effect=_fake_seller):
        concorrentes = catalogo.montar_top5(item, seller_id_proprio="111111")

    preco_efetivo = catalogo.nosso_preco_efetivo(concorrentes)
    assert preco_efetivo == 317.33  # preço do top5, não 459.90


def test_nosso_preco_efetivo_none_se_nao_visivel():
    """Anúncio fora do catálogo público → preço efetivo None."""
    item = _item(status="paused", qtd=0)
    raw_top5 = _top5(
        {"item_id": "MLB-A", "seller_id": "222", "price": 300.0},
    )
    with patch("src.ml_client.get_product_id_from_item", return_value="PID"), \
         patch("src.ml_client.get_top_sellers_for_product", return_value=raw_top5), \
         patch("src.ml_client.get_seller_info", side_effect=_fake_seller):
        concorrentes = catalogo.montar_top5(item, seller_id_proprio="111111")
    assert catalogo.nosso_preco_efetivo(concorrentes) is None


def test_derivar_competicao_empate_no_topo_invalida_buybox():
    from src.buybox.modelos import ConcorrenteDom

    concorrentes = [
        ConcorrenteDom(posicao=1, seller_id="111", seller_nome="Nós",
                       preco=300.0, e_nos=True),
        ConcorrenteDom(posicao=2, seller_id="222", seller_nome="Outro",
                       preco=300.0, e_nos=False),
    ]
    comp = catalogo.derivar_competicao(concorrentes)
    # Empate em 1º → ninguém tem buybox
    assert comp["tem_buybox"] is False
    assert comp["nossa_posicao"] == 1


def test_derivar_competicao_buybox_legitimo():
    from src.buybox.modelos import ConcorrenteDom

    concorrentes = [
        ConcorrenteDom(posicao=1, seller_id="111", seller_nome="Nós",
                       preco=300.0, e_nos=True),
        ConcorrenteDom(posicao=2, seller_id="222", seller_nome="Outro",
                       preco=310.0, e_nos=False),
    ]
    comp = catalogo.derivar_competicao(concorrentes)
    assert comp["tem_buybox"] is True
    assert comp["nossa_posicao"] == 1
    assert comp["preco_1o"] == 300.0
    assert comp["preco_2o"] == 310.0


def test_calcular_diffs_positivo_quando_mais_caro():
    diffs = catalogo.calcular_diffs(
        preco_atual=320.0, preco_1o=300.0, preco_2o=310.0,
    )
    assert diffs["diff_para_1o_rs"] == 20.0
    assert diffs["diff_para_1o_pct"] > 0
    assert diffs["diff_para_2o_rs"] == 10.0


def test_calcular_diffs_negativo_quando_mais_barato():
    diffs = catalogo.calcular_diffs(
        preco_atual=290.0, preco_1o=300.0, preco_2o=310.0,
    )
    assert diffs["diff_para_1o_rs"] == -10.0
    assert diffs["diff_para_2o_rs"] == -20.0
