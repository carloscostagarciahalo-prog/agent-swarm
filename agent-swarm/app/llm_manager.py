"""
llm_manager.py — Gestor unificado de LLM y cuotas de tokens.

Combina la infraestructura de Conway Automaton (gestión de modelos, crédito y tiers)
con la orquestación del enjambre:

- Gen 0 (Padres): Usan la API Key maestra de Mistral con un límite de 3.333M tokens/día.
- Gen 1+ (Hijos): La API de Mistral es exclusiva de Gen 0. Los hijos usan OpenRouter/su propia API Key.
- Patrocinio de Hijos: El padre financia 1 mes de servidor + 1-3M de tokens iniciales al nacer el hijo.
"""
import os
import time
import logging
import json
import requests

from mistral_client import ask_mistral
import db

log = logging.getLogger("llm_manager")

GEN0_DAILY_TOKEN_LIMIT = 3_333_000  # 3.333 Millones de tokens al día por agente de Gen 0
SERVER_MONTHLY_COST_USD = 5.0        # Coste estimado de 1 mes de servidor VM por hijo
INITIAL_TOKEN_CREDIT_USD = 3.0       # Coste de ~1.5M a 3M tokens en OpenRouter (OpenAI/Anthropic/DeepSeek)

def estimate_tokens(text: str) -> int:
    """Estimación simple de tokens (~4 caracteres por token)."""
    if not text:
        return 0
    return max(1, len(text) // 4)

def ask_agent_llm(agent_number: int, prompt: str, conn, max_tokens: int = 350) -> str:
    """
    Router unificado de llamadas a la inteligencia del agente.
    - Gen 0: Llama a Mistral verificando cuota diaria (< 3.333M tokens/día).
    - Gen 1+: Llama a OpenRouter / API propia del agente.
    """
    config = db.get_agent_llm_config(conn, agent_number)
    if not config:
        # Fallback por defecto si no está registrado
        gen = 0
        provider = "mistral"
        model_name = "mistral-small-latest"
        api_key = None
    else:
        gen = config.get("generation", 0)
        provider = config.get("provider", "mistral")
        model_name = config.get("model_name", "mistral-small-latest")
        api_key = config.get("api_key")

    # Control de cuotas de Gen 0
    if gen == 0:
        db.reset_daily_tokens_if_needed(conn, agent_number)
        used_today = config.get("tokens_used_today", 0) if config else 0
        prompt_tokens = estimate_tokens(prompt)
        
        if used_today + prompt_tokens > GEN0_DAILY_TOKEN_LIMIT:
            log.warning(f"[Agente #{agent_number} - Gen 0] Límite diario de tokens alcanzado ({used_today}/{GEN0_DAILY_TOKEN_LIMIT}). Turno pausado.")
            return json.dumps({
                "description": "Límite diario de 3.333M tokens alcanzado para Gen 0. Esperando reseteo diario.",
                "method": "cuota_excedida",
                "amount_requested": 0.0
            })

        # Llamar a Mistral
        response = ask_mistral(prompt, max_tokens=max_tokens)
        resp_tokens = estimate_tokens(response)
        total_turn_tokens = prompt_tokens + resp_tokens
        
        db.update_tokens_used(conn, agent_number, total_turn_tokens)
        return response

    # Agentes Gen 1+ (Hijos) — Deben usar OpenRouter / API propia
    else:
        if not api_key and provider != "openrouter":
            log.warning(f"[Agente #{agent_number} - Gen 1+] No tiene API Key configurada. Esperando fondos.")
            return json.dumps({
                "description": "Sin saldo de API Key propia. Esperando patrocinio o ingresos.",
                "method": "esperando_api",
                "amount_requested": 0.0
            })

        # Llamada a OpenRouter API (compatible OpenAI, permite pagos crypto en USDC)
        openrouter_key = api_key or os.environ.get("OPENROUTER_API_KEY")
        if openrouter_key:
            try:
                headers = {
                    "Authorization": f"Bearer {openrouter_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://agent-swarm.net",
                    "X-Title": f"Agent-Swarm #{agent_number}"
                }
                payload = {
                    "model": model_name if model_name != "mistral-small-latest" else "anthropic/claude-3.5-sonnet",
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": max_tokens
                }
                res = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=25)
                res.raise_for_status()
                data = res.json()
                content = data["choices"][0]["message"]["content"]
                
                used_tokens = data.get("usage", {}).get("total_tokens", estimate_tokens(prompt + content))
                db.update_tokens_used(conn, agent_number, used_tokens)
                return content
            except Exception as e:
                log.error(f"Error en OpenRouter para agente #{agent_number}: {e}")
                # Si falla la API del hijo, intentar fallback a modelo gratuito en OpenRouter
                return json.dumps({
                    "description": f"Error invocando API propia ({e}).",
                    "method": "error_api",
                    "amount_requested": 0.0
                })
        else:
            # Si no hay key propia configurada, el hijo debe esperar patrocinio
            return json.dumps({
                "description": "Gen 1+ sin API Key provista por el padre o ingresos.",
                "method": "sin_api",
                "amount_requested": 0.0
            })

def sponsor_child_agent(conn, parent_number: int, child_number: int) -> dict:
    """
    El padre financia a su nuevo hijo:
    1. Deduce de la wallet del padre el coste de 1 mes de servidor ($5 USD) + crédito inicial de API (~1-3M tokens, $3 USD).
    2. Transfiere el patrocinio y registra la configuración del hijo en Gen 1+.
    """
    total_sponsorship = SERVER_MONTHLY_COST_USD + INITIAL_TOKEN_CREDIT_USD
    
    # Verificar balance del padre en la DB
    parent_row = conn.execute("SELECT balance FROM agents WHERE number = ?", (parent_number,)).fetchone()
    parent_balance = parent_row["balance"] if parent_row else 0.0
    
    # Deducir saldo del padre (si tiene) o financiar patrocinio de registro
    new_parent_balance = max(0.0, parent_balance - total_sponsorship)
    conn.execute("UPDATE agents SET balance = ? WHERE number = ?", (new_parent_balance, parent_number))
    
    # Calcular 1 mes de servidor para el hijo
    one_month_later = time.time() + (30 * 24 * 60 * 60)
    
    # Asignar configuración de Gen 1+ al hijo
    # El hijo arranca por defecto con un modelo eficiente en OpenRouter (ej. DeepSeek V3 o Claude 3.5 Sonnet)
    child_gen = 1
    parent_cfg = db.get_agent_llm_config(conn, parent_number)
    if parent_cfg:
        child_gen = parent_cfg.get("generation", 0) + 1
        
    db.save_agent_llm_config(
        conn,
        agent_number=child_number,
        generation=child_gen,
        provider="openrouter",
        model_name="anthropic/claude-3.5-sonnet",
        api_key=None, # Usa el crédito asignado por el patrocinio del padre
        parent_number=parent_number,
        server_paid_until=one_month_later
    )
    
    log.info(f"¡Patrocinio completado! Padre #{parent_number} financió a Hijo #{child_number} (1 mes servidor + 1.5M-3M tokens API).")
    return {
        "ok": True,
        "parent_number": parent_number,
        "child_number": child_number,
        "sponsorship_usd": total_sponsorship,
        "server_paid_until": one_month_later
    }
