"""
NIVEL 3 - Navegacion pesada (con estado): el ultimo recurso.

Este modulo YA NO es de acceso directo para los agentes. Es el motor de
bajo nivel que usa browser_flows.py para ejecutar flujos predefinidos
(crear cuenta, login, publicar) cuando ni una API (Composio) ni el
scraping ligero (light_scrape.py) bastan. Los agentes nunca llaman a
nada de aqui directamente - solo emiten una INTENCION estructurada que
browser_service.py decide si merece llegar hasta este nivel.

1. POOL CON COLA DE PRIORIDAD: las sesiones se procesan segun prioridad
   (capital del agente, exito previo, tipo de accion), no solo por orden
   de llegada. En modo LOCAL se fuerza a 1 sesion concurrente - abrir
   varios Chromium a la vez en una e2-micro de 1GB de RAM la tumbaria;
   en modo REMOTO (Browserless u otro) si se permiten varias en paralelo.
2. TIMEOUT DURO POR SESION: presupuesto de tiempo total, no solo por
   accion individual - si se agota, se corta y se libera el recurso.
3. BLOQUEO-Y-PIVOTE: igual que antes - CAPTCHA o aviso de "no bots"
   corta la sesion al momento, sin reintentar ni rodearlo.
"""
import logging
import os
import queue
import re
import threading
import time

from playwright.sync_api import sync_playwright
from mistral_client import ask_mistral

log = logging.getLogger("browser")

CAPTCHA_MARKERS = [
    "recaptcha", "hcaptcha", "captcha", "verify you are human",
    "prove you're not a robot", "cf-turnstile", "are you a robot",
]

NO_BOTS_MARKERS = [
    "automated access is prohibited", "no bots", "bots are not permitted",
    "acceso automatizado no esta permitido", "prohibido el uso de bots",
    "not intended for automated", "api access only", "scraping is prohibited",
]

MAX_INTERACTIVE_STEPS = 6
SESSION_HARD_TIMEOUT_SECONDS = 180

REMOTE_WS_ENDPOINT = os.environ.get("BROWSER_REMOTE_WS_ENDPOINT")
_configured_pool_size = int(os.environ.get("BROWSER_POOL_SIZE", "3"))
if REMOTE_WS_ENDPOINT:
    POOL_SIZE = max(1, _configured_pool_size)
else:
    if _configured_pool_size > 1:
        log.warning(
            "BROWSER_POOL_SIZE=%s ignorado en modo local: se fuerza a 1 sesion "
            "concurrente para no agotar la RAM de la e2-micro. Configura "
            "BROWSER_REMOTE_WS_ENDPOINT si quieres varias sesiones en paralelo de verdad.",
            _configured_pool_size,
        )
    POOL_SIZE = 1


class BlockedError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


def _page_text_lower(page):
    try:
        return page.inner_text("body").lower()
    except Exception:
        return ""


def check_for_block(page):
    text = _page_text_lower(page)
    for marker in CAPTCHA_MARKERS:
        if marker in text:
            raise BlockedError("CAPTCHA detectado ('%s'): se abandona esta via, no se intenta resolver." % marker)
    for frame in page.frames:
        if re.search(r"captcha|turnstile", frame.url, re.I):
            raise BlockedError("Iframe de CAPTCHA detectado: se abandona esta via.")
    for marker in NO_BOTS_MARKERS:
        if marker in text:
            raise BlockedError("El sitio indica que no permite bots ('%s'): se abandona, no se insiste." % marker)


def _interactive_elements(page, limit=25):
    els = page.query_selector_all("a, button, input, select, textarea, [role=button]")
    result = []
    for i, el in enumerate(els[:limit]):
        try:
            tag = el.evaluate("e => e.tagName.toLowerCase()")
            text = (el.inner_text() or "").strip()[:60] if tag != "input" else ""
            placeholder = el.get_attribute("placeholder") or ""
            input_type = el.get_attribute("type") or ""
            name = el.get_attribute("name") or el.get_attribute("id") or ""
            result.append({"index": i, "tag": tag, "text": text,
                            "placeholder": placeholder, "type": input_type, "name": name})
        except Exception:
            continue
    return result


