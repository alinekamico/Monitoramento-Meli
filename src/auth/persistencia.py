from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import os
from contextlib import contextmanager

from dotenv import load_dotenv
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from .modelos import AuthBase, Usuario

load_dotenv()

def _auth_engine():
    host     = os.environ.get('DB_HOST', 'localhost')
    port     = os.environ.get('DB_PORT', '3306')
    user     = os.environ.get('DB_USER', 'root')
    password = os.environ.get('DB_PASSWORD', '')
    db_name  = os.environ.get('DB_NAME', 'buybox')
    url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{db_name}?charset=utf8mb4"
    return create_engine(url, future=True, pool_recycle=3600, pool_pre_ping=True)

_engine = None
_SessionLocal = None

def _get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _auth_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine

@contextmanager
def sessao():
    _get_engine()
    s = _SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

def init_auth_db() -> None:
    """Cria a tabela usuarios se não existir. Idempotente."""
    eng = _get_engine()
    AuthBase.metadata.create_all(eng)


def buscar_usuario_por_id(usuario_id: int) -> Optional[Usuario]:
    with sessao() as s:
        u = s.get(Usuario, usuario_id)
        if u:
            s.expunge(u)
        return u


def buscar_usuario_por_email(email: str) -> Optional[Usuario]:
    with sessao() as s:
        u = s.execute(
            select(Usuario).where(Usuario.email == email.lower().strip())
        ).scalar_one_or_none()
        if u:
            s.expunge(u)
        return u


def listar_usuarios() -> list[Usuario]:
    with sessao() as s:
        usuarios = list(s.execute(select(Usuario).order_by(Usuario.nome)).scalars())
        for u in usuarios:
            s.expunge(u)
        return usuarios


def criar_usuario(nome: str, email: str, senha_hash: str, perfil: str) -> Usuario:
    from werkzeug.security import generate_password_hash
    u = Usuario(
        nome=nome,
        email=email.lower().strip(),
        senha_hash=senha_hash,
        perfil=perfil,
        ativo=True,
        primeiro_login=True,
        criado_em=datetime.now(timezone.utc),
    )
    with sessao() as s:
        s.add(u)
        s.flush()
        s.expunge(u)
    return u


def atualizar_usuario(
    usuario_id: int,
    nome: Optional[str] = None,
    perfil: Optional[str] = None,
    ativo: Optional[bool] = None,
    senha_hash: Optional[str] = None,
    primeiro_login: Optional[bool] = None,
) -> bool:
    with sessao() as s:
        u = s.get(Usuario, usuario_id)
        if not u:
            return False
        if nome is not None:
            u.nome = nome
        if perfil is not None:
            u.perfil = perfil
        if ativo is not None:
            u.ativo = ativo
        if senha_hash is not None:
            u.senha_hash = senha_hash
        if primeiro_login is not None:
            u.primeiro_login = primeiro_login
    return True


def registrar_acesso(usuario_id: int) -> None:
    with sessao() as s:
        u = s.get(Usuario, usuario_id)
        if u:
            u.ultimo_acesso = datetime.now(timezone.utc)


def salvar_reset_token(usuario_id: int, token: str, expiry: datetime) -> None:
    with sessao() as s:
        u = s.get(Usuario, usuario_id)
        if u:
            u.reset_token = token
            u.reset_token_expiry = expiry


def buscar_usuario_por_reset_token(token: str) -> Optional[Usuario]:
    with sessao() as s:
        u = s.execute(
            select(Usuario).where(Usuario.reset_token == token)
        ).scalar_one_or_none()
        if u:
            s.expunge(u)
        return u


def limpar_reset_token(usuario_id: int) -> None:
    with sessao() as s:
        u = s.get(Usuario, usuario_id)
        if u:
            u.reset_token = None
            u.reset_token_expiry = None
