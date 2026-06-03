from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from flask_login import UserMixin
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

PERFIL_ADMIN    = "admin"
PERFIL_GERENTE  = "gerente"
PERFIL_OPERADOR = "operador"

PERFIS_VALIDOS = {PERFIL_ADMIN, PERFIL_GERENTE, PERFIL_OPERADOR}


class AuthBase(DeclarativeBase):
    pass


class Usuario(AuthBase, UserMixin):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nome: Mapped[str] = mapped_column(String(128))
    email: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    senha_hash: Mapped[str] = mapped_column(String(256))
    perfil: Mapped[str] = mapped_column(String(16), default=PERFIL_OPERADOR)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    ultimo_acesso: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    primeiro_login: Mapped[bool] = mapped_column(Boolean, default=True)
    reset_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    reset_token_expiry: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    def get_id(self) -> str:
        return str(self.id)

    @property
    def eh_admin(self) -> bool:
        return self.perfil == PERFIL_ADMIN

    @property
    def eh_gerente_ou_admin(self) -> bool:
        return self.perfil in {PERFIL_ADMIN, PERFIL_GERENTE}
