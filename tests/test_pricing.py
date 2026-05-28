"""Testes do algoritmo de preço ótimo do MVP Buybox."""

from __future__ import annotations

import pytest

from src.buybox.pricing import (
    MOTIVO_CUSTO_INVALIDO,
    MOTIVO_DEFENDER_BUYBOX,
    MOTIVO_MANTER,
    MOTIVO_OFF_CATALOGO,
    MOTIVO_PASSAR_1O,
    MOTIVO_RC_INVIAVEL,
    MOTIVO_RETOMAR_BUYBOX,
    MOTIVO_RUIDO,
    MOTIVO_SUBIDA_SEM_GANHO,
    MOTIVO_UNICO_VENDEDOR,
    calcular_preco_otimo,
)

# SKU usado nos testes: equivale ao WLK004 (custo 139.65, peso 2.0, Clássico)
_CUSTO_WLK004 = 139.65
_PESO_WLK004 = 2.0


def _chamar(settings: dict, **kwargs):
    base = dict(
        custo=_CUSTO_WLK004, peso=_PESO_WLK004,
        tipo_anuncio="Clássico", settings=settings,
    )
    base.update(kwargs)
    return calcular_preco_otimo(**base)


def test_em_primeiro_com_2o_acima_sugere_descer_10_centavos(settings):
    """Cenário ideal: defender buybox descendo R$0,10 do 2º."""
    # Preço candidato deve dar RC >= 60 — ajustamos preço para garantir
    r = _chamar(
        settings,
        preco_atual=320.0,
        preco_1o=320.0, preco_2o=325.0,
        nossa_posicao=1, tem_buybox=True,
    )
    assert r.preco_otimo_sugerido == pytest.approx(324.90, abs=0.01)
    assert r.rc_no_preco_otimo is not None
    assert r.rc_no_preco_otimo >= 60.0
    assert "Defender buybox" in r.motivo


def test_em_primeiro_sem_2o_mantem_preco(settings):
    """Único vendedor: não sugerir mudança."""
    r = _chamar(
        settings,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=None,
        nossa_posicao=1, tem_buybox=True,
    )
    assert r.preco_otimo_sugerido is None
    assert r.motivo == MOTIVO_MANTER


def test_diferenca_menor_que_ruido_nao_sugere(settings):
    """diff < 1 R$ (ruído) → não sugere mudança."""
    r = _chamar(
        settings,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=300.50,
        nossa_posicao=1, tem_buybox=True,
    )
    assert r.preco_otimo_sugerido is None
    assert r.motivo == MOTIVO_RUIDO


def test_fora_do_buybox_passa_o_1o(settings):
    """Estado transitório: precisa ultrapassar o 1º colocado."""
    r = _chamar(
        settings,
        preco_atual=320.0,
        preco_1o=350.0, preco_2o=None,
        nossa_posicao=None, tem_buybox=False,
    )
    assert r.preco_otimo_sugerido == pytest.approx(349.90, abs=0.01)
    assert "Passar o 1" in r.motivo


def test_fora_do_buybox_passa_o_concorrente_atual(settings):
    """Eu em 2º, concorrente em 1º a R$315 — desço para R$314,90."""
    r = _chamar(
        settings,
        preco_atual=320.0,
        preco_1o=315.0, preco_2o=325.0,
        nossa_posicao=2, tem_buybox=False,
    )
    # Lista ordenada por preço: 1º=315 (concorrente), 2º=325 (eu).
    # Para retomar o buybox, desço abaixo do 1º (concorrente).
    assert r.preco_otimo_sugerido == pytest.approx(314.90, abs=0.01)
    assert "Passar" in r.motivo or "Retomar" in r.motivo


def test_empate_em_primeiro_quebra_regra_geral(settings):
    """Empate em 1º: ML não dá buybox; sugere descer do 1º mesmo assim."""
    # Preço alto para garantir RC viável
    r = _chamar(
        settings,
        preco_atual=400.0,
        preco_1o=400.0, preco_2o=400.0,
        nossa_posicao=2, tem_buybox=False,
    )
    assert r.preco_otimo_sugerido == pytest.approx(399.90, abs=0.01)
    assert "Empate" in r.motivo


