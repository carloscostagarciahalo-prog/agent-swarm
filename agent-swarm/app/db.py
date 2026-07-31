"""
Capa de acceso a datos. SQLite porque es gratis, no necesita servidor,
y para el enjambre de agentes sobra de sobra en rendimiento.
"""
import sqlite3
import time
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "swarm.db")


def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
    number INTEGER PRIMARY KEY,       -- 1..100 iniciales, luego 101, 102...
    parent_number INTEGER,            -- de qué agente viene (NULL en la generación 0)
    generation INTEGER NOT NULL DEFAULT 0,
    balance REAL NOT NULL DEFAULT 0,
    alive INTEGER NOT NULL DEFAULT 1, -- 0 = eliminado o kill switch
    killed_reason TEXT,
    system_prompt TEXT NOT NULL,      -- "genoma" textual, heredado y mutado
    wallet_address TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time REAL NOT NULL,
    end_time REAL,
    status TEXT NOT NULL DEFAULT 'running'  -- running | evaluating | done
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    agent_number INTEGER NOT NULL,
    timestamp REAL NOT NULL,
    description TEXT NOT NULL,     -- qué intentó hacer
    method TEXT NOT NULL,          -- categoría del método (afiliación, trading, etc.)
    amount_requested REAL NOT NULL DEFAULT 0,  -- cuánto capital pidió arriesgar
    amount_result REAL NOT NULL DEFAULT 0,     -- ganancia/pérdida real
    status TEXT NOT NULL,          -- approved | rejected_hard | rejected_llm | rejected_risk
    reject_reason TEXT,
    FOREIGN KEY (cycle_id) REFERENCES cycles(id)
);

CREATE TABLE IF NOT EXISTS knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER NOT NULL,
    insight TEXT NOT NULL,     -- resumen destilado de qué funcionó
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS credentials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_number INTEGER NOT NULL,
    domain TEXT NOT NULL,
    username TEXT,
    password TEXT,       -- ver aviso de seguridad en el README (texto plano, mejorable)
    created_at REAL NOT NULL,
    UNIQUE(agent_number, domain)
);

CREATE TABLE IF NOT EXISTS composio_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,             -- nombre para ti, ej. "instagram_tienda_1"
    composio_user_id TEXT NOT NULL UNIQUE,  -- el user_id que autorizaste en Composio
    platform TEXT,                   -- ej. "instagram", "twitter" — informativo
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS account_assignments (
    agent_number INTEGER PRIMARY KEY,
    composio_account_id INTEGER NOT NULL,
    assigned_at REAL NOT NULL,
    FOREIGN KEY (composio_account_id) REFERENCES composio_accounts(id)
);

CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_number INTEGER NOT NULL,
    from_address TEXT,
    subject TEXT,
    body_text TEXT,
    extracted_link TEXT,     -- primer enlace https encontrado en el cuerpo, si hay
    extracted_code TEXT,     -- primer código numérico de 4-8 dígitos, si hay
    received_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS browser_usage (
    agent_number INTEGER NOT NULL,
    cycle_id INTEGER NOT NULL,
    heavy_count INTEGER NOT NULL DEFAULT 0,  -- acciones de Nivel 3 usadas este ciclo
    PRIMARY KEY (agent_number, cycle_id)
);

CREATE TABLE IF NOT EXISTS turn_usage (
    agent_number INTEGER NOT NULL,
    cycle_id INTEGER NOT NULL,
    turn_count INTEGER NOT NULL DEFAULT 0,   -- turnos ya tomados este ciclo
    PRIMARY KEY (agent_number, cycle_id)
);

CREATE TABLE IF NOT EXISTS owner_instructions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS kill_switch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_number INTEGER NOT NULL,
    action TEXT NOT NULL,   -- kill | pause | resume
    reason TEXT,
    timestamp REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS token_holdings (
    agent_number INTEGER,
    token_address TEXT,
    token_symbol TEXT,
    PRIMARY KEY (agent_number, token_address)
);

CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_number INTEGER,
    timestamp REAL,
    token_in TEXT,
    token_out TEXT,
    amount_in REAL,
    amount_out REAL,
    tx_hash TEXT,
    status TEXT
);

