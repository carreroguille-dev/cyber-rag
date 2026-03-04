## Diario de desarrollo

### 1. Planteamiento inicial del reto

La pregunta de partida fue: **¿cómo construir un sistema RAG agéntico fiable sobre un documento normativo complejo**, sin perder información crítica en el proceso de ingestión y chunking.

El primer paso fue **leer el PDF completo** y entender su naturaleza:
- 55 páginas con mezcla de texto narrativo, 13 tablas normativas, flujogramas y un glosario extenso.
- Estructura con secciones y anexos, pero con convenciones propias (pies de tabla después, glosario formateado como lista, etc.).

Desde el principio tuve claro que:
- Un chunker “por tokens” a ciegas era insuficiente.
- El diseño debía estar **documentado antes de implementarse**, de ahí los documentos de diseño en `docs/`.

### 2. Análisis del PDF y decisión de OCR por visión

Al estudiar el PDF, surgieron dos problemas:
- Las tablas y diagramas son demasiado importantes como para fiarse solo de la capa de texto del PDF.
- Librerías como PyMuPDF devuelven texto plano donde las tablas pierden estructura y las columnas se mezclan.

De ahí sale una decisión clave:
- **Usar un modelo de visión (gpt-5.2) como capa de OCR**, página a página.
- Pedir como salida **Markdown estructurado** (headings, listas, tablas, negritas) para que el chunker pueda ser genérico.

Esta decisión marca todo lo demás:
- El chunker no “sabe” nada específico de la Guía, solo entiende estructura Markdown.
- La caché en `data/markdown_cache/` permite iterar sobre el diseño de chunking sin volver a pagar OCR.

### 3. Diseño de la arquitectura antes del código

Antes de escribir código:
- Utilicé **Claude (web)** para iterar en la **arquitectura de alto nivel**:
  - Qué módulos necesitaba (`ingestion`, `retrieval`, `agent`, `guardrail`, `ui`, etc.).
  - Cómo se comunicarían entre sí.
  - Qué responsabilidades tendría cada fichero.
- De esa fase salieron la mayoría de documentos de diseño en `docs/` (chunking, guardrails, esquema de conocimiento, etc.).

Condición autoimpuesta:
- Cualquier duda de arquitectura (cómo modelar un chunk, cómo manejar la caché semántica, cómo orquestar tool calls) **se validaba primero en la capa de diseño** antes de tocar código.

### 4. Implementación incremental siguiendo los diseños

Con la arquitectura clara, pasé a la implementación:
- Con ayuda de **Claude Code**, fui creando los módulos siguiendo los contratos definidos en la documentación.
- Primero la **pipeline de ingesta**:
  - Render de PDF a PNG.
  - OCR a Markdown + caché.
  - Función `build_chunks()` basada en headings, tablas y glosario.
- Después la **indexación en Qdrant** con búsqueda híbrida:
  - Colección `guia_chunks` (knowledge base).
  - Colección `qa_cache` (caché semántica de Q&A).

La idea fue siempre que el código **reflejara lo que está en los documentos de diseño**, no al revés.

### 5. Dockerización y experiencia de uso

No quería que el proyecto dependiera de tener todo el entorno montado a mano, así que:
- Monté un `docker-compose.yml` con:
  - `qdrant` como único datastore.
  - Un servicio `ingest` que ejecuta la pipeline completa de ingesta.
  - Un servicio `app` que expone el agente vía una **UI minimalista en Gradio**.

Objetivo de UX:
- Con un solo comando el usuario debería poder:
  - Levantar Qdrant.
  - Ingestar el PDF (si no está ya indexado).
  - Abrir una interfaz web y empezar a preguntar.

### 6. Iteración, bugs y refinamientos

A partir de ahí, el diario recoge **las fases y problemas más relevantes**:
- Ajustes de Docker (healthchecks, volúmenes en solo lectura, rutas del PDF).
- Adaptación a **Gradio 6.x**, que rompía la API esperada.
- Actualización a la **nueva API de OpenAI** (parámetros deprecados).
- Correcciones en **Qdrant con vectores nombrados** para la caché.
- Bug sutil en el **loop ReAct del agente** con tool calls incompletos.
- Problemas con el **glosario** debido al formato real del OCR.
- Ajustes de **timeout** para preguntas complejas.
- Manejo correcto de **pies de tabla** que aparecen después del bloque.

Cada fase del devlog refleja una combinación de:
- **Hipótesis de diseño**.
- **Problema real encontrado en la práctica**.
- **Cambio concreto en código** para alinear la implementación con el diseño original.

### 7. Estado final del sistema

El resultado es un sistema donde:
- El **chunking está diseñado y documentado por adelantado** y luego implementado fielmente.
- Los **metadatos de chunk** permiten navegar por secciones, tablas y glosario sin hacks.
- La experiencia de uso es:
  - Un comando para levantar todo.
  - Una interfaz web para preguntar.
  - Respuestas siempre justificadas por chunks del documento.

### 8. Mejoras pendientes por falta de tiempo

Hay varias mejoras que considero importantes y que **no he implementado por límite de tiempo**, pero que dejarían el sistema en un estado mucho más sólido:

- **Refactorización para aplicar el Principio de Responsabilidad Única (SRP)**  

  - Separar las distintas capas del sistema para reducir la deuda técnica lo máximo posible. Haciendo el código más testeable, intercambiable y fácil de escalar.

- **Fichero de configuración centralizado**  

  - Unificar en un solo módulo/fichero (`config.yml`, `settings.py` o similar) todos los parámetros que ahora están dispersos. De este modo ajustar el comportamiento del sistema no dependería de tocar código, solo configuración.

- **Trazabilidad de los modelos con Langfuse**

  - En problemas no deterministas, como los que plantean los modelos, es imprescindible la trazabilidad para ver que respuesta, líneas de pensamiento, llamadas y flujos toman los agentes, etiquetar los fallos, optimizar los prompts de sistema y asegurar una mejora continua y un control de los costes.

- **Añadir test para el flujo CI/CD al subir a Github**  

  - Necesario para cuando se realicen cambios y se quiera pasar a producción, evita tener sobrecostos a futuro si se despliega o escala.
