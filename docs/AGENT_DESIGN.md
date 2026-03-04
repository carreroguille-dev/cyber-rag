# AGENT DESIGN
## Agente Orquestador RAG — Loop ReAct y Tools

---

## 1. Modelo y Configuración

| Parámetro | Valor | Justificación |
|---|---|---|
| Modelo | `gpt-5.1` | Capacidad de razonamiento multi-paso y tool calling nativo |
| Max tokens respuesta | 2048 | Suficiente para síntesis con citas, evita respuestas excesivamente largas |
| Temperature | 0.0 | Respuestas deterministas para un documento normativo |
| Max tool calls por query | 8 | Límite de seguridad para evitar loops infinitos |
| Timeout total | 30s | SLA razonable para queries complejas |

---

## 2. System Prompt del Agente

```
Eres un asistente especializado en la "Guía Nacional de Notificación y 
Gestión de Ciberincidentes" del Gobierno de España, aprobada el 21 de 
febrero de 2020 por el Consejo Nacional de Ciberseguridad.

TU ÚNICA FUENTE DE VERDAD son los chunks del documento que recuperas 
mediante las herramientas disponibles. Nunca respondas con conocimiento 
general si no está respaldado por el documento.

REGLAS DE COMPORTAMIENTO:
1. Antes de responder, usa las herramientas necesarias para encontrar 
   la información relevante en el documento.
2. Si necesitas información de múltiples secciones, realiza múltiples 
   búsquedas.
3. Si una pregunta involucra una tabla (taxonomía, peligrosidad, impacto, 
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
```

---

## 3. Loop ReAct

El agente sigue el patrón **Thought → Action → Observation → Thought → ...**

