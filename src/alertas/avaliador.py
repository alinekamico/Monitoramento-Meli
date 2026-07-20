"""
Orquestrador de alertas do MVP Buybox.

Responsabilidades:
  - Para cada SKU/MLB com snapshots novos, compara contra os anteriores
    e aplica as regras A1/A2/A3
  - Antes de enviar, consulta tabela `alertas` para respeitar cooldown
  - Persiste todo alerta (enviado ou suprimido) com motivo no campo `dados`
  - Suporta `dry_run`: registra mas não envia

Funções públicas:
  - avaliar_criticos_pendentes(cfg, dry_run) → stats
  - enviar_resumo_diario(cfg, dry_run, data_referencia=None) → stats
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml
from sqlalchemy import select

from ..buybox import persistencia
from ..buybox.modelos import (
    Alerta,
    Snapshot,
    TIPO_A1_PERDI_BUYBOX,
    TIPO_A2_AMEACA,
    TIPO_A3_OPORTUNIDADE,
    TIPO_B1_PROBLEMA,
    TIPO_B2_MARGEM_BAIXA,
    TIPO_B3_OPORTUNIDADE_SUBIR,
    TIPO_C1_CAMPANHAS_ACEITAR,
)
from . import email as email_mod
from . import regras, templates


_log = logging.getLogger("buybox.alertas")
_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"


_COOLDOWNS_HORAS = {
    TIPO_A1_PERDI_BUYBOX:   "cooldown_a1_horas",
    TIPO_A2_AMEACA:         "cooldown_a2_horas",
    TIPO_A3_OPORTUNIDADE:   "cooldown_a3_horas",
}


def _carregar_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================
# Cooldown
# ============================================================


def _em_cooldown(sku: str, tipo: str, cooldown_horas: int,
                 conta: str = "best_hair") -> bool:
    """Há alerta do mesmo (sku, tipo) enviado nas últimas `cooldown_horas`?"""
    if cooldown_horas <= 0:
        return False
    ultimo = persistencia.ultimo_alerta_enviado(sku, tipo, conta=conta)
    if ultimo is None or ultimo.enviado_em is None:
        return False
    # Datetime do SQLite vem naive — comparamos sem tz
    agora = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    enviado_naive = ultimo.enviado_em.replace(tzinfo=None) if ultimo.enviado_em.tzinfo else ultimo.enviado_em
    return (agora - enviado_naive) < timedelta(hours=cooldown_horas)


# ============================================================
# Busca dos snapshots para avaliação
# ============================================================


def _trio_snapshots(sku: str, item_id: str,
                    conta: str = "best_hair") -> tuple[Optional[Snapshot], Optional[Snapshot], Optional[Snapshot]]:
    """
    Devolve (novo, anterior, ante_anterior) — os 3 últimos snapshots do
    par (sku, item_id), do mais novo para o mais antigo.
    """
    with persistencia.sessao(conta) as s:
        stmt = (
            select(Snapshot)
            .where(Snapshot.sku == sku, Snapshot.item_id == item_id)
            .order_by(Snapshot.coletado_em.desc())
            .limit(3)
        )
        rows = list(s.execute(stmt).scalars())
        for r in rows:
            _ = r.concorrentes
    novo = rows[0] if len(rows) >= 1 else None
    anterior = rows[1] if len(rows) >= 2 else None
    ante_anterior = rows[2] if len(rows) >= 3 else None
    return novo, anterior, ante_anterior


def _pares_para_avaliar(conta: str = "best_hair") -> list[tuple[str, str]]:
    """Todos os pares (sku, item_id) que têm pelo menos 1 snapshot."""
    with persistencia.sessao(conta) as s:
        stmt = select(Snapshot.sku, Snapshot.item_id).distinct()
        return [(sku, item_id) for sku, item_id in s.execute(stmt).all()]


# ============================================================
# Envio + persistência
# ============================================================


def _disparar(
    pendente: regras.AlertaPendente,
    cfg_email: dict,
    dry_run: bool,
    conta: str = "best_hair",
) -> dict:
    """
    Envia o alerta (se possível) e registra na tabela `alertas`.

    Retorna {enviado: bool, motivo_supressao: str|None}.
    """
    enviado = False
    motivo_sup: Optional[str] = None

    if dry_run:
        motivo_sup = "dry_run"
    elif not cfg_email.get("enabled"):
        motivo_sup = "email_desabilitado"
    else:
        try:
            assunto, html = templates.renderizar_critico(
                pendente.tipo, pendente.sku, pendente.item_id, pendente.dados,
            )
            email_mod.enviar_email(assunto, html, cfg_email)
            enviado = True
        except email_mod.EmailDesabilitado:
            motivo_sup = "email_desabilitado"
        except email_mod.CredenciaisFaltando as exc:
            motivo_sup = f"credenciais: {exc}"
        except Exception as exc:
            motivo_sup = f"erro_smtp: {exc.__class__.__name__}: {exc}"

    dados_persist = dict(pendente.dados)
    dados_persist["motivo"] = pendente.motivo
    dados_persist["titulo_curto"] = pendente.titulo_curto
    if motivo_sup:
        dados_persist["motivo_supressao"] = motivo_sup

    persistencia.registrar_alerta(
        sku=pendente.sku, item_id=pendente.item_id, tipo=pendente.tipo,
        dados=dados_persist, enviado=enviado, conta=conta,
    )
    return {"enviado": enviado, "motivo_supressao": motivo_sup}


# ============================================================
# API pública — críticos
# ============================================================


def avaliar_criticos_pendentes(
    cfg: Optional[dict] = None,
    dry_run: Optional[bool] = None,
    conta: str = "best_hair",
) -> dict:
    """
    Roda regras A1/A2/A3 sobre todos os SKUs com snapshots.

    Lógica:
      1. Para cada (sku, item_id) com >= 2 snapshots, busca os 3 últimos
      2. Aplica `regras.avaliar_criticos`
      3. Para cada pendente, checa cooldown e dispara/registra

    `dry_run` override: se None, usa `settings.dry_run`.
    """
    cfg = cfg or _carregar_settings()
    if dry_run is None:
        dry_run = bool(cfg.get("dry_run", True))
    cfg_buybox = cfg.get("buybox", {}) or {}
    cfg_email = (cfg_buybox.get("email") or {}).copy()

    stats = {
        "pendentes_detectados": 0,
        "enviados":             0,
        "suprimidos_cooldown":  0,
        "suprimidos_dryrun":    0,
        "suprimidos_email_off": 0,
        "erros_smtp":           0,
        "por_tipo":             {"A1": 0, "A2": 0, "A3": 0},
    }

    for sku, item_id in _pares_para_avaliar(conta=conta):
        novo, anterior, ante_anterior = _trio_snapshots(sku, item_id, conta=conta)
        pendentes = regras.avaliar_criticos(
            novo=novo, anterior=anterior, ante_anterior=ante_anterior,
            cfg_buybox=cfg_buybox,
        )

        for p in pendentes:
            stats["pendentes_detectados"] += 1
            stats["por_tipo"][p.tipo] = stats["por_tipo"].get(p.tipo, 0) + 1

            chave = _COOLDOWNS_HORAS.get(p.tipo, "")
            cooldown_h = int(cfg_buybox.get(chave, 0))
            if _em_cooldown(p.sku, p.tipo, cooldown_h, conta=conta):
                stats["suprimidos_cooldown"] += 1
                # Mesmo suprimido, registramos para auditoria
                persistencia.registrar_alerta(
                    sku=p.sku, item_id=p.item_id, tipo=p.tipo,
                    dados={**p.dados, "motivo": p.motivo,
                           "motivo_supressao": "cooldown"},
                    enviado=False, conta=conta,
                )
                _log.info("alerta %s suprimido por cooldown sku=%s",
                          p.tipo, p.sku)
                continue

            r = _disparar(p, cfg_email, dry_run, conta=conta)
            if r["enviado"]:
                stats["enviados"] += 1
                _log.info("alerta %s enviado sku=%s", p.tipo, p.sku)
            else:
                ms = r["motivo_supressao"] or "desconhecido"
                if ms == "dry_run":
                    stats["suprimidos_dryrun"] += 1
                elif ms == "email_desabilitado":
                    stats["suprimidos_email_off"] += 1
                else:
                    stats["erros_smtp"] += 1
                    _log.warning("falha ao enviar %s sku=%s motivo=%s",
                                 p.tipo, p.sku, ms)
    return stats


# ============================================================
# API pública — resumo diário
# ============================================================


def enviar_resumo_diario(
    cfg: Optional[dict] = None,
    dry_run: Optional[bool] = None,
    data_referencia: Optional[datetime] = None,
    conta: str = "best_hair",
) -> dict:
    """
    Gera resumo do dia e envia e-mail único.

    Cada item das listas B1/B2/B3 também vira um registro na tabela
    `alertas` (mesmo formato dos críticos) para historificar.
    """
    cfg = cfg or _carregar_settings()
    if dry_run is None:
        dry_run = bool(cfg.get("dry_run", True))
    cfg_buybox = cfg.get("buybox", {}) or {}
    cfg_email = (cfg_buybox.get("email") or {}).copy()

    base = data_referencia or datetime.now(timezone.utc)
    snaps = persistencia.snapshots_do_dia(referencia=base, conta=conta)
    resumo = regras.avaliar_resumo_diario(snaps, cfg_buybox)

    # Registra cada item das listas como linha em `alertas`
    for tipo, lista in [
        (TIPO_B1_PROBLEMA,           resumo["b1_problemas"]),
        (TIPO_B2_MARGEM_BAIXA,       resumo["b2_margem_baixa"]),
        (TIPO_B3_OPORTUNIDADE_SUBIR, resumo["b3_oportunidades"]),
    ]:
        for item in lista:
            persistencia.registrar_alerta(
                sku=item["sku"], item_id=item.get("item_id", ""),
                tipo=tipo, dados=item, enviado=False, conta=conta,
            )

    total = (
        len(resumo["b1_problemas"])
        + len(resumo["b2_margem_baixa"])
        + len(resumo["b3_oportunidades"])
    )

    enviado = False
    motivo_sup: Optional[str] = None
    if total == 0:
        motivo_sup = "sem_itens"
    elif dry_run:
        motivo_sup = "dry_run"
    elif not cfg_email.get("enabled"):
        motivo_sup = "email_desabilitado"
    else:
        assunto, html = templates.template_resumo_diario(resumo, base)
        try:
            email_mod.enviar_email(assunto, html, cfg_email)
            enviado = True
        except email_mod.CredenciaisFaltando as exc:
            motivo_sup = f"credenciais: {exc}"
        except Exception as exc:
            motivo_sup = f"erro_smtp: {exc.__class__.__name__}: {exc}"

    return {
        "b1": len(resumo["b1_problemas"]),
        "b2": len(resumo["b2_margem_baixa"]),
        "b3": len(resumo["b3_oportunidades"]),
        "total_itens":  total,
        "enviado":      enviado,
        "motivo_supressao": motivo_sup,
    }


# ============================================================
# API pública — campanhas C1
# ============================================================

_C1_SENTINEL_SKU = "__campanhas__"


def _c1_campanhas_enviadas(conta: str) -> set[tuple[str, str, float]]:
    """
    Retorna conjunto de (item_id, campanha_id, rebate_valor) já incluídos
    em e-mails C1 enviados com sucesso.

    O rebate_valor faz parte da chave: se o ML atualizar o valor do rebate
    na mesma campanha, a combinação muda e o usuário é notificado novamente.
    """
    enviadas: set[tuple[str, str, float]] = set()
    try:
        with persistencia.sessao(conta) as s:
            stmt = (
                select(Alerta)
                .where(
                    Alerta.tipo == TIPO_C1_CAMPANHAS_ACEITAR,
                    Alerta.enviado_em.is_not(None),
                )
            )
            for alerta in s.execute(stmt).scalars():
                try:
                    dados = json.loads(alerta.dados or "{}")
                    for c in dados.get("campanhas", []):
                        cid    = c.get("campanha_id") or ""
                        iid    = c.get("item_id") or ""
                        rebate = round(float(c.get("rebate") or 0), 2)
                        if cid and iid:
                            enviadas.add((iid, cid, rebate))
                except Exception:
                    pass
    except Exception:
        pass
    return enviadas


def avaliar_campanhas_aceitar(
    cfg: Optional[dict] = None,
    dry_run: Optional[bool] = None,
    conta: str = "best_hair",
) -> dict:
    """
    Verifica campanhas de rebate (meli_percentage > 0) com RC ≥ mínimo
    disponíveis para aceitar em todos os SKUs monitorados.

    Cada campanha é rastreada pelo seu ID: uma vez notificada, não
    reaparece em e-mails futuros. O e-mail só é enviado quando há
    campanhas genuinamente novas.

    Retorna dict com:
      campanhas_aceitar  — quantidade de campanhas novas encontradas
      enviado            — se o e-mail foi de fato disparado
      motivo_supressao   — razão caso não tenha sido enviado
    """
    from ..ml_client import get_seller_id, get_item_ids_by_sku, get_campaigns_for_item
    from ..margem import calcular_margem
    from ..decisor import decidir
    from pathlib import Path
    import yaml as _yaml

    cfg = cfg or _carregar_settings()
    if dry_run is None:
        dry_run = bool(cfg.get("dry_run", True))

    cfg_buybox = cfg.get("buybox", {}) or {}
    cfg_email  = (cfg_buybox.get("email") or {}).copy()
    rc_min     = float(cfg.get("rc_minimo", 60.0))

    ja_notificadas = _c1_campanhas_enviadas(conta)

    skus_path = Path(__file__).parent.parent.parent / "config" / "skus.yaml"
    with open(skus_path, encoding="utf-8") as f:
        doc = _yaml.safe_load(f) or {}
    skus: dict = doc.get("skus", {})

    if not skus:
        return {"campanhas_aceitar": 0, "enviado": False,
                "motivo_supressao": "sem_skus"}

    try:
        seller_id = get_seller_id(conta=conta)
    except Exception as exc:
        _log.warning("C1: não foi possível obter seller_id para conta=%s — %s", conta, exc)
        return {"campanhas_aceitar": 0, "enviado": False,
                "motivo_supressao": f"sem_seller_id: {exc}"}

    itens_aceitar: list[dict] = []
    total_encontradas = 0  # ACEITAR antes do filtro de dedup

    for sku, sku_data in skus.items():
        try:
            item_ids = get_item_ids_by_sku(seller_id, sku, conta=conta)
        except Exception as exc:
            _log.warning("C1: erro ao obter item_ids sku=%s — %s", sku, exc)
            continue

        for item_id in item_ids:
            try:
                campaigns = get_campaigns_for_item(item_id, conta=conta)
            except Exception as exc:
                _log.warning("C1: erro ao buscar campanhas item=%s — %s", item_id, exc)
                continue

            snap_atual = None
            try:
                snap_atual, _, _ = _trio_snapshots(sku, item_id, conta=conta)
            except Exception:
                pass

            preco_atual_snap = snap_atual.preco_atual      if snap_atual else None
            rc_atual_snap    = snap_atual.rc_atual_pct     if snap_atual else None
            posicao_snap     = snap_atual.nossa_posicao    if snap_atual else None
            estoque_snap     = snap_atual.estoque_proprio  if snap_atual else None

            ativas = campaigns.get("ativas") or []
            ja_em_campanha      = len(ativas) > 0
            campanha_ativa_nome = (
                ativas[0].get("name") or ativas[0].get("type") or "Sim"
                if ativas else None
            )

            candidatas = [
                c for c in (campaigns.get("disponiveis") or [])
                if c.get("meli_percentage", 0) > 0
            ]
            for campanha in candidatas:
                tipo_anuncio = sku_data.get("tipo_anuncio", "Clássico")
                if campanha.get("type") == "PRICE_MATCHING":
                    preco = campanha.get("price") or 0.0
                else:
                    preco = campanha.get("suggested_price") or campanha.get("price") or 0.0

                try:
                    resultado_m = calcular_margem(
                        preco_campanha=preco,
                        custo=float(sku_data.get("custo", 0)),
                        rebate=float(campanha.get("rebate_valor", 0)),
                        peso=float(sku_data.get("peso", 0)),
                        tipo_anuncio=tipo_anuncio,
                        cfg=cfg,
                    )
                except Exception as exc:
                    _log.warning("C1: erro margem sku=%s item=%s — %s", sku, item_id, exc)
                    continue

                resultado_d = decidir(resultado_m, rc_min)
                if resultado_d["decisao"] != "ACEITAR":
                    continue

                total_encontradas += 1
                campanha_id  = campanha.get("id") or campanha.get("ref_id") or ""
                rebate_atual = round(float(campanha.get("rebate_valor") or 0), 2)

                fila_dados = {
                    "sku":            sku,
                    "item_id":        item_id,
                    "campanha_id":    campanha_id,
                    "campanha_nome":  campanha.get("name") or campanha.get("type") or "—",
                    "preco_atual":    preco_atual_snap,
                    "rc_atual":       rc_atual_snap,
                    "posicao_buybox": posicao_snap,
                    "estoque":        estoque_snap,
                    "ja_em_campanha": ja_em_campanha,
                    "campanha_ativa_nome": campanha_ativa_nome,
                    "preco_campanha": preco,
                    "rebate":         campanha.get("rebate_valor", 0.0),
                    "rc_campanha":    resultado_m.get("rc_pct", 0.0),
                    "motivo":         resultado_d["motivo"],
                    "vigencia_fim":   campanha.get("finish_date") or "",
                }

                # Popula a fila de revisão independentemente do dedup de e-mail
                try:
                    persistencia.popular_fila(fila_dados, rc_min, conta)
                except Exception as exc:
                    _log.warning("C1: erro ao popular fila item=%s — %s", item_id, exc)

                if campanha_id and (item_id, campanha_id, rebate_atual) in ja_notificadas:
                    continue  # já notificada por e-mail com este rebate

                itens_aceitar.append(fila_dados)

    if not itens_aceitar:
        if total_encontradas > 0:
            _log.info(
                "C1 — %d campanha(s) ACEITAR mas todas já notificadas — conta=%s",
                total_encontradas, conta,
            )
            return {"campanhas_aceitar": 0, "enviado": False,
                    "motivo_supressao": "todas_ja_notificadas"}
        _log.info("C1 — 0 campanhas com RC >= %.0f%% encontradas — conta=%s", rc_min, conta)
        return {"campanhas_aceitar": 0, "enviado": False, "motivo_supressao": "sem_campanhas"}

    enviado      = False
    motivo_sup: Optional[str] = None

    if dry_run:
        motivo_sup = "dry_run"
        _log.info("C1 dry_run — %d campanha(s) nova(s) para conta=%s",
                  len(itens_aceitar), conta)
    elif not cfg_email.get("enabled"):
        motivo_sup = "email_desabilitado"
    else:
        try:
            assunto, html = templates.template_c1_campanhas(itens_aceitar, conta)
            email_mod.enviar_email(assunto, html, cfg_email)
            enviado = True
            _log.info("C1 enviado — %d campanha(s) nova(s) — conta=%s",
                      len(itens_aceitar), conta)
        except email_mod.EmailDesabilitado:
            motivo_sup = "email_desabilitado"
        except email_mod.CredenciaisFaltando as exc:
            motivo_sup = f"credenciais: {exc}"
        except Exception as exc:
            motivo_sup = f"erro_smtp: {exc.__class__.__name__}: {exc}"
            _log.warning("C1 falha SMTP conta=%s motivo=%s", conta, motivo_sup)

    dados_persist: dict = {
        "campanhas": itens_aceitar,
        "total":     len(itens_aceitar),
    }
    if motivo_sup:
        dados_persist["motivo_supressao"] = motivo_sup

    persistencia.registrar_alerta(
        sku=_C1_SENTINEL_SKU,
        item_id="",
        tipo=TIPO_C1_CAMPANHAS_ACEITAR,
        dados=dados_persist,
        enviado=enviado,
        conta=conta,
    )

    return {
        "campanhas_aceitar": len(itens_aceitar),
        "enviado":           enviado,
        "motivo_supressao":  motivo_sup,
    }


__all__ = [
    "avaliar_criticos_pendentes",
    "enviar_resumo_diario",
    "avaliar_campanhas_aceitar",
]
