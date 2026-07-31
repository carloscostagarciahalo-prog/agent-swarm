import logging
import time
import db
import browser_flows

logger = logging.getLogger('opportunities')

def attempt_auto_connect(agent_number: int, conn, platform: str, url: str, reason: str, email_lookup) -> dict:
    """
    Intenta crear una cuenta automáticamente en una plataforma Web2.
    
    1. Verifica si el agente ya tiene credenciales para este dominio usando db.get_credentials
    2. Si sí, devuelve éxito con las credenciales existentes
    3. Si no, lanza el flujo del navegador para crear la cuenta usando browser_flows.flujo_crear_cuenta
    4. Registra el intento en la tabla opportunity_requests
    5. Devuelve el resultado: {success: bool, data: dict|None, error: str|None}
    
    El correo del agente es agente{agent_number}@nesion.net
    """
    try:
        email = f"agente{agent_number}@nesion.net"
        logger.info(f"Agente {agent_number} intentando conexión automática a {platform} ({url}). Razón: {reason}")
        
        # 1. Verifica si el agente ya tiene credenciales
        existing_creds = db.get_credentials(conn, agent_number, platform)
        
        # 2. Si las tiene, retornar éxito
        if existing_creds:
            logger.info(f"Agente {agent_number} ya tiene credenciales para {platform}.")
            log_opportunity(conn, agent_number, platform, reason, url, 'existing_credentials')
            return {"success": True, "data": existing_creds, "error": None}
            
        # 3. Flujo del navegador para crear cuenta
        logger.info(f"Iniciando flujo de creación de cuenta para agente {agent_number} en {platform}")
        flow_result = browser_flows.flujo_crear_cuenta(platform, url, email, email_lookup)
        
        if flow_result is None:
            flow_result = {"success": False, "data": None, "error": "Unknown error in browser flow"}
            
        # 4. Registrar intento
        status = 'success' if flow_result.get('success') else 'failed'
        log_opportunity(conn, agent_number, platform, reason, url, status)
        
        # 5. Devolver resultado
        return {
            "success": flow_result.get('success', False),
            "data": flow_result.get('data', None),
            "error": flow_result.get('error', None)
        }
    except Exception as e:
        logger.error(f"Error en attempt_auto_connect para agente {agent_number} en {platform}: {e}", exc_info=True)
        return {"success": False, "data": None, "error": str(e)}

def get_agent_platforms(conn, agent_number: int) -> list[str]:
    """
    Devuelve la lista de dominios de plataformas donde el agente tiene credenciales.
    """
    try:
        return db.list_credential_domains(conn, agent_number)
    except Exception as e:
        logger.error(f"Error al obtener plataformas del agente {agent_number}: {e}", exc_info=True)
        return []

def log_opportunity(conn, agent_number: int, platform: str, reason: str, url: str, status: str) -> None:
    """
    Registra un intento de oportunidad en la tabla opportunity_requests.
    """
    try:
        logger.info(f"Registrando oportunidad para agente {agent_number}: {platform} - Estado: {status}")
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO opportunity_requests (agent_number, platform, reason, url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (agent_number, platform, reason, url, status, int(time.time())))
        conn.commit()
    except Exception as e:
        logger.error(f"Error al registrar oportunidad para agente {agent_number} en {platform}: {e}", exc_info=True)
