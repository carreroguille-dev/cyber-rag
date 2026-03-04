import re

_CITATION_PATTERN = re.compile(
    r"\((Sección|Tabla|Anexo|Glosario|p\.|pp\.)\s", re.IGNORECASE
)

_RESPUESTA_SIN_INFO = (
    "Esta consulta no está cubierta por la Guía Nacional de Notificación "
    "y Gestión de Ciberincidentes."
)


def calcular_confianza(respuesta: str, chunks_fuente: list[str]) -> float:
    """
    Heurística de confianza:
      - 1.0 si hay citas en la respuesta y chunks recuperados
      - 0.8 si hay chunks pero sin citas detectadas (posible síntesis sin citar)
      - 0.5 si no hay chunks fuente (respuesta del agente sin tool calls)
      - 0.0 si la respuesta indica que no hay información en el documento
    """
    if _RESPUESTA_SIN_INFO in respuesta:
        return 0.0
    if not chunks_fuente:
        return 0.5
    if _CITATION_PATTERN.search(respuesta):
        return 1.0
    return 0.8


def limpiar_respuesta(respuesta: str) -> str:
    """
    Normalización básica del texto de respuesta:
      - Elimina saltos de línea triple o más
      - Elimina espacios en blanco al inicio/fin
    """
    respuesta = re.sub(r"\n{3,}", "\n\n", respuesta)
    return respuesta.strip()


def procesar_respuesta(respuesta_raw: str, chunks_fuente: list[str]) -> tuple[str, float]:
    """
    Aplica limpieza y calcula confianza.

    Args:
        respuesta_raw:  Texto de respuesta generado por el agente.
        chunks_fuente:  IDs de chunks consultados durante el razonamiento.

    Returns:
        (respuesta_limpia, score_confianza)
    """
    respuesta = limpiar_respuesta(respuesta_raw)
    confianza = calcular_confianza(respuesta, chunks_fuente)
    return respuesta, confianza
