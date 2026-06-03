"""
Reseta a senha de um usuário via CLI.

Uso:
  python -m scripts.resetar_senha
  python -m scripts.resetar_senha --email aline@kamico.com.br
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

from src.auth.persistencia import atualizar_usuario, buscar_usuario_por_email, init_auth_db


def main() -> None:
    parser = argparse.ArgumentParser(description="Reseta a senha de um usuário")
    parser.add_argument("--email", required=False)
    args = parser.parse_args()

    init_auth_db()

    email = args.email or input("E-mail do usuário: ").strip()
    usuario = buscar_usuario_por_email(email)
    if not usuario:
        print(f"Erro: usuário '{email}' não encontrado.", file=sys.stderr)
        sys.exit(1)

    print(f"Usuário encontrado: {usuario.nome} ({usuario.perfil})")
    nova = getpass.getpass("Nova senha: ")
    confirma = getpass.getpass("Confirme a nova senha: ")
    if nova != confirma:
        print("As senhas não conferem.", file=sys.stderr)
        sys.exit(1)
    if len(nova) < 8:
        print("A senha deve ter ao menos 8 caracteres.", file=sys.stderr)
        sys.exit(1)

    atualizar_usuario(usuario_id=usuario.id, senha_hash=generate_password_hash(nova))
    print(f"Senha de '{usuario.email}' atualizada com sucesso!")


if __name__ == "__main__":
    main()
