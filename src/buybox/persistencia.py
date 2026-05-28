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

_CONFIG_DIR = Path(__file__).parent.parent.parent / "config"
_PROJECT_ROOT = Path(__file__).parent.parent.parent

# Cache module-level (mesmo padrão do _frete_cache em margem.py)
_engine_cache: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


# ============================================================
# Setup
# ============================================================


def _load_buybox_settings() -> dict:
    """Lê apenas a seção buybox: do settings.yaml."""
    path = _CONFIG_DIR / "settings.yaml"
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    return cfg.get("buybox", {})


def _resolver_db_path(db_path: str) -> Path:
    """Caminhos relativos sempre resolvem a partir da raiz do projeto."""
    p = Path(db_path)
    if not p.is_absolute():
        p = _PROJECT_ROOT / p
    return p


def get_engine(db_path: Optional[str] = None) -> Engine:
    """
    Retorna a engine global (cacheada). Aceita db_path opcional para testes.

    Em testes, passar ":memory:" cria um banco SQLite em memória novo a
    cada chamada — útil para isolar fixtures sem tocar em arquivo.
    """
    global _engine_cache, _session_factory

    if db_path is not None:
        # Modo "ad hoc" (testes): nunca cacheia, sempre cria uma nova engine
        url = f"sqlite:///{db_path}" if db_path != ":memory:" else "sqlite:///:memory:"
        return create_engine(url, future=True)

    if _engine_cache is None:
        cfg = _load_buybox_settings()
        path = _resolver_db_path(cfg.get("db_path", "data/buybox.db"))
        path.parent.mkdir(parents=True, exist_ok=True)
        _engine_cache = create_engine(f"sqlite:///{path}", future=True)
        _session_factory = sessionmaker(bind=_engine_cache, expire_on_commit=False)
    return _engine_cache


def _factory() -> sessionmaker[Session]:
    """Garante que a factory existe (chama get_engine se necessário)."""
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


def reset_engine_cache() -> None:
    """Limpa o cache da engine. Usado em testes para forçar reinit."""
    global _engine_cache, _session_factory
    if _engine_cache is not None:
        _engine_cache.dispose()
    _engine_cache = None
    _session_factory = None


def init_db(engine: Optional[Engine] = None) -> Engine:
    """Cria as tabelas se não existirem + roda migrações. Idempotente."""
    eng = engine if engine is not None else get_engine()
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
def sessao() -> Iterator[Session]:
    """Contexto de sessão com commit/rollback automático."""
    factory = _factory()
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


def salvar_snapshot(dom: SnapshotDom) -> Optional[int]:
    """
    Persiste um snapshot e seus concorrentes em uma transação.

    Retorna o id criado, ou None se a inserção foi ignorada por já existir
    registro com mesmo (sku, item_id, coletado_em) — a constraint de
    idempotência impede duplicatas dentro do mesmo segundo.
    """
    snap_orm = _dom_para_orm(dom)
    try:
        with sessao() as s:
            s.add(snap_orm)
            s.flush()  # garante id antes do commit
            return snap_orm.id
    except IntegrityError:
        return None


def ultimo_snapshot(sku: str, item_id: Optional[str] = None) -> Optional[Snapshot]:
    """Último snapshot do SKU (ou do par sku+item_id se fornecido)."""
    with sessao() as s:
        stmt = select(Snapshot).where(Snapshot.sku == sku)
        if item_id is not None:
            stmt = stmt.where(Snapshot.item_id == item_id)
        stmt = stmt.order_by(Snapshot.coletado_em.desc()).limit(1)
        result = s.execute(stmt).scalar_one_or_none()
        if result is not None:
            # Materializa relação antes de fechar a sessão
            _ = result.concorrentes
        return result


def snapshots_24h(sku: str, item_id: Optional[str] = None) -> list[Snapshot]:
    """Atalho retrocompatível para `snapshots_periodo(sku, dias=1)`."""
    return snapshots_periodo(sku, item_id=item_id, horas=24)


def snapshots_periodo(
    sku: str,
    item_id: Optional[str] = None,
    *,
    horas: Optional[int] = None,
    desde: Optional[datetime] = None,
    ate: Optional[datetime] = None,
) -> list[Snapshot]:
    """
    Snapshots em um intervalo arbitrário (mais antigos primeiro).

    Modos de uso:
      - `horas=24` → últimas 24h (atalho legado)
      - `horas=24*30` → últimos 30 dias
      - `desde=datetime, ate=datetime` → intervalo customizado

    Quando ambos os modos forem informados, o intervalo customizado tem
    prioridade.
    """
    if desde is None and ate is None:
        horas_val = horas if horas is not None else 24
        desde = datetime.now(timezone.utc) - timedelta(hours=horas_val)
        ate = datetime.now(timezone.utc)
    elif desde is None:
        # ate informado mas desde não — assume 24h antes do `ate`
        desde = ate - timedelta(hours=24)
    elif ate is None:
        ate = datetime.now(timezone.utc)

    with sessao() as s:
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
) -> list[Snapshot]:
    """Todos os snapshots do dia (UTC) — opcionalmente filtrados por SKU."""
    base = referencia or datetime.now(timezone.utc)
    inicio = base.replace(hour=0, minute=0, second=0, microsecond=0)
    fim = inicio + timedelta(days=1)
    with sessao() as s:
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


def ultimo_snapshot_por_sku() -> dict[str, Snapshot]:
    """Mapa sku → último snapshot (útil para o endpoint da lista no dashboard)."""
    with sessao() as s:
        # Estratégia simples para SQLite: pega todos os SKUs distintos e
        # busca o último de cada. Volume baixo (≤ 50 SKUs), simplicidade ganha.
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
) -> int:
    """
    Cria registro na tabela alertas.

    enviado=False → suprimido por cooldown ou pelo modo dry-run.
                    Mantemos o registro mesmo assim para auditoria.
    """
    ts = disparado_em or datetime.now(timezone.utc)
    alerta = Alerta(
        sku=sku,
        item_id=item_id,
        tipo=tipo,
        disparado_em=ts,
        enviado_em=ts if enviado else None,
        dados=json.dumps(dados, ensure_ascii=False, default=str),
    )
    with sessao() as s:
        s.add(alerta)
        s.flush()
        return alerta.id


def ultimo_alerta_enviado(sku: str, tipo: str) -> Optional[Alerta]:
    """Último alerta efetivamente enviado (enviado_em != null) do par sku/tipo."""
    with sessao() as s:
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


def alertas_recentes(sku: str, dias: int = 7) -> list[Alerta]:
    """Alertas do SKU nos últimos N dias (mais recentes primeiro)."""
    desde = datetime.now(timezone.utc) - timedelta(days=dias)
    with sessao() as s:
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