def test_rc_inviavel_nao_sugere(settings):
    """Quando RC no preço candidato < rc_minimo, não sugere mudança."""
    r = _chamar(
        settings,
        preco_atual=200.0,
        preco_1o=150.0, preco_2o=160.0,
        nossa_posicao=2, tem_buybox=False,
    )
    assert r.preco_otimo_sugerido is None
    assert r.rc_no_preco_otimo is None
    assert "abaixo do mínimo" in r.motivo


def test_com_campanha_rebate_30_aumenta_rc(settings):
    """Rebate de campanha deve elevar o RC e viabilizar sugestão."""
    # Sem campanha esta combinação dá RC <60. Com 30% rebate, viabiliza.
    sem = _chamar(
        settings,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=305.0,
        nossa_posicao=1, tem_buybox=True,
    )
    # Campanha SMART/DEAL com original_price=305 e candidato dentro da faixa.
    com = _chamar(
        settings,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=305.0,
        nossa_posicao=1, tem_buybox=True,
        campanha_ativa={
            "rebate_pct": 30.0,
            "original_price": 305.0,
            "min_price": 290.0, "max_price": 310.0,
        },
    )
    # Sem rebate o preço candidato (304.90) fica abaixo do mínimo de RC
    assert sem.preco_otimo_sugerido is None
    # Com rebate de 30% (R$ 91,50 fixo sobre 305) o RC explode (>100%)
    assert com.preco_otimo_sugerido is not None
    assert com.rc_no_preco_otimo > 100.0


def test_custo_invalido_nao_calcula(settings):
    """custo zerado → motivo dedicado, sem cálculo."""
    r = _chamar(
        settings,
        custo=0,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=305.0,
        nossa_posicao=1, tem_buybox=True,
    )
    assert r.preco_otimo_sugerido is None
    assert r.motivo == MOTIVO_CUSTO_INVALIDO


def test_full_zera_insumo(settings):
    """is_full=True elimina o insumo fixo do cálculo."""
    sem_full = _chamar(
        settings,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=305.0,
        nossa_posicao=1, tem_buybox=True,
        is_full=False,
    )
    com_full = _chamar(
        settings,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=305.0,
        nossa_posicao=1, tem_buybox=True,
        is_full=True,
    )
    # Mesmo cenário: full deve ter RC maior (insumo zerado).
    # Quando RC sem_full está abaixo do mínimo mas full passa, sem_full
    # vem None e com_full traz sugestão.
    assert (
        sem_full.rc_no_preco_otimo or 0.0
    ) <= (com_full.rc_no_preco_otimo or 9999.0)


def test_rebate_aplicado_dentro_da_faixa_da_campanha(settings):
    """Preço candidato dentro da faixa [min_price, max_price] → rebate vale."""
    # Custo baixo (R$ 50) e preço alto para garantir RC viável
    r = _chamar(
        settings,
        custo=50.0,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=310.0,
        nossa_posicao=1, tem_buybox=True,
        campanha_ativa={
            "rebate_pct": 5.0,
            "min_price": 280.0,
            "max_price": 320.0,
            "original_price": 320.0,   # base do cálculo fixo do rebate
        },
    )
    # Candidato é 309.90 (310 - 0.10), dentro da faixa → rebate vale
    assert r.preco_otimo_sugerido is not None
    # Sem rebate, daria RC menor. Confirmamos que o rebate elevou.
    sem_rebate = _chamar(
        settings,
        custo=50.0,
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=310.0,
        nossa_posicao=1, tem_buybox=True,
    )
    assert (sem_rebate.rc_no_preco_otimo or 0) < r.rc_no_preco_otimo


def test_rebate_nao_aplica_se_candidato_fora_da_faixa(settings):
    """
    Caso real WL009: campanha exclusiva para R$ 167. Se o sistema sugere
    descer para R$ 158, o anúncio sai da campanha — rebate vira ZERO.
    """
    # Cenário: campanha só vale entre R$ 165 e R$ 170. Candidato seria 158.25,
    # fora da faixa, então rebate não conta. RC fica abaixo do mínimo.
    r = _chamar(
        settings,
        custo=71.38,                # custo real do WL009 atual
        preco_atual=167.0,
        preco_1o=158.35, preco_2o=167.0,
        nossa_posicao=2, tem_buybox=False,
        campanha_ativa={
            "rebate_pct": 3.0,
            "min_price": 165.0,
            "max_price": 170.0,
        },
    )
    # Não deveria sugerir, e o motivo deve mencionar a campanha
    assert r.preco_otimo_sugerido is None
    assert "campanha" in r.motivo.lower() or "rebate" in r.motivo.lower()


