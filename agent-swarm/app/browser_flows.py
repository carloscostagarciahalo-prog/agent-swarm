"""
NIVEL 3 — Flujos predefinidos. Los agentes NO improvisan una navegación
libre; piden una ACCIÓN de un vocabulario cerrado, y aquí hay un flujo
concreto y probado para cada una. Si no hay flujo específico para la
acción pedida, se usa un flujo genérico (igual de acotado: bloqueo-y-
pivote, límite de pasos, timeout duro), nunca "haz lo que haga falta".
"""
import db
from browser import run_heavy_session, domain_of


def flujo_crear_cuenta(agent_number: int, conn, target_url: str, goal: str, priority: float,
                        email_lookup) -> dict:
    """CREATE_*_ACCOUNT — registro guiado paso a paso, con credenciales
    nuevas que se persisten al terminar."""
    domain = domain_of(target_url)
    result = run_heavy_session(
        target_url, goal=goal or "crea una cuenta nueva en este sitio",
        saved_creds=None, email_lookup=email_lookup, priority=priority,
    )
    if result.get("credentials"):
        creds = result["credentials"]
        db.save_credentials(conn, agent_number, domain, creds.get("username", ""), creds.get("password", ""))
    return result


def flujo_login(agent_number: int, conn, target_url: str, priority: float, email_lookup) -> dict:
    """LOGIN_ACCOUNT — usa SIEMPRE credenciales ya guardadas; si no hay
    ninguna, no intenta registrar nada (eso es flujo_crear_cuenta, una
    acción distinta) — se rinde limpio."""
    domain = domain_of(target_url)
    saved = db.get_credentials(conn, agent_number, domain)
    if not saved:
        return {"status": "gave_up", "reason": "no hay credenciales guardadas para este dominio"}
    return run_heavy_session(
        target_url, goal="inicia sesión con las credenciales guardadas",
        saved_creds=saved, email_lookup=email_lookup, priority=priority,
    )


def flujo_postear(agent_number: int, conn, target_url: str, contenido: str, priority: float,
                   email_lookup) -> dict:
    """POST_CONTENT — requiere sesión ya iniciada (credenciales guardadas
    de un flujo_crear_cuenta o flujo_login anterior)."""
    domain = domain_of(target_url)
    saved = db.get_credentials(conn, agent_number, domain)
    if not saved:
        return {"status": "gave_up", "reason": "no hay cuenta creada todavía en este sitio"}
    goal = f"inicia sesión si hace falta y publica este contenido: {contenido[:200]}"
    return run_heavy_session(
        target_url, goal=goal, saved_creds=saved, email_lookup=email_lookup, priority=priority,
    )


def flujo_generico(agent_number: int, conn, target_url: str, goal: str, priority: float,
                    email_lookup) -> dict:
    """Cuando la acción pedida no tiene un flujo específico todavía —
    sigue acotado (mismos límites de pasos/tiempo/bloqueo), pero sin una
    plantilla optimizada para ese caso concreto."""
    domain = domain_of(target_url)
    saved = db.get_credentials(conn, agent_number, domain)
    result = run_heavy_session(
        target_url, goal=goal, saved_creds=saved, email_lookup=email_lookup, priority=priority,
    )
    if result.get("credentials"):
        creds = result["credentials"]
        db.save_credentials(conn, agent_number, domain, creds.get("username", ""), creds.get("password", ""))
    return result


# Vocabulario cerrado de acciones que SÍ pueden llegar a Nivel 3 (con o
# sin flujo específico). Cualquier otra cosa se rechaza antes de gastar
# un solo hueco del pool — ver browser_service.py.
HEAVY_ACTIONS = {
    "CREATE_EMAIL_ACCOUNT",   # en realidad resuelto sin navegador, ver browser_service.py
    "CREATE_TIKTOK_ACCOUNT",
    "CREATE_INSTAGRAM_ACCOUNT",
    "CREATE_ACCOUNT",         # genérico, cualquier plataforma
    "LOGIN_ACCOUNT",
    "POST_CONTENT",
}


def dispatch(action: str, agent_number: int, conn, params: dict, priority: float, email_lookup) -> dict:
    target_url = params.get("target_url", "")
    goal = params.get("goal", "")

    if action == "LOGIN_ACCOUNT":
        return flujo_login(agent_number, conn, target_url, priority, email_lookup)
    if action == "POST_CONTENT":
        return flujo_postear(agent_number, conn, target_url, params.get("contenido", ""), priority, email_lookup)
    if action in ("CREATE_TIKTOK_ACCOUNT", "CREATE_INSTAGRAM_ACCOUNT", "CREATE_ACCOUNT"):
        return flujo_crear_cuenta(agent_number, conn, target_url, goal, priority, email_lookup)
    return flujo_generico(agent_number, conn, target_url, goal, priority, email_lookup)