CREATE TABLE IF NOT EXISTS opportunity_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_number INTEGER,
    platform TEXT,
    reason TEXT,
    url TEXT,
    status TEXT DEFAULT 'pending',
    created_at REAL
);

CREATE TABLE IF NOT EXISTS agent_llm_config (
    agent_number INTEGER PRIMARY KEY,
    generation INTEGER NOT NULL DEFAULT 0,
    provider TEXT NOT NULL DEFAULT 'mistral',
    model_name TEXT NOT NULL DEFAULT 'mistral-small-latest',
    api_key TEXT,
    parent_number INTEGER,
    server_paid_until REAL,
    tokens_used_today INTEGER DEFAULT 0,
    last_token_reset REAL DEFAULT 0,
    last_cloned_tier REAL DEFAULT 0.0,
    created_at REAL NOT NULL
);
"""


def init_db(seed_prompt: str, n_agents: int = 100, starting_capital: float = 100.0):
    conn = get_conn()
    conn.executescript(SCHEMA)
    existing = conn.execute("SELECT COUNT(*) c FROM agents").fetchone()["c"]
    if existing == 0:
        import wallet as wallet_module
        per_agent = starting_capital / n_agents
        now = time.time()
        for i in range(1, n_agents + 1):
            w = wallet_module.wallet_for_agent(i)
            conn.execute(
                "INSERT INTO agents (number, parent_number, generation, balance, alive, "
                "system_prompt, wallet_address, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (i, None, 0, per_agent, 1, seed_prompt, w["address"], now),
            )
        conn.commit()
    conn.close()


def next_agent_number(conn) -> int:
    row = conn.execute("SELECT MAX(number) m FROM agents").fetchone()
    return (row["m"] or 0) + 1


def get_credentials(conn, agent_number: int, domain: str):
    row = conn.execute(
        "SELECT username, password FROM credentials WHERE agent_number = ? AND domain = ?",
        (agent_number, domain),
    ).fetchone()
    return {"username": row["username"], "password": row["password"]} if row else None


def list_credential_domains(conn, agent_number: int):
    rows = conn.execute(
        "SELECT domain, username FROM credentials WHERE agent_number = ?", (agent_number,)
    ).fetchall()
    return [{"domain": r["domain"], "username": r["username"]} for r in rows]


def save_credentials(conn, agent_number: int, domain: str, username: str, password: str):
    conn.execute(
        "INSERT INTO credentials (agent_number, domain, username, password, created_at) "
        "VALUES (?,?,?,?,?) ON CONFLICT(agent_number, domain) DO UPDATE SET "
        "username=excluded.username, password=excluded.password",
        (agent_number, domain, username, password, time.time()),
    )
    conn.commit()


def add_composio_account(conn, label: str, composio_user_id: str, platform: str = ""):
    """Tú registras aquí cada cuenta que ya autorizaste en el dashboard de
    Composio (una vez, a mano). A partir de ahí el sistema la reparte
    solo entre agentes."""
    conn.execute(
        "INSERT INTO composio_accounts (label, composio_user_id, platform, active, created_at) "
        "VALUES (?,?,?,1,?)",
        (label, composio_user_id, platform, time.time()),
    )
    conn.commit()


def assign_composio_account(conn, agent_number: int, platform: str | None = None) -> str | None:
    """Asigna al agente una cuenta del pool (reparto por número de agente,
    estable entre turnos) y devuelve su composio_user_id, o None si no hay
    ninguna cuenta activa disponible. Si el agente ya tenía una asignada,
    devuelve siempre la misma (continuidad, no cambia cada turno)."""
    existing = conn.execute(
        "SELECT ca.composio_user_id FROM account_assignments aa "
        "JOIN composio_accounts ca ON ca.id = aa.composio_account_id "
        "WHERE aa.agent_number = ? AND ca.active = 1", (agent_number,),
    ).fetchone()
    if existing:
        return existing["composio_user_id"]

    query = "SELECT id, composio_user_id FROM composio_accounts WHERE active = 1"
    params: tuple = ()
    if platform:
        query += " AND platform = ?"
        params = (platform,)
    accounts = conn.execute(query, params).fetchall()
    if not accounts:
        return None

    chosen = accounts[agent_number % len(accounts)]  # reparto estable, no aleatorio
    conn.execute(
        "INSERT OR REPLACE INTO account_assignments (agent_number, composio_account_id, assigned_at) "
        "VALUES (?,?,?)", (agent_number, chosen["id"], time.time()),
    )
    conn.commit()
    return chosen["composio_user_id"]


def save_inbound_email(conn, agent_number: int, from_address: str, subject: str,
                        body_text: str, extracted_link: str | None, extracted_code: str | None):
    conn.execute(
        "INSERT INTO inbox (agent_number, from_address, subject, body_text, "
        "extracted_link, extracted_code, received_at) VALUES (?,?,?,?,?,?,?)",
        (agent_number, from_address, subject, body_text, extracted_link, extracted_code, time.time()),
    )
    conn.commit()


def get_latest_email(conn, agent_number: int, since: float = 0.0):
    row = conn.execute(
        "SELECT * FROM inbox WHERE agent_number = ? AND received_at > ? "
        "ORDER BY received_at DESC LIMIT 1", (agent_number, since),
    ).fetchone()
    return dict(row) if row else None


def get_browser_usage(conn, agent_number: int, cycle_id: int) -> int:
    row = conn.execute(
        "SELECT heavy_count FROM browser_usage WHERE agent_number = ? AND cycle_id = ?",
        (agent_number, cycle_id),
    ).fetchone()
    return row["heavy_count"] if row else 0


def increment_browser_usage(conn, agent_number: int, cycle_id: int):
    conn.execute(
        "INSERT INTO browser_usage (agent_number, cycle_id, heavy_count) VALUES (?,?,1) "
        "ON CONFLICT(agent_number, cycle_id) DO UPDATE SET heavy_count = heavy_count + 1",
        (agent_number, cycle_id),
    )
    conn.commit()


def get_turn_count(conn, agent_number: int, cycle_id: int) -> int:
    row = conn.execute(
        "SELECT turn_count FROM turn_usage WHERE agent_number = ? AND cycle_id = ?",
        (agent_number, cycle_id),
    ).fetchone()
    return row["turn_count"] if row else 0


def increment_turn_count(conn, agent_number: int, cycle_id: int):
    conn.execute(
        "INSERT INTO turn_usage (agent_number, cycle_id, turn_count) VALUES (?,?,1) "
        "ON CONFLICT(agent_number, cycle_id) DO UPDATE SET turn_count = turn_count + 1",
        (agent_number, cycle_id),
    )
    conn.commit()


def set_owner_instruction(conn, message: str):
    """Desactiva la instrucción anterior (si había) y guarda la nueva como
    activa. Se muestra a TODOS los agentes en su próximo turno."""
    conn.execute("UPDATE owner_instructions SET active = 0 WHERE active = 1")
    conn.execute(
        "INSERT INTO owner_instructions (message, active, created_at) VALUES (?,1,?)",
        (message, time.time()),
    )
    conn.commit()


def get_owner_instruction(conn):
    row = conn.execute(
        "SELECT message, created_at FROM owner_instructions WHERE active = 1 "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    return dict(row) if row else None


def clear_owner_instruction(conn):
    conn.execute("UPDATE owner_instructions SET active = 0 WHERE active = 1")
    conn.commit()


# ── Trading & Portfolio helpers ──────────────────────────────────────

def save_trade(conn, agent_number: int, token_in: str, token_out: str,
               amount_in: float, amount_out: float, tx_hash: str, status: str):
    conn.execute(
        "INSERT INTO trades (agent_number, timestamp, token_in, token_out, "
        "amount_in, amount_out, tx_hash, status) VALUES (?,?,?,?,?,?,?,?)",
        (agent_number, time.time(), token_in, token_out, amount_in, amount_out,
         tx_hash, status),
    )
    conn.commit()


def get_agent_trades(conn, agent_number: int, limit: int = 20) -> list:
    rows = conn.execute(
        "SELECT * FROM trades WHERE agent_number = ? ORDER BY timestamp DESC LIMIT ?",
        (agent_number, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def save_token_holding(conn, agent_number: int, token_address: str, token_symbol: str):
    conn.execute(
        "INSERT OR REPLACE INTO token_holdings (agent_number, token_address, token_symbol) "
        "VALUES (?,?,?)", (agent_number, token_address, token_symbol),
    )
    conn.commit()


def get_token_holdings(conn, agent_number: int) -> list:
    rows = conn.execute(
        "SELECT token_address, token_symbol FROM token_holdings WHERE agent_number = ?",
        (agent_number,),
    ).fetchall()
    return [dict(r) for r in rows]


def save_opportunity_request(conn, agent_number: int, platform: str,
                              reason: str, url: str) -> int:
    cur = conn.execute(
        "INSERT INTO opportunity_requests (agent_number, platform, reason, url, "
        "status, created_at) VALUES (?,?,?,?,'pending',?)",
        (agent_number, platform, reason, url, time.time()),
    )
    conn.commit()
    return cur.lastrowid


def get_pending_opportunities(conn) -> list:
    rows = conn.execute(
        "SELECT * FROM opportunity_requests WHERE status = 'pending' "
        "ORDER BY created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def update_opportunity_status(conn, request_id: int, status: str):
    conn.execute(
        "UPDATE opportunity_requests SET status = ? WHERE id = ?",
        (status, request_id),
    )
    conn.commit()


# ── LLM Config & Autonomy helpers ────────────────────────────────────

def save_agent_llm_config(conn, agent_number: int, generation: int = 0,
                         provider: str = 'mistral', model_name: str = 'mistral-small-latest',
                         api_key: str | None = None, parent_number: int | None = None,
                         server_paid_until: float | None = None):
    now = time.time()
    if server_paid_until is None:
        server_paid_until = now + (30 * 24 * 60 * 60) # 1 mes por defecto
    conn.execute(
        "INSERT INTO agent_llm_config (agent_number, generation, provider, model_name, "
        "api_key, parent_number, server_paid_until, tokens_used_today, last_token_reset, "
        "last_cloned_tier, created_at) VALUES (?,?,?,?,?,?,?,0,?,0.0,?) "
        "ON CONFLICT(agent_number) DO UPDATE SET "
        "generation=excluded.generation, provider=excluded.provider, "
        "model_name=excluded.model_name, api_key=excluded.api_key, "
        "server_paid_until=excluded.server_paid_until",
        (agent_number, generation, provider, model_name, api_key, parent_number,
         server_paid_until, now, now),
    )
    conn.commit()


def get_agent_llm_config(conn, agent_number: int) -> dict | None:
    row = conn.execute("SELECT * FROM agent_llm_config WHERE agent_number = ?", (agent_number,)).fetchone()
    return dict(row) if row else None


def update_tokens_used(conn, agent_number: int, tokens: int):
    conn.execute(
        "UPDATE agent_llm_config SET tokens_used_today = tokens_used_today + ? WHERE agent_number = ?",
        (tokens, agent_number),
    )
    conn.commit()


def reset_daily_tokens_if_needed(conn, agent_number: int):
    config = get_agent_llm_config(conn, agent_number)
    if not config:
        return
    last_reset = config.get("last_token_reset", 0)
    now = time.time()
    if now - last_reset > 86400: # 24 horas transcurridas
        conn.execute(
            "UPDATE agent_llm_config SET tokens_used_today = 0, last_token_reset = ? WHERE agent_number = ?",
            (now, agent_number),
        )
        conn.commit()


def get_agent_revenue_48h(conn, agent_number: int) -> float:
    """Calcula los ingresos netos acumulados por el agente en las últimas 48 horas."""
    cutoff = time.time() - (48 * 60 * 60)
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_result), 0.0) r FROM actions "
        "WHERE agent_number = ? AND status = 'approved' AND timestamp > ?",
        (agent_number, cutoff),
    ).fetchone()
    return float(row["r"] if row else 0.0)


def update_last_cloned_tier(conn, agent_number: int, tier: float):
    conn.execute(
        "UPDATE agent_llm_config SET last_cloned_tier = ? WHERE agent_number = ?",
        (tier, agent_number),
    )
    conn.commit()


def get_all_historical_agents(conn) -> list:
    """Devuelve la lista de TODOS los agentes creados históricamente."""
    rows = conn.execute(
        "SELECT a.*, c.provider, c.model_name, c.server_paid_until, c.tokens_used_today, c.last_cloned_tier "
        "FROM agents a LEFT JOIN agent_llm_config c ON a.number = c.agent_number "
        "ORDER BY a.number ASC"
    ).fetchall()
    return [dict(r) for r in rows]