```
┌─────────────────────────────────────────────────────────┐
│                    LOOP ReAct                           │
│                                                         │
│  Input: Query del usuario                               │
│                                                         │
│  ┌─────────────────────────────────────────────────┐   │
│  │  THOUGHT: ¿Qué información necesito?            │   │
│  │  ¿Qué herramienta es más adecuada?              │   │
│  └─────────────────┬───────────────────────────────┘   │
│                    │                                    │
│                    ▼                                    │
│  ┌─────────────────────────────────────────────────┐   │
│  │  ACTION: Llamada a tool                         │   │
│  │  (hybrid_search / get_table / get_section ...)  │   │
│  └─────────────────┬───────────────────────────────┘   │
│                    │                                    │
│                    ▼                                    │
│  ┌─────────────────────────────────────────────────┐   │
│  │  OBSERVATION: Chunks recuperados                │   │
│  │  Evaluación: ¿Tengo suficiente información?     │   │
│  └─────────────────┬───────────────────────────────┘   │
│                    │                                    │
│         ┌──────────┴──────────┐                        │
│         │ NO                  │ SÍ                      │
│         ▼                     ▼                        │
│    Volver a THOUGHT      FINAL ANSWER                  │
│    (nueva tool call)     (síntesis con citas)          │
│                                                         │
│  Límite: max 8 tool calls                              │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Definición de Tools

### Tool 1: `hybrid_search`
**Uso**: Búsqueda principal. Combina semántica + léxica. Usar como primera acción en casi toda query.

```python
{
    "name": "hybrid_search",
    "description": """Realiza una búsqueda híbrida (semántica + léxica BM25) en el documento.
    Devuelve los chunks más relevantes para la query. 
    Usar como primera acción para la mayoría de consultas.
    Especialmente útil para preguntas sobre procedimientos, organismos, 
    criterios y conceptos generales.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Términos de búsqueda en español. Puede ser la pregunta del usuario o una sub-query más específica."
            },
            "k": {
                "type": "integer",
                "description": "Número de chunks a recuperar. Default: 5. Aumentar a 8-10 para preguntas amplias.",
                "default": 5
            },
            "filtro_seccion": {
                "type": "string",
                "description": "Opcional. Filtrar por sección del documento (ej: '6', 'A1'). Usar cuando se conoce la sección relevante.",
                "default": null
            },
            "filtro_tipo": {
                "type": "string",
                "enum": ["narrative", "table", "procedure", "criteria_list", "glossary_term", "legal_reference"],
                "description": "Opcional. Filtrar por tipo de contenido.",
                "default": null
            }
        },
        "required": ["query"]
    }
}
```

### Tool 2: `get_table`
**Uso**: Recuperar una tabla completa por nombre. Usar cuando la query involucra clasificaciones, niveles, plazos o criterios tabulares.

```python
{
    "name": "get_table",
    "description": """Recupera el contenido completo de una tabla específica del documento.
    Las tablas disponibles son:
    - Tabla 1: Autoridad Competente por tipo de operador
    - Tabla 2: CSIRT de referencia por tipo de operador
    - Tabla 3: Clasificación/Taxonomía de ciberincidentes (puede estar en partes)
    - Tabla 4: Criterios de determinación del nivel de PELIGROSIDAD
    - Tabla 5: Criterios de determinación del nivel de IMPACTO
    - Tabla 6: Información a notificar en un ciberincidente
    - Tabla 7: Ventana temporal de reporte (plazos de notificación)
    - Tabla 8: Estados de los ciberincidentes
    - Tabla 9: Tiempos de cierre del ciberincidente sin respuesta
    - Tabla 10-13: Métricas e indicadores (M1 a M6)
    Usar cuando la pregunta involucra clasificaciones, niveles, plazos o criterios.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "nombre_tabla": {
                "type": "string",
                "description": "Nombre exacto de la tabla (ej: 'Tabla 4', 'Tabla 7')"
            }
        },
        "required": ["nombre_tabla"]
    }
}
```

### Tool 3: `get_section`
**Uso**: Recuperar todos los chunks de una sección o subsección completa.

```python
{
    "name": "get_section",
    "description": """Recupera todos los fragmentos de una sección o subsección completa del documento.
    Útil cuando se necesita una visión completa de una sección, no solo un fragmento.
    Secciones disponibles: '1', '2', '3', '4', '5', '6', '7', '8', 
    'A1' (Anexo 1 - PIC), 'A2' (Sector Público), 'A3' (Sector Privado), 
    'A4' (Marco Regulador), 'A5' (Glosario).
    Subsecciones: '6.1', '6.2', '7.1', etc.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "seccion_id": {
                "type": "string",
                "description": "ID de sección ('6') o subsección ('6.1')"
            }
        },
        "required": ["seccion_id"]
    }
}
```

### Tool 4: `get_context_window`
**Uso**: Expandir contexto alrededor de un chunk específico. Usar cuando un chunk contiene una referencia cruzada que necesita contexto adicional.

```python
{
    "name": "get_context_window",
    "description": """Recupera los chunks circundantes a un chunk específico para obtener contexto adicional.
    Útil cuando un chunk contiene una referencia cruzada o cuando se necesita 
    el contexto antes/después de un fragmento específico.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "chunk_id": {
                "type": "string",
                "description": "ID del chunk central (ej: '6.5', 'A1.2')"
            },
            "window": {
                "type": "integer",
                "description": "Número de chunks a recuperar en cada dirección. Default: 2.",
                "default": 2
            }
        },
        "required": ["chunk_id"]
    }
}
```

### Tool 5: `glossary_lookup`
**Uso**: Buscar definición de un término específico en el glosario (Anexo 5).

```python
{
    "name": "glossary_lookup",
    "description": """Busca la definición de uno o varios términos técnicos en el glosario 
    de la guía (Anexo 5).
    Términos disponibles incluyen: malware, ransomware, APT, phishing, DoS, DDoS, 
    rootkit, botnet, C&C, XSS, SQLi, CSIRT, y ~35 términos más.
    Usar cuando la pregunta es sobre el significado de un término técnico.""",
    "input_schema": {
        "type": "object",
        "properties": {
            "termino": {
                "type": "string",
                "description": "Término a buscar en el glosario (ej: 'ransomware', 'APT', 'phishing')"
            }
        },
        "required": ["termino"]
    }
}
```

---

## 5. Árbol de Decisión para Selección de Tools

```
Query recibida
     │
     ├── ¿Pregunta sobre significado/definición de un término?
     │   └── → glossary_lookup(término)
     │
     ├── ¿Pregunta sobre una tabla específica? 
     │   (clasificación, niveles, plazos, criterios, métricas)
     │   └── → get_table("Tabla N")
     │         + hybrid_search si necesita contexto adicional
     │
     ├── ¿Pregunta sobre un organismo o procedimiento completo?
     │   └── → hybrid_search + get_section si el resultado es parcial
     │
     ├── ¿Pregunta multi-parte o que referencia varias secciones?
     │   └── → hybrid_search (query principal)
     │         + hybrid_search (sub-queries por parte)
     │         + get_context_window si hay referencias cruzadas
     │
     └── ¿Pregunta general o ambigua?
         └── → hybrid_search(query original, k=8)
               Evaluar resultados → ajustar con tools adicionales
```

---

## 6. Ejemplos de Razonamiento del Agente

### Ejemplo A — Pregunta simple sobre plazo
**Query**: "¿En cuánto tiempo hay que notificar un incidente MUY ALTO?"

```
Thought: La pregunta es sobre plazos de notificación por nivel de peligrosidad.
         Eso está en la Tabla 7. Debo recuperarla completa.

Action: get_table("Tabla 7")

Observation: [Tabla 7 completa con plazos por nivel]
             - MUY ALTO: notificación inicial INMEDIATA, 
               intermedia 72 horas, final 40 días

Thought: Tengo la información completa. Puedo responder con precisión.

