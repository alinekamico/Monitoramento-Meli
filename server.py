"""
Servidor local para o painel de campanhas.

Uso:
  python server.py

Depois abra dashboard.html no navegador (ou acesse http://localhost:5000).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

import yaml
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

from src import decisor, margem, ml_client, pdv
from src.buybox import persistencia as buybox_persist

_CONFIG_DIR = Path(__file__).parent / "config"

app = Flask(__name__, static_folder=str(Path(__file__).parent))
CORS(app)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _process_item(
    sku: str,
    sku_data: dict,
    item: dict,
    cfg: dict,
    rc_min: float,
) -> list[dict]:
    item_id      = item.get("id", "")
    listing_id   = item.get("listing_type_id", "")
    tipo_anuncio = "Premium" if "gold_pro" in listing_id else sku_data["tipo_anuncio"]
    is_full      = item.get("shipping", {}).get("logistic_type") == "fulfillment"
    item_cfg     = {**cfg, "insumo_fixo": 0.0} if is_full else cfg
    has_stock    = item.get("available_quantity", 0) > 0
    qty          = item.get("available_quantity", 0)

    rows: list[dict] = []

    try:
        campaigns = ml_client.get_campaigns_for_item(item_id)

        for campanha in campaigns["ativas"]:
            preco = campanha.get("price") or 0.0
            m = margem.calcular_margem(
                preco_campanha=preco,
                custo=sku_data["custo"],
                rebate=campanha.get("rebate_valor", 0.0),
                peso=sku_data["peso"],
                tipo_anuncio=tipo_anuncio,
                cfg=item_cfg,
            )
            rows.append({
                "sku":           sku,
                "item_id":       item_id,
                "campanha_nome": campanha.get("name") or campanha.get("type") or "—",
                "campanha_tipo": campanha.get("type") or "",
                "status":        "ATIVA",
                "has_stock":     has_stock,
                "quantidade":    qty,
                "is_full":       is_full,
                "tipo_anuncio":  tipo_anuncio,
                "rebate":        m["rebate"],
                "preco_campanha": m["preco_campanha"],
                "custo":         m["custo"],
                "comissao":      m["comissao"],
                "frete":         m["frete"],
                "imposto":       m["imposto"],
                "insumo":        m["insumo"],
                "reversa":       m["reversa"],
                "lucro_bruto":   m["lucro_bruto"],
                "margem_pct":    m["margem_pct"],
                "rc_pct":        m["rc_pct"],
                "start_date":    campanha.get("start_date") or "",
                "finish_date":   campanha.get("finish_date") or "",
                "decisao":       None,
                "motivo":        None,
            })

        candidatas = [c for c in campaigns["disponiveis"] if c.get("rebate_valor", 0) > 0]
        for campanha in candidatas:
            if campanha.get("type") == "PRICE_MATCHING":
                preco = campanha.get("price") or 0.0
            else:
                preco = campanha.get("suggested_price") or campanha.get("price") or 0.0

            m = margem.calcular_margem(
                preco_campanha=preco,
                custo=sku_data["custo"],
                rebate=campanha.get("rebate_valor", 0.0),
                peso=sku_data["peso"],
                tipo_anuncio=tipo_anuncio,
                cfg=item_cfg,
            )
            d = decisor.decidir(m, rc_min)
            rows.append({
                "sku":           sku,
                "item_id":       item_id,
                "campanha_nome": campanha.get("name") or campanha.get("type") or "—",
                "campanha_tipo": campanha.get("type") or "",
                "status":        "CANDIDATA",
                "has_stock":     has_stock,
                "quantidade":    qty,
                "is_full":       is_full,
                "tipo_anuncio":  tipo_anuncio,
                "rebate":        m["rebate"],
                "preco_campanha": m["preco_campanha"],
                "custo":         m["custo"],
                "comissao":      m["comissao"],
                "frete":         m["frete"],
                "imposto":       m["imposto"],
                "insumo":        m["insumo"],
                "reversa":       m["reversa"],
                "lucro_bruto":   m["lucro_bruto"],
                "margem_pct":    m["margem_pct"],
                "rc_pct":        m["rc_pct"],
                "start_date":    campanha.get("start_date") or "",
                "finish_date":   campanha.get("finish_date") or "",
                "decisao":       d["decisao"],
                "motivo":        d["motivo"],
            })

    except Exception as exc:
        rows.append({
            "sku":     sku,
            "item_id": item_id,
            "status":  "ERRO",
            "erro":    str(exc),
        })

    # Identifica a campanha com MAIOR RC entre as viáveis (incluindo started)
    # para destacar no painel — "a melhor opção do momento".
    candidatas_validas = [
        (i, r) for i, r in enumerate(rows)
        if r.get("status") != "ERRO" and (r.get("rc_pct") or 0) > 0
    ]
    if candidatas_validas:
        idx_melhor = max(candidatas_validas, key=lambda x: x[1]["rc_pct"])[0]
        rows[idx_melhor]["melhor_rc"] = True

    return rows


# ---------------------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    return send_from_directory(str(Path(__file__).parent), "dashboard.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/campaigns")
def get_campaigns():
    cfg    = _load_settings()
    rc_min = float(cfg["rc_minimo"])
    skus   = pdv.load_skus()

    seller_id = ml_client.get_seller_id()

    all_rows: list[dict] = []

    for sku, sku_data in skus.items():
        item_ids = ml_client.get_item_ids_by_sku(seller_id, sku)
        if not item_ids:
            continue
        items = ml_client.get_items_details(item_ids)
        time.sleep(0.15)

        for item in items:
            rows = _process_item(sku, sku_data, item, cfg, rc_min)
            all_rows.extend(rows)
            time.sleep(0.1)

    n_ativas     = sum(1 for r in all_rows if r.get("status") == "ATIVA")
    n_candidatas = sum(1 for r in all_rows if r.get("status") == "CANDIDATA")
    n_aceitar    = sum(1 for r in all_rows if r.get("decisao") == "ACEITAR")
    n_recusar    = sum(1 for r in all_rows if r.get("decisao") == "RECUSAR")
    n_erros      = sum(1 for r in all_rows if r.get("status") == "ERRO")

    return jsonify({
        "results":     all_rows,
        "rc_minimo":   rc_min,
        "timestamp":   time.time(),
        "summary": {
            "total":      len(all_rows),
            "ativas":     n_ativas,
            "candidatas": n_candidatas,
            "aceitar":    n_aceitar,
            "recusar":    n_recusar,
            "erros":      n_erros,
        },
    })


# ---------------------------------------------------------------------------
# Endpoints do MVP Buybox
# ---------------------------------------------------------------------------
# Leem snapshots já persistidos pelo coletor (data/buybox.db). Não fazem
# chamadas à API ML — são rápidos. A coleta é responsabilidade do
# scheduler/coletor; estes endpoints só leem.


def _categoria_status(snap) -> str:
    """Classificação visual usada no dashboard: verde/amarelo/vermelho/cinza."""
    if not snap.visivel_no_catalogo or snap.status_anuncio != "active":
        return "off"           # cinza
    if snap.tem_buybox:
        return "buybox"        # verde
    pos = snap.nossa_posicao or 99
    if pos <= 3:
        return "ameaca"        # amarelo
    return "fora"              # vermelho


def _serializar_snapshot_resumo(snap) -> dict:
    """Linha enxuta da listagem (aba Buybox)."""
    return {
        "sku":                  snap.sku,
        "item_id":               snap.item_id,
        "titulo":                snap.titulo,
        "categoria_status":      _categoria_status(snap),
        "nossa_posicao":         snap.nossa_posicao,
        "tem_buybox":            snap.tem_buybox,
        "preco_atual":           snap.preco_atual,
        "preco_cheio":           snap.preco_cheio,
        "preco_1o":              snap.preco_1o,
        "preco_2o":              snap.preco_2o,
        "diff_para_1o_rs":       snap.diff_para_1o_rs,
        "diff_para_1o_pct":      snap.diff_para_1o_pct,
        "diff_para_2o_rs":       snap.diff_para_2o_rs,
        "diff_para_2o_pct":      snap.diff_para_2o_pct,
        "margem_atual_pct":      snap.margem_atual_pct,
        "rc_atual_pct":          snap.rc_atual_pct,
        "preco_otimo_sugerido":  snap.preco_otimo_sugerido,
        "rc_no_preco_otimo":     snap.rc_no_preco_otimo,
        "motivo_sugestao":       snap.motivo_sugestao,
        "rebate_pct":            snap.rebate_pct,
        "campanha_ativa_nome":   snap.campanha_ativa_nome,
        "campanha_min_price":    snap.campanha_min_price,
        "campanha_max_price":    snap.campanha_max_price,
        "campanha_start_date":   snap.campanha_start_date,
        "campanha_finish_date":  snap.campanha_finish_date,
        "custo":                 snap.custo,
        "is_full":               snap.is_full,
        "tipo_anuncio":          snap.tipo_anuncio,
        "status_anuncio":        snap.status_anuncio,
        "estoque_proprio":       snap.estoque_proprio,
        "visivel_no_catalogo":   snap.visivel_no_catalogo,
        "qtd_concorrentes":      snap.qtd_concorrentes,
        "url_anuncio":           snap.url_anuncio,
        "thumbnail_url":         snap.thumbnail_url,
        "reviews_rating":        snap.reviews_rating,
        "reviews_total":         snap.reviews_total,
        "coletado_em":           snap.coletado_em.isoformat() if snap.coletado_em else None,
    }


def _calcular_breakdown(
    snap, preco_alvo: Optional[float] = None, cfg: Optional[dict] = None,
) -> dict:
    """
    Reproduz o cálculo do PDV linha a linha para o `preco_alvo`
    (default = preço atual). Devolve estrutura amigável ao frontend:

      {
        "preco": ..., "custo": ..., "comissao": ..., "frete": ...,
        "imposto": ..., "insumo": ..., "reversa": ...,
        "rebate_aplicado": bool, "rebate_motivo": str, "rebate": ...,
        "lucro_bruto": ..., "margem_pct": ..., "rc_pct": ...,
      }

    O campo `rebate_motivo` explica por que o rebate foi ou não aplicado,
    eliminando dúvidas como "por que o sistema soma rebate mas meu PDV não?".
    """
    from src.buybox.pricing import _rebate_em_reais, rebate_aplicavel

    cfg = cfg or _load_settings()
    preco = float(preco_alvo if preco_alvo is not None else snap.preco_atual or 0)

    # Fallback: snapshots antigos (pré-migração) não têm custo persistido.
    # Nesses casos, usamos o custo atual do skus.yaml.
    skus_yaml = pdv.load_skus()
    custo = float(snap.custo or 0)
    if custo <= 0:
        custo = float(skus_yaml.get(snap.sku, {}).get("custo", 0) or 0)
    if preco <= 0 or custo <= 0:
        return {"erro": "preço ou custo zerado"}

    # Insumo zerado se Full (mesma regra do runner/pricing)
    cfg_calc = {**cfg, "insumo_fixo": 0.0} if snap.is_full else cfg

    # Decide se o rebate se aplica neste preço alvo.
    # `original_price` da campanha → base para o rebate FIXO em R$
    # (o ML calcula sempre como pct × original_price, não × preço atual)
    # `preco_aplicado` → preço onde a campanha está vinculada. Mudar de
    # preço significa sair da campanha e perder o rebate.
    campanha = None
    if snap.rebate_pct and snap.rebate_pct > 0:
        original = (
            snap.campanha_original_price
            or snap.preco_cheio  # snapshots antigos pré-migração
            or snap.preco_atual  # último fallback
        )
        campanha = {
            "rebate_pct":     snap.rebate_pct,
            "min_price":      snap.campanha_min_price or 0,
            "max_price":      snap.campanha_max_price or 0,
            "preco_aplicado": snap.preco_atual,
            "original_price": original,
        }
    aplicavel = rebate_aplicavel(preco, campanha) if campanha else False
    rebate_rs = _rebate_em_reais(preco, campanha) if campanha else 0.0

    if not campanha:
        motivo_rebate = "Sem campanha ativa com rebate"
    elif not aplicavel:
        min_p = campanha.get("min_price") or 0
        max_p = campanha.get("max_price") or 0
        if min_p > 0 or max_p > 0:
            # Campanha SMART/DEAL com faixa explícita
            motivo_rebate = (
                f"Preço R$ {preco:.2f} fora da faixa da campanha "
                f"(R$ {min_p:.2f} – R$ {max_p:.2f}) — rebate de "
                f"{snap.rebate_pct:.0f}% não se aplica"
            )
        else:
            # Campanha externa sem faixa: mudar preço = sair da campanha
            motivo_rebate = (
                f"Ao mudar de R$ {campanha['preco_aplicado']:.2f} (preço da "
                f"campanha '{snap.campanha_ativa_nome or '—'}') para "
                f"R$ {preco:.2f}, sai da campanha externa e entra na sua "
                f"própria — rebate ML de {snap.rebate_pct:.0f}% deixa de valer"
            )
    else:
        base_rebate = campanha.get("original_price") or 0
        motivo_rebate = (
            f"Campanha '{snap.campanha_ativa_nome or '—'}': "
            f"ML subsidia {snap.rebate_pct:.1f}% sobre o preço cheio "
            f"(R$ {base_rebate:.2f}) — valor fixo durante a campanha"
        )

    # Peso vem do skus.yaml (não está no snapshot)
    peso = float(skus_yaml.get(snap.sku, {}).get("peso", 0) or 0)
    m = margem.calcular_margem(
        preco_campanha=preco, custo=custo, rebate=rebate_rs,
        peso=peso, tipo_anuncio=snap.tipo_anuncio, cfg=cfg_calc,
    )
    return {
        "preco":           m["preco_campanha"],
        "custo":           m["custo"],
        "comissao":        m["comissao"],
        "frete":           m["frete"],
        "imposto":         m["imposto"],
        "insumo":          m["insumo"],
        "reversa":         m["reversa"],
        "rebate":          m["rebate"],
        "rebate_aplicado": aplicavel,
        "rebate_motivo":   motivo_rebate,
        "rebate_pct":      snap.rebate_pct,
        "lucro_bruto":     m["lucro_bruto"],
        "margem_pct":      m["margem_pct"],
        "rc_pct":          m["rc_pct"],
        "tipo_anuncio":    snap.tipo_anuncio,
        "is_full":         snap.is_full,
    }


def _serializar_concorrentes(concorrentes, snap=None) -> list[dict]:
    """
    Serializa o top 5. Quando `snap` é informado, calcula também o RC e a
    margem que NÓS teríamos no preço de cada concorrente (para vencê-lo,
    descer R$ 0,10 — mas a coluna mostra o RC no preço EXATO dele para
    deixar visível o esforço de margem).
    """
    out = []
    cfg = _load_settings() if snap else None
    for c in concorrentes:
        item = {
            "posicao":            c.posicao,
            "seller_id":          c.seller_id,
            "seller_nome":        c.seller_nome,
            "preco":              c.preco,
            "tipo_envio":         c.tipo_envio,
            "frete_gratis":       c.frete_gratis,
            "reputacao":          c.reputacao,
            "url_anuncio":        c.url_anuncio,
            "e_nos":              c.e_nos,
            "total_vendas":       c.total_vendas or 0,
            "prazo_entrega_dias": c.prazo_entrega_dias,
        }
        if snap and c.preco and c.preco > 0:
            # "RC para vencer": preço do concorrente menos o passo padrão.
            # Esse é o preço mínimo que precisamos ter para passar o seller.
            passo = float((cfg.get("buybox") or {}).get("passo_abaixo_rs", 0.10))
            preco_vencer = c.preco if c.e_nos else round(c.preco - passo, 2)
            bd = _calcular_breakdown(snap, preco_vencer, cfg)
            if "erro" not in bd:
                item["nosso_rc_no_preco"] = bd["rc_pct"]
                item["nosso_margem_no_preco"] = bd["margem_pct"]
                item["nosso_rebate_no_preco"] = bd["rebate"]
                item["nosso_preco_para_vencer"] = preco_vencer
        out.append(item)
    return out


@app.route("/api/buybox/lista")
def buybox_lista():
    """Último snapshot de cada SKU para a tabela da aba Buybox."""
    por_sku = buybox_persist.ultimo_snapshot_por_sku()

    # Múltiplos MLBs por SKU? `ultimo_snapshot_por_sku` retorna 1 por SKU
    # (o mais recente). Para listar TODOS os MLBs do último ciclo,
    # buscamos o último de cada par (sku, item_id).
    with buybox_persist.sessao() as s:
        from sqlalchemy import select
        from src.buybox.modelos import Snapshot as _Snap

        pares = s.execute(
            select(_Snap.sku, _Snap.item_id).distinct()
        ).all()

        snapshots = []
        for sku, item_id in pares:
            stmt = (
                select(_Snap)
                .where(_Snap.sku == sku, _Snap.item_id == item_id)
                .order_by(_Snap.coletado_em.desc())
                .limit(1)
            )
            row = s.execute(stmt).scalar_one_or_none()
            if row is not None:
                _ = row.concorrentes  # materializa antes de fechar sessão
                snapshots.append(row)

    linhas = [_serializar_snapshot_resumo(snap) for snap in snapshots]

    # Contadores para os botões de filtro
    categorias = {"buybox": 0, "ameaca": 0, "fora": 0, "off": 0}
    oportunidades = 0
    for L in linhas:
        categorias[L["categoria_status"]] = categorias.get(L["categoria_status"], 0) + 1
        if (L["preco_otimo_sugerido"] is not None
                and L["preco_otimo_sugerido"] > (L["preco_atual"] or 0)):
            oportunidades += 1

    return jsonify({
        "results":       linhas,
        "timestamp":     time.time(),
        "summary": {
            "total":          len(linhas),
            "com_buybox":     categorias["buybox"],
            "em_risco":       categorias["ameaca"] + categorias["fora"],
            "off_catalogo":   categorias["off"],
            "oportunidades":  oportunidades,
        },
    })


@app.route("/api/buybox/sku/<sku>")
def buybox_sku_detalhe(sku: str):
    """
    Detalhe de um SKU: snapshots no período + top 5 atual + série de
    preços por concorrente.

    Query params (todos opcionais):
      - periodo: "24h" | "7d" | "30d" (default 24h)
      - desde, ate: ISO date/datetime (sobrescrevem periodo)
    """
    from datetime import datetime as _dt
    item_id = request.args.get("item_id")
    periodo = (request.args.get("periodo") or "24h").lower()
    desde_str = request.args.get("desde")
    ate_str = request.args.get("ate")

    desde_dt = None
    ate_dt = None
    if desde_str or ate_str:
        # Custom: prioridade sobre `periodo`
        try:
            if desde_str:
                desde_dt = _dt.fromisoformat(desde_str)
            if ate_str:
                ate_dt = _dt.fromisoformat(ate_str)
                # Inclui o dia inteiro se vier só data (sem hora)
                if "T" not in ate_str:
                    ate_dt = ate_dt.replace(hour=23, minute=59, second=59)
        except ValueError:
            return jsonify({"erro": "datas inválidas (use ISO 8601)"}), 400
        historico = buybox_persist.snapshots_periodo(
            sku, item_id=item_id, desde=desde_dt, ate=ate_dt,
        )
    else:
        horas = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}.get(periodo, 24)
        historico = buybox_persist.snapshots_periodo(
            sku, item_id=item_id, horas=horas,
        )
    if not historico:
        return jsonify({"sku": sku, "snapshots": [], "erro": "sem snapshots"}), 404

    ultimo = historico[-1]

    historico_resumo = [
        {
            "coletado_em":     s.coletado_em.isoformat(),
            "preco_atual":     s.preco_atual,
            "nossa_posicao":   s.nossa_posicao,
            "tem_buybox":      s.tem_buybox,
            "preco_1o":        s.preco_1o,
            "preco_2o":        s.preco_2o,
            "rc_atual_pct":    s.rc_atual_pct,
            "margem_atual_pct": s.margem_atual_pct,
        }
        for s in historico
    ]

    # Série de preços por concorrente para o gráfico de linhas.
    # Estrutura: {seller_id: {nome, e_nos, pontos: [{ts, preco}]}}
    serie_concorrentes: dict[str, dict] = {}
    for s in historico:
        ts = s.coletado_em.isoformat()
        for c in s.concorrentes:
            # Identificador estável; quando vier vazio, agrupa pelo nome
            chave = c.seller_id or f"_unnamed_{c.seller_nome}"
            if chave not in serie_concorrentes:
                serie_concorrentes[chave] = {
                    "seller_id":   c.seller_id,
                    "seller_nome": c.seller_nome or "—",
                    "e_nos":       bool(c.e_nos),
                    "pontos":      [],
                }
            serie_concorrentes[chave]["pontos"].append({
                "coletado_em": ts,
                "preco":       float(c.preco),
                "posicao":     c.posicao,
            })

    # Lista ordenada: nossa série primeiro, depois pelo nome
    series = sorted(
        serie_concorrentes.values(),
        key=lambda x: (not x["e_nos"], x["seller_nome"].lower()),
    )

    # Breakdown: decompõe a margem componente a componente, tanto para
    # o preço atual quanto para o preço ótimo sugerido (se houver).
    cfg = _load_settings()
    breakdown_atual = _calcular_breakdown(ultimo, ultimo.preco_atual, cfg)
    breakdown_otimo = None
    if ultimo.preco_otimo_sugerido and ultimo.preco_otimo_sugerido > 0:
        breakdown_otimo = _calcular_breakdown(
            ultimo, ultimo.preco_otimo_sugerido, cfg,
        )
    # Se não há sugestão mas há motivo "RC inviável", calcula o breakdown
    # do CANDIDATO REAL que o pricing testou (não simplesmente preço_1o-0,10).
    # Para quem está em 1º com buybox, o candidato é preço_2o-0,10 (subir).
    # Para quem está fora, é preço_1o-0,10 (retomar).
    else:
        from src.buybox.pricing import calcular_preco_candidato
        passo = float((cfg.get("buybox") or {}).get("passo_abaixo_rs", 0.10))
        candidato = calcular_preco_candidato(
            preco_atual=ultimo.preco_atual,
            preco_1o=ultimo.preco_1o,
            preco_2o=ultimo.preco_2o,
            nossa_posicao=ultimo.nossa_posicao,
            tem_buybox=ultimo.tem_buybox,
            passo=passo,
        )
        if candidato is not None:
            breakdown_otimo = _calcular_breakdown(ultimo, candidato, cfg)
            breakdown_otimo["preco_descartado"] = True

    return jsonify({
        "snapshot":            _serializar_snapshot_resumo(ultimo),
        "concorrentes":        _serializar_concorrentes(ultimo.concorrentes, snap=ultimo),
        "historico_24h":       historico_resumo,   # mantido p/ compatibilidade
        "historico_periodo":   historico_resumo,   # nome novo, semanticamente correto
        "periodo_aplicado":    periodo,
        "desde":               desde_dt.isoformat() if desde_dt else None,
        "ate":                 ate_dt.isoformat() if ate_dt else None,
        "serie_concorrentes":  series,
        "breakdown_atual":     breakdown_atual,
        "breakdown_otimo":     breakdown_otimo,
    })


@app.route("/api/buybox/sku/<sku>/alertas")
def buybox_sku_alertas(sku: str):
    """Histórico de alertas dos últimos 7 dias para um SKU."""
    import json as _json
    alertas = buybox_persist.alertas_recentes(sku, dias=7)
    return jsonify({
        "sku": sku,
        "alertas": [
            {
                "id":           a.id,
                "tipo":         a.tipo,
                "item_id":      a.item_id,
                "disparado_em": a.disparado_em.isoformat() if a.disparado_em else None,
                "enviado_em":   a.enviado_em.isoformat() if a.enviado_em else None,
                "dados":        _json.loads(a.dados) if a.dados else {},
            }
            for a in alertas
        ],
    })


@app.route("/api/buybox/sku/<sku>/prazos")
def buybox_sku_prazos(sku: str):
    """
    Consulta ao vivo o prazo de entrega de cada item do top 5 para o CEP
    de referência (buybox.cep_referencia). Lazy load: só dispara quando
    o modal de detalhe é aberto, não a cada ciclo do scheduler.

    Devolve [{seller_id, item_id_concorrente, prazo_dias}, ...].
    """
    item_id = request.args.get("item_id")
    if not item_id:
        return jsonify({"erro": "informe item_id"}), 400

    cfg = _load_settings()
    cep = (cfg.get("buybox") or {}).get("cep_referencia", "01310100")

    # Busca o último snapshot para sabermos quais sellers/concorrentes consultar
    historico = buybox_persist.snapshots_24h(sku, item_id=item_id)
    if not historico:
        return jsonify({"erro": "sem snapshot recente"}), 404

    ultimo = historico[-1]
    resultados = []
    for c in ultimo.concorrentes:
        # url_anuncio guarda o link público, item_id do concorrente vem dali.
        # Como o url tem formato .../MLB-XXXXXX-..., parse simples.
        item_concorrente = None
        if c.url_anuncio:
            import re
            m = re.search(r"MLB-?(\d+)", c.url_anuncio)
            if m:
                item_concorrente = f"MLB{m.group(1)}"
        if not item_concorrente:
            resultados.append({
                "seller_id": c.seller_id, "posicao": c.posicao,
                "prazo_dias": None, "erro": "item_id do anúncio indisponível",
            })
            continue

        prazo = ml_client.get_prazo_entrega_dias(item_concorrente, cep)
        resultados.append({
            "seller_id": c.seller_id, "posicao": c.posicao,
            "item_id":   item_concorrente,
            "prazo_dias": prazo,
        })
    return jsonify({"cep": cep, "prazos": resultados})


@app.route("/api/buybox/sku/<sku>/vendas")
def buybox_sku_vendas(sku: str):
    """
    Dados de vendas alinhados com snapshots de preço — usado pelo gráfico
    "Preço × Vendas" do modal de detalhe.

    Para cada snapshot no período, conta pedidos e receita na janela
    [snapshot_anterior, snapshot_atual]. Calcula também o preço médio
    ponderado dos pedidos quando o preço variou dentro da janela.

    Query params:
      - item_id: MLB do anúncio (obrigatório)
      - periodo: 7d (default) | 24h | 30d
    """
    from datetime import datetime as _dt, timezone, timedelta as _td

    item_id = request.args.get("item_id")
    if not item_id:
        return jsonify({"erro": "informe item_id"}), 400

    periodo = (request.args.get("periodo") or "7d").lower()
    horas   = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}.get(periodo, 24 * 7)

    ate_dt   = _dt.now(timezone.utc)
    desde_dt = ate_dt - _td(hours=horas)

    historico = buybox_persist.snapshots_periodo(sku, item_id=item_id, horas=horas)
    if not historico:
        return jsonify({
            "sku": sku, "item_id": item_id, "periodo": periodo,
            "buckets": [],
            "resumo": {"total_unidades": 0, "total_receita": 0.0, "por_preco": []},
        }), 200

    pedidos = ml_client.get_orders_for_item(
        item_id,
        desde_dt.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
        ate_dt.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
    )

    def _ts(iso: str) -> _dt:
        try:
            d = _dt.fromisoformat(iso.replace("Z", "+00:00"))
            return d.replace(tzinfo=None)
        except Exception:
            return _dt.min

    pedidos_ts = sorted(
        [(_ts(p.get("date_created", "")), p) for p in pedidos],
        key=lambda x: x[0],
    )

    buckets = []
    for i, snap in enumerate(historico):
        ts_atual = snap.coletado_em.replace(tzinfo=None)
        ts_prev  = (
            historico[i - 1].coletado_em.replace(tzinfo=None)
            if i > 0
            else ts_atual - _td(hours=1)
        )

        pedidos_bucket = [
            p for ts, p in pedidos_ts if ts_prev < ts <= ts_atual
        ]

        unidades = 0
        receita  = 0.0
        for p in pedidos_bucket:
            for oi in (p.get("order_items") or []):
                if (oi.get("item") or {}).get("id") == item_id:
                    qty = int(oi.get("quantity") or 0)
                    unidades += qty
                    receita  += float(oi.get("unit_price") or 0) * qty

        preco_medio = round(receita / unidades, 2) if unidades > 0 else None

        buckets.append({
            "ts":             snap.coletado_em.isoformat(),
            "preco_snapshot": snap.preco_atual,
            "preco_medio":    preco_medio,
            "unidades":       unidades,
            "receita":        round(receita, 2),
        })

    total_unidades = sum(b["unidades"] for b in buckets)
    total_receita  = round(sum(b["receita"]  for b in buckets), 2)

    por_preco: dict[float, dict] = {}
    for snap, bucket in zip(historico, buckets):
        chave = round(float(snap.preco_atual), 2)
        if chave not in por_preco:
            por_preco[chave] = {"preco": chave, "unidades": 0, "receita": 0.0}
        por_preco[chave]["unidades"] += bucket["unidades"]
        por_preco[chave]["receita"]   = round(
            por_preco[chave]["receita"] + bucket["receita"], 2
        )

    return jsonify({
        "sku":     sku,
        "item_id": item_id,
        "periodo": periodo,
        "buckets": buckets,
        "resumo": {
            "total_unidades": total_unidades,
            "total_receita":  total_receita,
            "por_preco":      sorted(por_preco.values(), key=lambda x: -x["unidades"]),
        },
    })


@app.route("/api/buybox/sku/<sku>/campanhas")
def buybox_sku_campanhas(sku: str):
    """
    Análise de campanhas do item específico (started + candidate) com o
    mesmo formato do painel de Campanhas — para mostrar dentro do modal
    de detalhe do Buybox, em vez de o usuário trocar de aba.

    Faz consulta LIVE no ML (não usa cache do banco) — assim o usuário vê
    o estado real das campanhas no momento que abriu o detalhe.
    """
    item_id = request.args.get("item_id")
    if not item_id:
        return jsonify({"erro": "informe item_id"}), 400

    cfg = _load_settings()
    rc_min = float(cfg["rc_minimo"])
    skus = pdv.load_skus()
    sku_data = skus.get(sku.upper())
    if not sku_data:
        return jsonify({"erro": f"SKU {sku} não está em skus.yaml"}), 404

    try:
        details = ml_client.get_items_details([item_id])
    except Exception as exc:
        return jsonify({"erro": f"falha ao buscar item: {exc}"}), 500
    if not details:
        return jsonify({"erro": "item não encontrado no ML"}), 404

    rows = _process_item(sku, sku_data, details[0], cfg, rc_min)
    return jsonify({
        "sku":       sku,
        "item_id":   item_id,
        "rc_minimo": rc_min,
        "campanhas": rows,
    })


@app.route("/api/buybox/skus-configurados")
def buybox_skus_configurados():
    """
    Lista os SKUs rastreáveis (lidos de config/skus.yaml) + se já têm
    snapshots. Alimenta o dropdown de seleção para coleta manual.
    """
    skus = pdv.load_skus()  # {SKU: {custo, peso, tipo_anuncio}}
    # SKUs que já foram coletados pelo menos 1 vez
    with buybox_persist.sessao() as s:
        from sqlalchemy import select
        from src.buybox.modelos import Snapshot as _Snap
        coletados = set(
            s.execute(select(_Snap.sku).distinct()).scalars()
        )

    return jsonify({
        "skus": [
            {
                "sku":          sku,
                "tipo_anuncio": dados.get("tipo_anuncio"),
                "ja_coletado":  sku in coletados,
            }
            for sku, dados in sorted(skus.items())
        ],
    })


@app.route("/api/buybox/forcar-coleta", methods=["POST"])
def buybox_forcar_coleta():
    """
    Dispara uma coleta imediata. Útil para operação ("acabei de mudar
    preço, atualize o painel").

    Payload opcional (JSON): {"skus": ["WLK004", "WL008"]}
    """
    from src.buybox import coletor as _coletor

    body = request.get_json(silent=True) or {}
    skus = body.get("skus") or None
    stats = _coletor.coletar(skus_filtro=skus)
    return jsonify(stats)


# ---------------------------------------------------------------------------
# Debug (diagnóstico de campos de campanha devolvidos pela API ML)
# ---------------------------------------------------------------------------

@app.route("/api/debug/raw-campanhas/<item_id>")
def debug_raw_campanhas(item_id: str):
    """
    Retorna o JSON bruto de /seller-promotions/items/{item_id}?app_version=v2
    sem nenhuma transformação, para inspecionar quais chaves a API retorna.
    Útil para diagnosticar por que datas de vigência aparecem vazias.

    Ex.: GET /api/debug/raw-campanhas/MLB1234567890
    """
    _BASE = "https://api.mercadolibre.com"
    try:
        raw = ml_client._request(
            "GET",
            f"{_BASE}/seller-promotions/items/{item_id}",
            params={"app_version": "v2"},
        )
        # Extrai as chaves de cada item da lista para facilitar a leitura
        if isinstance(raw, list):
            summary = [{"id": r.get("id"), "type": r.get("type"),
                        "status": r.get("status"), "keys": list(r.keys())}
                       for r in raw]
        else:
            summary = raw
        return jsonify({"item_id": item_id, "raw": raw, "keys_por_item": summary})
    except Exception as exc:
        return jsonify({"erro": str(exc)}), 500


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Painel de Campanhas ML — servidor local")
    print("Acesse: http://localhost:5000")
    print("Pressione Ctrl+C para encerrar.\n")
    app.run(host="127.0.0.1", port=5000, debug=False)
