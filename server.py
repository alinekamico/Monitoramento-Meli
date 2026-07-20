"""
Servidor local para o painel de campanhas.

Uso:
  python server.py

Depois abra dashboard.html no navegador (ou acesse http://localhost:5050).
"""

from __future__ import annotations

import concurrent.futures
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent))

import yaml
import os
from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_cors import CORS
from flask_login import LoginManager, login_required, current_user  # mantido para o blueprint de auth
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

from src import decisor, margem, ml_client, pdv
from src.buybox import persistencia as buybox_persist

_CONFIG_DIR = Path(__file__).parent / "config"

load_dotenv()
app = Flask(__name__, static_folder=str(Path(__file__).parent), template_folder=str(Path(__file__).parent / "templates"))
CORS(app)
_APP_PREFIX = os.environ.get('APP_PREFIX', '')
if _APP_PREFIX:
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    app.config['APPLICATION_ROOT'] = _APP_PREFIX
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-key-change-in-prod')

# Flask-Login
login_manager = LoginManager(app)
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Por favor, faça login para acessar esta página.'

from src.auth.persistencia import buscar_usuario_por_id
@login_manager.user_loader
def load_user(user_id):
    try:
        return buscar_usuario_por_id(int(user_id))
    except Exception as exc:
        app.logger.warning("user_loader falhou (DB indisponível?): %s", exc)
        return None

from src.auth.rotas import auth_bp
app.register_blueprint(auth_bp)

# ---------------------------------------------------------------------------
# Cache em memória para campanhas (evita re-fetch a cada reload de página)
# ---------------------------------------------------------------------------
# Estrutura: { conta: {"rows": [...], "ts": float, "rc_minimo": float} }
# TTL de 10 minutos — suficiente para a maioria dos ciclos de uso.
# Invalida automaticamente ao chamar com ?force=true.

_CAMPAIGN_CACHE: dict[str, dict] = {}
_CAMPAIGN_CACHE_TTL = 10 * 60   # segundos

# Controle de coleta em background — evita duas coletas simultâneas por conta.
# Estrutura: { conta: threading.Thread }
import threading
_coleta_em_andamento: dict[str, threading.Thread] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _conta_da_request() -> str:
    """Extrai e valida o parâmetro ?conta= do request. Default: best_hair."""
    import yaml as _yaml
    contas_file = _CONFIG_DIR / "contas.yaml"
    with open(contas_file, encoding="utf-8") as f:
        contas_cfg = _yaml.safe_load(f)
    contas_validas = set(contas_cfg.get("contas", {}).keys())
    padrao = contas_cfg.get("conta_padrao", "best_hair")
    conta = request.args.get("conta", padrao)
    return conta if conta in contas_validas else padrao


def _process_item(
    sku: str,
    sku_data: dict,
    item: dict,
    cfg: dict,
    rc_min: float,
    conta: str = "best_hair",
) -> list[dict]:
    item_id      = item.get("id", "")
    listing_id   = item.get("listing_type_id", "")
    tipo_anuncio = "Premium" if listing_id in ("gold_pro", "gold_premium") else sku_data["tipo_anuncio"]
    is_full      = item.get("shipping", {}).get("logistic_type") == "fulfillment"
    item_cfg     = {**cfg, "insumo_fixo": 0.0} if is_full else cfg
    has_stock    = item.get("available_quantity", 0) > 0
    qty          = item.get("available_quantity", 0)

    rows: list[dict] = []

    try:
        campaigns = ml_client.get_campaigns_for_item(item_id, conta=conta)

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

        candidatas = [c for c in campaigns["disponiveis"] if c.get("meli_percentage", 0) > 0]
        for campanha in candidatas:
            if campanha.get("type") == "PRICE_MATCHING":
                preco = campanha.get("price") or 0.0
            else:
                preco = campanha.get("suggested_price") or campanha.get("min_price") or campanha.get("price") or 0.0

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
@login_required
def index():
    return render_template("dashboard.html", app_prefix=os.environ.get("APP_PREFIX", ""))


