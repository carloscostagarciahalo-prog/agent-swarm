"""
Cliente mínimo para la API de Mistral. Una sola cuenta, un solo modelo,
respetando la cuota gratuita (no la satures con paralelismo alto).
"""
import os
import time
import requests

API_KEY = os.environ.get("MISTRAL_API_KEY")
MODEL = os.environ.get("MISTRAL_MODEL", "mistral-small-latest")
URL = "https://api.mistral.ai/v1/chat/completions"

_last_call = 0.0
MIN_INTERVAL = 1.1  # deja margen bajo el límite de 1 req/segundo de la free tier


import logging

log = logging.getLogger("mistral_client")


def ask_mistral(prompt: str, max_tokens: int = 500, temperature: float = 0.7, max_retries: int = 3) -> str:
    global _last_call
    if not API_KEY:
        raise RuntimeError("Falta MISTRAL_API_KEY en el entorno")

    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            wait = MIN_INTERVAL - (time.time() - _last_call)
            if wait > 0:
                time.sleep(wait)

            resp = requests.post(
                URL,
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                },
                timeout=60,
            )
            _last_call = time.time()
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            last_err = e
            if attempt < max_retries:
                log.warning("Petición a Mistral falló (intento %d/%d): %s. Reintentando en %ds...",
                            attempt, max_retries, e, attempt * 2)
                time.sleep(attempt * 2)
    raise last_err
