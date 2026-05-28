"""
Testes das regras de detecção de alertas A1/A2/A3 e do resumo B1/B2/B3.

Usamos os modelos ORM diretamente sem banco — basta instanciar Snapshot
+ SnapshotConcorrente em memória, as funções de regra não tocam em sessão.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.alertas import regras
from src.buybox.modelos import (
    Snapshot,
    SnapshotConcorrente,
    TIPO_A1_PERDI_BUYBOX,
    TIPO_A2_AMEACA,
    TIPO_A3_OPORTUNIDADE,
)


# ============================================================
# Fixtures auxiliares
# ============================================================


@pytest.fixture
def cfg_buybox() -> dict:
    return {
        "diferenca_ruido_rs":     1.00,
        "limite_a2_pct":          2.0,
        "margem_minima_b2_pct":   20.0,
        "rc_oportunidade_b3_pct": 70.0,
        "fracao_snapshots_b2":    0.5,
    }


def _snap(
    sku="WLK004", item_id="MLB100",
    ts=None,
    preco=300.0, posicao=1, buybox=True,
    preco_1o=300.0, preco_2o=310.0,
    margem=25.0, rc=55.0,
    sugerido=None, rc_sugerido=None,
    status="active", visivel=True, estoque=10,
    concorrentes=None,
):
    s = Snapshot(
        sku=sku, item_id=item_id,
        coletado_em=ts or datetime.now(timezone.utc).replace(microsecond=0),
        preco_atual=preco,
        nossa_posicao=posicao,
        tem_buybox=buybox,
        status_anuncio=status,
        estoque_proprio=estoque,
        is_full=False,
        tipo_anuncio="Clássico",
        preco_1o=preco_1o, preco_2o=preco_2o,
        diff_para_1o_rs=(preco - preco_1o) if preco_1o else None,
        diff_para_1o_pct=((preco - preco_1o) / preco_1o * 100) if preco_1o else None,
        diff_para_2o_rs=(preco - preco_2o) if preco_2o else None,
        diff_para_2o_pct=((preco - preco_2o) / preco_2o * 100) if preco_2o else None,
        qtd_concorrentes=2,
        margem_atual_pct=margem, rc_atual_pct=rc,
        preco_otimo_sugerido=sugerido, rc_no_preco_otimo=rc_sugerido,
        motivo_sugestao="—",
        visivel_no_catalogo=visivel,
        titulo="Produto teste",
        url_anuncio="https://produto.mercadolivre.com.br/MLB100",
    )
    s.concorrentes = concorrentes or [
        SnapshotConcorrente(posicao=1, seller_id="ME", seller_nome="Nós",
                            preco=preco, e_nos=True),
        SnapshotConcorrente(posicao=2, seller_id="222", seller_nome="Concorrente A",
                            preco=preco_2o or preco + 10.0),
    ]
    return s


# ============================================================
# A1
# ============================================================


def test_a1_dispara_quando_tinha_buybox_e_perdeu(cfg_buybox):
    ant = _snap(buybox=True, posicao=1)
    novo = _snap(
        buybox=False, posicao=2, preco=300.0, preco_1o=295.0, preco_2o=300.0,
        concorrentes=[
            SnapshotConcorrente(posicao=1, seller_id="222",
                                seller_nome="Concorrente A", preco=295.0,
                                url_anuncio="https://x"),
            SnapshotConcorrente(posicao=2, seller_id="ME",
                                seller_nome="Nós", preco=300.0, e_nos=True),
        ],
    )
    alerts = regras.avaliar_criticos(novo, ant, None, cfg_buybox)
    a1s = [a for a in alerts if a.tipo == TIPO_A1_PERDI_BUYBOX]
    assert len(a1s) == 1
    assert a1s[0].dados["quem_pegou"]["seller_nome"] == "Concorrente A"


def test_a1_nao_dispara_quando_continua_com_buybox(cfg_buybox):
    ant = _snap(buybox=True)
    novo = _snap(buybox=True)
    alerts = regras.avaliar_criticos(novo, ant, None, cfg_buybox)
    assert not any(a.tipo == TIPO_A1_PERDI_BUYBOX for a in alerts)


def test_a1_nao_dispara_sem_snapshot_anterior(cfg_buybox):
    novo = _snap(buybox=False)
    alerts = regras.avaliar_criticos(novo, None, None, cfg_buybox)
    assert alerts == []


# ============================================================
# A2
# ============================================================


def test_a2_dispara_quando_2o_a_menos_de_2pct_acima(cfg_buybox):
    ant = _snap(buybox=True)
    novo = _snap(
        buybox=True, preco=300.0, preco_1o=300.0, preco_2o=303.0,
    )
    alerts = regras.avaliar_criticos(novo, ant, None, cfg_buybox)
    a2 = [a for a in alerts if a.tipo == TIPO_A2_AMEACA]
    assert len(a2) == 1
    assert "1.0%" in a2[0].motivo or "1,0%" in a2[0].motivo


def test_a2_filtra_diferenca_de_ruido(cfg_buybox):
    """Diferença < R$ 1 não dispara A2 (ruído)."""
    ant = _snap(buybox=True)
    novo = _snap(
        buybox=True, preco=300.0, preco_1o=300.0, preco_2o=300.50,
    )
    alerts = regras.avaliar_criticos(novo, ant, None, cfg_buybox)
    assert not any(a.tipo == TIPO_A2_AMEACA for a in alerts)


def test_a2_nao_dispara_se_2o_muito_distante(cfg_buybox):
    """2º colocado a > 2% de distância — sem ameaça."""
    ant = _snap(buybox=True)
    novo = _snap(
        buybox=True, preco=300.0, preco_1o=300.0, preco_2o=320.0,  # 6,7%
    )
    alerts = regras.avaliar_criticos(novo, ant, None, cfg_buybox)
    assert not any(a.tipo == TIPO_A2_AMEACA for a in alerts)


def test_a2_nao_dispara_sem_buybox(cfg_buybox):
    """A2 só faz sentido quando ainda temos buybox."""
    ant = _snap(buybox=False)
    novo = _snap(buybox=False, preco_1o=295.0, preco_2o=303.0)
    alerts = regras.avaliar_criticos(novo, ant, None, cfg_buybox)
    assert not any(a.tipo == TIPO_A2_AMEACA for a in alerts)


# ============================================================
# A3
# ============================================================


def test_a3_dispara_quando_concorrente_sumiu_por_2_ciclos(cfg_buybox):
    """Seller-X estava em 2º há 2 ciclos, sumiu em ambos depois → A3."""
    ts = datetime.now(timezone.utc).replace(microsecond=0)

    ante = _snap(
        ts=ts - timedelta(hours=2), buybox=True,
        concorrentes=[
            SnapshotConcorrente(posicao=1, seller_id="ME", seller_nome="Nós",
                                preco=300.0, e_nos=True),
            SnapshotConcorrente(posicao=2, seller_id="X-SUMIDO",
                                seller_nome="VaiSumir", preco=305.0),
            SnapshotConcorrente(posicao=3, seller_id="333",
                                seller_nome="C", preco=315.0),
        ],
    )
    ant = _snap(
        ts=ts - timedelta(hours=1), buybox=True,
        concorrentes=[
            SnapshotConcorrente(posicao=1, seller_id="ME", seller_nome="Nós",
                                preco=300.0, e_nos=True),
            SnapshotConcorrente(posicao=2, seller_id="333",
                                seller_nome="C", preco=315.0),
        ],
    )
    novo = _snap(
        ts=ts, buybox=True,
        concorrentes=[
            SnapshotConcorrente(posicao=1, seller_id="ME", seller_nome="Nós",
                                preco=300.0, e_nos=True),
            SnapshotConcorrente(posicao=2, seller_id="333",
                                seller_nome="C", preco=315.0),
        ],
    )
    alerts = regras.avaliar_criticos(novo, ant, ante, cfg_buybox)
    a3 = [a for a in alerts if a.tipo == TIPO_A3_OPORTUNIDADE]
    assert len(a3) == 1
    assert a3[0].dados["concorrente_sumido"]["seller_id"] == "X-SUMIDO"


def test_a3_nao_dispara_se_apenas_1_ciclo_ausente(cfg_buybox):
    """Concorrente ainda presente em `anterior` — não confirma."""
    ante = _snap(concorrentes=[
        SnapshotConcorrente(posicao=1, seller_id="X", seller_nome="X", preco=300.0),
    ])
    ant = _snap(concorrentes=[  # X ainda presente
        SnapshotConcorrente(posicao=1, seller_id="X", seller_nome="X", preco=300.0),
    ])
    novo = _snap(concorrentes=[])
    alerts = regras.avaliar_criticos(novo, ant, ante, cfg_buybox)
    assert not any(a.tipo == TIPO_A3_OPORTUNIDADE for a in alerts)


def test_a3_nao_dispara_sem_ante_anterior(cfg_buybox):
    """1ª coleta após restart: sem `ante_anterior`, A3 não dispara."""
    ant = _snap(concorrentes=[
        SnapshotConcorrente(posicao=1, seller_id="X", seller_nome="X", preco=300.0),
    ])
    novo = _snap(concorrentes=[])
    alerts = regras.avaliar_criticos(novo, ant, None, cfg_buybox)
    assert not any(a.tipo == TIPO_A3_OPORTUNIDADE for a in alerts)


# ============================================================
# Resumo diário (B1/B2/B3)
# ============================================================


def test_b1_pega_anuncio_pausado(cfg_buybox):
    snaps = [_snap(status="paused", visivel=False, estoque=0)]
    r = regras.avaliar_resumo_diario(snaps, cfg_buybox)
    assert len(r["b1_problemas"]) == 1
    assert "pausado" in r["b1_problemas"][0]["motivo"]


def test_b1_ignora_anuncio_ativo(cfg_buybox):
    snaps = [_snap(status="active", visivel=True)]
    r = regras.avaliar_resumo_diario(snaps, cfg_buybox)
    assert r["b1_problemas"] == []


def test_b2_pega_margem_baixa_majoritaria(cfg_buybox):
    """Pelo menos 50% dos snapshots do dia com margem < 20% → B2."""
    snaps = [
        _snap(margem=15.0, item_id="MLB-A"),
        _snap(margem=18.0, item_id="MLB-A"),
        _snap(margem=22.0, item_id="MLB-A"),
    ]
    r = regras.avaliar_resumo_diario(snaps, cfg_buybox)
    assert len(r["b2_margem_baixa"]) == 1
    assert r["b2_margem_baixa"][0]["snapshots_ruins"] == 2


def test_b2_nao_pega_margem_baixa_minoritaria(cfg_buybox):
    snaps = [
        _snap(margem=15.0, item_id="MLB-A"),
        _snap(margem=22.0, item_id="MLB-A"),
        _snap(margem=25.0, item_id="MLB-A"),
    ]
    r = regras.avaliar_resumo_diario(snaps, cfg_buybox)
    assert r["b2_margem_baixa"] == []


def test_b3_oportunidade_subir_preco(cfg_buybox):
    """Buybox + RC > 70% + preço ótimo > preço atual → B3."""
    snaps = [_snap(
        buybox=True, rc=85.0,
        sugerido=320.0, rc_sugerido=80.0,
        preco=300.0,
    )]
    r = regras.avaliar_resumo_diario(snaps, cfg_buybox)
    assert len(r["b3_oportunidades"]) == 1
    assert r["b3_oportunidades"][0]["ganho_rs"] == 20.0


def test_b3_nao_pega_sem_buybox(cfg_buybox):
    snaps = [_snap(buybox=False, rc=85.0, sugerido=320.0, preco=300.0)]
    r = regras.avaliar_resumo_diario(snaps, cfg_buybox)
    assert r["b3_oportunidades"] == []


def test_b3_nao_pega_sugestao_menor_que_atual(cfg_buybox):
    """Ótimo abaixo do atual = defender buybox, não é B3."""
    snaps = [_snap(buybox=True, rc=85.0, sugerido=290.0, preco=300.0)]
    r = regras.avaliar_resumo_diario(snaps, cfg_buybox)
    assert r["b3_oportunidades"] == []
