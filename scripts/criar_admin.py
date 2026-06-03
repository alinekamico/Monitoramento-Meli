"""
Cria o primeiro usuário admin (ou qualquer usuário) via CLI.

Uso:
  python -m scripts.criar_admin
  python -m scripts.criar_admin --email ti@empresa.com --nome "TI" --perfil admin
"""
from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv

load_dotenv(override=False)

from werkzeug.security import generate_password_hash

from src.auth.modelos import PERFIS_VALIDOS
from src.auth.persistencia import buscar_usuario_por_email, criar_usuario, init_auth_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Cria um usuário no sistema")
    parser.add_argument("--email",  required=False)
    parser.add_argument("--nome",   required=False)
    parser.add_argument("--perfil", required=False, choices=list(PERFIS_VALIDOS), default="admin")
    args = parser.parse_args()

    init_auth_db()

    email = args.email or input("E-mail: ").strip()
    nome  = args.nome  or input("Nome:   ").strip()
    perfil = args.perfil

    if not email or not nome:
        print("E-mail e nome são obrigatórios.", file=sys.stderr)
        sys.exit(1)

    if buscar_usuario_por_email(email):
        print(f"Erro: e-mail '{email}' já está cadastrado.", file=sys.stderr)
        sys.exit(1)

    senha = getpass.getpass("Senha: ")
    confirma = getpass.getpass("Confirme a senha: ")
    if senha != confirma:
        print("As senhas não conferem.", file=sys.stderr)
        sys.exit(1)
    if len(senha) < 8:
        print("A senha deve ter ao menos 8 caracteres.", file=sys.stderr)
        sys.exit(1)

    u = criar_usuario(
        nome=nome,
        email=email,
        senha_hash=generate_password_hash(senha),
        perfil=perfil,
    )
    print(f"\nUsuário criado com sucesso!")
    print(f"  ID:     {u.id}")
    print(f"  Nome:   {u.nome}")
    print(f"  E-mail: {u.email}")
    print(f"  Perfil: {u.perfil}")


if __name__ == "__main__":
    main()