@app.route("/api/health")
def health():
    return jsonify({"ok": True})


@app.route("/api/contas")
def listar_contas():
    """Lista as contas configuradas (para o seletor do dashboard)."""
    return jsonify(ml_client.listar_contas())


@app.route("/api/skus", methods=["GET", "PUT"])
def gerenciar_skus():
    """
    GET → lista todos os SKUs com custo, peso e tipo_anuncio.
    PUT → atualiza custo, peso e/ou tipo_anuncio de um ou mais SKUs em skus.yaml.

    Payload PUT (JSON):
      [{"sku": "WLK004", "custo": 145.0, "peso": 2.0, "tipo_anuncio": "Clássico"}, ...]

    Apenas os campos enviados são atualizados (merge parcial por SKU).
    """
    skus_path = _CONFIG_DIR / "skus.yaml"

    if request.method == "GET":
        skus = pdv.load_skus()
        return jsonify([
            {
                "sku":          sku,
                "nome":         data.get("nome", ""),
                "custo":        round(float(data.get("custo", 0)), 2),
                "peso":         round(float(data.get("peso", 0)), 3),
                "tipo_anuncio": data.get("tipo_anuncio", "Clássico"),
            }
            for sku, data in sorted(skus.items())
        ])

    # PUT — valida e persiste
    items = request.get_json(silent=True, force=True)
    if not isinstance(items, list) or not items:
        return jsonify({"erro": "esperado array de SKUs"}), 400

    erros = []
    for it in items:
        sku = str(it.get("sku", "")).upper()
        if not sku:
            erros.append("sku ausente em um item")
            continue
        for campo in ("custo", "peso"):
            v = it.get(campo)
            if v is not None:
                try:
                    float(v)
                except (TypeError, ValueError):
                    erros.append(f"{sku}.{campo} deve ser numero")
        tipo = it.get("tipo_anuncio")
        if tipo is not None and tipo not in ("Clássico", "Premium"):
            erros.append(f"{sku}.tipo_anuncio invalido: '{tipo}'")
    if erros:
        return jsonify({"erro": "; ".join(erros)}), 400

    # Lê YAML preservando estrutura e demais campos
    with open(skus_path, encoding="utf-8") as f:
        yaml_doc = yaml.safe_load(f) or {}
    skus_yaml = yaml_doc.get("skus", {})

    for it in items:
        sku = str(it["sku"]).upper()
        entrada = skus_yaml.setdefault(sku, {})
        if "custo" in it:
            entrada["custo"] = round(float(it["custo"]), 2)
        if "peso" in it:
            entrada["peso"] = round(float(it["peso"]), 3)
        if "tipo_anuncio" in it:
            entrada["tipo_anuncio"] = it["tipo_anuncio"]
        if "nome" in it:
            nome_val = str(it["nome"]).strip()
            if nome_val:
                entrada["nome"] = nome_val
            elif "nome" in entrada:
                del entrada["nome"]

    yaml_doc["skus"] = skus_yaml
    with open(skus_path, "w", encoding="utf-8") as f:
        yaml.dump(yaml_doc, f, allow_unicode=True,
                  default_flow_style=False, sort_keys=True)

    # Invalida cache de campanhas (custo/tipo mudou → RC muda)
    _CAMPAIGN_CACHE.clear()

    return jsonify({"ok": True, "atualizados": len(items)})


@app.route("/api/skus/<sku_id>", methods=["DELETE"])
@login_required
def deletar_sku(sku_id: str):
    """Remove permanentemente um SKU de config/skus.yaml."""
    sku_id = sku_id.upper()
    skus_path = _CONFIG_DIR / "skus.yaml"

    with open(skus_path, encoding="utf-8") as f:
        yaml_doc = yaml.safe_load(f) or {}
    skus_yaml = yaml_doc.get("skus", {})

    if sku_id not in skus_yaml:
        return jsonify({"erro": f"SKU {sku_id} não encontrado"}), 404

    del skus_yaml[sku_id]
    yaml_doc["skus"] = skus_yaml

    with open(skus_path, "w", encoding="utf-8") as f:
        yaml.dump(yaml_doc, f, allow_unicode=True,
                  default_flow_style=False, sort_keys=True)

    # Invalida cache (SKU removido → resumo muda)
    _CAMPAIGN_CACHE.clear()

    return jsonify({"ok": True, "removido": sku_id})