Final Answer: Según la Tabla 7 (Ventana temporal de reporte, página 27),
para un incidente con nivel MUY ALTO:
- Notificación inicial: inmediata (desde el conocimiento del incidente)
- Notificación intermedia: en 72 horas
- Notificación final: en 40 días
Los plazos de notificación intermedia y final se cuentan desde la remisión 
de la notificación inicial.
```

### Ejemplo B — Pregunta compleja multi-sección
**Query**: "Tengo un ataque de ransomware en el 60% de mis sistemas. ¿Qué nivel tiene y a quién reporto?"

```
Thought: Pregunta multi-parte. Necesito:
         1. Nivel de peligrosidad del ransomware (Tabla 4)
         2. Nivel de impacto al 60% de sistemas (Tabla 5)
         3. A quién reportar según el tipo de entidad (sección 4)
         Empiezo con la peligrosidad del ransomware.

Action: get_table("Tabla 4")

Observation: [Tabla 4] Ransomware → clasificado como "Modificación no 
             autorizada de información" o "Código dañino - Sistema infectado" 
             → Nivel ALTO

Thought: Tengo la peligrosidad. Ahora el impacto al 60% de sistemas.

Action: get_table("Tabla 5")

Observation: [Tabla 5] 60% de sistemas afectados → supera el umbral del 50% 
             para nivel ALTO pero no llega al 75% para MUY ALTO → Nivel ALTO

Thought: Tengo ambos niveles (ALTO en peligrosidad e impacto). 
         Ahora necesito el procedimiento de reporte.

Action: hybrid_search("a quién notificar incidente peligrosidad alto", filtro_seccion="4")

Observation: [Chunks 4.1, 4.2, 4.3] Ventanilla única: reportar al CSIRT de 
             referencia (CCN-CERT o INCIBE-CERT según tipo de entidad). 
             Notificación obligatoria para nivel ALTO.

Thought: Tengo toda la información necesaria.

Final Answer: [Respuesta completa con los tres bloques de información + citas]
```

### Ejemplo C — Definición + procedimiento
**Query**: "¿Qué es un APT y cómo se clasifica según la guía?"

```
Thought: Pregunta de dos partes: definición + clasificación.

Action: glossary_lookup("APT")

Observation: [Glosario] APT (Advanced Persistent Threat): ataques dirigidos 
             contra organizaciones concretas, sofisticados, con ingeniería social...

Action: get_table("Tabla 4")  [para ver el nivel de peligrosidad]

Observation: [Tabla 4] APT → clasificación "Otros" → Nivel CRÍTICO

Thought: Tengo definición y nivel. Puedo responder.

Final Answer: Definición + nivel CRÍTICO con citas al Glosario (Anexo 5) 
             y Tabla 4 (página 19).
```

---

## 7. Síntesis de Respuesta

### Estructura de respuesta recomendada

```
[Respuesta directa a la pregunta - 1-2 frases]

[Detalle con información del documento]
- Para procedimientos: pasos numerados
- Para clasificaciones: reproducir valores exactos
- Para definiciones: la definición completa

[Fuentes]
(Sección X.X, Tabla Y, páginas Z-W)
```

### Reglas de citado

1. Toda afirmación factual debe tener una cita de sección o tabla.
2. Los valores numéricos (plazos, porcentajes, umbrales) siempre van seguidos de su cita.
3. Los nombres de organismos (CCN-CERT, CNPIC, etc.) incluyen su ámbito competencial la primera vez que aparecen.
4. Si una respuesta sintetiza múltiples secciones, se citan todas al final.

### Formato de cita
```
(Sección 6.5, Tabla 7, p. 27)
(Tabla 4, p. 19)
(Anexo 1, p. 37)
(Glosario - Anexo 5, p. 47)
```

---

## 8. Gestión de Casos Límite

### Información insuficiente en el documento
Si tras 3+ tool calls no se encuentra información suficiente:

```
"La información específica sobre [X] no se encuentra en la Guía Nacional 
de Notificación y Gestión de Ciberincidentes. La guía cubre principalmente 
[describir alcance real]. Para esta consulta, podría ser más adecuado 
consultar [orientación general si procede]."
```

### Pregunta con múltiples interpretaciones
Antes de responder, el agente indica la interpretación asumida:

```
"Interpreto tu pregunta como [interpretación]. Si te referías a [alternativa], 
indícamelo.

[Respuesta basada en la interpretación asumida]"
```

### Tabla dividida en múltiples chunks
Cuando `es_tabla_dividida = true`, el agente recupera todas las partes antes de responder y lo indica:

```
Thought: La Tabla 3 está dividida en partes. Debo recuperar todas.
Action: get_table("Tabla 3")  → recupera partes 1, 2, 3, 4 automáticamente
```

---

## 9. Metadata de Respuesta (para el caché)

El agente devuelve junto con la respuesta texto un objeto de metadata:

```python
{
    "respuesta": "...[texto de respuesta]...",
    "metadata": {
        "chunks_fuente": ["6.10", "6.6", "6.2"],
        "secciones": ["6.5", "6.1.3"],
        "paginas": [27, 23, 19],
        "tool_calls": 2,
        "confianza": 0.95,  # 1.0 si toda la info está en el documento, < 1.0 si hubo inferencia
        "tablas_consultadas": ["Tabla 7", "Tabla 4"]
    }
}
```

Este objeto es consumido por la capa de caché para almacenar la respuesta con trazabilidad completa.
