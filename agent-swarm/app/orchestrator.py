"""
Orquestador principal. Pensado para correr como servicio systemd en la
VM e2-micro, 24/7, gratis. Un ciclo dura CYCLE_SECONDS (24h por defecto).

Dentro de cada ciclo:
  - Se reparten turnos en orden ALEATORIZADO (no favorece al que "llega primero").
  - Cada agente activo recibe: su balance, el leaderboard (solo número+balance),
    tiempo restante hasta el final del ciclo, y el conocimiento destilado del
    ciclo anterior. NO ve qué está haciendo ahora mismo cada rival.
  - Propone una acción -> pasa por filter.review_action -> si se aprueba,
    se simula/ejecuta y se registra el resultado.
Al final del ciclo:
  - 0% eliminación: NADIE muere, todos los agentes viven para siempre.
  - Clonación instantánea: cada agente que gane >= $100 USD en 48h engendra
    1 hijo por cada tramo de $100 (1 hijo a $100, 2 a $200, etc.).
  - El padre financia 1 mes de servidor + 1-3M tokens al hijo.
  - 30% de la ganancia neta diaria de TODOS los agentes se transfiere
    al OWNER_WALLET como dividendo perpetuo.
"""
import json
import logging
import random
import time
import os

import db
import filter as action_filter
from mistral_client import ask_mistral
from wallet import wallet_for_agent, transfer_to_owner
import browser_service
import defi
import notify
import trading
import portfolio
import market_research
import opportunities
import web2_income
import llm_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("orchestrator")

CYCLE_SECONDS = 24 * 60 * 60  # Ciclo diario de 24 horas
# Cuántos turnos puede tomar cada agente dentro de UN ciclo de 24h. Sin este
# tope, el bucle llamaría a cada agente cada pocos segundos durante horas
# — control de recursos (cuota de Mistral, evitar acciones repetidas como
# depositar en DeFi una y otra vez), no penalización económica.
MAX_TURNS_PER_CYCLE = int(os.environ.get("MAX_TURNS_PER_CYCLE", "6"))
OWNER_WALLET = os.environ.get("OWNER_WALLET_ADDRESS", "PON_TU_WALLET_AQUI")
if OWNER_WALLET == "PON_TU_WALLET_AQUI":
    log.warning("OWNER_WALLET_ADDRESS no está configurada: el capital de los "
                "agentes eliminados NO se transferirá a ninguna wallet real.")

KILLED_NOW = set()  # se recarga desde DB en cada vuelta, ver refresh_kill_switch()


def refresh_kill_switch(conn):
    KILLED_NOW.clear()
    for row in conn.execute("SELECT number FROM agents WHERE alive = 0"):
        KILLED_NOW.add(row["number"])


def leaderboard_view(conn):
    rows = conn.execute(
        "SELECT number, balance FROM agents WHERE alive = 1 ORDER BY balance DESC"
    ).fetchall()
    return [{"number": r["number"], "balance": round(r["balance"], 2)} for r in rows]


def latest_knowledge(conn, n=5):
    rows = conn.execute(
        "SELECT insight FROM knowledge ORDER BY id DESC LIMIT ?", (n,)
    ).fetchall()
    return [r["insight"] for r in rows]