@app.route("/api/rc-minimo", methods=["GET", "PUT"])
def rc_minimo():
    """
    GET  → devolve o RC mínimo atual (float).
    PUT  → atualiza rc_minimo em settings.yaml e invalida o cache de campanhas.

    Payload PUT (JSON): {"rc_minimo": 65.0}
    """
    settings_path = _CONFIG_DIR / "settings.yaml"

    if request.method == "GET":
        cfg = _load_settings()
        return jsonify({"rc_minimo": float(cfg.get("rc_minimo", 60.0))})

    # PUT — valida e persiste
    body = request.get_json(silent=True) or {}
    novo = body.get("rc_minimo")
    if novo is None:
        return jsonify({"erro": "campo rc_minimo obrigatorio"}), 400
    try:
        novo = float(novo)
    except (TypeError, ValueError):
        return jsonify({"erro": "rc_minimo deve ser um numero"}), 400
    if not (0.0 <= novo <= 300.0):
        return jsonify({"erro": "rc_minimo fora do intervalo valido (0–300)"}), 400

    # Lê, atualiza e grava o YAML preservando o resto da configuração
    try:
        with open(settings_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        cfg["rc_minimo"] = novo
        with open(settings_path, "w", encoding="utf-8") as f:
            yaml.dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
    except Exception as exc:
        app.logger.exception("Erro ao gravar settings.yaml: %s", exc)
        return jsonify({"erro": f"Falha ao salvar configuração: {exc}"}), 500

    # Invalida cache de campanhas de todas as contas (RC mudou → summary muda)
    _CAMPAIGN_CACHE.clear()

    return jsonify({"rc_minimo": novo, "ok": True})


@app.route("/api/campaigns")
def get_campaigns():
    conta  = _conta_da_request()
    force  = request.args.get("force", "").lower() in ("1", "true", "yes")
    cfg    = _load_settings()
    rc_min = float(cfg["rc_minimo"])

    # ---- Cache hit ----
    entrada = _CAMPAIGN_CACHE.get(conta)
    if (
        not force
        and entrada is not None
        and (time.time() - entrada["ts"]) < _CAMPAIGN_CACHE_TTL
    ):
        entrada["from_cache"] = True
        return jsonify(entrada)

    # ---- Fetch live ----
    skus      = pdv.load_skus()
    seller_id = ml_client.get_seller_id(conta)

    def _processar_sku(sku: str, sku_data: dict) -> list[dict]:
        """Coleta e processa um SKU inteiro (busca + detalhes + campanhas)."""
        item_ids = ml_client.get_item_ids_by_sku(seller_id, sku, conta=conta)
        if not item_ids:
            return []
        items = ml_client.get_items_details(item_ids, conta=conta)
        rows: list[dict] = []
        for item in items:
            rows.extend(_process_item(sku, sku_data, item, cfg, rc_min, conta=conta))
        return rows

    # Paralelo com 3 workers — equilibrio entre velocidade e rate-limit do ML.
    # Com 6+ workers o endpoint /seller-promotions retorna 500 em rajadas e
    # os retries de 2 s tornam o total mais lento do que sequencial.
    all_rows: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futuros = {
            pool.submit(_processar_sku, sku, sku_data): sku
            for sku, sku_data in skus.items()
        }
        for fut in concurrent.futures.as_completed(futuros):
            try:
                all_rows.extend(fut.result())
            except Exception:
                pass   # erro já capturado dentro de _process_item

    n_ativas     = sum(1 for r in all_rows if r.get("status") == "ATIVA")
    n_candidatas = sum(1 for r in all_rows if r.get("status") == "CANDIDATA")
    n_aceitar    = sum(1 for r in all_rows if r.get("decisao") == "ACEITAR")
    n_recusar    = sum(1 for r in all_rows if r.get("decisao") == "RECUSAR")
    n_erros      = sum(1 for r in all_rows if r.get("status") == "ERRO")

    resposta = {
        "results":     all_rows,
        "rc_minimo":   rc_min,
        "timestamp":   time.time(),
        "from_cache":  False,
        "summary": {
            "total":      len(all_rows),
            "ativas":     n_ativas,
            "candidatas": n_candidatas,
            "aceitar":    n_aceitar,
            "recusar":    n_recusar,
            "erros":      n_erros,
        },
    }
    _CAMPAIGN_CACHE[conta] = {**resposta, "ts": time.time()}
    return jsonify(resposta)


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


@app.route("/api/calcular-pdv", methods=["POST"])
def calcular_pdv():
    """
    Calcula o breakdown do PDV para o Simulador de PDV.

    Payload JSON:
      { "preco": 147.0, "custo": 54.3, "rebate": 12.0,
        "peso": 1.5, "tipo_anuncio": "Clássico" }
    """
    body = request.get_json(silent=True) or {}
    try:
        preco        = float(body["preco"])
        custo        = float(body["custo"])
        rebate       = float(body.get("rebate", 0))
        peso         = float(body["peso"])
        tipo_anuncio = str(body.get("tipo_anuncio", "Clássico"))
        modalidade   = str(body.get("modalidade", "Normal"))
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"erro": f"parâmetro inválido: {exc}"}), 400

    if preco <= 0 or custo <= 0:
        return jsonify({"erro": "preco e custo devem ser positivos"}), 400
    if tipo_anuncio not in ("Clássico", "Premium"):
        return jsonify({"erro": "tipo_anuncio deve ser 'Clássico' ou 'Premium'"}), 400
    if modalidade not in ("Normal", "Full", "Super Full"):
        return jsonify({"erro": "modalidade deve ser 'Normal', 'Full' ou 'Super Full'"}), 400

    cfg = _load_settings()
    m = margem.calcular_margem(
        preco_campanha=preco,
        custo=custo,
        rebate=rebate,
        peso=peso,
        tipo_anuncio=tipo_anuncio,
        modalidade=modalidade,
        cfg=cfg,
    )
    return jsonify({**m, "rc_minimo": float(cfg.get("rc_minimo", 60.0))})


