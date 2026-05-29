"""Testes de CRUD da camada de persistência."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.orm import sessionmaker

from src.buybox import persistencia
from src.buybox.modelos import (
    Base,
    ConcorrenteDom,
    SnapshotDom,
    TIPO_A1_PERDI_BUYBOX,
    TIPO_A2_AMEACA,
)


@pytest.fixture(autouse=True)
def _isolar_banco_em_memoria(monkeypatch, tmp_path):
    """
    Cada teste roda contra um SQLite em arquivo temporário próprio.

    O cache de persistência agora é um dict keyed por conta.
    Apontamos "best_hair" para um arquivo isolado e resetamos ao final.
    """
    persistencia.reset_engine_cache()

    db_file = tmp_path / "test.db"
    eng = persistencia.get_engine("best_hair", str(db_file))
    Base.metadata.create_all(eng)

    factory = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(persistencia, "_engine_cache",   {"best_hair": eng})
    monkeypatch.setattr(persistencia, "_session_factory", {"best_hair": factory})

    yield

    persistencia.reset_engine_cache()


def _dom(sku="WLK004", item_id="MLB123", ts=None,
         preco=300.0, posicao=1, buybox=True):
    return SnapshotDom(
        sku=sku, item_id=item_id,
        coletado_em=ts or datetime.now(timezone.utc).replace(microsecond=0),
        preco_atual=preco,
        nossa_posicao=posicao,
        tem_buybox=buybox,
        status_anuncio="active",
        estoque_proprio=5,
        is_full=False,
        tipo_anuncio="Clássico",
        preco_1o=preco,
        preco_2o=preco + 5.0,
        qtd_concorrentes=2,
        margem_atual_pct=25.0,
        rc_atual_pct=55.0,
        concorrentes=[
            ConcorrenteDom(posicao=1, seller_id="S1", seller_nome="Eu", preco=preco, e_nos=True),
            ConcorrenteDom(posicao=2, seller_id="S2", seller_nome="Concorrente", preco=preco + 5),
        ],
    )


def test_salvar_e_recuperar_snapshot():
    dom = _dom()
    snap_id = persistencia.salvar_snapshot(dom)
    assert snap_id is not None

    ultimo = persistencia.ultimo_snapshot("WLK004")
    assert ultimo is not None
    assert ultimo.sku == "WLK004"
    assert ultimo.preco_atual == pytest.approx(300.0)
    assert len(ultimo.concorrentes) == 2
    assert ultimo.concorrentes[0].e_nos is True


def test_idempotencia_mesma_chave_devolve_none():
    ts = datetime.now(timezone.utc).replace(microsecond=0)
    primeiro = persistencia.salvar_snapshot(_dom(ts=ts))
    segundo = persistencia.salvar_snapshot(_dom(ts=ts))
    assert primeiro is not None
    assert segundo is None  # ignorado pela constraint única


def test_snapshots_24h_so_traz_ultimas_24h():
    agora = datetime.now(timezone.utc).replace(microsecond=0)
    ontem = agora - timedelta(hours=25)
    persistencia.salvar_snapshot(_dom(ts=ontem, item_id="MLB001"))
    persistencia.salvar_snapshot(_dom(ts=agora, item_id="MLB002"))

    rows = persistencia.snapshots_24h("WLK004")
    assert len(rows) == 1
    assert rows[0].item_id == "MLB002"


def test_registrar_alerta_e_recuperar_ultimo_enviado():
    ts = datetime.now(timezone.utc).replace(microsecond=0)
    persistencia.registrar_alerta(
        "WLK004", "MLB123", TIPO_A1_PERDI_BUYBOX,
        {"preco_anterior": 305}, enviado=True, disparado_em=ts,
    )
    # Um suprimido pelo cooldown depois — não deve "vencer" o anterior
    persistencia.registrar_alerta(
        "WLK004", "MLB123", TIPO_A1_PERDI_BUYBOX,
        {"motivo": "cooldown"}, enviado=False,
        disparado_em=ts + timedelta(minutes=30),
    )

    ult = persistencia.ultimo_alerta_enviado("WLK004", TIPO_A1_PERDI_BUYBOX)
    assert ult is not None
    assert ult.enviado_em is not None
    # SQLite armazena datetime sem tzinfo — comparar via timestamp naive
    assert ult.disparado_em.replace(tzinfo=None) == ts.replace(tzinfo=None)
    # Garante que o registro suprimido (mais recente, enviado=False)
    # não vence o enviado anterior
    assert "preco_anterior" in ult.dados


def test_filtragem_por_tipo_de_alerta():
    persistencia.registrar_alerta("WLK004", "MLB1", TIPO_A1_PERDI_BUYBOX, {}, True)
    persistencia.registrar_alerta("WLK004", "MLB1", TIPO_A2_AMEACA, {}, True)

    a1 = persistencia.ultimo_alerta_enviado("WLK004", TIPO_A1_PERDI_BUYBOX)
    a2 = persistencia.ultimo_alerta_enviado("WLK004", TIPO_A2_AMEACA)
    assert a1 is not None and a1.tipo == "A1"
    assert a2 is not None and a2.tipo == "A2"


def test_ultimo_snapshot_por_sku_devolve_mais_recente():
    agora = datetime.now(timezone.utc).replace(microsecond=0)
    ha_uma_hora = agora - timedelta(hours=1)
    persistencia.salvar_snapshot(_dom(sku="WL008", ts=ha_uma_hora, preco=100, item_id="MLB-A"))
    persistencia.salvar_snapshot(_dom(sku="WL008", ts=agora, preco=110, item_id="MLB-A"))
    persistencia.salvar_snapshot(_dom(sku="WLK004", ts=agora, preco=300, item_id="MLB-B"))

    por_sku = persistencia.ultimo_snapshot_por_sku()
    assert set(por_sku.keys()) == {"WL008", "WLK004"}
    assert por_sku["WL008"].preco_atual == pytest.approx(110.0)
    assert por_sku["WLK004"].preco_atual == pytest.approx(300.0)
