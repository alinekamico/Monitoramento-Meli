"""
Regras de detecção de alertas do MVP Buybox.

Camada de domínio: funções puras que recebem snapshots (do ORM) e
devolvem objetos `AlertaPendente`. Não conhecem SMTP nem o banco —
a orquestração com cooldown/envio fica em `avaliador.py`.

Regras críticas (e-mail imediato, individual):
  A1 — Perdi buybox
  A2 — Ameaça (concorrente próximo)
  A3 — Concorrente do top 3 sumiu (oportunidade)

Regras do resumo diário (e-mail único):
  B1 — Anúncios com problema (status != active OU off-catálogo)
  B2 — Margem baixa em >= metade dos snapshots do dia
  B3 — Oportunidades de subir preço (buybox + RC alto + ótimo > atual)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..buybox.modelos import (
    Snapshot,
    SnapshotConcorrente,
    TIPO_A1_PERDI_BUYBOX,
    TIPO_A2_AMEACA,
    TIPO_A3_OPORTUNIDADE,
    TIPO_B1_PROBLEMA,
    TIPO_B2_MARGEM_BAIXA,
    TIPO_B3_OPORTUNIDADE_SUBIR,
)


# ============================================================
# DTO interno
# ============================================================


@dataclass
class AlertaPendente:
    """Resultado da avaliação — vai para a tabela `alertas` + e-mail."""

    tipo: str             # A1/A2/A3/B1/B2/B3
    sku: str
    item_id: str
    titulo_curto: str     # cabeçalho do e-mail / linha do log
    motivo: str           # detalhamento curto
    dados: dict = field(default_factory=dict)  # contexto p/ template


# ============================================================
# Helpers
# ============================================================


def _top3_seller_ids(concorrentes: Iterable[SnapshotConcorrente]) -> set[str]:
    """Conjunto de seller_ids do top 3 — usado para detectar A3."""
    return {
        c.seller_id
        for c in concorrentes
        if c.posicao <= 3 and not c.e_nos
    }


def _info_concorrente(snapshot: Snapshot, seller_id: str) -> Optional[dict]:
    """Devolve dados do concorrente identificado pelo seller_id."""
    for c in snapshot.concorrentes:
        if c.seller_id == seller_id:
            return {
                "seller_id":   c.seller_id,
                "seller_nome": c.seller_nome,
                "posicao":     c.posicao,
                "preco":       c.preco,
                "tipo_envio":  c.tipo_envio,
                "url_anuncio": c.url_anuncio,
            }
    return None


def _concorrente_1o(snapshot: Snapshot) -> Optional[dict]:
    for c in snapshot.concorrentes:
        if c.posicao == 1:
            return {
                "seller_id":   c.seller_id,
                "seller_nome": c.seller_nome,
                "preco":       c.preco,
                "url_anuncio": c.url_anuncio,
            }
    return None


# ============================================================
# Regras críticas
# ============================================================


def _avaliar_a1(novo: Snapshot, anterior: Snapshot) -> Optional[AlertaPendente]:
    """A1 — perdemos buybox entre o snapshot anterior e o atual."""
    if not anterior.tem_buybox:
        return None
    if novo.tem_buybox:
        return None
    # Tinha buybox no anterior, perdeu agora
    quem_pegou = _concorrente_1o(novo)
    return AlertaPendente(
        tipo=TIPO_A1_PERDI_BUYBOX,
        sku=novo.sku,
        item_id=novo.item_id,
        titulo_curto=f"Perdi buybox — {novo.sku}",
        motivo=(
            f"Buybox passou para {quem_pegou['seller_nome']} "
            f"@ R$ {quem_pegou['preco']:.2f}"
            if quem_pegou else "Buybox perdido (vencedor desconhecido)"
        ),
        dados={
            "preco_anterior":         anterior.preco_atual,
            "preco_atual":            novo.preco_atual,
            "quem_pegou":             quem_pegou,
            "preco_otimo_sugerido":   novo.preco_otimo_sugerido,
            "rc_no_preco_otimo":      novo.rc_no_preco_otimo,
            "motivo_sugestao":        novo.motivo_sugestao,
            "url_anuncio":            novo.url_anuncio,
            "titulo":                 novo.titulo,
        },
    )


def _avaliar_a2(novo: Snapshot, ruido_rs: float, limite_pct: float) -> Optional[AlertaPendente]:
    """
    A2 — ameaça: ainda tenho buybox, mas:
      (a) preco_1o existe E preco_1o < meu preço (impossível se buybox de
          fato, mas pode ocorrer entre ciclos), OU
      (b) diff_para_2o_pct entre 0 e `limite_pct` (default 2%) — alguém
          chegou perto demais.

    Filtros:
      - Só dispara se temos buybox
      - Diferenças absolutas menores que `ruido_rs` (default R$ 1) viram None
    """
    if not novo.tem_buybox:
        return None

    motivo_partes = []
    ameaca = False

    # (a) Concorrente sob meu preço enquanto ainda tenho buybox
    if (novo.preco_1o is not None
            and novo.preco_1o > 0
            and novo.preco_1o < novo.preco_atual):
        diff = novo.preco_atual - novo.preco_1o
        if diff >= ruido_rs:
            ameaca = True
            motivo_partes.append(
                f"1º a R$ {novo.preco_1o:.2f} (R$ {diff:.2f} abaixo de você)"
            )

    # (b) 2º colocado perto demais
    if (novo.diff_para_2o_pct is not None
            and novo.preco_2o is not None
            and 0 <= -novo.diff_para_2o_pct <= limite_pct
            and abs(novo.preco_2o - novo.preco_atual) >= ruido_rs):
        # diff_para_2o_pct negativo = estou mais barato que o 2º (saudável)
        # 0..limite_pct positivo = estou X% acima dele, ele me cola
        # Para a regra do prompt: "entre 0 e 2%" — o 2º colocado está
        # ≤ 2% acima de mim, ou seja, prestes a me alcançar. Vou checar
        # via valor absoluto da diferença pct.
        pass

    if (novo.preco_2o is not None
            and novo.preco_2o > novo.preco_atual
            and (novo.preco_2o - novo.preco_atual) >= ruido_rs):
        pct = (novo.preco_2o - novo.preco_atual) / novo.preco_atual * 100
        if pct <= limite_pct:
            ameaca = True
            motivo_partes.append(
                f"2º a R$ {novo.preco_2o:.2f} (só {pct:.1f}% acima de você)"
            )

    if not ameaca:
        return None

    primeiro = _concorrente_1o(novo)
    return AlertaPendente(
        tipo=TIPO_A2_AMEACA,
        sku=novo.sku,
        item_id=novo.item_id,
        titulo_curto=f"Ameaça ao buybox — {novo.sku}",
        motivo="; ".join(motivo_partes),
        dados={
            "preco_atual":            novo.preco_atual,
            "preco_1o":               novo.preco_1o,
            "preco_2o":               novo.preco_2o,
            "concorrente_1o":         primeiro,
            "preco_otimo_sugerido":   novo.preco_otimo_sugerido,
            "rc_no_preco_otimo":      novo.rc_no_preco_otimo,
            "motivo_sugestao":        novo.motivo_sugestao,
            "url_anuncio":            novo.url_anuncio,
            "titulo":                 novo.titulo,
        },
    )


def _avaliar_a3(
    novo: Snapshot,
    anterior: Snapshot,
    ante_anterior: Optional[Snapshot],
) -> list[AlertaPendente]:
    """
    A3 — concorrente do top 3 sumiu, confirmação por 2 ciclos.

    Lógica:
      - Seller X aparecia no top 3 em `ante_anterior`
      - Ausente do top 5 em `anterior` E em `novo`
      → dispara A3 sobre X

    Sem `ante_anterior` (primeira coleta após reinício), não dispara
    para evitar falso-positivo na inicialização.

    Pode haver múltiplos concorrentes sumindo ao mesmo tempo — cada um
    gera um alerta separado.
    """
    if ante_anterior is None:
        return []

    top3_passado = _top3_seller_ids(ante_anterior.concorrentes)
    if not top3_passado:
        return []

    seller_ids_anterior = {c.seller_id for c in anterior.concorrentes}
    seller_ids_novo = {c.seller_id for c in novo.concorrentes}

    sumidos = top3_passado - seller_ids_anterior - seller_ids_novo
    if not sumidos:
        return []

    pendentes = []
    for seller_id in sumidos:
        info = _info_concorrente(ante_anterior, seller_id)
        if info is None:
            continue
        pendentes.append(AlertaPendente(
            tipo=TIPO_A3_OPORTUNIDADE,
            sku=novo.sku,
            item_id=novo.item_id,
            titulo_curto=f"Oportunidade — concorrente sumiu — {novo.sku}",
            motivo=(
                f"{info['seller_nome']} estava em {info['posicao']}º "
                f"@ R$ {info['preco']:.2f} e sumiu do top 5"
            ),
            dados={
                "concorrente_sumido":     info,
                "nossa_posicao_atual":    novo.nossa_posicao,
                "preco_atual":            novo.preco_atual,
                "preco_otimo_sugerido":   novo.preco_otimo_sugerido,
                "rc_no_preco_otimo":      novo.rc_no_preco_otimo,
                "motivo_sugestao":        novo.motivo_sugestao,
                "url_anuncio":            novo.url_anuncio,
                "titulo":                 novo.titulo,
            },
        ))
    return pendentes


def avaliar_criticos(
    novo: Snapshot,
    anterior: Optional[Snapshot],
    ante_anterior: Optional[Snapshot],
    cfg_buybox: dict,
) -> list[AlertaPendente]:
    """
    Roda as 3 regras críticas sobre um par de snapshots consecutivos.

    Sem `anterior` (1º snapshot do SKU): nada a comparar, devolve lista
    vazia. A3 só dispara quando há também `ante_anterior`.
    """
    if anterior is None:
        return []

    pendentes: list[AlertaPendente] = []

    # A1
    a1 = _avaliar_a1(novo, anterior)
    if a1 is not None:
        pendentes.append(a1)

    # A2 (ameaça)
    ruido = float(cfg_buybox.get("diferenca_ruido_rs", 1.00))
    limite_a2_pct = float(cfg_buybox.get("limite_a2_pct", 2.0))
    a2 = _avaliar_a2(novo, ruido_rs=ruido, limite_pct=limite_a2_pct)
    if a2 is not None:
        pendentes.append(a2)

    # A3 (concorrente sumiu)
    pendentes.extend(_avaliar_a3(novo, anterior, ante_anterior))

    return pendentes


# ============================================================
# Regras do resumo diário
# ============================================================


def _e_off_catalogo(snap: Snapshot) -> bool:
    return (not snap.visivel_no_catalogo) or snap.status_anuncio != "active"


def avaliar_resumo_diario(
    snapshots_do_dia: list[Snapshot],
    cfg_buybox: dict,
) -> dict:
    """
    Agrega snapshots de um dia inteiro e devolve as listas B1/B2/B3.

    Retorno:
      {
        "b1_problemas":         [dict, ...],
        "b2_margem_baixa":      [dict, ...],
        "b3_oportunidades":     [dict, ...],
        "total_skus_avaliados": int,
      }

    Cada item das listas tem campos suficientes para o template do
    e-mail consumir sem precisar reabrir o snapshot.
    """
    if not snapshots_do_dia:
        return {
            "b1_problemas": [], "b2_margem_baixa": [],
            "b3_oportunidades": [], "total_skus_avaliados": 0,
        }

    margem_min_b2 = float(cfg_buybox.get("margem_minima_b2_pct", 20.0))
    rc_oportunidade = float(cfg_buybox.get("rc_oportunidade_b3_pct", 70.0))
    fracao_b2 = float(cfg_buybox.get("fracao_snapshots_b2", 0.5))

    # Agrupa snapshots por (sku, item_id)
    grupos: dict[tuple[str, str], list[Snapshot]] = {}
    for s in snapshots_do_dia:
        grupos.setdefault((s.sku, s.item_id), []).append(s)

    b1_problemas: list[dict] = []
    b2_margem_baixa: list[dict] = []
    b3_oportunidades: list[dict] = []

    for (sku, item_id), snaps in grupos.items():
        snaps_ord = sorted(snaps, key=lambda s: s.coletado_em)
        ultimo = snaps_ord[-1]

        # B1 — anúncio com problema no último snapshot
        if _e_off_catalogo(ultimo):
            motivo = "pausado" if ultimo.status_anuncio != "active" else "off-catálogo"
            if (ultimo.estoque_proprio or 0) == 0:
                motivo += ", sem estoque"
            b1_problemas.append({
                "sku":            sku,
                "item_id":        item_id,
                "titulo":         ultimo.titulo,
                "status":         ultimo.status_anuncio,
                "estoque":        ultimo.estoque_proprio,
                "motivo":         motivo,
                "url_anuncio":    ultimo.url_anuncio,
            })

        # B2 — margem baixa na maioria dos snapshots do dia
        snaps_validos = [s for s in snaps_ord if s.preco_atual > 0]
        if snaps_validos:
            margens_baixas = sum(
                1 for s in snaps_validos
                if s.margem_atual_pct < margem_min_b2
            )
            if margens_baixas / len(snaps_validos) >= fracao_b2:
                margem_media = sum(s.margem_atual_pct for s in snaps_validos) / len(snaps_validos)
                rc_medio = sum(s.rc_atual_pct for s in snaps_validos) / len(snaps_validos)
                b2_margem_baixa.append({
                    "sku":             sku,
                    "item_id":         item_id,
                    "titulo":          ultimo.titulo,
                    "margem_media":    round(margem_media, 2),
                    "rc_medio":        round(rc_medio, 2),
                    "snapshots":       len(snaps_validos),
                    "snapshots_ruins": margens_baixas,
                    "preco_atual":     ultimo.preco_atual,
                    "url_anuncio":     ultimo.url_anuncio,
                })

        # B3 — oportunidade de subir preço (sobre o último snapshot)
        if (ultimo.tem_buybox
                and ultimo.rc_atual_pct > rc_oportunidade
                and ultimo.preco_otimo_sugerido is not None
                and ultimo.preco_otimo_sugerido > ultimo.preco_atual):
            ganho_rs = ultimo.preco_otimo_sugerido - ultimo.preco_atual
            b3_oportunidades.append({
                "sku":                  sku,
                "item_id":              item_id,
                "titulo":               ultimo.titulo,
                "preco_atual":          ultimo.preco_atual,
                "preco_otimo_sugerido": ultimo.preco_otimo_sugerido,
                "rc_atual_pct":         ultimo.rc_atual_pct,
                "rc_no_preco_otimo":    ultimo.rc_no_preco_otimo,
                "ganho_rs":             round(ganho_rs, 2),
                "motivo_sugestao":      ultimo.motivo_sugestao,
                "url_anuncio":          ultimo.url_anuncio,
            })

    return {
        "b1_problemas":         b1_problemas,
        "b2_margem_baixa":      b2_margem_baixa,
        "b3_oportunidades":     b3_oportunidades,
        "total_skus_avaliados": len(grupos),
    }


__all__ = [
    "AlertaPendente",
    "avaliar_criticos",
    "avaliar_resumo_diario",
]
