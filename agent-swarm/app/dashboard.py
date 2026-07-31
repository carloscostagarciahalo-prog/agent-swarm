"""
Panel de control. Dos superficies distintas:
  - /api/public/*  -> lo que un agente puede consultar (clasificación,
                      tiempo hasta la siguiente selección). Sin detalle
                      de qué está haciendo cada uno ahora mismo.
  - /              -> tu panel visual (mismo endpoint público + detalle
                      privado por agente, protegido con contraseña simple).
  - /api/admin/kill -> kill switch, protegido.
"""
import os
import time
from flask import Flask, jsonify, render_template, request, abort

import db
import wallet as wallet_module
import inbox
import portfolio
from orchestrator import CYCLE_SECONDS

app = Flask(__name__)
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "cambia-esto")
INBOX_WEBHOOK_SECRET = os.environ.get("INBOX_WEBHOOK_SECRET", "cambia-esto-tambien")
if ADMIN_TOKEN == "cambia-esto":
    import logging
    logging.getLogger("dashboard").warning(
        "ADMIN_TOKEN sigue en su valor por defecto. Cambialo en el .env antes "
        "de exponer el panel a internet."
    )


def require_admin():
    token = request.headers.get("X-Admin-Token") or request.args.get("token")
    if token != ADMIN_TOKEN:
        abort(401)


# ---------- Lo que tambien pueden leer los agentes ----------

@app.route("/health")
def health():
    """Sin token: pensado para servicios externos de monitorizacion
    gratuitos (UptimeRobot, etc.) que solo necesitan saber si el panel
    responde y si hay un ciclo corriendo -- nada sensible aqui."""
    conn = db.get_conn()
    row = conn.execute(
        "SELECT status FROM cycles ORDER BY id DESC LIMIT 1"
    ).fetchone()
    alive_count = conn.execute("SELECT COUNT(*) c FROM agents WHERE alive = 1").fetchone()["c"]
    conn.close()
    return jsonify({
        "status": "ok",
        "cycle_status": row["status"] if row else "no_cycle_yet",
        "agents_alive": alive_count,
    })


@app.route("/api/public/leaderboard")
def public_leaderboard():
    conn = db.get_conn()
    rows = conn.execute(
        "SELECT number, balance FROM agents WHERE alive = 1 ORDER BY balance DESC"
    ).fetchall()
    conn.close()
    return jsonify([{"number": r["number"], "balance": round(r["balance"], 2)} for r in rows])


