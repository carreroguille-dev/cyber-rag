import asyncio
import os

import gradio as gr
from dotenv import load_dotenv

load_dotenv()

from src.guardrail import guardrail
from src.cache.semantic_cache import cache_lookup, cache_store
from src.agent.agent import run_agent

# ---------------------------------------------------------------------------
# Constantes de la interfaz
# ---------------------------------------------------------------------------

TITULO = "Asistente — Guía Nacional de Notificación y Gestión de Ciberincidentes"

DESCRIPCION = """
Consulta la **Guía Nacional de Notificación y Gestión de Ciberincidentes**
(Consejo Nacional de Ciberseguridad, 2020).

Puedes preguntar sobre clasificación de incidentes, plazos de notificación,
organismos responsables, procedimientos de gestión y más.
"""

PLACEHOLDER = "Ej: ¿Cuánto tiempo tengo para notificar un incidente crítico?"

MENSAJE_BIENVENIDA = (
    "Hola, soy el asistente de la Guía Nacional de Ciberincidentes. "
    "¿En qué puedo ayudarte?"
)


# ---------------------------------------------------------------------------
# Función principal del chat
# ---------------------------------------------------------------------------

async def responder(mensaje: str, historial: list) -> tuple[str, list]:
    """
    Orquesta el pipeline completo para cada mensaje del usuario.

    Args:
        mensaje:   Texto del usuario.
        historial: Historial de la conversación (lista de dicts {role, content}).

    Returns:
        ("", historial_actualizado)  — el string vacío limpia el input box.
    """
    if not mensaje.strip():
        return "", historial

    historial = historial + [{"role": "user", "content": mensaje}]

    resultado_guardrail = await guardrail(mensaje)
    if resultado_guardrail["accion"] == "REJECT":
        respuesta = resultado_guardrail["mensaje"]
        historial = historial + [{"role": "assistant", "content": respuesta}]
        return "", historial

    respuesta_cacheada = await cache_lookup(mensaje)
    if respuesta_cacheada:
        historial = historial + [{"role": "assistant", "content": respuesta_cacheada}]
        return "", historial

    resultado = await run_agent(mensaje)
    respuesta = resultado["respuesta"]

    await cache_store(mensaje, respuesta, resultado["metadata"])

    historial = historial + [{"role": "assistant", "content": respuesta}]
    return "", historial


# ---------------------------------------------------------------------------
# Construcción de la interfaz
# ---------------------------------------------------------------------------

def construir_interfaz() -> gr.Blocks:
    with gr.Blocks(title="Asistente Ciberincidentes") as interfaz:

        gr.Markdown(f"# {TITULO}")
        gr.Markdown(DESCRIPCION)

        chatbot = gr.Chatbot(
            value=[{"role": "assistant", "content": MENSAJE_BIENVENIDA}],
            height=500,
            show_label=False,
        )

        with gr.Row():
            input_texto = gr.Textbox(
                placeholder=PLACEHOLDER,
                show_label=False,
                scale=9,
                autofocus=True,
            )
            btn_enviar = gr.Button("Enviar", variant="primary", scale=1)

        btn_limpiar = gr.Button("🗑️ Limpiar chat", variant="secondary", size="sm")

        btn_enviar.click(
            fn=responder,
            inputs=[input_texto, chatbot],
            outputs=[input_texto, chatbot],
        )

        input_texto.submit(
            fn=responder,
            inputs=[input_texto, chatbot],
            outputs=[input_texto, chatbot],
        )

        btn_limpiar.click(
            fn=lambda: [{"role": "assistant", "content": MENSAJE_BIENVENIDA}],
            outputs=[chatbot],
        )

    return interfaz


# ---------------------------------------------------------------------------
# Punto de entrada
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = construir_interfaz()
    app.launch(
        server_name="0.0.0.0",
        server_port=7860,
        theme=gr.themes.Soft(),
        css=".gradio-container { max-width: 800px; margin: auto; }",
    )
