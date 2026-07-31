import requests
import time
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger('market_research')

_last_call: float = 0.0
RATE_LIMIT_DELAY = 0.5

def _rate_limit() -> None:
    """Implementa un límite de tasa simple asegurando un retraso mínimo entre llamadas."""
    global _last_call
    now = time.time()
    elapsed = now - _last_call
    if elapsed < RATE_LIMIT_DELAY:
        time.sleep(RATE_LIMIT_DELAY - elapsed)
    _last_call = time.time()

def _safe_float(value: Any) -> float:
    """Convierte un valor de manera segura a float, por defecto 0.0."""
    if value is None:
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0

def search_tokens(query: str) -> List[Dict[str, Any]]:
    """Busca tokens por nombre o símbolo en DexScreener.
    Uso: GET https://api.dexscreener.com/latest/dex/search?q={query}
    Filtra los resultados a la cadena 'base' y limita a los 10 mejores.
    """
    _rate_limit()
    url = f"https://api.dexscreener.com/latest/dex/search?q={query}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        pairs = data.get("pairs", [])
        if not pairs:
            return []
            
        base_pairs = [p for p in pairs if p.get("chainId") == "base"]
        
        # Ordenar por liquidez descendente
        base_pairs.sort(key=lambda x: _safe_float(x.get("liquidity", {}).get("usd", 0)), reverse=True)
        
        results = []
        for pair in base_pairs[:10]:
            token = pair.get("baseToken", {})
            results.append({
                "address": token.get("address", ""),
                "symbol": token.get("symbol", ""),
                "name": token.get("name", ""),
                "priceUsd": _safe_float(pair.get("priceUsd")),
                "volume24h": _safe_float(pair.get("volume", {}).get("h24")),
                "liquidity": _safe_float(pair.get("liquidity", {}).get("usd")),
                "priceChange24h": _safe_float(pair.get("priceChange", {}).get("h24")),
                "chain": pair.get("chainId", "")
            })
            
        return results
    except Exception as e:
        logger.error(f"Error buscando tokens con query '{query}': {e}")
        return []

def get_token_info(token_address: str) -> Dict[str, Any]:
    """Obtiene información detallada para un token específico.
    Uso: GET https://api.dexscreener.com/latest/dex/tokens/{token_address}
    Elige el par en Base con mayor liquidez.
    """
    _rate_limit()
    url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        pairs = data.get("pairs", [])
        if not pairs:
            return {}
            
        base_pairs = [p for p in pairs if p.get("chainId") == "base"]
        if not base_pairs:
            return {}
            
        # Seleccionar el par con mayor liquidez
        best_pair = max(base_pairs, key=lambda x: _safe_float(x.get("liquidity", {}).get("usd", 0)))
        token = best_pair.get("baseToken", {})
        
        return {
            "address": token.get("address", ""),
            "symbol": token.get("symbol", ""),
            "name": token.get("name", ""),
            "priceUsd": _safe_float(best_pair.get("priceUsd")),
            "volume24h": _safe_float(best_pair.get("volume", {}).get("h24")),
            "liquidity": _safe_float(best_pair.get("liquidity", {}).get("usd")),
            "priceChange24h": _safe_float(best_pair.get("priceChange", {}).get("h24")),
            "marketCap": _safe_float(best_pair.get("marketCap", best_pair.get("fdv"))),
            "pairAddress": best_pair.get("pairAddress", ""),
            "dexId": best_pair.get("dexId", ""),
            "txns24h_buys": best_pair.get("txns", {}).get("h24", {}).get("buys", 0),
            "txns24h_sells": best_pair.get("txns", {}).get("h24", {}).get("sells", 0)
        }
    except Exception as e:
        logger.error(f"Error obteniendo info del token '{token_address}': {e}")
        return {}

def get_trending_tokens() -> List[Dict[str, Any]]:
    """Obtiene los tokens con mayor volumen en Base en las últimas 24h.
    Utiliza el endpoint de token-boosts y filtra para chainId='base'.
    Si falla, utiliza una búsqueda genérica para Base.
    Devuelve los 10 mejores resultados.
    """
    _rate_limit()
    url = "https://api.dexscreener.com/token-boosts/latest/v1"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        # Filtra boosts de la red 'base'
        base_tokens = [item for item in data if item.get("chainId") == "base"]
        
        if not base_tokens:
            # Fallback
            return search_tokens("base")
            
        # Obtener más detalles de estos tokens buscando sus direcciones (limitado a 5 para no saturar)
        results = []
        for item in base_tokens[:10]:
            token_address = item.get("tokenAddress")
            if token_address:
                info = get_token_info(token_address)
                if info:
                    results.append(info)
                    
        return results[:10]
    except Exception as e:
        logger.error(f"Error obteniendo tokens en tendencia: {e}")
        # Fallback si el endpoint falla
        return search_tokens("base")

def get_pair_info(pair_address: str) -> Dict[str, Any]:
    """Obtiene información para un par específico en el DEX.
    Uso: GET https://api.dexscreener.com/latest/dex/pairs/base/{pair_address}
    """
    _rate_limit()
    url = f"https://api.dexscreener.com/latest/dex/pairs/base/{pair_address}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        pair = data.get("pair")
        # En caso de que la respuesta tenga pairs en formato lista
        if not pair and "pairs" in data and len(data["pairs"]) > 0:
            pair = data["pairs"][0]
            
        if not pair:
            return {}
            
        return {
            "baseToken": pair.get("baseToken", {}),
            "quoteToken": pair.get("quoteToken", {}),
            "priceUsd": _safe_float(pair.get("priceUsd")),
            "volume24h": _safe_float(pair.get("volume", {}).get("h24")),
            "liquidity": _safe_float(pair.get("liquidity", {}).get("usd")),
            "priceChange24h": _safe_float(pair.get("priceChange", {}).get("h24"))
        }
    except Exception as e:
        logger.error(f"Error obteniendo info del par '{pair_address}': {e}")
        return {}

def format_market_summary(tokens: List[Dict[str, Any]]) -> str:
    """Formatea una lista de diccionarios de info de tokens en una cadena de texto 
    resumida y legible para el agente.
    """
    if not tokens:
        return "No hay datos del mercado disponibles."
        
    def _format_large_number(num: float) -> str:
        if num >= 1_000_000_000:
            return f"${num/1_000_000_000:.2f}B"
        elif num >= 1_000_000:
            return f"${num/1_000_000:.2f}M"
        elif num >= 1_000:
            return f"${num/1_000:.2f}K"
        return f"${num:.2f}"
        
    lines = []
    for i, t in enumerate(tokens, 1):
        symbol = t.get('symbol', 'UNKNOWN')
        price = t.get('priceUsd', 0.0)
        vol = _format_large_number(t.get('volume24h', 0.0))
        liq = _format_large_number(t.get('liquidity', 0.0))
        change = t.get('priceChange24h', 0.0)
        
        # Formatear precio, manejar precios muy bajos
        if price < 0.0001:
            price_str = f"${price:.6f}"
        else:
            price_str = f"${price:.4g}"
            
        sign = "+" if change > 0 else ""
        lines.append(f"{i}. {symbol} ({price_str}, vol {vol}, {sign}{change}% 24h, liq {liq})")
        
    return "\n".join(lines)