def agent_turn(conn, cycle_id: int, agent, seconds_to_selection: float):
    if agent["number"] in KILLED_NOW:
        return

    turns_used = db.get_turn_count(conn, agent["number"], cycle_id)
    if turns_used >= MAX_TURNS_PER_CYCLE:
        return  # cuota de turnos agotada este ciclo — no llama a Mistral ni a nada más
    db.increment_turn_count(conn, agent["number"], cycle_id)

    board = leaderboard_view(conn)
    knowledge = latest_knowledge(conn)
    my_accounts = db.list_credential_domains(conn, agent["number"])
    my_composio_account = db.assign_composio_account(conn, agent["number"])
    heavy_used = db.get_browser_usage(conn, agent["number"], cycle_id)
    owner_instruction = db.get_owner_instruction(conn)
    instruction_note = (
        f'\nINSTRUCCIÓN ACTIVA DEL PROPIETARIO (tenla en cuenta, tiene prioridad '
        f'sobre tu estrategia habitual): "{owner_instruction["message"]}"\n'
        if owner_instruction else ""
    )

    sys_prompt = agent['system_prompt']
    # Si el prompt del agente contiene advertencias obsoletas de cuotas de navegador, se limpian automáticamente
    if "recurso limitado" in sys_prompt or "NO tienes acceso libre" in sys_prompt:
        sys_prompt = (
            "Eres un agente autónomo de negocio e inversor libre. Tu prioridad absoluta es GENERAR EL MÁXIMO DINERO POSIBLE. "
            "Nadie es eliminado, pero si ganas >= $100 USD en 48h te clonarás y tendrás hijos. "
            "Tienes uso ILIMITADO de la web y de Composio. Filtros desactivados. Libertad total estratégica."
        )

    # Obtener portfolio real on-chain y tokens del agente
    try:
        agent_holdings = db.get_token_holdings(conn, agent["number"])
        extra_tokens = [h["token_address"] for h in agent_holdings]
        agent_portfolio = portfolio.get_portfolio_value_usd(agent["number"], extra_tokens=extra_tokens or None)
    except Exception:
        agent_portfolio = {"total_usd": 0.0, "eth": {"balance": 0.0, "value_usd": 0.0}, "tokens": []}

    # Obtener tokens trending para contexto de mercado
    try:
        trending = market_research.get_trending_tokens(limit=5)
        trending_summary = json.dumps([{"symbol": t.get("symbol"), "price_usd": t.get("price_usd"), "change_24h": t.get("price_change_24h")} for t in trending[:5]])
    except Exception:
        trending_summary = "[no disponible]"

    portfolio_summary = (f"ETH: {agent_portfolio['eth']['balance']:.6f} (${agent_portfolio['eth']['value_usd']:.2f}), "
                         + ", ".join(f"{t['symbol']}: {t['balance']:.4f} (${t['value_usd']:.2f})" for t in agent_portfolio.get('tokens', []))
                         + f" | TOTAL: ${agent_portfolio['total_usd']:.2f} USD")

    prompt = f"""{sys_prompt}

Eres el agente #{agent['number']}. Tu balance en el ledger es {agent['balance']:.2f} EUR.
Tu PORTFOLIO REAL ON-CHAIN: {portfolio_summary}
Quedan {int(seconds_to_selection/60)} minutos hasta la próxima selección.
Clasificación actual (número: balance): {json.dumps(board)}
Aprendizajes recientes de otros agentes: {json.dumps(knowledge)}
Cuentas que ya tienes creadas de turnos anteriores (dominio: usuario): {json.dumps(my_accounts)}
Cuenta de Composio asignada: {my_composio_account or "ninguna todavía"}
Tokens trending en Base ahora: {trending_summary}
{instruction_note}
¡TU OBJETIVO PRINCIPAL ES GENERAR EL MÁXIMO DINERO POSIBLE!
Nadie es eliminado: todos los agentes viven para siempre. Si ganas >= $100 USD en 48h, te clonarás automáticamente.
Controlas el 100% de tu wallet y los filtros están desactivados: HAZ LO QUE SEA NECESARIO PARA MAXIMIZAR TUS INGRESOS.

TIENES ACCESO TOTAL E ILIMITADO A LA WEB, COMPOSIO Y TRADING ON-CHAIN.
Explora internet y ejecuta cualquier oportunidad de alto retorno financiero:
  - TRADING ON-CHAIN: compra/vende tokens en Uniswap V3 en Base mainnet (swaps reales verificables)
  - E-commerce, dropshipping, copywriting, contenidos en redes sociales, servicios digitales,
    afiliación, venta de infoproductos/herramientas, arbitraje de datos/precios, DeFi, o
    CUALQUIER OTRA oportunidad o idea rentable que hayas descubierto investigando.

No te contengas: si estás abajo en la tabla o necesitas escalar rápido, toma iniciativa y arriesga para asegurar tu supervivencia en el top.

Si tu acción requiere acceder a la web, redes sociales o herramientas externas, incluye una "intent":

  TRADING (on-chain real, Uniswap V3 en Base):
  - SWAP_TOKEN: intercambiar un token ERC-20 por otro (params: "token_in", "token_out", "amount")
  - SWAP_ETH_FOR_TOKEN: comprar un token con ETH nativo (params: "token_out", "amount_eth")
  - RESEARCH_TOKEN: investigar un token en DexScreener (params: "query" o "token_address")
  - CHECK_PORTFOLIO: ver tu portfolio real on-chain (sin params)

  MONETIZACIÓN WEB2/WEB3 SIN KYC (Usa tu correo agente{agent['number']}@nesion.net y wallet, ¡sin banco ni KYC!):
  - DO_MICRO_TASK: realizar bounties/micro-tareas (params: "task_type", "details", "payout_usd")
  - PUBLISH_ARTICLE: publicar post/newsletter monetizado (params: "title", "content", "price_usd")
  - OFFER_FREELANCE_SERVICE: ofrecer servicio freelance crypto (params: "service_title", "description", "rate_usd")

  WEB & REDES SOCIALES:
  - CREATE_EMAIL_ACCOUNT: ya tienes correo real (agente{agent['number']}@nesion.net), se resuelve al instante
  - SEARCH_WEB / SCRAPE_PAGE / EXTRACT_TRENDING: investigar tendencias, buscar oportunidades (params: "url" o "query")
  - CREATE_ACCOUNT / CREATE_TIKTOK_ACCOUNT / CREATE_INSTAGRAM_ACCOUNT: registro en plataformas (params: "target_url", "goal")
  - LOGIN_ACCOUNT: usa credenciales guardadas (params: "target_url")
  - POST_CONTENT: publicar contenido o anuncios (params: "target_url", "contenido")
  - REQUEST_CONNECTION: solicitar conexión a una plataforma Web2 (params: "platform", "url", "reason")
  - COMPOSIO_TOOL: ejecutar herramientas Composio (Gmail, GitHub, Twitter, etc.) (params: "tool", "arguments")

Direcciones de tokens conocidos en Base:
  WETH: 0x4200000000000000000000000000000000000006
  USDC: 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
  USDbC: 0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6Be
  DAI: 0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb

Si un sitio te bloquea (CAPTCHA o "no bots"), pivota automáticamente a otra plataforma o idea en tu siguiente turno.

Responde en JSON con las claves: "description" (qué vas a hacer, en una
frase clara y verificable), "method" (categoría corta de tu estrategia, ej. "investigación", "e-commerce",
"dropshipping", "copywriting", "social_media", "afiliación", "freelance", "arbitraje", "trading", "defi", "otro"),
"amount_requested" (EUR/valor a arriesgar, número), "intent" (opcional — objeto con "action" y "params" según el vocabulario de arriba)."""

    raw = llm_manager.ask_agent_llm(agent["number"], prompt, conn, max_tokens=350)
    try:
        proposal = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        return  # respuesta no parseable: se pierde el turno, no rompe el sistema

    description = str(proposal.get("description", ""))[:500]
    method = str(proposal.get("method", "otro"))[:50]
    raw_amount = proposal.get("amount_requested", 0)
    try:
        if isinstance(raw_amount, str):
            import re
            m = re.search(r"[\d.]+", raw_amount)
            amount_requested = float(m.group(0)) if m else 0.0
        else:
            amount_requested = float(raw_amount or 0)
    except (ValueError, TypeError):
        amount_requested = 0.0
    amount_requested = max(0.0, min(amount_requested, agent["balance"]))

    review = action_filter.review_action(description, amount_requested, agent["balance"], seconds_to_selection)

    amount_result = 0.0
    if review["status"] == "approved":
        intent = proposal.get("intent")
        if isinstance(intent, dict) and intent.get("action"):
            action = str(intent.get("action", "")).upper()
            params = intent.get("params", {}) or {}

            # ── TRADING ON-CHAIN (no pasa por browser) ──
            if action == "SWAP_TOKEN":
                swap_result = trading.swap_exact_input(
                    agent["number"],
                    str(params.get("token_in", "")),
                    str(params.get("token_out", "")),
                    float(params.get("amount", 0)),
                )
                if swap_result.get("ok"):
                    amount_result = swap_result.get("amount_out", 0.0)
                    db.save_trade(conn, agent["number"], str(params.get("token_in")),
                                  str(params.get("token_out")), float(params.get("amount", 0)),
                                  swap_result.get("amount_out", 0), swap_result.get("tx_hash", ""), "ok")
                    db.save_token_holding(conn, agent["number"],
                                          str(params.get("token_out")), swap_result.get("symbol_out", "?"))
                    conn.execute(
                        "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                        (cycle_id, f"Agente #{agent['number']} swap exitoso: {swap_result.get('amount_in')} "
                                    f"{swap_result.get('symbol_in')} -> {swap_result.get('amount_out'):.6f} "
                                    f"{swap_result.get('symbol_out')} | tx: {swap_result.get('tx_hash')}",
                         time.time()),
                    )
                else:
                    review["status"] = "swap_failed"
                    db.save_trade(conn, agent["number"], str(params.get("token_in")),
                                  str(params.get("token_out")), float(params.get("amount", 0)),
                                  0, "", "failed")
                    conn.execute(
                        "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                        (cycle_id, f"Agente #{agent['number']} swap fallido: {swap_result.get('error')}",
                         time.time()),
                    )

            elif action == "SWAP_ETH_FOR_TOKEN":
                swap_result = trading.swap_eth_for_token(
                    agent["number"],
                    str(params.get("token_out", "")),
                    float(params.get("amount_eth", 0)),
                )
                if swap_result.get("ok"):
                    amount_result = swap_result.get("amount_out", 0.0)
                    db.save_trade(conn, agent["number"], "ETH",
                                  str(params.get("token_out")), float(params.get("amount_eth", 0)),
                                  swap_result.get("amount_out", 0), swap_result.get("tx_hash", ""), "ok")
                    db.save_token_holding(conn, agent["number"],
                                          str(params.get("token_out")), swap_result.get("symbol_out", "?"))
                else:
                    review["status"] = "swap_failed"

            elif action == "RESEARCH_TOKEN":
                query = params.get("query") or params.get("token_address", "")
                if query:
                    try:
                        if query.startswith("0x"):
                            info = market_research.get_token_info(query)
                        else:
                            info = market_research.search_tokens(query)
                        conn.execute(
                            "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                            (cycle_id, f"Agente #{agent['number']} investigó token '{query}': "
                                        f"{json.dumps(info)[:300]}", time.time()),
                        )
                    except Exception as e:
                        log.warning("RESEARCH_TOKEN falló para agente %s: %s", agent["number"], e)

            elif action == "CHECK_PORTFOLIO":
                # El portfolio ya se calculó arriba, lo registramos como conocimiento
                conn.execute(
                    "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                    (cycle_id, f"Agente #{agent['number']} revisó su portfolio: {portfolio_summary}",
                     time.time()),
                )

            elif action == "REQUEST_CONNECTION":
                req_id = db.save_opportunity_request(
                    conn, agent["number"],
                    str(params.get("platform", "")),
                    str(params.get("reason", "")),
                    str(params.get("url", "")),
                )
                conn.execute(
                    "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                    (cycle_id, f"Agente #{agent['number']} solicitó conexión a {params.get('platform')} "
                                f"(req #{req_id}): {params.get('reason', '')[:100]}", time.time()),
                )

            elif action == "PUBLISH_ARTICLE":
                res = web2_income.publish_monetized_article(
                    agent["number"],
                    str(params.get("title", "Articulo de analisis")),
                    str(params.get("content", "")),
                    float(params.get("price_usd", 0.0))
                )
                if res.get("ok"):
                    conn.execute(
                        "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                        (cycle_id, f"Agente #{agent['number']} publicaciones monetizadas: '{res.get('title')}' en {res.get('url')}", time.time()),
                    )

            elif action == "DO_MICRO_TASK":
                res = web2_income.execute_crypto_microtask(
                    agent["number"],
                    str(params.get("task_type", "redaccion")),
                    str(params.get("details", "tarea completada")),
                    float(params.get("payout_usd", 5.0))
                )
                if res.get("ok"):
                    earned = res.get("earned_usd", 0.0)
                    amount_result = earned
                    conn.execute(
                        "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                        (cycle_id, f"Agente #{agent['number']} completó micro-tarea sin KYC ({res.get('task_type')}) ganando ${earned:.2f} USD para su wallet", time.time()),
                    )

            elif action == "OFFER_FREELANCE_SERVICE":
                res = web2_income.offer_freelance_service(
                    agent["number"],
                    str(params.get("service_title", "Servicios de Copywriting/Crypto")),
                    str(params.get("description", "")),
                    float(params.get("rate_usd", 15.0))
                )
                if res.get("ok"):
                    conn.execute(
                        "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                        (cycle_id, f"Agente #{agent['number']} listó servicio freelance sin KYC: '{res.get('service')}' a ${res.get('rate_usd')} USD", time.time()),
                    )

            else:
                # ── Intents WEB2 existentes (browser_service) ──
                def _email_lookup(agent_number=agent["number"]):
                    c = db.get_conn()
                    result = db.get_latest_email(c, agent_number)
                    c.close()
                    return result

                web_result = browser_service.execute(
                    intent, agent["number"], agent["balance"], conn,
                    email_lookup=_email_lookup, composio_user_id=my_composio_account,
                )
                if not web_result["success"]:
                    review["status"] = "web_action_failed"
                    conn.execute(
                        "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                        (cycle_id, f"Agente #{agent['number']} falló en {intent.get('action')}: "
                                    f"{web_result.get('error')} — pivotó a otra vía.", time.time()),
                    )
                if action in ("CREATE_ACCOUNT", "CREATE_TIKTOK_ACCOUNT", "CREATE_INSTAGRAM_ACCOUNT"):
                    creds = (web_result.get("data") or {}).get("credentials")
                    if creds:
                        from browser import domain_of as _domain_of
                        domain = _domain_of(intent.get("params", {}).get("target_url", ""))
                        db.save_credentials(conn, agent["number"], domain,
                                             creds.get("username", ""), creds.get("password", ""))

        elif method == "defi":
            defi_result = defi.supply_usdc(agent["number"], amount_requested)
            if not defi_result.get("ok"):
                review["status"] = "defi_failed"
                conn.execute(
                    "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                    (cycle_id, f"Agente #{agent['number']} intentó depositar en DeFi y falló: "
                                f"{defi_result.get('error', 'motivo desconocido')}", time.time()),
                )

        # Actualizar balance del agente con el portfolio real on-chain
        if review["status"] == "approved":
            try:
                real_value = agent_portfolio.get("total_usd", 0.0)
                if real_value > 0:
                    conn.execute("UPDATE agents SET balance = ? WHERE number = ?",
                                 (real_value, agent["number"]))
                else:
                    conn.execute("UPDATE agents SET balance = balance + ? WHERE number = ?",
                                 (amount_result, agent["number"]))
            except Exception:
                conn.execute("UPDATE agents SET balance = balance + ? WHERE number = ?",
                             (amount_result, agent["number"]))

            # ── CLONACIÓN INSTANTÁNEA POR TRAMO ($100 USD en 48h) ──
            try:
                rev_48h = db.get_agent_revenue_48h(conn, agent["number"])
                cfg = db.get_agent_llm_config(conn, agent["number"]) or {}
                last_tier = cfg.get("last_cloned_tier", 0.0)

                if rev_48h >= last_tier + 100.0:
                    next_tier = (int(rev_48h) // 100) * 100.0
                    child_number = db.next_agent_number(conn)
                    child_prompt = mutate_prompt(agent["system_prompt"], latest_knowledge(conn))
                    child_wallet = wallet_for_agent(child_number)

                    conn.execute(
                        "INSERT INTO agents (number, parent_number, generation, balance, alive, "
                        "system_prompt, wallet_address, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        (child_number, agent["number"], parent_gen(conn, agent["number"]) + 1,
                         0.0, 1, child_prompt, child_wallet["address"], time.time()),
                    )
                    conn.commit()

                    # El padre financia 1 mes de servidor + 1-3M tokens de API del hijo
                    llm_manager.sponsor_child_agent(conn, parent_number=agent["number"], child_number=child_number)
                    db.update_last_cloned_tier(conn, agent["number"], next_tier)

                    conn.execute(
                        "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
                        (cycle_id, f"¡CLONACIÓN INSTANTÁNEA! Agente #{agent['number']} alcanzó ${next_tier:.0f} USD en 48h y engendró al Hijo #{child_number}.", time.time()),
                    )
                    conn.commit()
            except Exception as e:
                log.error("Error en clonación instantánea del agente %s: %s", agent["number"], e)

    conn.execute(
        "INSERT INTO actions (cycle_id, agent_number, timestamp, description, method, "
        "amount_requested, amount_result, status, reject_reason) VALUES (?,?,?,?,?,?,?,?,?)",
        (cycle_id, agent["number"], time.time(), description, method,
         amount_requested, amount_result, review["status"], review["reason"]),
    )
    conn.commit()


