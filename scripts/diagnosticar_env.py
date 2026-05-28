"""
Diagnóstico das credenciais de e-mail — não expõe valores sensíveis.

Mostra:
  1. Se o arquivo .env existe e onde
  2. Quais nomes de variáveis estão definidas (sem mostrar valores)
  3. O tamanho da senha (sem mostrar o conteúdo)
  4. Se há espaços/aspas problemáticos
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv  # noqa: E402


def _mascarar(valor: str) -> str:
    """Mostra só primeiros e últimos 2 chars."""
    if not valor:
        return "(vazio)"
    if len(valor) <= 4:
        return "***"
    return f"{valor[:2]}…{valor[-2:]} ({len(valor)} chars)"


def main() -> None:
    root = Path(__file__).parent.parent
    env_path = root / ".env"

    print(f"Pasta do projeto: {root}")
    print(f"Procurando .env em: {env_path}")
    print(f"  Existe?         {env_path.exists()}")
    if not env_path.exists():
        print()
        print("✗ Arquivo .env não encontrado nesse caminho.")
        print("  Crie em: " + str(env_path))
        return

    tamanho = env_path.stat().st_size
    print(f"  Tamanho:        {tamanho} bytes")
    print()

    print("Carregando .env…")
    load_dotenv(env_path, override=True)
    print()

    print("Variáveis de e-mail (mascaradas):")
    for nome in ["EMAIL_REMETENTE", "EMAIL_SENHA_APP"]:
        valor = os.getenv(nome, "")
        print(f"  {nome:20s} = {_mascarar(valor)}")
        if valor:
            # Detecta problemas comuns
            if valor != valor.strip():
                print(f"    ⚠ Contém espaços no início/fim — remova-os")
            if valor.startswith('"') or valor.startswith("'"):
                print(f"    ⚠ Contém aspas — remova-as (use formato CHAVE=valor sem aspas)")
            if " " in valor and nome == "EMAIL_SENHA_APP":
                print(f"    ⚠ Contém espaços internos — Google mostra a senha com espaços")
                print(f"      mas o SMTP do Gmail aceita SEM espaços")

    print()
    print("Outras variáveis ML (não relacionadas, só pra confirmar que o .env tá sendo lido):")
    for nome in ["ML_APP_ID", "ML_ACCESS_TOKEN", "ML_SELLER_ID"]:
        valor = os.getenv(nome, "")
        print(f"  {nome:20s} = {_mascarar(valor)}")


if __name__ == "__main__":
    main()
