"""
Derivación determinista de direcciones a partir de UNA semilla maestra.
La semilla vive solo en la variable de entorno MASTER_SEED del servidor
(nunca en el código que los agentes pueden leer o proponer modificar).

IMPORTANTE (léelo antes de mandar dinero real):
Este módulo deriva direcciones reales de Ethereum/Base. El "balance" que
usa el resto del sistema (leaderboard, selección, reparto) vive en la
base de datos local, NO se lee on-chain automáticamente. Trátalo como
un marcador/juego hasta que conectes ejecución real. Conectarlo a fondos
reales de forma 100% automática (leer saldo on-chain, dejar que un
agente firme transacciones sin límite) es la parte de más riesgo de
todo el proyecto: hazlo solo después de tener el filtro y el kill
switch probados durante un tiempo, y con topes de gasto explícitos
(revisa la nota sobre "spending limit wallets" en el README).

Por defecto esto apunta a BASE MAINNET (dinero real, red de bajo coste).
Para pruebas sin arriesgar fondos reales, exporta estas variables antes
de nada (Base Sepolia, la testnet de Base):
    RPC_URL=https://sepolia.base.org
    CHAIN_ID=84532
"""
import os
import time
import logging
from eth_account import Account
from eth_account.hdaccount import ETHEREUM_DEFAULT_PATH, generate_mnemonic
from web3 import Web3

Account.enable_unaudited_hdwallet_features()
log = logging.getLogger("wallet")

# Producción: Base mainnet. Barato (céntimos de dólar por transferencia)
# y compatible con el mismo código EVM que el resto del proyecto.
# Para probar sin dinero real, sobreescribe estas dos variables en el
# .env con los valores de Base Sepolia indicados arriba.
RPC_URL = os.environ.get("RPC_URL", "https://mainnet.base.org")
CHAIN_ID = int(os.environ.get("CHAIN_ID", "8453"))  # Base mainnet
GAS_LIMIT_TRANSFER = 21000

if CHAIN_ID == 8453:
    log.warning(
        "wallet.py está configurado en BASE MAINNET: cualquier transferencia "
        "que se ejecute aquí mueve fondos reales y es irreversible."
    )


def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    parts = line.split("=", 1)
                    key = parts[0].replace("export", "").strip()
                    val = parts[1].strip().strip("'\"")
                    if key and key not in os.environ:
                        os.environ[key] = val

load_env()


def get_or_create_master_seed() -> str:
    load_env()
    seed = os.environ.get("MASTER_SEED")
    if not seed:
        seed = generate_new_seed()
        os.environ["MASTER_SEED"] = seed
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        try:
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f'\nMASTER_SEED="{seed}"\n')
        except Exception as e:
            log.warning("No se pudo guardar MASTER_SEED en .env: %s", e)
        print(f"\n[MASTER_SEED] SE HA GENERADO UNA NUEVA MASTER_SEED AUTOMÁTICAMENTE Y GUARDADO EN .env:\n   {seed}\n")
    return seed


def generate_new_seed() -> str:
    """Llamar UNA sola vez al montar el sistema. Guarda el resultado en
    el .env de la VM y no lo pierdas: sin él no recuperas ninguna wallet."""
    return generate_mnemonic(num_words=24, lang="english")


def account_for_agent(agent_number: int) -> Account:
    """Devuelve el objeto Account completo (incluye la clave privada,
    derivada al vuelo, nunca guardada en la base de datos)."""
    seed = get_or_create_master_seed()
    path = f"m/44'/60'/0'/0/{agent_number}"
    return Account.from_mnemonic(seed, account_path=path)


def wallet_for_agent(agent_number: int) -> dict:
    """Versión pública: solo la dirección, para guardar en la DB y mostrar
    en el panel. Se llama automáticamente cada vez que nace un agente."""
    acct = account_for_agent(agent_number)
    return {"address": acct.address, "path": f"m/44'/60'/0'/0/{agent_number}"}


def _web3() -> Web3:
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        raise RuntimeError(f"No se pudo conectar al nodo RPC: {RPC_URL}")
    return w3


def onchain_balance(address: str) -> float:
    """Saldo real en la red configurada (ETH o la moneda nativa de la red
    elegida), en unidades normales, no wei."""
    w3 = _web3()
    wei = w3.eth.get_balance(Web3.to_checksum_address(address))
    return float(Web3.from_wei(wei, "ether"))


def transfer_to_owner(agent_number: int, owner_address: str, max_retries: int = 3) -> dict:
    """Transfiere TODO el saldo on-chain de la wallet del agente a tu
    wallet. Se llama automáticamente cuando un agente es eliminado en la
    selección. Deja algo de margen para el gas: si el saldo es menor que
    el coste estimado de gas, no intenta transferir (evita transacciones
    fallidas que solo queman gas)."""
    w3 = _web3()
    acct = account_for_agent(agent_number)
    address = Web3.to_checksum_address(acct.address)
    owner_address = Web3.to_checksum_address(owner_address)

    balance_wei = w3.eth.get_balance(address)
    if balance_wei == 0:
        return {"ok": True, "skipped": True, "reason": "balance on-chain es 0"}

    gas_price = w3.eth.gas_price
    gas_cost = gas_price * GAS_LIMIT_TRANSFER
    send_wei = balance_wei - gas_cost
    if send_wei <= 0:
        return {"ok": False, "skipped": True, "reason": "balance no cubre el coste de gas"}

    last_error = None
    for attempt in range(1, max_retries + 1):
        try:
            nonce = w3.eth.get_transaction_count(address, "pending")
            tx = {
                "to": owner_address,
                "value": send_wei,
                "gas": GAS_LIMIT_TRANSFER,
                "gasPrice": gas_price,
                "nonce": nonce,
                "chainId": CHAIN_ID,
            }
            signed = acct.sign_transaction(tx)
            tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            return {
                "ok": receipt.status == 1,
                "tx_hash": tx_hash.hex(),
                "amount_eth": float(Web3.from_wei(send_wei, "ether")),
            }
        except Exception as e:  # noqa: BLE001 - queremos capturar y reintentar cualquier fallo de red
            last_error = str(e)
            log.warning("Intento %s/%s de transferencia del agente #%s falló: %s",
                        attempt, max_retries, agent_number, last_error)
            time.sleep(2 * attempt)

    return {"ok": False, "error": last_error}


if __name__ == "__main__":
    # Utilidad de línea de comandos para generar la semilla la primera vez
    print(generate_new_seed())
