"""
Módulo de trading on-chain: swaps reales en Uniswap V3 en Base mainnet.

Permite a los agentes comprar y vender tokens ERC-20 en DEXes de forma
totalmente autónoma. Cada swap es una transacción real verificable en
BaseScan. Sigue el mismo patrón de firma local de defi.py.

Direcciones verificadas en Base mainnet:
  SwapRouter02: 0x2626664c2603336E57B271c5C0b26F421741e481
  QuoterV2:     0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a
  WETH:         0x4200000000000000000000000000000000000006
  USDC:         0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913
"""
import os
import time
import logging
from web3 import Web3

import wallet

log = logging.getLogger("trading")

# ── Direcciones Uniswap V3 en Base mainnet ─────────────────────────
SWAP_ROUTER = Web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")
QUOTER_V2   = Web3.to_checksum_address("0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a")
WETH        = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC        = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# Fee tiers disponibles en Uniswap V3 (en centésimas de basis point)
FEE_TIERS = [500, 3000, 10000, 100]  # 0.05%, 0.3%, 1%, 0.01%
DEFAULT_FEE = 3000  # 0.3% — el más común

# Slippage por defecto (1%)
DEFAULT_SLIPPAGE_PCT = 1.0

# Deadline: 5 minutos desde ahora
SWAP_DEADLINE_SECONDS = 300

# ── ABIs mínimos ───────────────────────────────────────────────────

ERC20_ABI = [
    {"inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}],
     "stateMutability": "nonpayable", "type": "function"},
    {"inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}],
     "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}],
     "stateMutability": "view", "type": "function"},
]

# SwapRouter02.exactInputSingle — executa un swap de token A → token B
SWAP_ROUTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn", "type": "address"},
                {"name": "tokenOut", "type": "address"},
                {"name": "fee", "type": "uint24"},
                {"name": "recipient", "type": "address"},
                {"name": "amountIn", "type": "uint256"},
                {"name": "amountOutMinimum", "type": "uint256"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "name": "params",
            "type": "tuple",
        }],
        "name": "exactInputSingle",
        "outputs": [{"name": "amountOut", "type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
]

# QuoterV2.quoteExactInputSingle — consulta cuánto recibirías sin ejecutar
QUOTER_ABI = [
    {
        "inputs": [{
            "components": [
                {"name": "tokenIn", "type": "address"},
                {"name": "tokenOut", "type": "address"},
                {"name": "amountIn", "type": "uint256"},
                {"name": "fee", "type": "uint24"},
                {"name": "sqrtPriceLimitX96", "type": "uint160"},
            ],
            "name": "params",
            "type": "tuple",
        }],
        "name": "quoteExactInputSingle",
        "outputs": [
            {"name": "amountOut", "type": "uint256"},
            {"name": "sqrtPriceX96After", "type": "uint160"},
            {"name": "initializedTicksCrossed", "type": "uint32"},
            {"name": "gasEstimate", "type": "uint256"},
        ],
        "stateMutability": "nonpayable",
        "type": "function",
    },
]


# ── Utilidades ─────────────────────────────────────────────────────

def _w3():
    return wallet._web3()


def _token_decimals(w3, token_address: str) -> int:
    """Obtiene los decimales de un token ERC-20."""
    try:
        token = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )
        return token.functions.decimals().call()
    except Exception:
        return 18  # fallback


def _token_symbol(w3, token_address: str) -> str:
    """Obtiene el símbolo de un token ERC-20."""
    try:
        token = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )
        return token.functions.symbol().call()
    except Exception:
        return "???"


def _token_balance(w3, token_address: str, owner: str) -> int:
    """Balance crudo (en wei/unidades mínimas) de un ERC-20."""
    try:
        token = w3.eth.contract(
            address=Web3.to_checksum_address(token_address), abi=ERC20_ABI
        )
        return token.functions.balanceOf(Web3.to_checksum_address(owner)).call()
    except Exception:
        return 0


# ── Cotización (sin ejecutar swap) ─────────────────────────────────

def get_quote(token_in: str, token_out: str, amount_in: float,
              fee: int = DEFAULT_FEE) -> dict:
    """Consulta cuánto se recibiría por un swap, sin ejecutarlo.

    Returns: {"ok": True, "amount_out": float, "amount_out_raw": int}
             o {"ok": False, "error": str}
    """
    try:
        w3 = _w3()
        token_in = Web3.to_checksum_address(token_in)
        token_out = Web3.to_checksum_address(token_out)

        decimals_in = _token_decimals(w3, token_in)
        decimals_out = _token_decimals(w3, token_out)
        amount_in_raw = int(amount_in * (10 ** decimals_in))

        quoter = w3.eth.contract(address=QUOTER_V2, abi=QUOTER_ABI)

        # call() en vez de transact() — solo lectura
        result = quoter.functions.quoteExactInputSingle((
            token_in,
            token_out,
            amount_in_raw,
            fee,
            0,  # sqrtPriceLimitX96 = 0 (sin límite)
        )).call()

        amount_out_raw = result[0]
        amount_out = amount_out_raw / (10 ** decimals_out)

        return {
            "ok": True,
            "amount_in": amount_in,
            "amount_out": amount_out,
            "amount_out_raw": amount_out_raw,
            "symbol_in": _token_symbol(w3, token_in),
            "symbol_out": _token_symbol(w3, token_out),
            "fee_tier": fee,
        }
    except Exception as e:
        log.warning("Error al consultar quote %s→%s: %s", token_in, token_out, e)
        return {"ok": False, "error": str(e)}


