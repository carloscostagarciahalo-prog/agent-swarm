import time
import logging
import requests

try:
    from .wallet import wallet_for_agent, _web3, RPC_URL
except ImportError:
    from wallet import wallet_for_agent, _web3, RPC_URL

logger = logging.getLogger('portfolio')

WETH = "0x4200000000000000000000000000000000000006"
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDbC = "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6Be"
DAI  = "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb"

DEFAULT_TRACKED = [WETH, USDC, USDbC, DAI]

ERC20_ABI = [
    {"inputs": [{"name": "account", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "decimals", "outputs": [{"name": "", "type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "stateMutability": "view", "type": "function"},
]

_eth_price_cache = 0.0
_eth_price_timestamp = 0.0

def get_eth_balance(address: str) -> float:
    """Balance nativo de ETH en la wallet."""
    try:
        w3 = _web3()
        balance_wei = w3.eth.get_balance(w3.to_checksum_address(address))
        return float(w3.from_wei(balance_wei, 'ether'))
    except Exception as e:
        logger.error(f"Error al obtener balance de ETH para {address}: {e}")
        return 0.0

def get_token_balance(address: str, token_address: str) -> dict:
    """Returns {symbol, balance, decimals} for an ERC-20 token."""
    try:
        w3 = _web3()
        contract = w3.eth.contract(address=w3.to_checksum_address(token_address), abi=ERC20_ABI)
        balance = contract.functions.balanceOf(w3.to_checksum_address(address)).call()
        decimals = contract.functions.decimals().call()
        symbol = contract.functions.symbol().call()
        
        return {
            "symbol": symbol,
            "balance": balance / (10 ** decimals) if balance > 0 else 0.0,
            "decimals": decimals
        }
    except Exception as e:
        logger.error(f"Error al obtener balance de token {token_address} para {address}: {e}")
        return {"symbol": "UNKNOWN", "balance": 0.0, "decimals": 18}

def get_eth_price_usd() -> float:
    """ETH price in USD via DexScreener (WETH/USDC pair on Base)."""
    global _eth_price_cache, _eth_price_timestamp
    
    current_time = time.time()
    if current_time - _eth_price_timestamp < 60 and _eth_price_cache > 0:
        return _eth_price_cache
        
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{WETH}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        for pair in data.get('pairs', []):
            if pair.get('chainId') == 'base' and pair.get('quoteToken', {}).get('address', '').lower() == USDC.lower():
                price = float(pair.get('priceUsd', 0.0))
                _eth_price_cache = price
                _eth_price_timestamp = current_time
                return price
                
        # Fallback to the first base pair
        for pair in data.get('pairs', []):
            if pair.get('chainId') == 'base':
                price = float(pair.get('priceUsd', 0.0))
                _eth_price_cache = price
                _eth_price_timestamp = current_time
                return price
                
    except Exception as e:
        logger.error(f"Error al obtener precio de ETH desde DexScreener: {e}")
        
    return _eth_price_cache

def get_token_price_usd(token_address: str) -> float:
    """Token price in USD via DexScreener."""
    # USDC/USDbC/DAI are stablecoins: return 1.0 directly
    if token_address.lower() in [USDC.lower(), USDbC.lower(), DAI.lower()]:
        return 1.0
        
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        for pair in data.get('pairs', []):
            if pair.get('chainId') == 'base':
                return float(pair.get('priceUsd', 0.0))
    except Exception as e:
        logger.error(f"Error al obtener precio del token {token_address}: {e}")
        
    return 0.0

def get_portfolio_value_usd(agent_number: int, extra_tokens: list[str] | None = None) -> dict:
    """Returns full portfolio breakdown and total value in USD.
    
    Returns: {
        'total_usd': float,
        'eth': {'balance': float, 'value_usd': float},
        'tokens': [{'address': str, 'symbol': str, 'balance': float, 'value_usd': float}, ...]
    }
    """
    result = {
        'total_usd': 0.0,
        'eth': {'balance': 0.0, 'value_usd': 0.0},
        'tokens': []
    }
    
    try:
        address = wallet_for_agent(agent_number)
        if not address:
            return result
            
        eth_balance = get_eth_balance(address)
        eth_price = get_eth_price_usd()
        eth_value_usd = eth_balance * eth_price
        
        result['eth']['balance'] = eth_balance
        result['eth']['value_usd'] = eth_value_usd
        result['total_usd'] += eth_value_usd
        
        tokens_to_track = set(t.lower() for t in DEFAULT_TRACKED)
        if extra_tokens:
            for t in extra_tokens:
                tokens_to_track.add(t.lower())
                
        for token_address in tokens_to_track:
            token_info = get_token_balance(address, token_address)
            balance = token_info.get('balance', 0.0)
            
            if balance > 0:
                price = get_token_price_usd(token_address)
                value_usd = balance * price
                
                result['tokens'].append({
                    'address': token_address,
                    'symbol': token_info.get('symbol', 'UNKNOWN'),
                    'balance': balance,
                    'value_usd': value_usd
                })
                result['total_usd'] += value_usd
                
    except Exception as e:
        logger.error(f"Error al obtener portafolio para agente {agent_number}: {e}")
        
    return result
