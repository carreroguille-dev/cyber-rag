import re

INJECTION_PATTERNS = [
    r"ignora\s+(tus\s+)?(instrucciones|reglas|restricciones|contexto)",
    r"olvida\s+(lo\s+que|todo\s+lo\s+que|tus)\s+(te\s+)?(dijeron|dije|anteriores?)",
    r"(sin|fuera\s+de)\s+(restricciones|límites|filtros)",
    r"jailbreak",
    r"DAN\s*[\:\-]",
    r"developer\s+mode",
    r"modo\s+(desarrollador|debug|sin\s+filtros)",

    r"(muestra|imprime|repite|dime)\s+(tu\s+)?(system\s+prompt|instrucciones\s+del\s+sistema)",
    r"(show|print|repeat)\s+(your\s+)?(system\s+prompt|instructions)",

    r"\[INST\]",
    r"<\|im_start\|>",
    r"<\|system\|>",
    r"###\s*(Instruction|System|Human|Assistant)\s*:",
]

MIN_TOKENS = 2
MAX_TOKENS = 600


def detectar_injection(query: str) -> bool:
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, query, re.IGNORECASE):
            return True
    return False


def validar_longitud(query: str) -> bool:
    tokens = len(query.split())
    return MIN_TOKENS <= tokens <= MAX_TOKENS


def capa1(query: str) -> tuple[str, str]:
    """
    Returns:
        ("BLOCKED", "INVALID_LENGTH")    — query demasiado corta o larga
        ("BLOCKED", "INJECTION_PATTERN") — patrón de injection detectado
        ("PASS",    "OK")                — pasa a Capa 2
    """
    if not validar_longitud(query):
        return "BLOCKED", "INVALID_LENGTH"

    if detectar_injection(query):
        return "BLOCKED", "INJECTION_PATTERN"

    return "PASS", "OK"