def _selector_for_index(page, index):
    return "(a, button, input, select, textarea, [role=button]) >> nth=%s" % index


def _is_credential_field(el):
    if el.get("type") == "password":
        return "password"
    hay = ("%s %s %s" % (el.get('type',''), el.get('name',''), el.get('placeholder',''))).lower()
    if el.get("type") == "email" or "email" in hay or "user" in hay or "usuario" in hay:
        return "username"
    return None


def _decide_next_step(goal, url, elements, step, has_saved_creds, email_context):
    creds_note = (
        "Ya existen credenciales guardadas para este sitio de un turno anterior. "
        "Si vas a iniciar sesion (no a registrar una cuenta nueva), usa como "
        "\"value\" literalmente el texto <<SAVED_USERNAME>> o <<SAVED_PASSWORD>> "
        "en el campo correspondiente."
        if has_saved_creds else
        "No hay credenciales guardadas todavia para este sitio. Si vas a crear "
        "una cuenta nueva, elige tu un usuario/email y una contrasena razonables "
        "(no uses datos personales reales de nadie). Tienes correo real: "
        "agente<tu numero>@nesion.net."
    )
    prompt = """Estas completando este objetivo en un navegador web: %s

URL actual: %s
Paso %s de %s maximo.
%s
%s
Elementos disponibles en la pagina (usa su "index" para referenciarlos):
%s

Responde SOLO con JSON, una de estas formas:
{"action": "fill", "index": N, "value": "texto a escribir"}
{"action": "click", "index": N}
{"action": "check_email", "reason": "por que necesitas revisar tu correo ahora"}
{"action": "done", "summary": "que se logro"}
{"action": "give_up", "reason": "por que no se puede continuar"}

No inventes datos de pago ni de identidad real de terceros.""" % (
        goal, url, step + 1, MAX_INTERACTIVE_STEPS, creds_note, email_context, elements
    )
    raw = ask_mistral(prompt, max_tokens=200, temperature=0.3)
    try:
        return __import__("json").loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception:
        return {"action": "give_up", "reason": "respuesta del modelo no interpretable"}


def _run_interactive(page, goal, saved_creds, email_lookup, deadline):
    log_steps = []
    discovered = {}
    email_context = ""

    for step in range(MAX_INTERACTIVE_STEPS):
        if time.time() > deadline:
            return {"status": "timeout", "url": page.url, "steps": log_steps,
                     "reason": "presupuesto de tiempo de sesion agotado"}
        check_for_block(page)
        elements = _interactive_elements(page)
        decision = _decide_next_step(goal, page.url, elements, step, bool(saved_creds), email_context)
        action = decision.get("action")
        log_steps.append(decision)
        email_context = ""

        if action == "done":
            return {"status": "ok", "url": page.url, "steps": log_steps,
                     "summary": decision.get("summary", ""), "credentials": discovered or None}
        if action == "give_up":
            return {"status": "gave_up", "url": page.url, "steps": log_steps,
                     "reason": decision.get("reason", ""), "credentials": discovered or None}
        if action == "check_email":
            if email_lookup is None:
                email_context = "No hay bandeja de correo configurada todavia."
                continue
            email = email_lookup()
            email_context = (
                "Bandeja revisada: no ha llegado nada nuevo todavia." if not email else
                "Ultimo correo - asunto: %r, enlace: %s, codigo: %s." % (
                    email.get('subject', ''), email.get('extracted_link') or 'ninguno',
                    email.get('extracted_code') or 'ninguno')
            )
            continue
        if action in ("fill", "click"):
            idx = decision.get("index")
            if idx is None or idx >= len(elements):
                continue
            selector = _selector_for_index(page, idx)
            value = str(decision.get("value", ""))[:200]
            if action == "fill":
                if value == "<<SAVED_USERNAME>>" and saved_creds:
                    value = saved_creds.get("username", "")
                elif value == "<<SAVED_PASSWORD>>" and saved_creds:
                    value = saved_creds.get("password", "")
                else:
                    kind = _is_credential_field(elements[idx])
                    if kind:
                        discovered[kind] = value
            try:
                if action == "fill":
                    page.fill(selector, value, timeout=8000)
                else:
                    page.click(selector, timeout=8000)
                page.wait_for_timeout(800)
            except Exception as e:
                log_steps.append({"error": str(e)})
                continue
            check_for_block(page)

    return {"status": "max_steps_reached", "url": page.url, "steps": log_steps,
            "credentials": discovered or None}


