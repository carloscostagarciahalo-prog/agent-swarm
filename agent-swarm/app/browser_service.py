"""
browser_service.py — el ÚNICO punto de entrada que el orquestador debe
usar para cualquier cosa que toque la web. Los agentes ya no piden
"navega a esta URL y haz X" — emiten una INTENCIÓN de un vocabulario
cerrado, y este módulo decide con qué nivel resolverla:

  1. ¿Hay una integración de API (Composio) que resuelva esto?      -> úsala
  2. ¿Basta con lectura ligera sin navegador (scraping/búsqueda)?    -> úsala
  3. Si no, y solo si no, se recurre al navegador pesado (Nivel 3),
     con cuota por agente y por ciclo, cola de prioridad, y todas las
     protecciones de bloqueo-y-pivote ya existentes.

El navegador es el ÚLTIMO recurso, nunca el primero. Cualquier acción
que pueda resolverse sin él se resuelve sin él.
"""
import logging
import os
import time

import db
import light_scrape
import browser_flows
import composio_tool

log = logging.getLogger("browser_service")

# Vocabulario cerrado de intenciones que un agente puede pedir. Cualquier
# otra cosa se rechaza antes de gastar un solo recurso.
RESEARCH_ACTIONS = {"SCRAPE_PAGE", "SEARCH_WEB", "SEARCH_GOOGLE", "EXTRACT_TRENDING"}
HEAVY_ACTIONS = browser_flows.HEAVY_ACTIONS

# Acciones de Nivel 3 (navegador pesado). Sin límite por agente/ciclo (uso ilimitado).
MAX_HEAVY_ACTIONS_PER_CYCLE = None


# Mapeo de intenciones a herramientas de Composio conocidas. Si el agente
# tiene cuenta asignada y la intención está aquí, se resuelve vía API
# oficial y NUNCA llega al navegador.
COMPOSIO_ACTION_MAP = {
    "POST_CONTENT_TWITTER": "TWITTER_CREATE_TWEET",
    "POST_CONTENT_INSTAGRAM": "INSTAGRAM_CREATE_POST",
}


def _compute_priority(agent_balance: float, agent_number: int, conn, action: str) -> float:
    """Menor valor = se procesa antes en la cola. Se basa en: capital del
    agente (más capital, más prioridad — está demostrando que funciona),
    éxito previo reciente (agentes con más acciones aprobadas últimamente
    suben de prioridad), y tipo de acción (crear cuenta pesa más que
    postear con una cuenta ya existente, así que va después)."""
    balance_score = -agent_balance  # más balance -> número más negativo -> antes en la cola
    recent_success = conn.execute(
        "SELECT COUNT(*) c FROM actions WHERE agent_number = ? AND status = 'approved' "
        "AND timestamp > ?", (agent_number, time.time() - 86400),
    ).fetchone()["c"]
    success_score = -recent_success * 2.0
    action_weight = 5.0 if action in ("CREATE_ACCOUNT", "CREATE_TIKTOK_ACCOUNT", "CREATE_INSTAGRAM_ACCOUNT") else 0.0
    return balance_score + success_score + action_weight