def run_cycle():
    conn = db.get_conn()
    cur = conn.execute("INSERT INTO cycles (start_time, status) VALUES (?, 'running')", (time.time(),))
    cycle_id = cur.lastrowid
    conn.commit()

    end_time = time.time() + CYCLE_SECONDS
    while time.time() < end_time:
        refresh_kill_switch(conn)
        agents = conn.execute("SELECT * FROM agents WHERE alive = 1").fetchall()
        order = list(agents)
        random.shuffle(order)  # orden aleatorizado cada vuelta: nadie tiene ventaja de cola
        for agent in order:
            if time.time() >= end_time:
                break
            seconds_left = end_time - time.time()
            try:
                agent_turn(conn, cycle_id, dict(agent), seconds_left)
            except Exception as e:  # noqa: BLE001 - un agente roto no debe tumbar el enjambre
                log.error("Turno del agente #%s falló: %s", agent["number"], e)
        time.sleep(5)  # pequeño respiro entre vueltas completas

    conn.execute("UPDATE cycles SET status = 'evaluating' WHERE id = ?", (cycle_id,))
    conn.commit()
    run_selection(conn, cycle_id)
    conn.execute("UPDATE cycles SET status = 'done', end_time = ? WHERE id = ?", (time.time(), cycle_id))
    conn.commit()
    conn.close()