@app.route("/api/buybox/lista")
def buybox_lista():
    """Último snapshot de cada SKU para a tabela da aba Buybox."""
    conta = _conta_da_request()
    por_sku = buybox_persist.ultimo_snapshot_por_sku(conta)

    # Múltiplos MLBs por SKU? Buscamos o último de cada par (sku, item_id).
    with buybox_persist.sessao(conta) as s:
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
    conta   = _conta_da_request()
    item_id = request.args.get("item_id")
    periodo = (request.args.get("periodo") or "24h").lower()
    desde_str = request.args.get("desde")
    ate_str = request.args.get("ate")

    desde_dt = None
    ate_dt = None
    if desde_str or ate_str:
        try:
            if desde_str:
                desde_dt = _dt.fromisoformat(desde_str)
            if ate_str:
                ate_dt = _dt.fromisoformat(ate_str)
                if "T" not in ate_str:
                    ate_dt = ate_dt.replace(hour=23, minute=59, second=59)
        except ValueError:
            return jsonify({"erro": "datas inválidas (use ISO 8601)"}), 400
        historico = buybox_persist.snapshots_periodo(
            sku, item_id=item_id, desde=desde_dt, ate=ate_dt, conta=conta,
        )
    else:
        horas = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}.get(periodo, 24)
        historico = buybox_persist.snapshots_periodo(
            sku, item_id=item_id, horas=horas, conta=conta,
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
    conta   = _conta_da_request()
    alertas = buybox_persist.alertas_recentes(sku, dias=7, conta=conta)
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
    conta   = _conta_da_request()
    item_id = request.args.get("item_id")
    if not item_id:
        return jsonify({"erro": "informe item_id"}), 400

    cfg = _load_settings()
    cep = (cfg.get("buybox") or {}).get("cep_referencia", "01310100")

    historico = buybox_persist.snapshots_24h(sku, item_id=item_id, conta=conta)
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

        prazo = ml_client.get_prazo_entrega_dias(item_concorrente, cep, conta=conta)
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

    conta   = _conta_da_request()
    item_id = request.args.get("item_id")
    if not item_id:
        return jsonify({"erro": "informe item_id"}), 400

    periodo = (request.args.get("periodo") or "7d").lower()
    horas   = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}.get(periodo, 24 * 7)

    # Usa fuso horário de Brasília (UTC-3) para formatar os timestamps
    # corretamente na API do ML. Usar timezone.utc com sufixo -03:00 causaria
    # janela 3h deslocada (UTC 18h formatado como -03:00 = 21h UTC para o ML).
    BRT      = timezone(_td(hours=-3))
    ate_dt   = _dt.now(BRT)
    desde_dt = ate_dt - _td(hours=horas)

    historico = buybox_persist.snapshots_periodo(sku, item_id=item_id, horas=horas,
                                                  conta=conta)
    if not historico:
        return jsonify({
            "sku": sku, "item_id": item_id, "periodo": periodo,
            "buckets": [],
            "resumo": {"total_unidades": 0, "total_receita": 0.0, "por_preco": []},
        }), 200

    max_p = {"24h": 100, "7d": 500, "30d": 1500}.get(periodo, 500)
    pedidos = ml_client.get_orders_for_item(
        item_id,
        desde_dt.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
        ate_dt.strftime("%Y-%m-%dT%H:%M:%S.000-03:00"),
        max_pedidos=max_p,
        conta=conta,
    )

    def _ts(iso: str) -> _dt:
        """
        Converte timestamp ISO (com ou sem fuso) para datetime UTC naive.

        Os snapshots são armazenados como UTC naive no MySQL. Os pedidos do
        ML chegam com offset BRT (-03:00), então "2026-06-08T12:00:00-03:00"
        precisa ser normalizado para UTC (15:00) antes da comparação com
        os timestamps dos snapshots. Sem essa normalização, os pedidos das
        janelas horárias nunca coincidiam e a contagem ficava sempre zero.
        """
        try:
            d = _dt.fromisoformat(iso.replace("Z", "+00:00"))
            if d.tzinfo is not None:
                # Converte para UTC e remove o fuso — mesma base dos snapshots
                return d.astimezone(timezone.utc).replace(tzinfo=None)
            return d  # já naive, assume UTC (compatibilidade)
        except Exception:
            return _dt.min

    desde_utc_naive = desde_dt.astimezone(timezone.utc).replace(tzinfo=None)
    ate_utc_naive   = ate_dt.astimezone(timezone.utc).replace(tzinfo=None)

    pedidos_ts = sorted(
        [
            (ts, p)
            for p in pedidos
            for ts in [_ts(p.get("date_created", ""))]
            if desde_utc_naive <= ts <= ate_utc_naive
        ],
        key=lambda x: x[0],
    )

    # Agrupa pedidos por slot fixo: hora (≤48h) ou dia (>48h)
    _por_hora = horas <= 48
    from collections import defaultdict as _dd

    def _slot(dt: _dt) -> tuple:
        return (dt.year, dt.month, dt.day, dt.hour) if _por_hora else (dt.year, dt.month, dt.day)

    vendas_slot: dict = _dd(lambda: {"unidades": 0, "receita": 0.0})
    for ts, p in pedidos_ts:
        key = _slot(ts)
        for oi in (p.get("order_items") or []):
            if (oi.get("item") or {}).get("id") == item_id:
                qty = int(oi.get("quantity") or 0)
                vendas_slot[key]["unidades"] += qty
                vendas_slot[key]["receita"]  += float(oi.get("unit_price") or 0) * qty

    # Preços: um ponto por snapshot (todos do período)
    precos_list = [
        {"ts": snap.coletado_em.isoformat(), "valor": float(snap.preco_atual)}
        for snap in historico
    ]

    # Vendas: um ponto por slot com dados
    vendas_list = []
    for key, v in sorted(vendas_slot.items()):
        if _por_hora:
            yr, mo, dy, hr = key
            ts_slot = _dt(yr, mo, dy, hr, 0, 0).isoformat()
        else:
            yr, mo, dy = key
            ts_slot = _dt(yr, mo, dy, 12, 0, 0).isoformat()
        vendas_list.append({
            "ts":       ts_slot,
            "unidades": v["unidades"],
            "receita":  round(v["receita"], 2),
        })

    total_unidades = sum(v["unidades"] for v in vendas_list)
    total_receita  = round(sum(v["receita"] for v in vendas_list), 2)

    # por_preco: agrega vendas pelo preço do snapshot mais recente do mesmo slot
    preco_do_slot: dict = {}
    for snap in historico:
        preco_do_slot[_slot(snap.coletado_em.replace(tzinfo=None))] = float(snap.preco_atual)

    por_preco: dict = {}
    for key, v in vendas_slot.items():
        if v["unidades"] == 0:
            continue
        chave = round(preco_do_slot.get(key, 0.0), 2)
        if chave not in por_preco:
            por_preco[chave] = {"preco": chave, "unidades": 0, "receita": 0.0}
        por_preco[chave]["unidades"] += v["unidades"]
        por_preco[chave]["receita"]   = round(por_preco[chave]["receita"] + v["receita"], 2)

    return jsonify({
        "sku":     sku,
        "item_id": item_id,
        "periodo": periodo,
        "precos":  precos_list,
        "vendas":  vendas_list,
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
    conta   = _conta_da_request()
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
        details = ml_client.get_items_details([item_id], conta=conta)
    except Exception as exc:
        return jsonify({"erro": f"falha ao buscar item: {exc}"}), 500
    if not details:
        return jsonify({"erro": "item não encontrado no ML"}), 404

    rows = _process_item(sku, sku_data, details[0], cfg, rc_min, conta=conta)
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
    conta = _conta_da_request()
    skus = pdv.load_skus()  # {SKU: {custo, peso, tipo_anuncio}}
    # SKUs que já foram coletados pelo menos 1 vez
    with buybox_persist.sessao(conta) as s:
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
    Dispara coleta imediata em background e retorna imediatamente.

    Retorna 202 (accepted) ao iniciar ou 409 (conflict) se já houver uma
    coleta em andamento para a mesma conta.

    Payload opcional (JSON): {"skus": ["WLK004", "WL008"]}
    """
    from src.buybox import coletor as _coletor

    conta = _conta_da_request()

    # Bloqueia dupla coleta simultânea para a mesma conta
    t = _coleta_em_andamento.get(conta)
    if t is not None and t.is_alive():
        return jsonify({"status": "em_andamento",
                        "mensagem": "Coleta já em andamento para esta conta."}), 409

    body = request.get_json(silent=True) or {}
    skus = body.get("skus") or None

    def _rodar():
        try:
            _coletor.coletar(skus_filtro=skus, conta=conta)
        finally:
            _coleta_em_andamento.pop(conta, None)

    thread = threading.Thread(target=_rodar, daemon=True, name=f"coleta-{conta}")
    _coleta_em_andamento[conta] = thread
    thread.start()

    n_skus = len(skus) if skus else 23
    return jsonify({"status": "iniciada",
                    "mensagem": f"Coleta de {n_skus} SKU(s) iniciada em background.",
                    "conta": conta}), 202


# ---------------------------------------------------------------------------
# OAuth callback — captura o code do fluxo de autorização ML
# ---------------------------------------------------------------------------

@app.route("/oauth/callback")
def oauth_callback():
    """
    Redirect URI usada no cadastro do App ML.
    Captura o ?code= e exibe na tela para o script gerar_tokens.py.

    Cadastre no painel de Dev ML:
      http://localhost:5000/oauth/callback
    """
    code  = request.args.get("code", "")
    erro  = request.args.get("error", "")
    descr = request.args.get("error_description", "")

    if erro:
        return f"""
        <html><body style="font-family:sans-serif;padding:40px;background:#1a1d27;color:#e2e8f0">
          <h2 style="color:#ef4444">❌ Erro na autorização</h2>
          <p><b>{erro}</b>: {descr}</p>
          <p>Tente novamente.</p>
        </body></html>
        """, 400

    if not code:
        return f"""
        <html><body style="font-family:sans-serif;padding:40px;background:#1a1d27;color:#e2e8f0">
          <h2 style="color:#f97316">⚠️ Nenhum código recebido</h2>
          <p>Parâmetro <code>code</code> não encontrado na URL.</p>
        </body></html>
        """, 400

    return f"""
    <html><body style="font-family:sans-serif;padding:40px;background:#1a1d27;color:#e2e8f0">
      <h2 style="color:#22c55e">✅ Autorização concluída!</h2>
      <p>Copie o código abaixo e cole no terminal onde o script está esperando:</p>
      <pre style="background:#0f1117;padding:20px;border-radius:8px;
                  font-size:16px;color:#facc15;word-break:break-all;
                  border:1px solid #2a2d3a">{code}</pre>
      <p style="color:#64748b;font-size:13px">
        Você pode fechar esta aba depois de copiar.
      </p>
    </body></html>
    """


# ---------------------------------------------------------------------------
# Debug (diagnóstico de campos de campanha devolvidos pela API ML)
# ---------------------------------------------------------------------------

@app.route("/api/debug/raw-orders/<item_id>")
def debug_raw_orders(item_id: str):
    """
    Testa o endpoint /orders/search para o item diretamente, expondo o erro
    real (403 de escopo ausente, 400 de parâmetro errado, etc.).

    Ex.: GET /api/debug/raw-orders/MLB1234567890
    """
    from datetime import datetime as _dt, timezone, timedelta as _td
    conta     = _conta_da_request()
    seller_id = ml_client.get_seller_id(conta)
    BRT       = timezone(_td(hours=-3))
    ate_dt    = _dt.now(BRT)
    desde_dt  = ate_dt - _td(hours=24 * 7)
    import requests as _req
    params = {
        "seller":            seller_id,
        "q":                 item_id,
        "sort":              "date_desc",
        "limit":             5,
        "offset":            0,
    }
    try:
        resp = _req.get(
            "https://api.mercadolibre.com/orders/search",
            headers=ml_client._auth_headers(conta),
            params=params,
            timeout=20,
        )
        return jsonify({
            "item_id":     item_id,
            "seller_id":   seller_id,
            "status_code": resp.status_code,
            "raw":         resp.json(),
        })
    except Exception as exc:
        return jsonify({"erro": str(exc), "item_id": item_id, "seller_id": seller_id}), 500


@app.route("/api/debug/raw-campanhas/<item_id>")
def debug_raw_campanhas(item_id: str):
    """
    Retorna o JSON bruto de /seller-promotions/items/{item_id}?app_version=v2
    sem nenhuma transformação, para inspecionar quais chaves a API retorna.
    Útil para diagnosticar por que datas de vigência aparecem vazias.

    Ex.: GET /api/debug/raw-campanhas/MLB1234567890
    """
    conta = _conta_da_request()
    _BASE = "https://api.mercadolibre.com"
    try:
        raw = ml_client._request(
            "GET",
            f"{_BASE}/seller-promotions/items/{item_id}",
            conta=conta,
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

@app.route("/usuarios")
@login_required
def usuarios_page():
    if not current_user.is_authenticated or current_user.perfil != 'admin':
        return redirect(url_for('auth.login'))
    return render_template('usuarios.html', app_prefix=os.environ.get('APP_PREFIX', ''))


if __name__ == "__main__":
    print("Painel de Campanhas ML — servidor local")
    print("Acesse: http://localhost:5000")
    print("Pressione Ctrl+C para encerrar.\n")
    app.run(host="0.0.0.0", port=5050, debug=False)
