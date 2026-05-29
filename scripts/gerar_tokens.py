"""
Gerador de tokens OAuth para uma conta Mercado Livre.

Passo a passo automático:
  1. Você informa APP_ID e CLIENT_SECRET do App ML da conta
  2. O script gera o link de autorização
  3. Você abre o link no navegador, faz login com a conta correta e copia o código
  4. O script troca o código pelo access_token + refresh_token e salva no .env

Uso:
  python scripts/gerar_tokens.py --conta best_hair
  python scripts/gerar_tokens.py --conta hair_pro

Pre-requisito: o App ML precisa ter como Redirect URI cadastrada:
  https://httpbin.org/get
  (nao precisa de servidor local — o codigo aparece no JSON do httpbin)
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv, set_key

# ── setup de path para rodar como script direto ──────────────
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

import yaml

_ENV_FILE = _ROOT / ".env"
_CONTAS_FILE = _ROOT / "config" / "contas.yaml"

_TOKEN_URL = "https://api.mercadolibre.com/oauth/token"
_AUTH_BASE  = "https://auth.mercadolivre.com.br/authorization"
# Redirect URI: deve ser EXATAMENTE a mesma cadastrada no App ML.
# Usamos httpbin.org/get pois aceita GET + exibe os params em JSON.
_REDIRECT_URI = "https://httpbin.org/get"


def _load_contas() -> dict:
    with open(_CONTAS_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _env_key(sufixo: str, campo: str) -> str:
    """Ex: _env_key('HAIRPRO', 'APP_ID') -> 'ML_APP_ID_HAIRPRO'"""
    return f"ML_{campo}_{sufixo}"


def _get_or_ask(sufixo: str, campo: str, label: str) -> str:
    """Lê do .env se já existe, senão pede ao usuário."""
    load_dotenv(_ENV_FILE, override=True)
    key = _env_key(sufixo, campo)
    val = os.getenv(key, "").strip()
    if val:
        print(f"  {label}: {'*' * (len(val) - 4) + val[-4:]}  (já está no .env)")
        return val
    val = input(f"  {label}: ").strip()
    if not val:
        print("Valor não informado. Abortando.")
        sys.exit(1)
    return val


def _salvar_env(sufixo: str, campo: str, valor: str) -> None:
    if not _ENV_FILE.exists():
        _ENV_FILE.touch()
    set_key(str(_ENV_FILE), _env_key(sufixo, campo), valor)
    os.environ[_env_key(sufixo, campo)] = valor


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera tokens OAuth ML para uma conta configurada em config/contas.yaml"
    )
    parser.add_argument(
        "--conta", required=True,
        help="ID da conta (ex: best_hair, hair_pro)"
    )
    args = parser.parse_args()

    cfg = _load_contas()
    contas = cfg.get("contas", {})

    if args.conta not in contas:
        print(f"Conta '{args.conta}' não encontrada em config/contas.yaml.")
        print(f"Contas disponíveis: {', '.join(contas.keys())}")
        sys.exit(1)

    conta_cfg = contas[args.conta]
    nome      = conta_cfg["nome"]
    sufixo    = conta_cfg["env_sufixo"]

    print(f"\n{'='*60}")
    print(f"  Gerador de tokens — {nome}")
    print(f"{'='*60}\n")

    # ── Passo 1: credenciais do App ───────────────────────────
    print("PASSO 1 — Credenciais do App ML")
    print("  Acesse: https://developers.mercadolibre.com.br/apps")
    print(f"  Faça login com a conta {nome} e abra o App registrado.\n")

    app_id     = _get_or_ask(sufixo, "APP_ID",        "App ID (ML_APP_ID)")
    client_sec = _get_or_ask(sufixo, "CLIENT_SECRET", "Client Secret")

    _salvar_env(sufixo, "APP_ID",        app_id)
    _salvar_env(sufixo, "CLIENT_SECRET", client_sec)

    # ── Passo 2: gerar link de autorização ───────────────────
    print("\nPASSO 2 — Autorizar o App para a conta")
    params = {
        "response_type": "code",
        "client_id":     app_id,
        "redirect_uri":  _REDIRECT_URI,
    }
    auth_url = f"{_AUTH_BASE}?{urlencode(params)}"

    print(f"\n  Link de autorização:\n  {auth_url}\n")
    print("  ANTES de abrir o link:")
    print("  -> Certifique-se de que o servidor Flask esta rodando:")
    print("    python server.py\n")
    print(f"  1. Abra o link acima no navegador")
    print(f"  2. Faca LOGIN com a conta {nome} (nao com outra!)")
    print("  3. Autorize o App")
    print("  4. O navegador redirecionara para httpbin.org/get")
    print("     e mostrara um JSON. Localize o campo:")
    print('     "args": { "code": "TG-XXXXXXXXXX" }')
    print("     Copie o valor de \"code\" (comeca com TG-)\n")

    try:
        webbrowser.open(auth_url)
        print("  (Tentamos abrir o navegador automaticamente)\n")
    except Exception:
        pass

    code = input("  Cole aqui o código exibido na página de callback: ").strip()
    if not code:
        print("Código não informado. Abortando.")
        sys.exit(1)

    # ── Passo 3: trocar code por tokens ──────────────────────
    print("\nPASSO 3 — Trocando código por tokens...")
    resp = requests.post(_TOKEN_URL, data={
        "grant_type":   "authorization_code",
        "client_id":    app_id,
        "client_secret": client_sec,
        "code":         code,
        "redirect_uri": _REDIRECT_URI,
    }, timeout=20)

    if not resp.ok:
        print(f"\n  Erro na troca de tokens: {resp.status_code}")
        print(f"  Resposta: {resp.text}")
        sys.exit(1)

    data = resp.json()
    access_token  = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    user_id       = str(data.get("user_id", ""))

    if not access_token:
        print(f"\n  access_token não encontrado na resposta: {data}")
        sys.exit(1)

    _salvar_env(sufixo, "ACCESS_TOKEN",  access_token)
    _salvar_env(sufixo, "REFRESH_TOKEN", refresh_token)
    if user_id:
        _salvar_env(sufixo, "SELLER_ID", user_id)

    print(f"\n  [OK] access_token  salvo ({access_token[:8]}...)")
    print(f"  [OK] refresh_token salvo ({refresh_token[:8]}...)" if refresh_token else "  [AVISO] refresh_token ausente")
    print(f"  [OK] seller_id     salvo ({user_id})" if user_id else "")

    print(f"\n{'='*60}")
    print(f"  Tokens da conta {nome} gerados com sucesso!")
    print(f"  Variáveis salvas no .env com sufixo _{sufixo}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
