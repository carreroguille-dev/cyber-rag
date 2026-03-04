# CHUNKING STRATEGY
## Fragmentación del PDF — Guía Nacional de Ciberincidentes

---

## 1. Principio Fundamental

**No fragmentar por tokens. Fragmentar por unidades semánticas del documento.**

El chunking clásico (ventana deslizante de N tokens con overlap) destruye las tablas normativas, separa los criterios de sus definiciones, y rompe las referencias cruzadas que son el núcleo de este documento. Cada chunk debe ser una unidad de conocimiento completa y autocontenida.

---

## 2. Mapa de Tipos de Contenido

Antes de definir la estrategia, se identifican los 6 tipos de contenido presentes en el PDF y su tratamiento diferenciado:

| Tipo | Ejemplos en el PDF | Estrategia |
|---|---|---|
| `narrative` | Secciones 1, 2, 3, intro de secciones | Chunk por subsección, máx. 1000 tokens |
| `table` | Tablas 3-13 | Un chunk por tabla completa, nunca fragmentar |
| `procedure` | Ventanilla única (Sec. 4), fases gestión (Sec. 7) | Chunk por paso/fase, con contexto del procedimiento |
| `criteria_list` | Listas de criterios de peligrosidad/impacto | Un chunk por nivel completo |
| `glossary_term` | Anexo 5 (~40 términos) | Un chunk por término con su definición completa |
| `legal_reference` | Anexo 4 (Marco Regulador) | Un chunk por categoría legal |

---

## 3. Estrategia por Sección del Documento

### Sección 1 — Introducción (páginas 5-6)
**Tipo**: `narrative`

Dos chunks:
- **Chunk 1.1**: Párrafo de organismos competentes + lista de CSIRTs (CCN-CERT, INCIBE-CERT, CNPIC, ESP-DEF-CERT) con sus ámbitos
- **Chunk 1.2**: Definición de la guía como referencia estatal y su alineación normativa

### Sección 2 — Objeto (páginas 7-9)
**Tipo**: `narrative` + `criteria_list`

Tres chunks:
- **Chunk 2.1**: Objeto del documento + audiencia objetivo (lista de destinatarios)
- **Chunk 2.2**: Tablas de Autoridad Competente (Tabla 1) — chunk completo con la tabla
- **Chunk 2.3**: Tabla de CSIRT de referencia (Tabla 2) + ítems que emanan del documento

### Sección 3 — Alcance (página 10)
**Tipo**: `narrative`

Un chunk:
- **Chunk 3.1**: Criterios de notificación obligatoria vs potestativa, con condiciones para cada caso

### Sección 4 — Ventanilla Única (páginas 11-13)
**Tipo**: `procedure`

Cuatro chunks:
- **Chunk 4.0**: Descripción general del sistema de ventanilla única (referencia al flujograma)
- **Chunk 4.1**: Pasos 1-5 del procedimiento de ventanilla única (texto completo del proceso)
- **Chunk 4.2**: Reporte a CCN-CERT (herramienta LUCIA, email, PGP)
- **Chunk 4.3**: Reporte a INCIBE-CERT (buzones, RedIRIS, operadores PIC)
- **Chunk 4.4**: Reporte a ESP-DEF-CERT (canales, URL, urgencia)

### Sección 5 — Taxonomía (páginas 14-17)
**Tipo**: `table`

**CRÍTICO**: La Tabla 3 es la taxonomía completa de ciberincidentes. Debe mantenerse como UN ÚNICO chunk o dividirse por categoría, nunca por número de líneas.

División recomendada por categorías (para no superar límite de tokens):
- **Chunk 5.1**: Tabla 3 — Contenido abusivo + Contenido dañino
- **Chunk 5.2**: Tabla 3 — Obtención de información + Intento de intrusión + Intrusión
- **Chunk 5.3**: Tabla 3 — Disponibilidad + Compromiso de la información + Fraude
- **Chunk 5.4**: Tabla 3 — Vulnerable + Otros (incluye APT)

> Cada chunk de tabla incluye en su metadata la referencia `tabla: "Tabla 3"` y `categoria_inicio` / `categoria_fin` para que el agente sepa que es una tabla dividida.

### Sección 6 — Notificación (páginas 18-28)
**Tipo**: `narrative` + `table` + `criteria_list`

Esta es la sección más densa y crítica. División detallada:

