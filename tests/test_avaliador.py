"""
Testes do orquestrador de alertas (`src.alertas.avaliador`).

Cobertura:
  - Cooldown respeitado (alerta enviado bloqueia o próximo na janela)
  - Cooldown ignora alertas suprimidos (dry-run/email-off não bloqueiam)
  - Modo dry-run registra alerta com motivo_supressao='dry_run' sem enviar
  - email_desabilitado registra com motivo_supressao='email_desabilitado'
  - Falha SMTP registrada como erro_smtp:... sem derrubar o ciclo
  - Resumo diário envia 1 e-mail consolidado e registra 1 linha por item
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from src.alertas import avaliador
from src.buybox import persistencia
from src.buybox.modelos import (
    Alerta,
    Base,
    ConcorrenteDom,
    SnapshotDom,
    TIPO_A1_PERDI_BUYBOX,
)


# ============================================================
# Fixtures
# ============================================================


@pytest.fixture(autouse=True)
def _isolar_banco_em_memoria(monkeypatch, tmp_path):
    """Cada teste roda contra um SQLite isolado em arquivo temp."""
    persistencia.reset_engine_cache()

    db_file = tmp_path / "test.db"
    eng = persistencia.get_engine("best_hair", str(db_file))
    Base.metadata.create_all(eng)

    factory = sessionmaker(bind=eng, expire_on_commit=False)
    monkeypatch.setattr(persistencia, "_engine_cache",    {"best_hair": eng})
    monkeypatch.setattr(persistencia, "_session_factory", {"best_hair": factory})

    yield

    persistencia.reset_engine_cache()


@pytest.fixture
def cfg() -> dict:
    return {
        "dry_run": False,
        "buybox": {
            "diferenca_ruido_rs":  1.00,
            "limite_a2_pct":       2.0,
            "cooldown_a1_horas":   6,
            "cooldown_a2_horas":   2,
            "cooldown_a3_horas":   4,
            "rc_oportunidade_b3_pct": 70.0,
            "margem_minima_b2_pct":   20.0,
            "fracao_snapshots_b2":    0.5,
            "email": {
                "enabled":      True,
                "smtp_host":    "smtp.gmail.com",
                "smtp_port":    587,
                "destinatarios": ["x@x.com"],
                "remetente_env": "EMAIL_REMETENTE_TEST",
                "senha_env":     "EMAIL_SENHA_TEST",
            },
        },
    }


def _criar_par_snapshots_perda_buybox(sku="WLK004", item_id="MLB100"):
    """
    Cria 2 snapshots consecutivos onde perdemos buybox — gatilho A1.
    Retorna os ids para uso opcional.
    """
    ts2 = datetime.now(timezone.utc).replace(microsecond=0)
    ts1 = ts2 - timedelta(hours=1)

    # rebate_pct > 0 simula item participando de campanha de rebate ativa —
    # condição necessária para alertas críticos dispararem e-mail.
    persistencia.salvar_snapshot(SnapshotDom(
        sku=sku, item_id=item_id, coletado_em=ts1,
        preco_atual=300.0, nossa_posicao=1, tem_buybox=True,
        status_anuncio="active", estoque_proprio=10, is_full=False,
        tipo_anuncio="Clássico", preco_1o=300.0, preco_2o=320.0,
        qtd_concorrentes=2, margem_atual_pct=25.0, rc_atual_pct=55.0,
        visivel_no_catalogo=True,
        rebate_pct=10.0, campanha_ativa_id="CAMP-1", campanha_ativa_nome="Deal",
        concorrentes=[
            ConcorrenteDom(posicao=1, seller_id="ME", seller_nome="Nós",
                           preco=300.0, e_nos=True),
            ConcorrenteDom(posicao=2, seller_id="222",
                           seller_nome="Concorrente", preco=320.0),
        ],
    ))
    persistencia.salvar_snapshot(SnapshotDom(
        sku=sku, item_id=item_id, coletado_em=ts2,
        preco_atual=300.0, nossa_posicao=2, tem_buybox=False,
        status_anuncio="active", estoque_proprio=10, is_full=False,
        tipo_anuncio="Clássico", preco_1o=295.0, preco_2o=300.0,
        qtd_concorrentes=2, margem_atual_pct=24.0, rc_atual_pct=53.0,
        preco_otimo_sugerido=294.9, rc_no_preco_otimo=51.0,
        motivo_sugestao="Retomar buybox",
        visivel_no_catalogo=True, titulo="Kit Wella",
        rebate_pct=10.0, campanha_ativa_id="CAMP-1", campanha_ativa_nome="Deal",
        concorrentes=[
            ConcorrenteDom(posicao=1, seller_id="222", seller_nome="Concorrente",
                           preco=295.0),
            ConcorrenteDom(posicao=2, seller_id="ME", seller_nome="Nós",
                           preco=300.0, e_nos=True),
        ],
    ))


def _alertas_no_banco() -> list[Alerta]:
    with persistencia.sessao() as s:
        return list(s.execute(
            select(Alerta).order_by(Alerta.id)
        ).scalars())


# ============================================================
# Tests
# ============================================================


def test_envio_smtp_e_cooldown(cfg, monkeypatch):
    """Envia na 1ª avaliação; suprime na 2ª por cooldown."""
    monkeypatch.setenv("EMAIL_REMETENTE_TEST", "fake@x.com")
    monkeypatch.setenv("EMAIL_SENHA_TEST", "fake_pwd")
    _criar_par_snapshots_perda_buybox()

    with patch("src.alertas.email.smtplib.SMTP") as mock_smtp:
        s1 = avaliador.avaliar_criticos_pendentes(cfg=cfg, dry_run=False)
        assert s1["pendentes_detectados"] == 1
        assert s1["enviados"] == 1
        assert s1["suprimidos_cooldown"] == 0
        assert mock_smtp.called

        # 2ª avaliação — sem novos snapshots, mas o mesmo A1 vai ser
        # detectado de novo (regra é pura). Cooldown precisa suprimir.
        mock_smtp.reset_mock()
        s2 = avaliador.avaliar_criticos_pendentes(cfg=cfg, dry_run=False)
        assert s2["pendentes_detectados"] == 1
        assert s2["enviados"] == 0
        assert s2["suprimidos_cooldown"] == 1
        assert not mock_smtp.called

    alertas = _alertas_no_banco()
    assert len(alertas) == 2
    assert alertas[0].enviado_em is not None  # primeiro: enviado
    assert alertas[1].enviado_em is None      # segundo: suprimido
    dados_2 = json.loads(alertas[1].dados)
    assert dados_2["motivo_supressao"] == "cooldown"


def test_dry_run_registra_mas_nao_envia(cfg):
    """dry_run=True suprime envio com motivo='dry_run'."""
    _criar_par_snapshots_perda_buybox()

    with patch("src.alertas.email.smtplib.SMTP") as mock_smtp:
        s = avaliador.avaliar_criticos_pendentes(cfg=cfg, dry_run=True)
        assert s["enviados"] == 0
        assert s["suprimidos_dryrun"] == 1
        assert not mock_smtp.called

    alertas = _alertas_no_banco()
    assert len(alertas) == 1
    assert alertas[0].enviado_em is None
    assert json.loads(alertas[0].dados)["motivo_supressao"] == "dry_run"


def test_email_desabilitado_registra_mas_nao_envia(cfg):
    """email.enabled=false suprime envio com motivo='email_desabilitado'."""
    cfg["buybox"]["email"]["enabled"] = False
    _criar_par_snapshots_perda_buybox()

    with patch("src.alertas.email.smtplib.SMTP") as mock_smtp:
        s = avaliador.avaliar_criticos_pendentes(cfg=cfg, dry_run=False)
        assert s["enviados"] == 0
        assert s["suprimidos_email_off"] == 1
        assert not mock_smtp.called

    alertas = _alertas_no_banco()
    assert json.loads(alertas[0].dados)["motivo_supressao"] == "email_desabilitado"


def test_dry_run_nao_dispara_cooldown(cfg, monkeypatch):
    """
    Alerta suprimido por dry_run NÃO bloqueia o próximo envio real.

    Razão: queremos que ligar o e-mail produza alertas imediatamente,
    em vez de esperar a janela do "cooldown" expirar de um envio que
    nunca aconteceu.
    """
    monkeypatch.setenv("EMAIL_REMETENTE_TEST", "fake@x.com")
    monkeypatch.setenv("EMAIL_SENHA_TEST", "fake_pwd")
    _criar_par_snapshots_perda_buybox()

    # 1ª passada em dry-run
    s1 = avaliador.avaliar_criticos_pendentes(cfg=cfg, dry_run=True)
    assert s1["suprimidos_dryrun"] == 1
    assert s1["enviados"] == 0

    # 2ª passada com envio real — não deve cair em cooldown
    with patch("src.alertas.email.smtplib.SMTP") as mock_smtp:
        s2 = avaliador.avaliar_criticos_pendentes(cfg=cfg, dry_run=False)
        assert s2["enviados"] == 1
        assert s2["suprimidos_cooldown"] == 0
        assert mock_smtp.called


def test_falha_smtp_registra_erro_sem_quebrar_ciclo(cfg, monkeypatch):
    """Erro de SMTP é capturado e logado como erro_smtp no banco."""
    monkeypatch.setenv("EMAIL_REMETENTE_TEST", "fake@x.com")
    monkeypatch.setenv("EMAIL_SENHA_TEST", "fake_pwd")
    _criar_par_snapshots_perda_buybox()

    with patch("src.alertas.email.smtplib.SMTP", side_effect=Exception("kaboom")):
        s = avaliador.avaliar_criticos_pendentes(cfg=cfg, dry_run=False)
        # Não jogou exceção
        assert s["pendentes_detectados"] == 1
        assert s["enviados"] == 0
        assert s["erros_smtp"] == 1

    alertas = _alertas_no_banco()
    motivo = json.loads(alertas[0].dados)["motivo_supressao"]
    assert motivo.startswith("erro_smtp:")
    assert "kaboom" in motivo


def test_resumo_diario_envia_um_email_e_registra_itens(cfg, monkeypatch):
    """Resumo gera 1 e-mail e 1 linha em `alertas` por item B1/B2/B3."""
    monkeypatch.setenv("EMAIL_REMETENTE_TEST", "fake@x.com")
    monkeypatch.setenv("EMAIL_SENHA_TEST", "fake_pwd")

    # 3 anúncios distintos: 1 pausado (B1), 1 margem baixa (B2), 1 oportunidade (B3)
    agora = datetime.now(timezone.utc).replace(microsecond=0)
    persistencia.salvar_snapshot(SnapshotDom(
        sku="A", item_id="MLB-A", coletado_em=agora,
        preco_atual=300.0, nossa_posicao=None, tem_buybox=False,
        status_anuncio="paused", estoque_proprio=0, is_full=False,
        tipo_anuncio="Clássico", preco_1o=None, preco_2o=None,
        qtd_concorrentes=0, margem_atual_pct=20.0, rc_atual_pct=50.0,
        visivel_no_catalogo=False, concorrentes=[],
    ))
    persistencia.salvar_snapshot(SnapshotDom(
        sku="B", item_id="MLB-B", coletado_em=agora,
        preco_atual=100.0, nossa_posicao=1, tem_buybox=True,
        status_anuncio="active", estoque_proprio=5, is_full=False,
        tipo_anuncio="Clássico", preco_1o=100.0, preco_2o=105.0,
        qtd_concorrentes=2, margem_atual_pct=15.0, rc_atual_pct=22.0,
        visivel_no_catalogo=True,
        concorrentes=[
            ConcorrenteDom(posicao=1, seller_id="ME", seller_nome="Nós",
                           preco=100.0, e_nos=True),
        ],
    ))
    persistencia.salvar_snapshot(SnapshotDom(
        sku="C", item_id="MLB-C", coletado_em=agora,
        preco_atual=200.0, nossa_posicao=1, tem_buybox=True,
        status_anuncio="active", estoque_proprio=12, is_full=False,
        tipo_anuncio="Clássico", preco_1o=200.0, preco_2o=240.0,
        qtd_concorrentes=2, margem_atual_pct=45.0, rc_atual_pct=85.0,
        preco_otimo_sugerido=239.9, rc_no_preco_otimo=82.0,
        motivo_sugestao="Defender buybox",
        visivel_no_catalogo=True,
        concorrentes=[
            ConcorrenteDom(posicao=1, seller_id="ME", seller_nome="Nós",
                           preco=200.0, e_nos=True),
        ],
    ))

    with patch("src.alertas.email.smtplib.SMTP") as mock_smtp:
        r = avaliador.enviar_resumo_diario(cfg=cfg, dry_run=False)
        assert r["b1"] == 1 and r["b2"] == 1 and r["b3"] == 1
        assert r["enviado"] is True
        assert mock_smtp.call_count == 1  # 1 e-mail só

    # 1 linha por item B1+B2+B3 (total 3) na tabela alertas
    alertas = _alertas_no_banco()
    tipos = sorted(a.tipo for a in alertas)
    assert tipos == ["B1", "B2", "B3"]