def execute(intent: dict, agent_number: int, agent_balance: float, conn,
            email_lookup, composio_user_id: str | None) -> dict:
    """Punto de entrada único. `intent` = {"action": str, "params": dict}.
    Devuelve siempre {success, data, logs, error} — nunca lanza excepción
    hacia el llamador."""
    action = str(intent.get("action", "")).upper()
    params = intent.get("params", {}) or {}
    logs = [f"intent recibida: {action}"]

    if not action:
        return {"success": False, "data": None, "logs": logs, "error": "acción vacía"}

    # ---------- NIVEL 1: API primero, siempre que exista integración ----------
    composio_tool_slug = COMPOSIO_ACTION_MAP.get(action) or params.get("composio_tool")
    if action == "COMPOSIO_TOOL":
        composio_tool_slug = params.get("tool")
    if composio_tool_slug and composio_user_id and composio_tool.is_configured():
        logs.append(f"resuelto vía API (Composio: {composio_tool_slug}) — navegador no necesario")
        result = composio_tool.execute_tool(
            composio_tool_slug, params.get("arguments") or params.get("composio_arguments", {}), agent_number,
            user_id=composio_user_id,
        )
        return {"success": bool(result.get("ok")), "data": result.get("result"),
                "logs": logs, "error": result.get("error")}

    # CREATE_EMAIL_ACCOUNT es un caso especial: cada agente YA tiene un
    # correo real y funcional (agente<número>@nesion.net) desde que nace,
    # así que esto nunca necesita navegador — se resuelve al instante.
    if action == "CREATE_EMAIL_ACCOUNT":
        address = f"agente{agent_number}@nesion.net"
        logs.append("resuelto al instante: el correo ya existe automáticamente, sin navegador")
        return {"success": True, "data": {"email": address}, "logs": logs, "error": None}

    # ---------- NIVEL 2: scraping ligero para investigación ----------
    if action in RESEARCH_ACTIONS:
        logs.append("resuelto con scraping ligero (sin navegador)")
        if action == "SCRAPE_PAGE":
            result = light_scrape.scrape_page(params.get("url", ""))
        elif action in ("SEARCH_WEB", "SEARCH_GOOGLE"):
            result = light_scrape.search_web(params.get("query", ""))
        else:  # EXTRACT_TRENDING
            result = light_scrape.extract_trending(params.get("url", ""), params.get("selector_hint"))

        if result.get("success"):
            return {"success": True, "data": result.get("data"), "logs": logs, "error": None}

        if action in ("SEARCH_WEB", "SEARCH_GOOGLE"):
            # Una búsqueda no tiene una URL única que visitar, y escalar a
            # un motor de búsqueda real en el navegador choca casi siempre
            # con CAPTCHA — no tiene sentido gastar cuota de Nivel 3 aquí.
            logs.append(f"búsqueda ligera falló ({result.get('error')}) — no se escala a navegador")
            return {"success": False, "data": None, "logs": logs, "error": result.get("error")}

        # SCRAPE_PAGE / EXTRACT_TRENDING sí tienen una URL concreta: si el
        # scraping ligero no bastó (probablemente necesita JS), se escala a
        # Nivel 3 SOLO para lectura, como sesión sin credenciales.
        logs.append(f"scraping ligero insuficiente ({result.get('error')}) — escalando a navegador como último recurso")
        action = "SCRAPE_PAGE"  # cae al flujo genérico de solo-lectura de abajo

    # ---------- NIVEL 3: navegador pesado, último recurso ----------
    if action not in HEAVY_ACTIONS and action != "SCRAPE_PAGE":
        return {"success": False, "data": None, "logs": logs,
                "error": f"acción '{action}' no reconocida (vocabulario cerrado)"}

    cycle_row = conn.execute("SELECT id FROM cycles WHERE status = 'running' ORDER BY id DESC LIMIT 1").fetchone()
    used = db.get_browser_usage(conn, agent_number, cycle_id)
    priority = _compute_priority(agent_balance, agent_number, conn, action)
    db.increment_browser_usage(conn, agent_number, cycle_id)
    logs.append(f"navegador pesado autorizado (uso #{used + 1} este ciclo - ilimitado, prioridad {priority:.1f})")

    if action == "SCRAPE_PAGE":
        # Lectura de solo texto, sin login ni credenciales — la ruta más
        # barata dentro del propio Nivel 3.
        from browser import run_heavy_session
        result = run_heavy_session(params.get("url", ""), goal=None, priority=priority)
    else:
        result = browser_flows.dispatch(action, agent_number, conn, params, priority, email_lookup)

    status = result.get("status")
    success = status in ("ok",)
    return {
        "success": success,
        "data": {"summary": result.get("summary"), "url": result.get("url"),
                  "content_excerpt": result.get("content_excerpt"),
                  "credentials": result.get("credentials")},
        "logs": logs + [f"navegador: status={status}"],
        "error": None if success else (result.get("reason") or status),
    }