- **Chunk 6.0**: Criterios generales para la notificación (introducción de la sección)
- **Chunk 6.1**: Nivel de peligrosidad — definición del concepto
- **Chunk 6.2**: **Tabla 4 completa** — Criterios de determinación del nivel de peligrosidad (CRÍTICO a BAJO). **Nunca fragmentar.**
- **Chunk 6.3**: Nivel de impacto — definición del concepto + parámetros evaluados (lista de 9 criterios)
- **Chunk 6.4**: **Tabla 5 — Niveles CRÍTICO y MUY ALTO** con todos sus criterios
- **Chunk 6.5**: **Tabla 5 — Niveles ALTO, MEDIO, BAJO y SIN IMPACTO** con todos sus criterios
- **Chunk 6.6**: Niveles con notificación obligatoria (CRÍTICO/MUY ALTO/ALTO) + referencia a normativa
- **Chunk 6.7**: Interacción con CSIRT de referencia (herramientas, ticketing, email)
- **Chunk 6.8**: Apertura del incidente — proceso de registro, identificador único
- **Chunk 6.9**: **Tabla 6 completa** — Información a notificar (todos los campos: Asunto, OSE/PSD, Sector, Fechas, Descripción, Recursos, Origen, Taxonomía, Niveles, Plan de acción, Adjuntos, Regulación, FFCCSE)
- **Chunk 6.10**: **Tabla 7 completa** — Ventana temporal de reporte (plazos por nivel: inmediata, 24/48/72h, 20/40 días)
- **Chunk 6.11**: **Tablas 8 y 9** — Estados de cierre + Tiempos de cierre sin respuesta

> **Nota Tabla 5**: Se divide en dos chunks por volumen, pero ambos comparten `tabla: "Tabla 5"` en metadata. El agente debe recuperar ambos para respuestas sobre criterios de impacto.

### Sección 7 — Gestión (páginas 29-32)
**Tipo**: `procedure`

Un chunk por fase:
- **Chunk 7.0**: Definición de gestión de ciberincidentes + referencia al flujograma de fases
- **Chunk 7.1**: Fase de Preparación (3 pilares + puntos clave)
- **Chunk 7.2**: Fase de Identificación (principios de detección)
- **Chunk 7.3**: Fase de Contención (triaje, decisiones, acciones)
- **Chunk 7.4**: Fase de Mitigación (medidas según tipo, recomendaciones)
- **Chunk 7.5**: Fase de Recuperación (criterios, monitorización post-producción)
- **Chunk 7.6**: Actuaciones Post-Incidente (lecciones aprendidas, informe final)

### Sección 8 — Métricas (páginas 33-35)
**Tipo**: `table`

- **Chunk 8.0**: Introducción a métricas e indicadores
- **Chunk 8.1**: **Tabla 10** — Métrica M1: Alcance del sistema
- **Chunk 8.2**: **Tablas 11** — Métricas M2 y M3: Resolución de incidentes (ALTO/MUY ALTO/CRÍTICO y BAJO/MEDIO)
- **Chunk 8.3**: **Tabla 12** — Métrica M4: Recursos consumidos
- **Chunk 8.4**: **Tabla 13** — Métricas M5 y M6: Gestión de incidentes

### Anexo 1 — Infraestructuras Críticas (páginas 36-39)
**Tipo**: `procedure` + `narrative`

- **Chunk A1.1**: Introducción y ámbito (CNPIC como autoridad)
- **Chunk A1.2**: Comunicaciones obligatorias por peligrosidad (CRÍTICO/MUY ALTO/ALTO)
- **Chunk A1.3**: Comunicaciones obligatorias por impacto
- **Chunk A1.4**: Comunicación al Ministerio Fiscal y otros organismos
- **Chunk A1.5**: Descripción de los flujogramas PIC (gestión y respuesta operativa)

### Anexo 2 — Sector Público (página 40)
**Tipo**: `narrative`

- **Chunk A2.1**: Instrucción Técnica de Seguridad (BOE, Guía CCN-STIC 817) + notificación obligatoria CRÍTICO/MUY ALTO/ALTO al CCN via LUCIA

### Anexo 3 — Sector Privado (página 41)
**Tipo**: `narrative`

- **Chunk A3.1**: Notificación a INCIBE-CERT para entidades privadas y ciudadanía (Art. 11 RDL 12/2018)

### Anexo 4 — Marco Regulador (páginas 42-44)
**Tipo**: `legal_reference`

- **Chunk A4.1**: Normativa de carácter general (Código Penal, LOPD, Telecomunicaciones, RDL 12/2018...)
- **Chunk A4.2**: Normativa Sector Público (CCN, ENS, Ley 40/2015...)
- **Chunk A4.3**: Normativa Infraestructuras Críticas (Ley 8/2011, RD 704/2011, PNPIC...)
- **Chunk A4.4**: Normativa Redes Militares y Defensa (RD 998/2017, OM 10/2013...)

### Anexo 5 — Glosario (páginas 45-54)
**Tipo**: `glossary_term`

Un chunk por término. Aproximadamente 40 chunks con estructura uniforme:

```
término: "Ransomware"
definición: "Se engloba bajo este epígrafe a aquel malware que infecta..."
categoria_glosario: "Contenido Dañino"
```