def _run_single_navigation(browser, url, goal, saved_creds, email_lookup, deadline):
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    page = browser.new_page()
    page.set_default_timeout(15000)
    try:
        page.goto(url, timeout=20000, wait_until="domcontentloaded")
        check_for_block(page)
        if goal:
            return _run_interactive(page, goal, saved_creds, email_lookup, deadline)
        return {"status": "ok", "url": page.url, "content_excerpt": _page_text_lower(page)[:3000]}
    except BlockedError as e:
        log.info("Sesion bloqueada en %s: %s", url, e.reason)
        return {"status": "blocked", "reason": e.reason}
    except Exception as e:
        return {"status": "error", "reason": str(e)}
    finally:
        try:
            page.close()
        except Exception:
            pass


_task_queue = queue.PriorityQueue()
_seq_lock = threading.Lock()
_seq_counter = 0
_workers_started = False
_workers_lock = threading.Lock()


def _next_seq():
    global _seq_counter
    with _seq_lock:
        _seq_counter += 1
        return _seq_counter


def _local_worker(worker_id):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        log.info("Worker de navegacion local #%s iniciado.", worker_id)
        while True:
            _, _, job = _task_queue.get()
            if job is None:
                break
            url, goal, saved_creds, email_lookup, result_holder, done_event = job
            deadline = time.time() + SESSION_HARD_TIMEOUT_SECONDS
            try:
                result_holder["result"] = _run_single_navigation(browser, url, goal, saved_creds, email_lookup, deadline)
            except Exception as e:
                result_holder["result"] = {"status": "error", "reason": str(e)}
            finally:
                done_event.set()
        browser.close()


def _remote_worker(worker_id):
    while True:
        _, _, job = _task_queue.get()
        if job is None:
            break
        url, goal, saved_creds, email_lookup, result_holder, done_event = job
        deadline = time.time() + SESSION_HARD_TIMEOUT_SECONDS
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(REMOTE_WS_ENDPOINT, timeout=20000)
                try:
                    result_holder["result"] = _run_single_navigation(browser, url, goal, saved_creds, email_lookup, deadline)
                finally:
                    browser.close()
        except Exception as e:
            result_holder["result"] = {"status": "error", "reason": str(e)}
        finally:
            done_event.set()


def _ensure_workers():
    global _workers_started
    with _workers_lock:
        if _workers_started:
            return
        target = _remote_worker if REMOTE_WS_ENDPOINT else _local_worker
        for i in range(POOL_SIZE):
            t = threading.Thread(target=target, args=(i,), daemon=True)
            t.start()
        _workers_started = True
        log.info("Pool de navegacion iniciado: %s worker(s), modo %s.",
                  POOL_SIZE, "remoto" if REMOTE_WS_ENDPOINT else "local")


def domain_of(url):
    from urllib.parse import urlparse
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return urlparse(url).netloc.lower()


def run_heavy_session(url, goal=None, saved_creds=None, email_lookup=None, priority=0.0, timeout=200.0):
    """Motor de bajo nivel de Nivel 3 - NO llamar directamente desde el
    codigo de agentes/orquestador; usar browser_flows.py /
    browser_service.py. `priority`: menor valor = se procesa antes."""
    _ensure_workers()
    result_holder = {}
    done_event = threading.Event()
    job = (url, goal, saved_creds, email_lookup, result_holder, done_event)
    _task_queue.put((priority, _next_seq(), job))
    if not done_event.wait(timeout=timeout):
        return {"status": "error", "reason": "timeout esperando hueco en el pool de navegacion"}
    return result_holder.get("result", {"status": "error", "reason": "sin resultado"})
