"""
Modelos do MVP Buybox.

Define:
  - Modelos ORM (SQLAlchemy 2.x, estilo `Mapped[...]`) das tabelas:
      snapshots, snapshot_concorrentes, alertas
  - Dataclasses leves para transitar dados entre coletor/pricing/alertas
    sem acoplar a camada de domínio à sessão do banco.

Convenções:
  - Timestamps sempre em UTC (datetime.now(timezone.utc)).
  - Preço/percentuais em Float (precisão suficiente para R$ × % no nosso volume).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


# ============================================================
# Tipos de alerta — strings curtas para indexar na tabela alertas
# ============================================================

TIPO_A1_PERDI_BUYBOX = "A1"
TIPO_A2_AMEACA = "A2"
TIPO_A3_OPORTUNIDADE = "A3"
TIPO_B1_PROBLEMA = "B1"
TIPO_B2_MARGEM_BAIXA = "B2"
TIPO_B3_OPORTUNIDADE_SUBIR = "B3"
TIPO_C1_CAMPANHAS_ACEITAR = "C1"

TIPOS_CRITICOS = {TIPO_A1_PERDI_BUYBOX, TIPO_A2_AMEACA, TIPO_A3_OPORTUNIDADE}
TIPOS_RESUMO_DIARIO = {TIPO_B1_PROBLEMA, TIPO_B2_MARGEM_BAIXA, TIPO_B3_OPORTUNIDADE_SUBIR}
TIPOS_CAMPANHAS = {TIPO_C1_CAMPANHAS_ACEITAR}

# ============================================================
# Status da fila de revisão manual
# ============================================================

FILA_PENDENTE  = "PENDENTE"
FILA_APROVADO  = "APROVADO"
FILA_REJEITADO = "REJEITADO"
FILA_ADIADO    = "ADIADO"
FILA_APLICADO  = "APLICADO"  # fase 2: auto-aceite via API


# ============================================================
# ORM
# ============================================================


class Base(DeclarativeBase):
    """Base declarativa única para todos os modelos do MVP Buybox."""


class Snapshot(Base):
    """Estado do anúncio + competição em um ponto no tempo."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    sku: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[str] = mapped_column(String(32), index=True)
    coletado_em: Mapped[datetime] = mapped_column(DateTime, index=True)

    # Seu anúncio
    preco_atual: Mapped[float] = mapped_column(Float)
    nossa_posicao: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    tem_buybox: Mapped[bool] = mapped_column(Boolean, default=False)
    status_anuncio: Mapped[str] = mapped_column(String(32))
    estoque_proprio: Mapped[int] = mapped_column(Integer, default=0)
    is_full: Mapped[bool] = mapped_column(Boolean, default=False)
    tipo_anuncio: Mapped[str] = mapped_column(String(16))  # Clássico / Premium

    # Competição
    preco_1o: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    preco_2o: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    diff_para_1o_rs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    diff_para_1o_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    diff_para_2o_rs: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    diff_para_2o_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    qtd_concorrentes: Mapped[int] = mapped_column(Integer, default=0)

    # Campanha
    campanha_ativa_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    campanha_ativa_nome: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    rebate_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Faixa de preço válida da campanha — fora dela, perde o rebate
    campanha_min_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    campanha_max_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Preço cheio sobre o qual o ML calcula o rebate fixo em R$
    campanha_original_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Vigência da campanha (strings ISO da API ML) — armazenadas como TEXT
    # porque não precisamos fazer comparação temporal no banco.
    campanha_start_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    campanha_finish_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Custo (CMV) do SKU no momento da coleta — guardado para auditoria
    # e para reproduzir o cálculo de margem mesmo se o YAML mudar depois.
    custo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Margem
    margem_atual_pct: Mapped[float] = mapped_column(Float, default=0.0)
    rc_atual_pct: Mapped[float] = mapped_column(Float, default=0.0)

    # Sugestão de pricing
    preco_otimo_sugerido: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rc_no_preco_otimo: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    motivo_sugestao: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Metadado opcional do anúncio para mostrar no dashboard
    titulo: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    url_anuncio: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    # Avaliações do produto/anúncio (catálogo, agregado pelo ML)
    reviews_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reviews_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # True quando nosso anúncio aparece no /products/{id}/items (visível
    # ao cliente). False para pausado, sem estoque ou omitido pelo ML.
    visivel_no_catalogo: Mapped[bool] = mapped_column(Boolean, default=True)

    # Preço cheio (item.price) — útil para mostrar a economia quando
    # estamos em campanha started com desconto
    preco_cheio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    concorrentes: Mapped[list["SnapshotConcorrente"]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="SnapshotConcorrente.posicao",
    )

    __table_args__ = (
        Index("ix_snapshots_sku_coletado_em", "sku", "coletado_em"),
        Index("ix_snapshots_buybox", "tem_buybox", "coletado_em"),
        # Idempotência: não duplicar snapshot do mesmo (sku, item, momento)
        Index(
            "uq_snapshots_sku_item_ts",
            "sku", "item_id", "coletado_em",
            unique=True,
        ),
    )