def find_best_fee(token_in: str, token_out: str, amount_in: float) -> int:
    """Prueba los fee tiers disponibles y devuelve el que da mejor precio."""
    best_fee = DEFAULT_FEE
    best_out = 0
    for fee in FEE_TIERS:
        q = get_quote(token_in, token_out, amount_in, fee=fee)
        if q.get("ok") and q["amount_out"] > best_out:
            best_out = q["amount_out"]
            best_fee = fee
    return best_fee


# ── Swap real on-chain ─────────────────────────────────────────────

def swap_exact_input(agent_number: int, token_in: str, token_out: str,
                     amount_in: float, slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
                     fee: int | None = None) -> dict:
    """Ejecuta un swap real de token_in → token_out en Uniswap V3.

    Args:
        agent_number: Número del agente (para derivar su wallet)
        token_in: Dirección del token a vender
        token_out: Dirección del token a comprar
        amount_in: Cantidad a vender (en unidades humanas, ej: 10.0 USDC)
        slippage_pct: Tolerancia de slippage (1.0 = 1%)
        fee: Fee tier de Uniswap (None = auto-detectar mejor)

    Returns:
        {"ok": True, "tx_hash": str, "amount_in": float, "amount_out": float, ...}
        o {"ok": False, "error": str}
    """
    try:
        w3 = _w3()
        acct = wallet.account_for_agent(agent_number)
        sender = acct.address

        token_in = Web3.to_checksum_address(token_in)
        token_out = Web3.to_checksum_address(token_out)

        decimals_in = _token_decimals(w3, token_in)
        decimals_out = _token_decimals(w3, token_out)
        symbol_in = _token_symbol(w3, token_in)
        symbol_out = _token_symbol(w3, token_out)
        amount_in_raw = int(amount_in * (10 ** decimals_in))

        # Verificar que el agente tiene suficiente balance
        balance_raw = _token_balance(w3, token_in, sender)
        if balance_raw < amount_in_raw:
            balance_human = balance_raw / (10 ** decimals_in)
            return {
                "ok": False,
                "error": f"Balance insuficiente: tienes {balance_human:.6f} {symbol_in}, "
                         f"necesitas {amount_in:.6f} {symbol_in}",
            }

        # Auto-detectar mejor fee tier si no se especifica
        if fee is None:
            fee = find_best_fee(token_in, token_out, amount_in)
            log.info("Fee tier auto-detectado: %d (%.2f%%)", fee, fee / 10000)

        # Obtener quote para calcular amountOutMinimum con slippage
        quote = get_quote(token_in, token_out, amount_in, fee=fee)
        if not quote.get("ok"):
            return {"ok": False, "error": f"No se pudo obtener precio: {quote.get('error')}"}

        amount_out_min_raw = int(quote["amount_out_raw"] * (1 - slippage_pct / 100))

        # ── Paso 1: Approve ──
        log.info("[Agente %d] Aprobando %s %s para SwapRouter...",
                 agent_number, amount_in, symbol_in)
        token_contract = w3.eth.contract(address=token_in, abi=ERC20_ABI)

        gas_price = w3.eth.gas_price
        nonce = w3.eth.get_transaction_count(sender, "pending")

        approve_tx = token_contract.functions.approve(
            SWAP_ROUTER, amount_in_raw
        ).build_transaction({
            "from": sender,
            "nonce": nonce,
            "gas": 60000,
            "gasPrice": gas_price,
            "chainId": wallet.CHAIN_ID,
        })
        signed_approve = acct.sign_transaction(approve_tx)
        approve_hash = w3.eth.send_raw_transaction(signed_approve.raw_transaction)
        w3.eth.wait_for_transaction_receipt(approve_hash, timeout=120)
        log.info("[Agente %d] Approve confirmado: %s", agent_number, approve_hash.hex())

        # ── Paso 2: Swap ──
        log.info("[Agente %d] Ejecutando swap: %s %s → %s (min out: %s)...",
                 agent_number, amount_in, symbol_in, symbol_out,
                 amount_out_min_raw / (10 ** decimals_out))

        router = w3.eth.contract(address=SWAP_ROUTER, abi=SWAP_ROUTER_ABI)
        deadline = int(time.time()) + SWAP_DEADLINE_SECONDS
        nonce = w3.eth.get_transaction_count(sender, "pending")

        swap_tx = router.functions.exactInputSingle((
            token_in,
            token_out,
            fee,
            sender,        # recipient
            amount_in_raw,
            amount_out_min_raw,
            0,             # sqrtPriceLimitX96 = 0
        )).build_transaction({
            "from": sender,
            "nonce": nonce,
            "gas": 300000,
            "gasPrice": gas_price,
            "chainId": wallet.CHAIN_ID,
            "value": 0,
        })
        signed_swap = acct.sign_transaction(swap_tx)
        swap_hash = w3.eth.send_raw_transaction(signed_swap.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(swap_hash, timeout=120)

        if receipt.status != 1:
            return {"ok": False, "error": "Transacción revertida on-chain",
                    "tx_hash": swap_hash.hex()}

        # Leer balance final para calcular amount_out real
        final_balance = _token_balance(w3, token_out, sender)
        # Aproximación: usamos el quote como referencia
        amount_out_approx = quote["amount_out"]

        log.info("[Agente %d] Swap exitoso: %s %s → ~%s %s | tx: %s",
                 agent_number, amount_in, symbol_in,
                 f"{amount_out_approx:.6f}", symbol_out, swap_hash.hex())

        return {
            "ok": True,
            "tx_hash": swap_hash.hex(),
            "token_in": token_in,
            "token_out": token_out,
            "symbol_in": symbol_in,
            "symbol_out": symbol_out,
            "amount_in": amount_in,
            "amount_out": amount_out_approx,
            "fee_tier": fee,
            "gas_used": receipt.gasUsed,
        }

    except Exception as e:
        log.error("[Agente %d] Error en swap %s→%s: %s",
                  agent_number, token_in, token_out, e)
        return {"ok": False, "error": str(e)}


def swap_eth_for_token(agent_number: int, token_out: str, amount_eth: float,
                       slippage_pct: float = DEFAULT_SLIPPAGE_PCT) -> dict:
    """Swap ETH nativo → token ERC-20 (wrapping automático via WETH)."""
    try:
        w3 = _w3()
        acct = wallet.account_for_agent(agent_number)
        sender = acct.address
        token_out = Web3.to_checksum_address(token_out)

        # Verificar balance ETH
        eth_balance = w3.eth.get_balance(sender)
        amount_in_raw = w3.to_wei(amount_eth, "ether")
        if eth_balance < amount_in_raw + w3.to_wei(0.001, "ether"):  # reservar gas
            return {"ok": False, "error": f"ETH insuficiente: {w3.from_wei(eth_balance, 'ether'):.6f} ETH"}

        decimals_out = _token_decimals(w3, token_out)
        symbol_out = _token_symbol(w3, token_out)
        fee = find_best_fee(WETH, token_out, amount_eth)

        # Quote
        quote = get_quote(str(WETH), str(token_out), amount_eth, fee=fee)
        if not quote.get("ok"):
            return {"ok": False, "error": f"No se pudo obtener precio: {quote.get('error')}"}

        amount_out_min_raw = int(quote["amount_out_raw"] * (1 - slippage_pct / 100))

        # Swap (ETH se envía como value, WETH como tokenIn)
        router = w3.eth.contract(address=SWAP_ROUTER, abi=SWAP_ROUTER_ABI)
        gas_price = w3.eth.gas_price
        nonce = w3.eth.get_transaction_count(sender, "pending")

        swap_tx = router.functions.exactInputSingle((
            WETH,
            token_out,
            fee,
            sender,
            amount_in_raw,
            amount_out_min_raw,
            0,
        )).build_transaction({
            "from": sender,
            "nonce": nonce,
            "gas": 300000,
            "gasPrice": gas_price,
            "chainId": wallet.CHAIN_ID,
            "value": amount_in_raw,  # enviar ETH como msg.value
        })
        signed = acct.sign_transaction(swap_tx)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if receipt.status != 1:
            return {"ok": False, "error": "Transacción revertida", "tx_hash": tx_hash.hex()}

        log.info("[Agente %d] ETH→%s swap exitoso | tx: %s",
                 agent_number, symbol_out, tx_hash.hex())

        return {
            "ok": True,
            "tx_hash": tx_hash.hex(),
            "token_in": "ETH",
            "token_out": token_out,
            "symbol_in": "ETH",
            "symbol_out": symbol_out,
            "amount_in": amount_eth,
            "amount_out": quote["amount_out"],
            "fee_tier": fee,
            "gas_used": receipt.gasUsed,
        }
    except Exception as e:
        log.error("[Agente %d] Error en swap ETH→%s: %s", agent_number, token_out, e)
        return {"ok": False, "error": str(e)}