def test_rebate_aplica_sem_faixa_definida(settings):
    """Campanha sem min/max e sem preco_aplicado: aplica o rebate
    (compatibilidade com snapshots antigos)."""
    r1 = _chamar(
        settings,
        preco_atual=400.0,
        preco_1o=400.0, preco_2o=410.0,
        nossa_posicao=1, tem_buybox=True,
        campanha_ativa={"rebate_pct": 5.0},  # sem min/max, sem preco_aplicado
    )
    assert r1.preco_otimo_sugerido is not None


def test_campanha_externa_sem_faixa_perde_rebate_ao_mudar_preco(settings):
    """
    Caso real do usuário: campanha 'Nova campanha de desconto' está
    aplicada em R$ 167 com 3% de rebate ML. Se baixarmos para 158,25,
    o sistema usa a campanha PRÓPRIA do seller (sem rebate ML).
    """
    # Custo alto (R$ 71,38 — custo real do WL009) força RC ruim sem rebate
    r = _chamar(
        settings,
        custo=71.38,
        preco_atual=167.0,
        preco_1o=158.35, preco_2o=167.0,
        nossa_posicao=2, tem_buybox=False,
        campanha_ativa={
            "rebate_pct":     3.0,
            "preco_aplicado": 167.0,
            # sem min_price / max_price — campanha SELLER_CAMPAIGN típica
        },
    )
    # Candidato é 158.25 (158.35 - 0.10), diferente de 167 → sai da campanha
    # → rebate não vale → RC ~46% < 60% → não sugere
    assert r.preco_otimo_sugerido is None
    assert "campanha externa" in r.motivo or "campanha" in r.motivo.lower()


def test_calcular_preco_candidato_em_buybox_usa_preco_2o():
    """
    Bug reportado pelo usuário (MLB3272862433):
    estamos em 1º com buybox (preço 168), 2º colocado a R$ 174,53.
    Candidato testado pelo sistema deve ser preço_2o - 0,10 = R$ 174,43
    (subir para defender buybox), não preço_1o - 0,10 = R$ 167,90.
    """
    from src.buybox.pricing import calcular_preco_candidato
    cand = calcular_preco_candidato(
        preco_atual=168.0,
        preco_1o=168.0, preco_2o=174.53,
        nossa_posicao=1, tem_buybox=True,
    )
    assert cand == 174.43


def test_calcular_preco_candidato_fora_buybox_usa_preco_1o():
    """Fora do buybox: candidato é preço_1o (concorrente) - 0,10."""
    from src.buybox.pricing import calcular_preco_candidato
    cand = calcular_preco_candidato(
        preco_atual=197.90,
        preco_1o=161.50, preco_2o=197.90,  # preço_2o = NÓS
        nossa_posicao=2, tem_buybox=False,
    )
    assert cand == 161.40  # 161.50 - 0.10 (passa o concorrente)


def test_calcular_preco_candidato_unico_vendedor_devolve_none():
    """Sem 2º (único no catálogo): nada a testar."""
    from src.buybox.pricing import calcular_preco_candidato
    cand = calcular_preco_candidato(
        preco_atual=300.0,
        preco_1o=300.0, preco_2o=None,
        nossa_posicao=1, tem_buybox=True,
    )
    assert cand is None


def test_rebate_calculado_sobre_original_price_nao_preco_atual():
    """
    Bug reportado pelo usuário (MLB3272862433):
    preço cheio R$ 329,90, preço campanha R$ 168, rebate 4,9%.
    Painel Campanhas mostra R$ 16,17 (329,90 × 4,9%) — esse é o correto.
    Sistema deve usar mesma base, não calcular sobre preço atual (R$ 168).
    """
    from src.buybox.pricing import _calcular_rebate_valor
    campanha = {
        "rebate_pct":     4.9,
        "original_price": 329.90,
        "preco_aplicado": 168.0,
    }
    assert _calcular_rebate_valor(campanha) == pytest.approx(16.17, abs=0.01)


