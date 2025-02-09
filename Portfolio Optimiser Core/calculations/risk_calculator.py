from typing import List, Dict, Optional
from datetime import datetime, timedelta
import numpy as np
from decimal import Decimal
import math

class RiskCalculator:
    def __init__(self):
        self.RISK_WEIGHTS = {
            'tvl': 0.25,
            'volatility': 0.20,
            'age': 0.15,
            'il': 0.20,
            'protocol': 0.20
        }
        
        self.PROTOCOL_BASE_SCORES = {
            'pancakeswap': 0.9,  # Most established
            'venus': 0.85,
            'alpaca': 0.8,
            'biswap': 0.75
        }

    def calculate_impermanent_loss_risk(
        self,
        token0_price_history: List[float],
        token1_price_history: List[float]
    ) -> float:
        """
        Calculate impermanent loss risk based on price volatility
        Returns a risk score between 0 (low risk) and 1 (high risk)
        """
        try:
            if len(token0_price_history) != len(token1_price_history):
                raise ValueError("Price history lengths must match")

            # Calculate price ratios
            price_ratios = [t1/t0 for t0, t1 in zip(token0_price_history, token1_price_history)]
            
            # Calculate price ratio volatility
            ratio_volatility = np.std(price_ratios)
            
            # Calculate max drawdown
            max_drawdown = self._calculate_max_drawdown(price_ratios)
            
            # Calculate IL risk score (higher volatility/drawdown = higher risk)
            vol_score = min(1.0, ratio_volatility)
            drawdown_score = min(1.0, max_drawdown)
            
            il_risk = (vol_score * 0.7 + drawdown_score * 0.3)
            return il_risk

        except Exception as e:
            raise Exception(f"Error calculating IL risk: {str(e)}")

    def calculate_pool_volatility(self, price_history: List[float]) -> float:
        """
        Calculate pool price volatility score
        Returns a value between 0 (low volatility) and 1 (high volatility)
        """
        try:
            if len(price_history) < 2:
                return 0.0

            # Calculate daily returns
            returns = np.diff(price_history) / price_history[:-1]
            
            # Calculate annualized volatility
            daily_vol = np.std(returns)
            annual_vol = daily_vol * math.sqrt(365)
            
            # Normalize to 0-1 range (assuming 100% annual vol as max)
            volatility_score = min(1.0, annual_vol)
            
            return float(volatility_score)

        except Exception as e:
            raise Exception(f"Error calculating volatility: {str(e)}")

    def calculate_tvl_risk(self, tvl: float) -> float:
        """
        Calculate risk based on TVL
        Lower TVL = higher risk
        """
        try:
            # TVL thresholds in USD
            TVL_THRESHOLDS = {
                10_000_000: 0.1,  # >$10M: very low risk
                5_000_000: 0.3,   # >$5M: low risk
                1_000_000: 0.5,   # >$1M: medium risk
                500_000: 0.7,     # >$500K: high risk
                0: 0.9            # <$500K: very high risk
            }
            
            for threshold, risk in TVL_THRESHOLDS.items():
                if tvl >= threshold:
                    return risk
                    
            return 1.0  # Maximum risk for edge cases

        except Exception as e:
            raise Exception(f"Error calculating TVL risk: {str(e)}")

    def calculate_age_risk(self, age_in_days: int) -> float:
        """
        Calculate risk based on pool age
        Newer pools = higher risk
        """
        try:
            # Age thresholds in days
            AGE_THRESHOLDS = {
                365: 0.2,   # >1 year: very low risk
                180: 0.4,   # >6 months: low risk
                90: 0.6,    # >3 months: medium risk
                30: 0.8,    # >1 month: high risk
                0: 1.0      # <1 month: very high risk
            }
            
            for threshold, risk in AGE_THRESHOLDS.items():
                if age_in_days >= threshold:
                    return risk
                    
            return 1.0

        except Exception as e:
            raise Exception(f"Error calculating age risk: {str(e)}")

    def calculate_protocol_health_score(self, 
        protocol: str,
        tvl_history: List[float],
        user_count: int,
        days_since_audit: int
    ) -> float:
        """
        Calculate protocol health score based on various metrics
        """
        try:
            base_score = self.PROTOCOL_BASE_SCORES.get(protocol.lower(), 0.5)
            
            # TVL growth score
            tvl_growth = (tvl_history[-1] / tvl_history[0] - 1) if len(tvl_history) > 1 else 0
            tvl_growth_score = min(1.0, max(0.0, (tvl_growth + 0.5) / 1.5))
            
            # User adoption score
            user_score = min(1.0, user_count / 100000)  # Normalize to 100K users
            
            # Audit freshness score
            audit_score = max(0.0, min(1.0, days_since_audit / 365))
            
            # Weighted average
            health_score = (
                base_score * 0.4 +
                tvl_growth_score * 0.2 +
                user_score * 0.2 +
                audit_score * 0.2
            )
            
            return float(health_score)

        except Exception as e:
            raise Exception(f"Error calculating protocol health: {str(e)}")

    def calculate_composite_risk_score(self,
        tvl_score: float,
        volatility_score: float,
        age_score: float,
        il_risk: float,
        protocol_score: float
    ) -> float:
        """
        Calculate weighted composite risk score from multiple factors
        Returns a value between 0 (lowest risk) and 1 (highest risk)
        """
        try:
            composite_score = (
                tvl_score * self.RISK_WEIGHTS['tvl'] +
                volatility_score * self.RISK_WEIGHTS['volatility'] +
                age_score * self.RISK_WEIGHTS['age'] +
                il_risk * self.RISK_WEIGHTS['il'] +
                (1 - protocol_score) * self.RISK_WEIGHTS['protocol']
            )
            
            return min(1.0, max(0.0, float(composite_score)))

        except Exception as e:
            raise Exception(f"Error calculating composite risk: {str(e)}")

    def _calculate_max_drawdown(self, values: List[float]) -> float:
        """Helper function to calculate maximum drawdown"""
        peak = values[0]
        max_drawdown = 0.0
        
        for value in values:
            if value > peak:
                peak = value
            drawdown = (peak - value) / peak
            max_drawdown = max(max_drawdown, drawdown)
            
        return max_drawdown 