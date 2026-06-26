"""
Força a verificação de campanhas C1 em todas as contas agora,
ignorando o cooldown.

Para cada conta, busca campanhas candidatas com RC >= rc_minimo e,
se encontrar, envia o e-mail de C1. Imprime um relatório detalhado.

Uso:
    python -m scripts.testar_c1
    python -m scripts.testar_c1 --conta best_hair
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import yaml
from dotenv import load_dotenv

from src.alertas import avaliador
from src.buybox import persistencia


_CONFIG_DIR = Path(__file__).parent.parent / "config"


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Testa verificação C1 (campanhas para aceitar) agora."
    )
    parser.add_argument("--conta", help="Limita a uma conta específica.")
    args = parser.parse_args()

    with open(_CONFIG_DIR / "settings.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    with open(_CONFIG_DIR / "contas.yaml", encoding="utf-8") as f:
        contas_cfg = yaml.safe_load(f)

    todas_contas = list(contas_cfg.get("contas", {}).keys())
    contas = [args.conta] if args.conta else todas_contas

    for conta in contas:
        if conta not in todas_contas:
            print(f"\n[{conta}] Conta não encontrada em contas.yaml.")
            continue

        persistencia.init_db(conta)

    # Força cooldown = 0 para este teste
    cfg_teste = {**cfg, "buybox": {**(cfg.get("buybox") or {}), "cooldown_c1_horas": 0}}

    rc_min = float(cfg.get("rc_minimo", 60.0))
    print(f"\nRC mínimo: {rc_min:.0f}%")
    print(f"Contas: {', '.join(contas)}")
    print(f"dry_run: {cfg.get('dry_run', True)}")
    print("-" * 60)

    saiu_ok = True
    for conta in contas:
        if conta not in todas_contas:
            continue

        print(f"\n[{conta}] Verificando campanhas…")
        try:
            r = avaliador.avaliar_campanhas_aceitar(
                cfg=cfg_teste,
                dry_run=bool(cfg.get("dry_run", True)),
                conta=conta,
            )
        except Exception as exc:
            print(f"  ERRO: {exc.__class__.__name__}: {exc}")
            saiu_ok = False
            continue

        n = r.get("campanhas_aceitar", 0)
        enviado = r.get("enviado", False)
        motivo = r.get("motivo_supressao", "")

        if n == 0:
            print(f"  Nenhuma campanha com RC >= {rc_min:.0f}% encontrada.")
        else:
            print(f"  {n} campanha(s) para aceitar encontrada(s).")

        if enviado:
            print(f"  E-mail enviado com sucesso.")
        elif motivo == "dry_run":
            print(f"  E-mail NÃO enviado — dry_run=true (mude em settings.yaml para enviar de verdade).")
        elif motivo == "sem_campanhas":
            pass  # já impresso acima
        elif motivo:
            print(f"  E-mail NÃO enviado — motivo: {motivo}")

    print("\n" + "-" * 60)
    return 0 if saiu_ok else 1


if __name__ == "__main__":
    sys.exit(main())
