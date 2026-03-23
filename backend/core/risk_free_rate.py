#!/usr/bin/env python3
"""
Risk-Free Rate Fetcher for Black-Scholes Option Pricing

Fetches 91-Day T-Bill rate once per day at market open and caches it.
Falls back to hardcoded 6.80% if fetch fails.
"""

import requests
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class RiskFreeRateFetcher:
    """Fetches and caches risk-free rate for the trading day"""
    
    def __init__(self):
        self.rate = 0.068  # Default: 6.80% (91-Day T-Bill typical rate)
        self.last_fetch_date = None
        self.fetch_attempted = False
        
    def get_rate(self):
        """
        Get risk-free rate for today.
        Fetches once per day, caches result.
        
        Returns:
            float: Risk-free rate as decimal (e.g., 0.068 for 6.8%)
        """
        today = datetime.now().date()
        
        # If already fetched today, return cached rate
        if self.last_fetch_date == today and self.fetch_attempted:
            return self.rate
        
        # Attempt to fetch fresh rate
        if not self.fetch_attempted or self.last_fetch_date != today:
            self._fetch_rate_from_source()
            self.last_fetch_date = today
            self.fetch_attempted = True
        
        return self.rate
    
    def _fetch_rate_from_source(self):
        """
        Attempt to fetch current T-Bill rate from available sources.
        Falls back to default if all sources fail.
        """
        logger.info("Fetching risk-free rate for today...")
        
        # Try multiple sources in order of preference
        sources = [
            self._fetch_from_rbi_api,
            self._fetch_from_nse_website,
            self._fetch_from_investing_com
        ]
        
        for fetch_method in sources:
            try:
                rate = fetch_method()
                if rate and 0.04 <= rate <= 0.10:  # Sanity check: 4-10% range
                    self.rate = rate
                    logger.info(f"✅ Risk-free rate fetched: {rate*100:.2f}% from {fetch_method.__name__}")
                    return
            except Exception as e:
                logger.debug(f"Failed to fetch from {fetch_method.__name__}: {e}")
                continue
        
        # All sources failed, use default
        logger.warning(f"WARNING: Could not fetch risk-free rate. Using default: {self.rate*100:.2f}%")
    
    def _fetch_from_rbi_api(self):
        """
        Fetch repo rate from RBI API (if available).
        
        Note: RBI doesn't have a public real-time API for T-Bill rates.
        This is a placeholder for future implementation.
        """
        # RBI API endpoint (placeholder - update if official API becomes available)
        # Currently RBI doesn't offer a simple public API for this
        return None
    
    def _fetch_from_nse_website(self):
        """
        Fetch T-Bill rate from NSE India website.
        
        NSE publishes government securities data.
        This requires web scraping as no official API exists.
        """
        try:
            # NSE publishes G-Sec data but requires complex scraping
            # For now, return None (can implement scraping if needed)
            return None
        except Exception as e:
            logger.debug(f"NSE fetch error: {e}")
            return None
    
    def _fetch_from_investing_com(self):
        """
        Fetch India 91-Day T-Bill rate from Investing.com API.
        
        This is a fallback option using publicly available financial data.
        """
        try:
            # Investing.com has financial data APIs
            # This is simplified - actual implementation would use proper API
            
            # For now, return None (can implement with proper API key)
            return None
        except Exception as e:
            logger.debug(f"Investing.com fetch error: {e}")
            return None
    
    def reset(self):
        """Reset fetcher (useful for testing or forcing refresh)"""
        self.fetch_attempted = False
        self.last_fetch_date = None
        logger.info("Risk-free rate fetcher reset")


# Global instance for the application
_global_rate_fetcher = None

def get_risk_free_rate():
    """
    Convenience function to get current risk-free rate.
    
    Returns:
        float: Risk-free rate as decimal (e.g., 0.068 for 6.8%)
    
    Example:
        >>> rate = get_risk_free_rate()
        >>> print(f"Using risk-free rate: {rate*100:.2f}%")
        Using risk-free rate: 6.80%
    """
    global _global_rate_fetcher
    
    if _global_rate_fetcher is None:
        _global_rate_fetcher = RiskFreeRateFetcher()
    
    return _global_rate_fetcher.get_rate()


if __name__ == "__main__":
    # Test the fetcher
    logging.basicConfig(level=logging.INFO)
    
    print("=" * 80)
    print("RISK-FREE RATE FETCHER - TEST")
    print("=" * 80)
    print()
    
    fetcher = RiskFreeRateFetcher()
    
    # First fetch (will attempt to fetch, fall back to default)
    rate1 = fetcher.get_rate()
    print(f"First call: {rate1*100:.2f}%")
    
    # Second fetch (should use cached value)
    rate2 = fetcher.get_rate()
    print(f"Second call (cached): {rate2*100:.2f}%")
    
    print()
    print("Using convenience function:")
    rate3 = get_risk_free_rate()
    print(f"Rate: {rate3*100:.2f}%")
    
    print()
    print("=" * 80)
    print("TEST COMPLETE")
    print("=" * 80)
    print()
    print("Integration:")
    print("  - Import: from core.risk_free_rate import get_risk_free_rate")
    print("  - Usage: rate = get_risk_free_rate()")
    print("  - Fetches once per day at first call (market open)")
    print("  - Caches for entire trading session")
    print("  - Falls back to 6.80% if fetch fails")