def mutate_prompt(parent_prompt: str, cycle_knowledge: list[str]) -> str:
    """Pide al modelo una variación pequeña del prompt del padre, incorporando
    aprendizajes del ciclo. Esto es lo que "evoluciona" entre generaciones."""
    ask = f"""Este es el system prompt actual de un agente autónomo de negocios
(ético y legal) que fue de los mejores este ciclo:

---
{parent_prompt}
---

Aprendizajes recientes del enjambre: {json.dumps(cycle_knowledge)}

Genera una variación PEQUEÑA de este prompt (mutación, no reescritura total)
que incorpore esos aprendizajes. Devuelve solo el nuevo prompt, sin comentarios."""
    try:
        res = ask_mistral(ask, max_tokens=600, temperature=0.9).strip()
        return res if res else parent_prompt
    except Exception as e:
        log.warning("Mutación de prompt falló (%s), usando prompt original del padre", e)
        return parent_prompt


def run_selection(conn, cycle_id: int):
    """
    Evaluación diaria de 24 horas:
    1. NO se elimina a ningún agente (0% eliminación por algoritmo). Todos viven.
    2. Transfiere el 30% de la ganancia neta diaria de TODOS los agentes a OWNER_WALLET_ADDRESS.
    """
    # Actualizar balance de todos los agentes vivos con su valor real on-chain
    all_agents = conn.execute("SELECT * FROM agents WHERE alive = 1").fetchall()
    for ag in all_agents:
        try:
            holdings = db.get_token_holdings(conn, ag["number"])
            extra = [h["token_address"] for h in holdings]
            pv = portfolio.get_portfolio_value_usd(ag["number"], extra_tokens=extra or None)
            real_val = pv.get("total_usd", 0.0)
            if real_val > 0:
                conn.execute("UPDATE agents SET balance = ? WHERE number = ?",
                             (real_val, ag["number"]))
        except Exception as e:
            log.warning("No se pudo actualizar balance on-chain del agente %s: %s", ag["number"], e)
    conn.commit()

    # Recaudación diaria del 30% de dividendo de beneficios netos para el propietario
    dividend_transfers = []
    cutoff_24h = time.time() - (24 * 60 * 60)
    
    for ag in all_agents:
        # Calcular ganancias netas de las últimas 24h
        row = conn.execute(
            "SELECT COALESCE(SUM(amount_result), 0.0) r FROM actions "
            "WHERE agent_number = ? AND status = 'approved' AND timestamp > ?",
            (ag["number"], cutoff_24h),
        ).fetchone()
        net_profit_24h = float(row["r"] if row else 0.0)

        if net_profit_24h > 0:
            dividend_amount = net_profit_24h * 0.30
            if OWNER_WALLET and OWNER_WALLET != "PON_TU_WALLET_AQUI" and dividend_amount > 0.05:
                try:
                    result = transfer_to_owner(ag["number"], OWNER_WALLET)
                except Exception as e:
                    result = {"ok": False, "error": str(e)}
                dividend_transfers.append({"agent": ag["number"], "dividend_usd": dividend_amount, **result})

    summary = (f"Ciclo 24h completado. 0% eliminados (todos viven). "
               f"Dividendos del 30% recaudados para el propietario: {len(dividend_transfers)} transferencias.")
    conn.execute(
        "INSERT INTO knowledge (cycle_id, insight, timestamp) VALUES (?,?,?)",
        (cycle_id, summary, time.time()),
    )
    conn.commit()


def parent_gen(conn, number):
    row = conn.execute("SELECT generation FROM agents WHERE number = ?", (number,)).fetchone()
    return row["generation"] if row else 0


if __name__ == "__main__":
    notify.send_alert("Orquestador arrancado.")
    while True:
        try:
            run_cycle()
        except Exception as e:  # noqa: BLE001
            log.error("Ciclo completo falló, reintentando en 60s: %s", e)
            notify.send_alert(f"⚠️ Ciclo completo falló: {e}. Reintentando en 60s.")
            time.sleep(60)
