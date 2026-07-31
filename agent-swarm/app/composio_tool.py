"""
Integración con Composio: da acceso a los agentes a más de 1.000
herramientas con autenticación ya gestionada (Gmail, GitHub, redes
sociales, Notion, Stripe, etc.), en vez de tener que programar cada
integración a mano como con browser.py.

AVISO IMPORTANTE sobre "sin intervención humana":
La primera vez que se conecta una cuenta nueva a un toolkit que usa OAuth
(la mayoría de apps grandes: Gmail, Twitter/X, etc.), la plataforma
externa pide el consentimiento de un humano haciendo clic en su propia
pantalla de login — esto lo exige la propia plataforma (Google, X, etc.),
no Composio ni nosotros, y no hay forma de saltárselo sin violar sus
términos. Esto significa dos formas prácticas de usar esto:

  A) UNA cuenta compartida por varios/todos los agentes (tú autorizas una
     vez, ej. una cuenta de Twitter de la empresa) — sigue siendo
     "autónomo" en el sentido de que los agentes deciden qué publicar y
     cuándo, sin pedirte permiso turno a turno.
  B) Una cuenta por agente — requeriría que tú (u otro humano) hicieras
     el consentimiento OAuth 100 veces, una por agente. No es
     recomendable ni escala bien.

Este módulo está pensado para el patrón (A) por defecto.
"""
import os
import logging
from composio import Composio

log = logging.getLogger("composio_tool")

API_KEY = os.environ.get("COMPOSIO_API_KEY")
# ID de "usuario" compartido en Composio para todo el enjambre (patrón A).
# Cámbialo si prefieres separar agentes en varias cuentas compartidas
# distintas (ej. una para redes sociales, otra para email).
SHARED_USER_ID = os.environ.get("COMPOSIO_SHARED_USER_ID", "agent-swarm")

_client = None


def _get_client() -> Composio:
    global _client
    if not API_KEY:
        raise RuntimeError("Falta COMPOSIO_API_KEY en el entorno.")
    if _client is None:
        _client = Composio(api_key=API_KEY)
    return _client


def is_configured() -> bool:
    return bool(API_KEY)


def list_available_tools(toolkits: list[str] | None = None) -> list:
    """Útil para que TÚ (no el agente) veas qué herramientas están
    conectadas y disponibles antes de dejarlas en manos del enjambre."""
    client = _get_client()
    return client.tools.get(user_id=SHARED_USER_ID, toolkits=toolkits or [])


def execute_tool(tool_slug: str, arguments: dict, agent_number: int, user_id: str | None = None) -> dict:
    """El agente pide ejecutar una herramienta concreta (ej.
    "INSTAGRAM_CREATE_POST") con unos argumentos. Si se pasa `user_id`,
    se ejecuta bajo ESA cuenta concreta del pool (ver db.assign_composio_account);
    si no, cae en la cuenta compartida por defecto. Queda registrado con
    el número de agente que lo pidió, para auditoría en el panel."""
    if not is_configured():
        return {"ok": False, "error": "COMPOSIO_API_KEY no configurada."}
    client = _get_client()
    effective_user_id = user_id or SHARED_USER_ID
    try:
        result = client.tools.execute(
            tool_slug, user_id=effective_user_id, arguments=arguments,
        )
        log.info("Agente #%s ejecutó %s vía Composio (cuenta %s)", agent_number, tool_slug, effective_user_id)
        return {"ok": True, "result": result}
    except Exception as e:  # noqa: BLE001 - fallos de auth, rate limit, etc.
        log.warning("Agente #%s falló ejecutando %s vía Composio: %s", agent_number, tool_slug, e)
        return {"ok": False, "error": str(e)}
