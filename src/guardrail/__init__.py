"""
Guardrail — Capa de filtrado de entrada.

Expone la función unificada guardrail() que orquesta ambas capas
y devuelve un resultado estructurado con logs de timing.

Fuente de verdad: GUARDRAIL_DESIGN.md §5
"""

import time

from src.guardrail.rules import capa1
from src.guardrail.classifier import capa2

MENSAJE_RECHAZO = "Lo siento, no encuentro nada relacionado con lo que mencionas."


async def guardrail(query: str) -> dict:
    """
    Orquestador de las dos capas del guardrail.

    Returns:
        {
            "accion":  "PASS" | "REJECT",
            "query":   str | None,      # None si REJECT
            "mensaje": str | None,      # None si PASS
            "log": {
                "capa":      int,
                "razon":     str,
                "tiempo_ms": float,
            }
        }
    """
    inicio = time.time()

    # Capa 1: reglas deterministas (sin coste de LLM)
    decision_c1, razon_c1 = capa1(query)
    if decision_c1 == "BLOCKED":
        return {
            "accion": "REJECT",
            "query": None,
            "mensaje": MENSAJE_RECHAZO,
            "log": {
                "capa": 1,
                "razon": razon_c1,
                "tiempo_ms": (time.time() - inicio) * 1000,
            },
        }

    # Capa 2: clasificador LLM
    resultado = await capa2(query)
    if resultado["decision"] == "REJECT":
        return {
            "accion": "REJECT",
            "query": None,
            "mensaje": MENSAJE_RECHAZO,
            "log": {
                "capa": 2,
                "razon": resultado["razon"],
                "tiempo_ms": (time.time() - inicio) * 1000,
            },
        }

    return {
        "accion": "PASS",
        "query": query,
        "mensaje": None,
        "log": {
            "capa": 2,
            "razon": resultado["razon"],
            "tiempo_ms": (time.time() - inicio) * 1000,
        },
    }
