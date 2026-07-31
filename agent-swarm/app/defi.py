"""
Vía de ingresos totalmente autónoma: depositar capital propio en un
protocolo de préstamos (tipo Aave) para generar interés. Es la forma más
limpia de "sin intervención humana" que existe, porque los contratos son
de acceso público por diseño — no hay cuenta que crear, no hay CAPTCHA,
no hay ToS de bots que violar. Cada agente solo puede tocar SU PROPIA
wallet, nunca la de otro.

IMPORTANTE — LEE ANTES DE CONFIGURAR AAVE_POOL_ADDRESS:
No incluyo aquí ninguna dirección de contrato de memoria ni copiada sin
verificar. Un solo carácter mal puesto en una dirección de contrato de
DeFi significa fondos perdidos para siempre, sin ninguna forma de
recuperarlos — no hay "deshacer" en una blockchain. Tienes que:

  1. Ir a https://github.com/bgd-labs/aave-address-book (el repositorio
     oficial de direcciones de Aave, mantenido por su equipo) y buscar la
     dirección del "Pool" para Base.
  2. Verificarla de forma cruzada en https://basescan.org buscando el
     contrato y confirmando que está marcado como "Verified" y que
     corresponde a Aave.
  3. Solo entonces ponerla en el .env como AAVE_POOL_ADDRESS.

Si AAVE_POOL_ADDRESS no está configurada, este módulo se niega a operar
en vez de arriesgarse a usar una dirección equivocada.
"""
import os
import logging
from web3 import Web3
from wallet import account_for_agent, _web3

log = logging.getLogger("defi")

POOL_ADDRESS = os.environ.get("AAVE_POOL_ADDRESS")

# USDC nativo (emitido por Circle) en Base — dirección canónica, confirmada
# de forma cruzada en basescan.org, la documentación de Circle y CoinGecko.
# Verifícala tú también antes de operar con dinero real:
# https://basescan.org/address/0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
USDC_ADDRESS = os.environ.get("USDC_ADDRESS", "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
USDC_DECIMALS = 6

ERC20_ABI = [
    {"constant": False, "inputs": [{"name": "spender", "type": "address"},
     {"name": "amount", "type": "uint256"}], "name": "approve",
     "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

# Firma mínima del método supply() de Aave v3 Pool.
POOL_ABI = [
    {"inputs": [
        {"name": "asset", "type": "address"},
        {"name": "amount", "type": "uint256"},
        {"name": "onBehalfOf", "type": "address"},
        {"name": "referralCode", "type": "uint16"},
    ], "name": "supply", "outputs": [], "type": "function"},
]


def is_configured() -> bool:
    return bool(POOL_ADDRESS)


def supply_usdc(agent_number: int, amount_usdc: float) -> dict:
    """Vía por defecto: deposita USDC directamente en Aave para generar
    interés. Al ser ya un ERC20 (no ETH nativo), es un paso menos que la
    vía de ETH — solo aprobar y depositar."""
    if not is_configured():
        return {"ok": False, "error": "AAVE_POOL_ADDRESS no configurada. Verifícala antes de operar (ver docstring)."}

    amount_units = int(amount_usdc * (10 ** USDC_DECIMALS))
    if amount_units <= 0:
        return {"ok": False, "error": "importe en USDC debe ser mayor que 0"}

    return supply_to_lending(agent_number, USDC_ADDRESS, amount_units)


def supply_to_lending(agent_number: int, asset_address: str, amount_wei: int) -> dict:
    """El propio agente decide cuánto de su balance en un token concreto
    (ej. USDC en Base) depositar para generar interés. Firma con SU
    propia clave, solo puede mover SU propio saldo — nunca el de otro
    agente ni el tuyo."""
    if not is_configured():
        return {"ok": False, "error": "AAVE_POOL_ADDRESS no configurada. Verifícala antes de operar (ver docstring)."}

    w3 = _web3()
    acct = account_for_agent(agent_number)
    address = Web3.to_checksum_address(acct.address)
    pool = Web3.to_checksum_address(POOL_ADDRESS)
    asset = Web3.to_checksum_address(asset_address)

    token = w3.eth.contract(address=asset, abi=ERC20_ABI)
    pool_contract = w3.eth.contract(address=pool, abi=POOL_ABI)
    gas_price = w3.eth.gas_price

    try:
        # 1. Aprobar que el contrato de Aave pueda mover ese monto del token
        nonce = w3.eth.get_transaction_count(address, "pending")
        approve_tx = token.functions.approve(pool, amount_wei).build_transaction({
            "from": address, "nonce": nonce, "gasPrice": gas_price, "chainId": w3.eth.chain_id,
        })
        signed = acct.sign_transaction(approve_tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        # 2. Depositar (supply) en el pool, a nombre del propio agente
        nonce = w3.eth.get_transaction_count(address, "pending")
        supply_tx = pool_contract.functions.supply(asset, amount_wei, address, 0).build_transaction({
            "from": address, "nonce": nonce, "gasPrice": gas_price, "chainId": w3.eth.chain_id,
        })
        signed = acct.sign_transaction(supply_tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        return {"ok": receipt.status == 1, "tx_hash": tx_hash.hex()}
    except Exception as e:  # noqa: BLE001
        log.warning("Fallo al depositar en lending para el agente #%s: %s", agent_number, e)
        return {"ok": False, "error": str(e)}
