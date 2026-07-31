"""
Ejecuta esto UNA vez por cada cuenta que ya hayas autorizado en el
dashboard de Composio (composio.dev), para añadirla al pool que se
reparte entre agentes.

Uso:
    python3 add_composio_account.py

Te pedirá:
- Un nombre para ti (ej. "instagram_tienda_dropshipping_1")
- El user_id exacto que usaste al conectar la cuenta en Composio
- La plataforma (ej. "instagram") — opcional, solo informativo
"""
import db

if __name__ == "__main__":
    label = input("Nombre para esta cuenta (solo para ti, ej. instagram_tienda_1): ").strip()
    composio_user_id = input("user_id de Composio para esta cuenta: ").strip()
    platform = input("Plataforma (opcional, ej. instagram/twitter/gmail): ").strip()

    conn = db.get_conn()
    conn.executescript(db.SCHEMA)  # por si aún no existen las tablas nuevas
    db.add_composio_account(conn, label, composio_user_id, platform)
    conn.close()

    print(f"Cuenta '{label}' añadida al pool. Se asignará automáticamente a un agente "
          f"en su próximo turno (reparto estable, no cambia una vez asignada).")