def test_rebate_fallback_para_preco_aplicado_sem_original():
    """Quando original_price não vem da API, usa preco_aplicado como base."""
    from src.buybox.pricing import _calcular_rebate_valor
    campanha = {
        "rebate_pct":     3.0,
        "preco_aplicado": 167.0,
        # sem original_price
    }
    # 167 × 3% = 5.01
    assert _calcular_rebate_valor(campanha) == pytest.approx(5.01, abs=0.01)


def test_campanha_externa_mantem_rebate_se_preco_inalterado(settings):
    """
    Quando o candidato bate com `preco_aplicado` (tolerância R$ 0,05),
    o rebate continua valendo — não saímos da campanha.
    """
    # Cenário: estou em 1º com buybox a R$ 167. 2º está a R$ 167,01 → diff
    # menor que ruído, sistema não sugere. Mas verifica que o rebate
    # AINDA seria aplicável se o candidato fosse 167.
    from src.buybox.pricing import rebate_aplicavel
    campanha = {
        "rebate_pct":     3.0,
        "preco_aplicado": 167.0,
    }
    assert rebate_aplicavel(167.0, campanha) is True
    assert rebate_aplicavel(166.97, campanha) is True   # dentro da tolerância
    assert rebate_aplicavel(166.50, campanha) is False  # fora da tolerância
    assert rebate_aplicavel(167.50, campanha) is False  # fora pra cima


def test_off_catalogo_nao_sugere_preco(settings):
    """
    Anúncio pausado/sem estoque (não visível ao cliente) — não há buybox
    a preservar, sugestão de preço seria ruído. Motivo dedicado para
    alimentar o alerta B1 'anúncios com problema'.
    """
    r = _chamar(
        settings,
        preco_atual=320.0,
        preco_1o=315.0, preco_2o=325.0,
        nossa_posicao=None, tem_buybox=False,
        visivel_no_catalogo=False,
    )
    assert r.preco_otimo_sugerido is None
    assert r.motivo == MOTIVO_OFF_CATALOGO


def test_fora_do_top5_sem_preco_1o_marca_unico_vendedor(settings):
    """Sem 1º válido e fora do buybox: tratamos como único vendedor."""
    r = _chamar(
        settings,
        preco_atual=300.0,
        preco_1o=None, preco_2o=None,
        nossa_posicao=None, tem_buybox=False,
    )
    assert r.preco_otimo_sugerido is None
    assert r.motivo == MOTIVO_UNICO_VENDEDOR


def test_subida_sem_ganho_de_rc_nao_sugere(settings):
    """
    Caso MLB4232704563: estamos em 1º com buybox, 2º colocado é apenas R$5
    mais caro. O candidato seria R$299,90 (subida de R$4,90), mas perder o
    rebate de ≈R$16 (4.85% × R$329,90) elimina todo o ganho de receita
    (4,90 × ~80% de margem ≈ R$3,94 < R$16). RC cai → não sugerir.
    """
    r = _chamar(
        settings,
        preco_atual=295.0,
        preco_1o=295.0, preco_2o=300.0,
        nossa_posicao=1, tem_buybox=True,
        campanha_ativa={
            "rebate_pct":     4.85,        # ≈ R$16 de rebate
            "original_price": 329.9,
            "preco_aplicado": 295.0,       # campanha só vale nesse preço
        },
    )
    assert r.preco_otimo_sugerido is None
    assert "RC" in r.motivo or "rc" in r.motivo.lower()


def test_subida_com_ganho_de_rc_sugere(settings):
    """
    Quando subir o preço realmente melhora o RC (sem perder rebate),
    a sugestão deve ser feita normalmente.
    """
    # Sem campanha: subir de 320 para 324,90 (2º a 325) melhora RC pois
    # não há rebate a perder.
    r = _chamar(
        settings,
        preco_atual=310.0,
        preco_1o=310.0, preco_2o=325.0,
        nossa_posicao=1, tem_buybox=True,
        campanha_ativa=None,
    )
    assert r.preco_otimo_sugerido is not None
    assert r.preco_otimo_sugerido == pytest.approx(324.90, abs=0.01)
    assert r.rc_no_preco_otimo is not None