@app.route("/api/public/next_selection")
def next_selection():
    conn = db.get_conn()
    row = conn.execute(
        "SELECT start_time FROM cycles WHERE status = 'running' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"seconds_left": None})
    remaining = max(0, row["start_time"] + CYCLE_SECONDS - time.time())
    return jsonify({"seconds_left": int(remaining)})


# ---------- Solo para ti ----------

@app.route("/api/inbox/receive", methods=["POST"])
def receive_email():
    secret = request.headers.get("X-Inbox-Secret")
    if secret != INBOX_WEBHOOK_SECRET:
        abort(401)
    data = request.json or {}
    conn = db.get_conn()
    stored = inbox.parse_and_store(
        conn, db,
        to_address=str(data.get("to", "")),
        from_address=str(data.get("from", "")),
        subject=str(data.get("subject", ""))[:300],
        body_text=str(data.get("text", ""))[:5000],
    )
    conn.close()
    return jsonify({"stored": stored})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/admin/total_generated")
def total_generated():
    require_admin()
    conn = db.get_conn()
    row = conn.execute("SELECT COALESCE(SUM(amount_result),0) t FROM actions").fetchone()
    conn.close()
    return jsonify({"total": round(row["t"], 2)})


@app.route("/api/admin/historical_agents")
def historical_agents():
    """Devuelve la lista completa de TODOS los agentes creados históricamente."""
    require_admin()
    conn = db.get_conn()
    agents = db.get_all_historical_agents(conn)
    conn.close()
    return jsonify(agents)


@app.route("/api/admin/agent/<int:number>")
def agent_detail(number):
    require_admin()
    conn = db.get_conn()
    agent = conn.execute("SELECT * FROM agents WHERE number = ?", (number,)).fetchone()
    if not agent:
        abort(404)
    actions = conn.execute(
        "SELECT * FROM actions WHERE agent_number = ? ORDER BY timestamp DESC LIMIT 50",
        (number,),
    ).fetchall()

    # Configuración de LLM y cuotas
    llm_cfg = db.get_agent_llm_config(conn, number)

    # Portfolio real on-chain
    agent_portfolio = None
    try:
        holdings = db.get_token_holdings(conn, number)
        extra = [h["token_address"] for h in holdings]
        agent_portfolio = portfolio.get_portfolio_value_usd(number, extra_tokens=extra or None)
    except Exception:
        agent_portfolio = None

    # Trades del agente
    trades = db.get_agent_trades(conn, number, limit=20)

    conn.close()

    onchain = None
    if agent["wallet_address"]:
        try:
            onchain = wallet_module.onchain_balance(agent["wallet_address"])
        except Exception:
            onchain = None

    return jsonify({
        "number": agent["number"],
        "balance": round(agent["balance"], 2),
        "wallet_address": agent["wallet_address"],
        "onchain_balance": onchain,
        "portfolio": agent_portfolio,
        "trades": trades,
        "generation": agent["generation"],
        "parent_number": agent["parent_number"],
        "alive": bool(agent["alive"]),
        "llm_config": llm_cfg,
        "actions": [dict(a) for a in actions],
    })


@app.route("/api/admin/agent/<int:number>/portfolio")
def agent_portfolio_endpoint(number):
    """Portfolio real on-chain con tokens y valores USD."""
    require_admin()
    conn = db.get_conn()
    agent = conn.execute("SELECT * FROM agents WHERE number = ?", (number,)).fetchone()
    if not agent:
        abort(404)
    holdings = db.get_token_holdings(conn, number)
    extra = [h["token_address"] for h in holdings]
    conn.close()
    try:
        pv = portfolio.get_portfolio_value_usd(number, extra_tokens=extra or None)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    return jsonify(pv)


@app.route("/api/admin/agent/<int:number>/trades")
def agent_trades_endpoint(number):
    """Historial de trades on-chain del agente."""
    require_admin()
    conn = db.get_conn()
    trades = db.get_agent_trades(conn, number, limit=50)
    conn.close()
    return jsonify(trades)


@app.route("/api/admin/opportunities")
def opportunities_list():
    """Solicitudes de conexion a plataformas Web2 pendientes."""
    require_admin()
    conn = db.get_conn()
    pending = db.get_pending_opportunities(conn)
    conn.close()
    return jsonify(pending)


@app.route("/api/admin/opportunities/<int:req_id>/approve", methods=["POST"])
def approve_opportunity(req_id):
    """Aprobar una solicitud de conexion."""
    require_admin()
    conn = db.get_conn()
    db.update_opportunity_status(conn, req_id, "approved")
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/opportunities/<int:req_id>/reject", methods=["POST"])
def reject_opportunity(req_id):
    """Rechazar una solicitud de conexion."""
    require_admin()
    conn = db.get_conn()
    db.update_opportunity_status(conn, req_id, "rejected")
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/admin/instruction", methods=["GET"])
def get_instruction():
    require_admin()
    conn = db.get_conn()
    instruction = db.get_owner_instruction(conn)
    conn.close()
    return jsonify(instruction or {"message": None, "created_at": None})


@app.route("/api/admin/instruction", methods=["POST"])
def set_instruction():
    require_admin()
    data = request.get_json(silent=True) or {}
    message = str(data.get("message", "")).strip()[:1000]
    conn = db.get_conn()
    if message:
        db.set_owner_instruction(conn, message)
    else:
        db.clear_owner_instruction(conn)
    conn.close()
    return jsonify({"ok": True, "message": message or None})


@app.route("/api/admin/kill", methods=["POST"])
def kill_agent():
    require_admin()
    data = request.get_json(silent=True) or {}
    number = data.get("number")
    if number is None:
        return jsonify({"ok": False, "error": "Falta el numero de agente"}), 400
    reason = str(data.get("reason", "manual"))
    conn = db.get_conn()
    conn.execute("UPDATE agents SET alive = 0, killed_reason = ? WHERE number = ?", (reason, number))
    conn.execute(
        "INSERT INTO kill_switch_log (agent_number, action, reason, timestamp) VALUES (?,?,?,?)",
        (number, "kill", reason, time.time()),
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

