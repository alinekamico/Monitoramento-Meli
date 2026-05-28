"""Configuração comum dos testes."""

from __future__ import annotations

import sys
from pathlib import Path

# Permite `from src.buybox import ...` sem precisar instalar o pacote
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


import pytest
import yaml


@pytest.fixture(scope="session")
def settings() -> dict:
    """Settings.yaml carregado uma vez por sessão."""
    cfg_path = _ROOT / "config" / "settings.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        return yaml.safe_load(f)
