"""
Recibe el correo entrante de agenteN@nesion.net (vía un Cloudflare Worker,
ver README sección 15) y lo asocia al agente correcto extrayendo enlaces
de verificación y códigos numéricos automáticamente.
"""
import re

EMAIL_LOCAL_PART_RE = re.compile(r"^agente(\d+)@", re.I)
LINK_RE = re.compile(r"https?://[^\s\"'<>]+")
CODE_RE = re.compile(r"\b(\d{4,8})\b")


def agent_number_from_address(to_address: str) -> int | None:
    """'agente101@nesion.net' -> 101. None si no cuadra con el patrón."""
    match = EMAIL_LOCAL_PART_RE.match(to_address.strip().lower())
    return int(match.group(1)) if match else None


def extract_link(body_text: str) -> str | None:
    match = LINK_RE.search(body_text or "")
    return match.group(0) if match else None


def extract_code(body_text: str) -> str | None:
    match = CODE_RE.search(body_text or "")
    return match.group(1) if match else None


def parse_and_store(conn, db_module, to_address: str, from_address: str,
                     subject: str, body_text: str) -> bool:
    """Devuelve True si se guardó (el destinatario cuadraba con un agente),
    False si se descarta (dirección no reconocida — evita ruido/spam)."""
    agent_number = agent_number_from_address(to_address)
    if agent_number is None:
        return False
    db_module.save_inbound_email(
        conn, agent_number, from_address, subject or "", body_text or "",
        extract_link(body_text), extract_code(body_text),
    )
    return True
