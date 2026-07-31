"""
NIVEL 2 — Navegación ligera (sin navegador real).

Para investigación, lectura de páginas y búsquedas simples. Sin login,
sin persistencia de cookies entre llamadas, timeout corto. Es mucho más
barato y rápido que abrir un Chromium — se usa siempre que la tarea no
requiera JavaScript ni interacción (rellenar formularios, hacer clic).

Si una página no se puede leer así (requiere JS pesado, devuelve vacío,
etc.), el llamador (browser_service.py) escala a Nivel 3 — navegador de
verdad — como último recurso, nunca al revés.
"""
import logging
import requests
from bs4 import BeautifulSoup

log = logging.getLogger("light_scrape")

TIMEOUT = 8
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; agent-swarm-research/1.0)"}


def scrape_page(url: str) -> dict:
    """Lee el texto visible de una página. Sin JS: si la página depende
    de JavaScript para mostrar contenido, esto puede volver vacío — en
    ese caso el llamador debe escalar a Nivel 3."""
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = " ".join(soup.get_text(separator=" ").split())[:4000]
        if len(text) < 50:  # señal de que la página necesita JS de verdad
            return {"success": False, "error": "contenido insuficiente sin JS, requiere navegador"}
        return {"success": True, "data": {"url": url, "text": text}}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def search_web(query: str, max_results: int = 5) -> dict:
    """Búsqueda sin navegador vía el endpoint HTML de DuckDuckGo (no
    requiere JS ni API key). Si algún día empieza a bloquear peticiones
    automatizadas, esto devuelve success=False y el llamador escala a
    Nivel 3 — nunca se intenta evadir el bloqueo aquí tampoco."""
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query}, headers=HEADERS, timeout=TIMEOUT,
        )
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        if "captcha" in resp.text.lower() or "unusual traffic" in resp.text.lower():
            return {"success": False, "error": "bloqueado (posible CAPTCHA) — no se insiste, se reporta"}
        results = []
        for link in soup.select(".result__a")[:max_results]:
            results.append({"title": link.get_text(strip=True), "url": link.get("href", "")})
        return {"success": True, "data": {"query": query, "results": results}}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}


def extract_trending(url: str, selector_hint: str | None = None) -> dict:
    """Extracción genérica de titulares/enlaces destacados de una página
    (útil para "qué está de moda" sin necesitar el sitio en concreto).
    `selector_hint` (opcional): selector CSS si el agente ya sabe dónde
    mirar en un sitio conocido; si no se da, usa h1/h2/h3 genéricos."""
    result = scrape_page(url)
    if not result["success"]:
        return result
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        soup = BeautifulSoup(resp.text, "html.parser")
        if selector_hint:
            items = [el.get_text(strip=True) for el in soup.select(selector_hint)[:20]]
            return {"success": True, "data": {"url": url, "items": items}}
        headings = [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"]) if h.get_text(strip=True)]
        return {"success": True, "data": {"url": url, "headings": headings[:20]}}
    except Exception as e:  # noqa: BLE001
        return {"success": False, "error": str(e)}
