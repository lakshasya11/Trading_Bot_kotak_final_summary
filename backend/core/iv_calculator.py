"""
Implied Volatility Calculator using Black-Scholes Model
Calculates IV from market option prices using Newton-Raphson method
"""

import math
from datetime import datetime, date, timedelta
from scipy.stats import norm
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def black_scholes_call(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Calculate Call option price using Black-Scholes formula
    
    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry (years)
        r: Risk-free rate (decimal)
        sigma: Volatility (decimal)
    
    Returns:
        Call option theoretical price
    """
    if T <= 0:
        return max(S - K, 0)
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    call_price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return call_price


def black_scholes_put(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Calculate Put option price using Black-Scholes formula
    
    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry (years)
        r: Risk-free rate (decimal)
        sigma: Volatility (decimal)
    
    Returns:
        Put option theoretical price
    """
    if T <= 0:
        return max(K - S, 0)
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    
    put_price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return put_price


def vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Calculate Vega (sensitivity of option price to volatility)
    Used in Newton-Raphson iteration
    
    Args:
        S: Spot price
        K: Strike price
        T: Time to expiry (years)
        r: Risk-free rate (decimal)
        sigma: Volatility (decimal)
    
    Returns:
        Vega value
    """
    if T <= 0:
        return 0
    
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    vega_val = S * norm.pdf(d1) * math.sqrt(T)
    return vega_val / 100  # Convert to 1% change


def calculate_time_to_expiry(expiry_date: datetime) -> float:
    """
    Calculate time to expiry in years
    
    Args:
        expiry_date: Expiry datetime
    
    Returns:
        Time to expiry in years (trading days / 252)
    """
    now = datetime.now()
    
    # If expiry_date is date object, convert to datetime
    if isinstance(expiry_date, date) and not isinstance(expiry_date, datetime):
        expiry_date = datetime.combine(expiry_date, datetime.min.time())
    
    time_diff = expiry_date - now
    days_to_expiry = time_diff.total_seconds() / 86400
    
    # Convert to years (using trading days convention)
    # If expiry is same day, use minimum of 1 hour
    if days_to_expiry < 0.04:  # Less than 1 hour
        return 1.0 / (252 * 6.5)  # 1 hour in trading year
    
    years_to_expiry = days_to_expiry / 365.0
    return max(years_to_expiry, 0.001)  # Minimum time


def calculate_implied_volatility(
    market_price: float,
    spot_price: float,
    strike_price: float,
    expiry_date: datetime,
    risk_free_rate: float,
    option_type: str = 'CE',
    initial_guess: float = 0.05,  # Changed from 0.15 to 0.05 (5%) for weekly options
    max_iterations: int = 100,  # Increased from 50
    tolerance: float = 0.01  # Relaxed from 0.0001 to 0.01
) -> Optional[float]:
    """
    Calculate Implied Volatility using Newton-Raphson method
    
    Args:
        market_price: Current market price of option
        spot_price: Current spot price
        strike_price: Strike price
        expiry_date: Expiry datetime
        risk_free_rate: Risk-free rate (decimal, e.g., 0.068 for 6.8%)
        option_type: 'CE' for Call, 'PE' for Put
        initial_guess: Starting volatility guess (default 5% for weekly options)
        max_iterations: Maximum Newton-Raphson iterations
        tolerance: Convergence tolerance
    
    Returns:
        Implied volatility as decimal (e.g., 0.05 for 5%) or None if failed
    """
    # Validate inputs
    if market_price <= 0:
        return None
    
    if spot_price <= 0 or strike_price <= 0:
        return None
    
    # Calculate time to expiry
    T = calculate_time_to_expiry(expiry_date)
    
    # Check intrinsic value
    if option_type == 'CE':
        intrinsic_value = max(spot_price - strike_price, 0)
    else:
        intrinsic_value = max(strike_price - spot_price, 0)
    
    # Market price must be >= intrinsic value
    if market_price < intrinsic_value * 0.95:  # 5% tolerance for bid-ask
        logger.warning(f"Market price {market_price} < intrinsic {intrinsic_value} for {option_type} {strike_price}")
        return None
    
    # Newton-Raphson iteration
    sigma = initial_guess
    
    for i in range(max_iterations):
        # Calculate theoretical price
        if option_type == 'CE':
            theo_price = black_scholes_call(spot_price, strike_price, T, risk_free_rate, sigma)
        else:
            theo_price = black_scholes_put(spot_price, strike_price, T, risk_free_rate, sigma)
        
        # Calculate price difference
        price_diff = theo_price - market_price
        
        # Check convergence
        if abs(price_diff) < tolerance:
            return sigma
        
        # Calculate vega (don't divide by 100 here - use raw value)
        d1 = (math.log(spot_price / strike_price) + (risk_free_rate + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        vega_val = spot_price * norm.pdf(d1) * math.sqrt(T)
        
        # Avoid division by zero
        if abs(vega_val) < 1e-10:
            logger.warning(f"Vega too small: {vega_val}")
            return None
        
        # Newton-Raphson update
        sigma = sigma - price_diff / vega_val
        
        # Keep sigma in reasonable range
        sigma = max(0.01, min(sigma, 2.0))  # 1% to 200%
    
    # Failed to converge
    logger.warning(f"IV calculation failed to converge after {max_iterations} iterations")
    return None


def calculate_valuation_percentage(option_iv: float, atm_iv: float) -> float:
    """
    Calculate how over/undervalued an option is relative to ATM IV
    
    Args:
        option_iv: Implied volatility of the option (decimal)
        atm_iv: ATM baseline IV (decimal)
    
    Returns:
        Percentage over/undervalued (negative = undervalued, positive = overvalued)
        e.g., -10.5 means 10.5% undervalued
    """
    if atm_iv <= 0:
        return 0.0
    
    valuation_pct = ((option_iv - atm_iv) / atm_iv) * 100
    return valuation_pct


def get_color_for_valuation(valuation_pct: float) -> str:
    """
    Get color code for valuation percentage
    
    Undervalued (negative): Shades of green
    Overvalued (positive): Shades of red
    
    Args:
        valuation_pct: Valuation percentage
    
    Returns:
        RGB color string
    """
    if valuation_pct < 0:
        # Undervalued - Green shades
        # -30% or more undervalued: Dark green
        # 0%: Very light green
        intensity = min(abs(valuation_pct) / 30.0, 1.0)  # Cap at 30%
        # RGB: (0, 255 * intensity, 0)
        g = int(150 + 105 * intensity)  # 150 to 255
        return f"rgba(0, {g}, 0, {0.2 + 0.6 * intensity})"
    
    elif valuation_pct > 0:
        # Overvalued - Red shades
        # +30% or more overvalued: Dark red
        # 0%: Very light red
        intensity = min(valuation_pct / 30.0, 1.0)  # Cap at 30%
        # RGB: (255 * intensity, 0, 0)
        r = int(150 + 105 * intensity)  # 150 to 255
        return f"rgba({r}, 0, 0, {0.2 + 0.6 * intensity})"
    
    else:
        # Perfectly valued - White/transparent
        return "rgba(255, 255, 255, 0)"


# Test function
if __name__ == "__main__":
    print("=" * 100)
    print("IMPLIED VOLATILITY CALCULATOR - TEST")
    print("=" * 100)
    print()
    
    # Test parameters (3-day weekly options)
    spot = 24500
    strike = 24500  # ATM
    expiry = datetime.now().replace(hour=15, minute=30, second=0) + timedelta(days=3)
    risk_free_rate = 0.068  # 6.8%
    
    # Test CE
    ce_market_price = 50
    ce_iv = calculate_implied_volatility(
        ce_market_price, spot, strike, expiry, risk_free_rate, 'CE'
    )
    print(f"CE Market Price: ₹{ce_market_price}")
    print(f"CE Implied Volatility: {ce_iv * 100:.2f}%" if ce_iv else "Failed")
    print()
    
    # Test PE
    pe_market_price = 37
    pe_iv = calculate_implied_volatility(
        pe_market_price, spot, strike, expiry, risk_free_rate, 'PE'
    )
    print(f"PE Market Price: ₹{pe_market_price}")
    print(f"PE Implied Volatility: {pe_iv * 100:.2f}%" if pe_iv else "Failed")
    print()
    
    # Test valuation
    if ce_iv and pe_iv:
        atm_iv = (ce_iv + pe_iv) / 2
        print(f"ATM IV Baseline: {atm_iv * 100:.2f}%")
        print()
        
        # Test different strikes
        strikes = [24000, 24250, 24500, 24750, 25000]
        print("Strike | IV    | Valuation | Color")
        print("-" * 60)
        
        for s in strikes:
            # Simulate different IVs for different strikes
            test_iv = atm_iv * (1 + (abs(s - spot) / spot) * 0.5)  # IV increases with distance
            val_pct = calculate_valuation_percentage(test_iv, atm_iv)
            color = get_color_for_valuation(val_pct)
            print(f"{s:5d}  | {test_iv*100:5.2f}% | {val_pct:+6.1f}%   | {color}")
    
    print()
    print("=" * 100)
    print("Test complete!")
    print("=" * 100)
