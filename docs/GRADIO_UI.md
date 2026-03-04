# GRADIO UI
## Interfaz de Chat para Prototipo — Guía Nacional de Ciberincidentes

---

## 1. Propósito

Interfaz mínima de chat construida con Gradio para probar el sistema completo
en local o en cualquier entorno Docker. El objetivo es validar el comportamiento
del pipeline completo (guardrail → caché → agente) sin necesidad de construir
un frontend propio.

**Principio**: lo más sencillo posible. Un chat, nada más.

---

## 2. Interfaz

```
┌─────────────────────────────────────────────────────────┐
│   🛡️  Asistente — Guía Nacional de Ciberincidentes      │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  [Historial del chat]                                   │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 👤 ¿A quién reporto si soy empresa privada?     │   │
│  └─────────────────────────────────────────────────┘   │
│  ┌─────────────────────────────────────────────────┐   │
│  │ 🤖 Las empresas privadas deben reportar a       │   │
│  │ INCIBE-CERT según el artículo 11 del Real       │   │
│  │ Decreto-ley 12/2018... (Sección 4.2, p. 13)    │   │
│  └─────────────────────────────────────────────────┘   │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  [ Escribe tu pregunta...                ] [Enviar]     │
│                                    [Limpiar chat]       │
└─────────────────────────────────────────────────────────┘
```

Sin historial persistente entre sesiones, sin autenticación, sin configuración
expuesta al usuario. Solo el chat.

---

## 3. Implementación — `src/ui/app.py`

```python
import gradio as gr
import asyncio
from src.guardrail.rules import capa1
from src.guardrail.classifier import capa2
from src.cache.semantic_cache import cache_lookup, cache_store
from src.agent.agent import run_agent

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


async def responder(mensaje: str, historial: list) -> tuple[str, list]:
    """
    Función principal del chat. Recibe el mensaje del usuario
    y devuelve la respuesta junto con el historial actualizado.
    """

    # --- Guardrail Capa 1 (sin coste) ---
    decision_c1, _ = capa1(mensaje)
    if decision_c1 == "BLOCKED":
        respuesta = "Lo siento, no encuentro nada relacionado con lo que mencionas."
        historial.append((mensaje, respuesta))
        return "", historial

    # --- Guardrail Capa 2 (gpt-5-nano) ---
    resultado_c2 = await capa2(mensaje)
    if resultado_c2["decision"] == "REJECT":
        respuesta = "Lo siento, no encuentro nada relacionado con lo que mencionas."
        historial.append((mensaje, respuesta))
        return "", historial

    # --- Caché semántica ---
    respuesta_cacheada = await cache_lookup(mensaje)
    if respuesta_cacheada:
        historial.append((mensaje, respuesta_cacheada))
        return "", historial

    # --- Agente RAG ---
    resultado = await run_agent(mensaje)
    respuesta = resultado["respuesta"]

    # Guardar en caché para futuras consultas
    await cache_store(mensaje, respuesta, resultado["metadata"])

    historial.append((mensaje, respuesta))
    return "", historial


def construir_interfaz() -> gr.Blocks:
    with gr.Blocks(
        title="Asistente Ciberincidentes",
        theme=gr.themes.Soft(),
        css=".gradio-container { max-width: 800px; margin: auto; }"
    ) as interfaz:

        gr.Markdown(f"# 🛡️ {TITULO}")
        gr.Markdown(DESCRIPCION)

        chatbot = gr.Chatbot(
            value=[(None, MENSAJE_BIENVENIDA)],
            height=500,
            show_label=False,
            bubble_full_width=False
        )

        with gr.Row():
            input_texto = gr.Textbox(
                placeholder=PLACEHOLDER,
                show_label=False,
                scale=9,
                autofocus=True
            )
            btn_enviar = gr.Button("Enviar", variant="primary", scale=1)

        btn_limpiar = gr.Button("🗑️ Limpiar chat", variant="secondary", size="sm")

        # Enviar con botón o con Enter
        btn_enviar.click(
            fn=responder,
            inputs=[input_texto, chatbot],
            outputs=[input_texto, chatbot]
        )
        input_texto.submit(
            fn=responder,
            inputs=[input_texto, chatbot],
            outputs=[input_texto, chatbot]
        )

        # Limpiar historial
        btn_limpiar.click(
            fn=lambda: [(None, MENSAJE_BIENVENIDA)],
            outputs=[chatbot]
        )

    return interfaz


if __name__ == "__main__":
    app = construir_interfaz()
    app.launch(
        server_name="0.0.0.0",  # Necesario para Docker
        server_port=7860,
        show_api=False          # Sin endpoint REST expuesto
    )
```

---

## 4. Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Dependencias del sistema para PyMuPDF
RUN apt-get update && apt-get install -y \
    libmupdf-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 7860

CMD ["python", "-m", "src.ui.app"]
```

---

## 5. docker-compose.yml

```yaml
version: "3.9"

services:

  # Base de datos vectorial
  qdrant:
    image: qdrant/qdrant:latest
    ports:
      - "6333:6333"
    volumes:
      - qdrant_data:/qdrant/storage
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/readyz"]
      interval: 5s
      timeout: 3s
      retries: 10
    restart: unless-stopped

  # Ingesta del PDF (se ejecuta una vez y termina)
  ingest:
    build: .
    command: python -m src.ingestion.indexer
    env_file: .env
    environment:
      - QDRANT_HOST=qdrant
      - QDRANT_PORT=6333
    volumes:
      - ./data:/app/data:ro
    depends_on:
      qdrant:
        condition: service_healthy
    restart: "no"   # Solo se ejecuta una vez

  # Aplicación principal + interfaz Gradio
  app:
    build: .
    command: python -m src.ui.app
    ports:
      - "7860:7860"
    env_file: .env
    environment:
      - QDRANT_HOST=qdrant
      - QDRANT_PORT=6333
    depends_on:
      qdrant:
        condition: service_healthy
      ingest:
        condition: service_completed_successfully
    restart: unless-stopped

volumes:
  qdrant_data:
```

---

## 6. Variables de Entorno — `.env.example`

```bash
# Copiar a .env y rellenar con los valores reales
# cp .env.example .env

# OpenAI — requerido para embeddings, guardrail y agente
OPENAI_API_KEY=sk-...

# Qdrant — configurado automáticamente en Docker
# Solo cambiar si se usa una instancia externa
QDRANT_HOST=localhost
QDRANT_PORT=6333

# Versión del documento (para invalidación del caché)
DOCUMENT_VERSION=1.0
```

---

## 7. Arranque y Uso

### Primera vez (construye imágenes, indexa el PDF y lanza el chat)
```bash
cp .env.example .env
# Editar .env con la API key de OpenAI

docker compose up --build
```

La primera vez el servicio `ingest` procesa el PDF y carga los chunks en Qdrant.
Esto tarda ~1-2 minutos. Una vez completado, el chat estará disponible en:

```
http://localhost:7860
```

### Siguientes arranques (la ingesta no se repite)
```bash
docker compose up
```

El servicio `ingest` detecta que las colecciones ya existen y termina inmediatamente,
por lo que el chat está disponible en segundos.

### Parar el sistema
```bash
docker compose down
```

### Parar y eliminar todos los datos (incluyendo el índice Qdrant)
```bash
docker compose down -v
```

---

## 8. requirements.txt

```
# LLM y embeddings
openai>=1.0.0

# Base de datos vectorial
qdrant-client>=1.7.0

# Parser PDF
pymupdf>=1.23.0

# Interfaz de usuario
gradio>=4.0.0

# Utilidades
python-dotenv>=1.0.0
```