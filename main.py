"""
Central de Promoções ML — entrypoint CLI.

Uso:
  python main.py                    # roda com configuração do settings.yaml
  python main.py --dry-run          # força dry-run (só analisa, não aceita)
  python main.py --executar         # força execução real (ignora dry_run=true do YAML)
  python main.py --sku WLK004       # processa somente um SKU (útil pra testes)
  python main.py --dry-run --sku WL008
"""

import argparse
import sys
from pathlib import Path

# Garante que o pacote src seja encontrado ao rodar direto do diretório do projeto
sys.path.insert(0, str(Path(__file__).parent))

from src.runner import run


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analisa campanhas da Central de Promoções ML e decide aceitar/recusar."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        help="Força modo dry-run: analisa e loga sem aceitar nenhuma campanha.",
    )
    mode.add_argument(
        "--executar",
        action="store_true",
        help="Força modo de execução real (sobrepõe dry_run=true do settings.yaml).",
    )
    parser.add_argument(
        "--sku",
        metavar="SKU",
        help="Processa somente este SKU (ex: WLK004). Útil para testes pontuais.",
    )
    args = parser.parse_args()

    dry_run_override = None
    if args.dry_run:
        dry_run_override = True
    elif args.executar:
        dry_run_override = False

    run(dry_run_override=dry_run_override, filter_sku=args.sku)


if __name__ == "__main__":
    main()