Términos agrupados por categoría del glosario:
- Contenido Abusivo: Spam, Acoso, Extorsión, Mensajes ofensivos, Delito, Pederastia, Racismo, Apología de la violencia
- Contenido Dañino: Malware, Virus, Gusano, Troyano, Spyware, Rootkit, Dialer, Ransomware, Bot dañino, RAT, C&C, Conexión sospechosa
- Obtención de Información: Escaneo de puertos, Escaneo de red, Sniffing, Transferencia DNS, Ingeniería social, Phishing, Spear Phishing
- Intrusiones: Explotación, SQLi, XSS, CSRF, Defacement, RFI/LFI, Evasión, Pharming, Fuerza bruta, Diccionario, Robo de credenciales
- Disponibilidad: DoS, DDoS, Sabotaje, Inundación SYN/UDP, DNS Open-Resolver, Mala configuración
- Compromiso de la Información: Acceso no autorizado, Modificación, Borrado, Exfiltración, POODLE/FREAK
- Fraude: Uso no autorizado, Suplantación, Propiedad intelectual
- Vulnerabilidades: Tecnología vulnerable, Política de seguridad precaria
- Otros: Ciberterrorismo, Daños PIC, APT/AVT, Dominios DGA, Criptografía, Proxy
- General: Ciberseguridad, Ciberespacio, Redes y sistemas, OSE, PSD, Ciberincidente, Taxonomía, RGPD, OpenPGP, Telnet, RDP, VNC, SNMP, Redis, ICMP

---

## 4. Esquema de Metadatos por Chunk

Cada chunk almacena el siguiente payload en Qdrant:

```json
{
  "chunk_id": "6.9",
  "seccion": "6",
  "subseccion": "6.4",
  "titulo_seccion": "Información a Notificar",
  "titulo_documento": "Guía Nacional de Notificación y Gestión de Ciberincidentes",
  "pagina_inicio": 25,
  "pagina_fin": 26,
  "tipo_contenido": "table",
  "tabla": "Tabla 6",
  "es_tabla_dividida": false,
  "categoria_glosario": null,
  "texto": "...[contenido completo del chunk]...",
  "tokens_aproximados": 420,
  "referencias_cruzadas": ["6.1", "6.2", "6.3"],
  "terminos_clave": ["OSE", "PSD", "taxonomía", "peligrosidad", "impacto", "FFCCSE"]
}
```

### Campos obligatorios
| Campo | Tipo | Descripción |
|---|---|---|
| `chunk_id` | string | Identificador único (ej. "6.9", "A1.2", "glosario.ransomware") |
| `seccion` | string | Sección principal del documento |
| `titulo_seccion` | string | Título legible de la sección |
| `pagina_inicio` | int | Página de inicio en el PDF |
| `pagina_fin` | int | Página de fin en el PDF |
| `tipo_contenido` | enum | `narrative`, `table`, `procedure`, `criteria_list`, `glossary_term`, `legal_reference` |
| `texto` | string | Contenido completo del chunk |

### Campos opcionales pero recomendados
| Campo | Tipo | Descripción |
|---|---|---|
| `tabla` | string | Nombre de la tabla si `tipo_contenido = "table"` |
| `es_tabla_dividida` | bool | True si la tabla ocupa varios chunks |
| `parte_tabla` | int | Número de parte si `es_tabla_dividida = true` (1, 2, ...) |
| `categoria_glosario` | string | Categoría del glosario si `tipo_contenido = "glossary_term"` |
| `referencias_cruzadas` | list[string] | IDs de chunks relacionados |
| `terminos_clave` | list[string] | Términos técnicos presentes (para mejorar BM25) |
| `ambito` | enum | `general`, `sector_publico`, `infraestructuras_criticas`, `sector_privado`, `defensa` |

---

## 5. Reglas de Chunking Inviolables

1. **Las tablas no se fragmentan por tokens**. Si una tabla supera el límite, se divide por filas completas (nunca partiendo una fila a mitad), manteniendo siempre la cabecera en cada parte.

2. **El glosario es atómico por término**. Cada entrada del glosario es un chunk independiente. Nunca agrupar varios términos en un chunk.

3. **Los procedimientos incluyen su contexto**. Un chunk de tipo `procedure` incluye siempre una frase introductoria que indique a qué procedimiento pertenece (ej: "En el marco del proceso de ventanilla única, paso 3:...").

4. **Overlap permitido solo en narrativa**. Los chunks de tipo `narrative` pueden incluir la última frase del chunk anterior como contexto, pero solo en texto narrativo, nunca en tablas.

5. **Las referencias cruzadas se preservan en metadata**. Si un chunk menciona "consultar Tabla 4", el campo `referencias_cruzadas` incluye el chunk_id de la Tabla 4.

---

## 6. Estimación de Chunks

| Sección | Chunks estimados |
|---|---|
| Secciones 1-3 | 7 |
| Sección 4 (Ventanilla) | 5 |
| Sección 5 (Taxonomía) | 4 |
| Sección 6 (Notificación) | 12 |
| Sección 7 (Gestión) | 7 |
| Sección 8 (Métricas) | 5 |
| Anexos 1-4 | 14 |
| Anexo 5 (Glosario) | ~40 |
| **Total estimado** | **~94 chunks** |

Para 94 chunks con embeddings de 1536 dimensiones (text-embedding-3-small), el índice completo ocupa aproximadamente **700KB** en Qdrant. Perfectamente manejable en cualquier instancia Docker.
