"""
Camada de persistência do MVP Buybox.

Responsabilidades:
  - Criar engine SQLAlchemy apontando para o db_path do settings.yaml
  - init_db(): cria tabelas se não existirem (idempotente)
  - Helpers de CRUD: salvar_snapshot, ultimo_snapshot, registrar_alerta,
    ultimo_alerta_do_tipo, snapshots_do_dia, snapshots_24h
  - Idempotência: salvar_snapshot ignora silenciosamente se já existe
    registro com mesmo (sku, item_id, coletado_em).

A camada é fina de propósito: queries específicas do dashboard ou de
regras de alerta podem ficar nos módulos consumidores; aqui ficam
apenas operações genéricas reutilizáveis.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import yaml
from sqlalchemy import create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from . import modelos
from .modelos import (
    Alerta,
    Base,
    ConcorrenteDom,
    Snapshot,
    SnapshotConcorrente,
    SnapshotDom,
)

_CONFIG_DIR   = Path(__file__).parent.parent.parent / "config"
_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Cache module-level keyed por conta (ex: "best_hair", "hair_pro")
# Mantemos a forma de dict para suportar múltiplas contas sem reinicializar.
_engine_cache: dict[str, Engine] = {}
_session_factory: dict[str, sessionmaker[Session]] = {}


# ============================================================
# Setup
# ============================================================


def _load_buybox_settings() -> dict:
    """Lê apenas a seção buybox: do settings.yaml."""
    path = _CONFIG_DIR / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("buybox", {})


def _load_conta_cfg(conta: str) -> dict:
    """Retorna a configuração da conta em config/contas.yaml."""
    path = _CONFIG_DIR / "contas.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("contas", {}).get(conta, {})


def _resolver_db_path(db_path: str) -> Path:
    """Caminhos relativos sempre resolvem a partir da raiz do projeto."""
    p = Path(db_path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p


def get_engine(conta: str = "best_hair", db_path: Optional[str] = None) -> Engine:
    """
    Retorna engine para a conta (cacheada por conta). Aceita db_path para testes.

    Em testes, passar db_path diferente de None ignora o conta e cria
    engine ad-hoc — útil para isolar fixtures sem tocar em arquivo.
    """
    global _engine_cache, _session_factory

    if db_path is not None:
        # Modo ad-hoc (testes): nunca cacheia
        url = f"sqlite:///{db_path}" if db_path != ":memory:" else "sqlite:///:memory:"
        return create_engine(url, future=True)

    if conta not in _engine_cache:
        conta_cfg = _load_conta_cfg(conta)
        db_path_str = conta_cfg.get("db_path") or f"data/{conta}.db"
        path = _resolver_db_path(db_path_str)
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(f"sqlite:///{path}", future=True)
        _engine_cache[conta] = engine
        _session_factory[conta] = sessionmaker(bind=engine, expire_on_commit=False)
        # Auto-init: cria tabelas e aplica migrações para bancos novos/existentes.
        # Idempotente — não apaga dados, apenas adiciona estrutura que faltar.
        Base.metadata.create_all(engine)
        _migrar_schema(engine)

    return _engine_cache[conta]


def _factory(conta: str = "best_hair") -> sessionmaker[Session]:
    """Retorna a session factory para a conta."""
    if conta not in _session_factory:
        get_engine(conta)
    return _session_factory[conta]


def reset_engine_cache(conta: Optional[str] = None) -> None:
    """
    Limpa o cache de engine(s). Sem argumento, limpa todas as contas.
    Usado em testes para forçar reinit.
    """
    global _engine_cache, _session_factory
    if conta is not None:
        eng = _engine_cache.pop(conta, None)
        if eng is not None:
            eng.dispose()
        _session_factory.pop(conta, None)
    else:
        for eng in _engine_cache.values():
            eng.dispose()
        _engine_cache.clear()
        _session_factory.clear()


def init_db(conta: str = "best_hair", engine: Optional[Engine] = None) -> Engine:
    """Cria as tabelas se não existirem + roda migrações. Idempotente."""
    eng = engine if engine is not None else get_engine(conta)
    Base.metadata.create_all(eng)
    _migrar_schema(eng)
    return eng


# Colunas adicionadas após a v1 do schema — precisam ser
# inseridas em DBs já existentes via ALTER TABLE.
_MIGRACOES = [
    ("snapshots",             "custo",                   "REAL"),
    ("snapshots",             "campanha_min_price",      "REAL"),
    ("snapshots",             "campanha_max_price",      "REAL"),
    ("snapshots",             "campanha_original_price", "REAL"),
    # Sprint 1.3 — foto do anúncio
    ("snapshots",             "thumbnail_url",           "TEXT"),
    # Sprint 1.4 — vigência da campanha
    ("snapshots",             "campanha_start_date",     "TEXT"),
    ("snapshots",             "campanha_finish_date",    "TEXT"),
    # Sprint 3.3 — relevância do produto
    ("snapshots",             "reviews_rating",          "REAL"),
    ("snapshots",             "reviews_total",           "INTEGER"),
    # Sprint 1.2 — vendas dos concorrentes (na tabela snapshot_concorrentes)
    ("snapshot_concorrentes", "total_vendas",            "INTEGER DEFAULT 0"),
    # Sprint 3.1 — prazo de entrega
    ("snapshot_concorrentes", "prazo_entrega_dias",      "INTEGER"),
]


def _migrar_schema(eng: Engine) -> None:
    """
    Adiciona colunas novas em bancos existentes (SQLite-friendly).
    Cada migração checa se a coluna já existe antes de tentar inserir.
    """
    from sqlalchemy import text
    with eng.begin() as conn:
        for tabela, coluna, tipo in _MIGRACOES:
            existentes = {
                row[1] for row in conn.exec_driver_sql(
                    f"PRAGMA table_info({tabela})"
                ).fetchall()
            }
            if coluna not in existentes:
                conn.exec_driver_sql(
                    f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}"
                )


@contextmanager
def sessao(conta: str = "best_hair") -> Iterator[Session]:
    """Contexto de sessão com commit/rollback automático."""
    factory = _factory(conta)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ============================================================
# Snapshots
# ============================================================


def _dom_para_orm(dom: SnapshotDom) -> Snapshot:
    snap = Snapshot(
        sku=dom.sku,
        item_id=dom.item_id,
        coletado_em=dom.coletado_em,
        preco_atual=dom.preco_atual,
        nossa_posicao=dom.nossa_posicao,
        tem_buybox=dom.tem_buybox,
        status_anuncio=dom.status_anuncio,
        estoque_proprio=dom.estoque_proprio,
        is_full=dom.is_full,
        tipo_anuncio=dom.tipo_anuncio,
        preco_1o=dom.preco_1o,
        preco_2o=dom.preco_2o,
        diff_para_1o_rs=dom.diff_para_1o_rs,
        diff_para_1o_pct=dom.diff_para_1o_pct,
        diff_para_2o_rs=dom.diff_para_2o_rs,
        diff_para_2o_pct=dom.diff_para_2o_pct,
        qtd_concorrentes=dom.qtd_concorrentes,
        campanha_ativa_id=dom.campanha_ativa_id,
        campanha_ativa_nome=dom.campanha_ativa_nome,
        rebate_pct=dom.rebate_pct,
        campanha_min_price=dom.campanha_min_price,
        campanha_max_price=dom.campanha_max_price,
        campanha_original_price=dom.campanha_original_price,
        campanha_start_date=dom.campanha_start_date,
        campanha_finish_date=dom.campanha_finish_date,
        custo=dom.custo,
        margem_atual_pct=dom.margem_atual_pct,
        rc_atual_pct=dom.rc_atual_pct,
        preco_otimo_sugerido=dom.preco_otimo_sugerido,
        rc_no_preco_otimo=dom.rc_no_preco_otimo,
        motivo_sugestao=dom.motivo_sugestao,
        titulo=dom.titulo,
        url_anuncio=dom.url_anuncio,
        thumbnail_url=dom.thumbnail_url,
        reviews_rating=dom.reviews_rating,
        reviews_total=dom.reviews_total,
        visivel_no_catalogo=dom.visivel_no_catalogo,
        preco_cheio=dom.preco_cheio,
    )
    snap.concorrentes = [
        SnapshotConcorrente(
            posicao=c.posicao,
            seller_id=c.seller_id,
            seller_nome=c.seller_nome,
            preco=c.preco,
            tipo_envio=c.tipo_envio,
            frete_gratis=c.frete_gratis,
            reputacao=c.reputacao,
            url_anuncio=c.url_anuncio,
            e_nos=c.e_nos,
            total_vendas=c.total_vendas,
            prazo_entrega_dias=c.prazo_entrega_dias,
        )
        for c in dom.concorrentes
    ]
    return snap


def salvar_snapshot(dom: SnapshotDom, conta: str = "best_hair") -> Optional[int]:
    """
    Persiste um snapshot e seus concorrentes em uma transação.

    Retorna o id criado, ou None se a inserção foi ignorada por já existir
    registro com mesmo (sku, item_id, coletado_em).
    """
    snap_orm = _dom_para_orm(dom)
    try:
        with sessao(conta) as s:
            s.add(snap_orm)
            s.flush()
            return snap_orm.id
    except IntegrityError:
        return None


def ultimo_snapshot(sku: str, item_id: Optional[str] = None,
                    conta: str = "best_hair") -> Optional[Snapshot]:
    """Último snapshot do SKU (ou do par sku+item_id se fornecido)."""
    with sessao(conta) as s:
        stmt = select(Snapshot).where(Snapshot.sku == sku)
        if item_id is not None:
            stmt = stmt.where(Snapshot.item_id == item_id)
        stmt = stmt.order_by(Snapshot.coletado_em.desc()).limit(1)
        result = s.execute(stmt).scalar_one_or_none()
        if result is not None:
            _ = result.concorrentes
        return result


def snapshots_24h(sku: str, item_id: Optional[str] = None,
                  conta: str = "best_hair") -> list[Snapshot]:
    """Atalho retrocompatível para `snapshots_periodo(sku, horas=24)`."""
    return snapshots_periodo(sku, item_id=item_id, horas=24, conta=conta)


def snapshots_periodo(
    sku: str,
    item_id: Optional[str] = None,
    *,
    horas: Optional[int] = None,
    desde: Optional[datetime] = None,
    ate: Optional[datetime] = None,
    conta: str = "best_hair",
) -> list[Snapshot]:
    """Snapshots em um intervalo arbitrário (mais antigos primeiro)."""
    if desde is None and ate is None:
        horas_val = horas if horas is not None else 24
        desde = datetime.now(timezone.utc) - timedelta(hours=horas_val)
        ate = datetime.now(timezone.utc)
    elif desde is None:
        desde = ate - timedelta(hours=24)
    elif ate is None:
        ate = datetime.now(timezone.utc)

    with sessao(conta) as s:
        stmt = (
            select(Snapshot)
            .where(
                Snapshot.sku == sku,
                Snapshot.coletado_em >= desde,
                Snapshot.coletado_em <= ate,
            )
            .order_by(Snapshot.coletado_em.asc())
        )
        if item_id is not None:
            stmt = stmt.where(Snapshot.item_id == item_id)
        rows = list(s.execute(stmt).scalars())
        for r in rows:
            _ = r.concorrentes
        return rows


def snapshots_do_dia(
    sku: Optional[str] = None,
    referencia: Optional[datetime] = None,
    conta: str = "best_hair",
) -> list[Snapshot]:
    """Todos os snapshots do dia (UTC) — opcionalmente filtrados por SKU."""
    base = referencia or datetime.now(timezone.utc)
    inicio = base.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = inicio + timedelta(days=1)
    with sessao(conta) as s:
        stmt = (
            select(Snapshot)
            .where(Snapshot.coletado_em >= inicio, Snapshot.coletado_em < fim)
            .order_by(Snapshot.sku.asc(), Snapshot.coletado_em.asc())
        )
        if sku is not None:
            stmt = stmt.where(Snapshot.sku == sku)
        rows = list(s.execute(stmt).scalars())
        for r in rows:
            _ = r.concorrentes
        return rows


def ultimo_snapshot_por_sku(conta: str = "best_hair") -> dict[str, Snapshot]:
    """Mapa sku → último snapshot (útil para o endpoint da lista no dashboard)."""
    with sessao(conta) as s:
        skus = list(s.execute(select(Snapshot.sku).distinct()).scalars())
        resultado: dict[str, Snapshot] = {}
        for sku in skus:
            stmt = (
                select(Snapshot)
                .where(Snapshot.sku == sku)
                .order_by(Snapshot.coletado_em.desc())
                .limit(1)
            )
            row = s.execute(stmt).scalar_one_or_none()
            if row is not None:
                _ = row.concorrentes
                resultado[sku] = row
        return resultado


# ============================================================
# Alertas
# ============================================================


def registrar_alerta(
    sku: str,
    item_id: str,
    tipo: str,
    dados: dict,
    enviado: bool,
    disparado_em: Optional[datetime] = None,
    conta: str = "best_hair",
) -> int:
    """Cria registro na tabela alertas (auditoria completa, incluindo suprimidos)."""
    ts = disparado_em or datetime.now(timezone.utc)
    alerta = Alerta(
        sku=sku,
        item_id=item_id,
        tipo=tipo,
        disparado_em=ts,
        enviado_em=ts if enviado else None,
        dados=json.dumps(dados, ensure_ascii=False, default=str),
    )
    with sessao(conta) as s:
        s.add(alerta)
        s.flush()
        return alerta.id


def ultimo_alerta_enviado(sku: str, tipo: str,
                           conta: str = "best_hair") -> Optional[Alerta]:
    """Último alerta efetivamente enviado do par sku/tipo."""
    with sessao(conta) as s:
        stmt = (
            select(Alerta)
            .where(
                Alerta.sku == sku,
                Alerta.tipo == tipo,
                Alerta.enviado_em.is_not(None),
            )
            .order_by(Alerta.disparado_em.desc())
            .limit(1)
        )
        return s.execute(stmt).scalar_one_or_none()


def alertas_recentes(sku: str, dias: int = 7,
                     conta: str = "best_hair") -> list[Alerta]:
    """Alertas do SKU nos últimos N dias (mais recentes primeiro)."""
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    with sessao(conta) as s:
        stmt = (
            select(Alerta)
            .where(Alerta.sku == sku, Alerta.disparado_em >= desde)
            .order_by(Alerta.disparado_em.desc())
        )
        return list(s.execute(stmt).scalars())


__all__ = [
    "init_db",
    "get_engine",
    "reset_engine_cache",
    "sessao",
    "salvar_snapshot",
    "ultimo_snapshot",
    "snapshots_24h",
    "snapshots_periodo",
    "snapshots_do_dia",
    "ultimo_snapshot_por_sku",
    "registrar_alerta",
    "ultimo_alerta_enviado",
    "alertas_recentes",
    "modelos",
]