class SnapshotConcorrente(Base):
    """Linha do top 5 de um snapshot."""

    __tablename__ = "snapshot_concorrentes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id", ondelete="CASCADE"),
        index=True,
    )

    posicao: Mapped[int] = mapped_column(Integer)  # 1..5
    seller_id: Mapped[str] = mapped_column(String(32))
    seller_nome: Mapped[str] = mapped_column(String(128), default="")
    preco: Mapped[float] = mapped_column(Float)
    tipo_envio: Mapped[str] = mapped_column(String(16), default="")   # full/flex/normal
    frete_gratis: Mapped[bool] = mapped_column(Boolean, default=False)
    reputacao: Mapped[str] = mapped_column(String(16), default="")
    url_anuncio: Mapped[str] = mapped_column(String(512), default="")
    e_nos: Mapped[bool] = mapped_column(Boolean, default=False)
    # Vendas históricas do seller (transactions.total da API ML)
    total_vendas: Mapped[int] = mapped_column(Integer, default=0)
    # Prazo de entrega em dias úteis para o CEP de referência (preenchido
    # quando buybox.cep_referencia está configurado)
    prazo_entrega_dias: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    snapshot: Mapped[Snapshot] = relationship(back_populates="concorrentes")


class FilaRevisao(Base):
    """Campanha candidata aguardando revisão manual antes de ser aceita."""

    __tablename__ = "fila_revisao"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Identificação
    sku: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[str] = mapped_column(String(32), index=True)
    campanha_id: Mapped[str] = mapped_column(String(64))
    campanha_nome: Mapped[str] = mapped_column(String(128), default="")

    # Dados no momento da coleta
    rc_pct: Mapped[float] = mapped_column(Float)
    rc_minimo: Mapped[float] = mapped_column(Float)
    preco_atual: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    preco_campanha: Mapped[float] = mapped_column(Float)
    rebate: Mapped[float] = mapped_column(Float, default=0.0)
    posicao_buybox: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    estoque: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ja_em_campanha: Mapped[bool] = mapped_column(Boolean, default=False)
    motivo: Mapped[str] = mapped_column(String(255), default="")
    vigencia_fim: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Controle de revisão
    status: Mapped[str] = mapped_column(String(16), index=True, default="PENDENTE")
    ts_coleta: Mapped[datetime] = mapped_column(DateTime, index=True)
    ts_acao: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    observacao: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_fila_status_ts", "status", "ts_coleta"),
        Index("ix_fila_item_campanha", "item_id", "campanha_id"),
    )


class Alerta(Base):
    """Registro de alerta disparado (ou suprimido por cooldown)."""

    __tablename__ = "alertas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    sku: Mapped[str] = mapped_column(String(32), index=True)
    item_id: Mapped[str] = mapped_column(String(32), index=True)
    tipo: Mapped[str] = mapped_column(String(8), index=True)  # A1/A2/A3/B1/B2/B3
    disparado_em: Mapped[datetime] = mapped_column(DateTime, index=True)
    # enviado_em=null → suprimido por cooldown ou modo dry-run
    enviado_em: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    dados: Mapped[str] = mapped_column(Text, default="{}")  # JSON serializado

    __table_args__ = (
        Index("ix_alertas_sku_tipo_ts", "sku", "tipo", "disparado_em"),
    )


# ============================================================
# Dataclasses de domínio (independentes de sessão ORM)
# ============================================================


@dataclass
class ConcorrenteDom:
    """Concorrente individual no top 5, usado pelo coletor e pricing."""

    posicao: int
    seller_id: str
    seller_nome: str
    preco: float
    tipo_envio: str = ""
    frete_gratis: bool = False
    reputacao: str = ""
    url_anuncio: str = ""
    e_nos: bool = False
    total_vendas: int = 0
    prazo_entrega_dias: Optional[int] = None


@dataclass
class SnapshotDom:
    """Snapshot de domínio — fonte para o ORM Snapshot e para as regras de alerta."""

    sku: str
    item_id: str
    coletado_em: datetime

    preco_atual: float
    nossa_posicao: Optional[int]
    tem_buybox: bool
    status_anuncio: str
    estoque_proprio: int
    is_full: bool
    tipo_anuncio: str

    preco_1o: Optional[float]
    preco_2o: Optional[float]
    qtd_concorrentes: int

    margem_atual_pct: float
    rc_atual_pct: float

    concorrentes: list[ConcorrenteDom] = field(default_factory=list)

    campanha_ativa_id: Optional[str] = None
    campanha_ativa_nome: Optional[str] = None
    rebate_pct: Optional[float] = None
    campanha_min_price: Optional[float] = None
    campanha_max_price: Optional[float] = None
    campanha_original_price: Optional[float] = None
    campanha_start_date: Optional[str] = None
    campanha_finish_date: Optional[str] = None

    custo: Optional[float] = None

    preco_otimo_sugerido: Optional[float] = None
    rc_no_preco_otimo: Optional[float] = None
    motivo_sugestao: Optional[str] = None

    titulo: Optional[str] = None
    url_anuncio: Optional[str] = None
    thumbnail_url: Optional[str] = None
    reviews_rating: Optional[float] = None
    reviews_total: Optional[int] = None

    visivel_no_catalogo: bool = True
    preco_cheio: Optional[float] = None

    # Diffs são calculados sob demanda; ficam aqui só pra evitar recalcular
    diff_para_1o_rs: Optional[float] = None
    diff_para_1o_pct: Optional[float] = None
    diff_para_2o_rs: Optional[float] = None
    diff_para_2o_pct: Optional[float] = None
