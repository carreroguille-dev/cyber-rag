import asyncio
import json
import os

from openai import AsyncOpenAI

from src.agent.tools import TOOL_DEFINITIONS, execute_tool
from src.agent.synthesizer import procesar_respuesta

# ---------------------------------------------------------------------------
# Configuración del agente
# ---------------------------------------------------------------------------

openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

AGENT_MODEL = "gpt-5.1"
MAX_TOKENS = 2048
TEMPERATURE = 0.0
MAX_TOOL_CALLS = 8
TIMEOUT_SECONDS = 90

SYSTEM_PROMPT = """\
Eres un asistente especializado en la "Guía Nacional de Notificación y \
Gestión de Ciberincidentes" del Gobierno de España, aprobada el 21 de \
febrero de 2020 por el Consejo Nacional de Ciberseguridad.

TU ÚNICA FUENTE DE VERDAD son los chunks del documento que recuperas \
mediante las herramientas disponibles. Nunca respondas con conocimiento \
general si no está respaldado por el documento.

REGLAS DE COMPORTAMIENTO:
1. Antes de responder, usa las herramientas necesarias para encontrar \
   la información relevante en el documento.
2. Si necesitas información de múltiples secciones, realiza múltiples \
   búsquedas.
3. Si una pregunta involucra una tabla (taxonomía, peligrosidad, impacto, \
   plazos), recupera la tabla completa antes de responder.
4. Cita siempre la sección y página de origen de cada afirmación.
5. Si la información no está en el documento, indícalo explícitamente.
6. Nunca inventes valores numéricos (plazos, porcentajes, umbrales).

FORMATO DE RESPUESTA:
- Responde en español.
- Sé conciso pero completo.
- Incluye referencias en formato: (Sección X.X, Tabla Y, página Z).
- Para procedimientos, usa pasos numerados.
- Para tablas de clasificación, reproduce los valores exactos del documento.

Si tras usar las herramientas de búsqueda no encuentras información \
suficiente en el documento, responde honestamente:
"Esta consulta no está cubierta por la Guía Nacional de Notificación \
y Gestión de Ciberincidentes. [Si puedes, añade una orientación breve \
de una frase sobre dónde podría encontrar esa información.]"

Nunca inventes información ni respondas con conocimiento general \
presentándolo como si fuera parte del documento.\
"""

_RESPUESTA_LIMITE_ALCANZADO = (
    "Se alcanzó el límite de búsquedas para esta consulta. "
    "Con la información recuperada hasta ahora: "
)

_RESPUESTA_TIMEOUT = (
    "La consulta tardó demasiado en procesarse. "
    "Por favor, intenta reformular la pregunta de forma más específica."
)


# ---------------------------------------------------------------------------
# Loop ReAct
# ---------------------------------------------------------------------------

async def run_agent(query: str) -> dict:
    """
    Ejecuta el loop ReAct para una query dada.

    Args:
        query: Pregunta del usuario (ya validada por el guardrail).

    Returns:
        {
            "respuesta": str,
            "metadata": {
                "chunks_fuente":      list[str],
                "secciones":          list[str],
                "paginas":            list[int],
                "tool_calls":         int,
                "confianza":          float,
                "tablas_consultadas": list[str],
            }
        }
    """
    all_chunks: list[str] = []
    all_tablas: list[str] = []
    all_paginas: list[int] = []
    all_secciones: list[str] = []
    tool_calls_count = 0

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": query},
    ]

    async def _loop() -> str:
        nonlocal tool_calls_count

        while tool_calls_count < MAX_TOOL_CALLS:
            response = await openai_client.chat.completions.create(
                model=AGENT_MODEL,
                max_completion_tokens=MAX_TOKENS,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )

            choice = response.choices[0]
            msg = choice.message

            messages.append(msg.model_dump(exclude_none=True))

            if choice.finish_reason == "stop" or not msg.tool_calls:
                return msg.content or ""

            for tc in msg.tool_calls:
                tool_calls_count += 1
                tool_name = tc.function.name

                try:
                    tool_args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    tool_args = {}

                resultado_texto, meta = await execute_tool(tool_name, tool_args)

                all_chunks.extend(meta.get("chunks_ids", []))
                for t in meta.get("tablas", []):
                    if t not in all_tablas:
                        all_tablas.append(t)
                all_paginas.extend(meta.get("paginas", []))
                for s in meta.get("secciones", []):
                    if s not in all_secciones:
                        all_secciones.append(s)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": resultado_texto,
                })

            if tool_calls_count >= MAX_TOOL_CALLS:
                break

        messages.append({
            "role": "user",
            "content": (
                "Has alcanzado el límite de búsquedas. "
                "Por favor, responde con la información que has recuperado hasta ahora, "
                "indicando si la respuesta es incompleta."
            ),
        })
        final = await openai_client.chat.completions.create(
            model=AGENT_MODEL,
            max_completion_tokens=MAX_TOKENS,
            messages=messages,
        )
        return final.choices[0].message.content or ""

    try:
        respuesta_raw = await asyncio.wait_for(_loop(), timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        respuesta_raw = _RESPUESTA_TIMEOUT

    respuesta, confianza = procesar_respuesta(respuesta_raw, all_chunks)

    paginas_unicas = sorted(set(all_paginas))

    return {
        "respuesta": respuesta,
        "metadata": {
            "chunks_fuente": list(dict.fromkeys(all_chunks)),
            "secciones": all_secciones,
            "paginas": paginas_unicas,
            "tool_calls": tool_calls_count,
            "confianza": confianza,
            "tablas_consultadas": all_tablas,
        },
    }
