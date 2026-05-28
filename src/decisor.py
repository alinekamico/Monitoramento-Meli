"""
Decide se vale participar de uma campanha com base no RC calculado.
"""

from __future__ import annotations

_MOTIVO_ABAIXO  = "RC {rc:.1f}% abaixo do mínimo de {minimo:.0f}%"
_MOTIVO_ACIMA   = "RC {rc:.1f}% >= mínimo de {minimo:.0f}%"
_MOTIVO_SEM_PDV = "Preço de campanha zerado ou custo não encontrado"


def decidir(margem: dict, rc_minimo: float) -> dict:
    """
    Retorna {decisao, motivo}.

    decisao: "ACEITAR" | "RECUSAR"
    """
    rc  = margem.get("rc_pct", 0.0)
    pdv = margem.get("preco_campanha", 0.0)

    if pdv <= 0:
        return {"decisao": "RECUSAR", "motivo": _MOTIVO_SEM_PDV}

    if rc >= rc_minimo:
        return {
            "decisao": "ACEITAR",
            "motivo":  _MOTIVO_ACIMA.format(rc=rc, minimo=rc_minimo),
        }

    return {
        "decisao": "RECUSAR",
        "motivo":  _MOTIVO_ABAIXO.format(rc=rc, minimo=rc_minimo),
    }
