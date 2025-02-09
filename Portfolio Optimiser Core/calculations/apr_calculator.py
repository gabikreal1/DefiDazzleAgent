from typing import Tuple, List
from decimal import Decimal
from web3.contract import Contract
import math

class APRCalculator:
    def __init__(self, blocks_per_year: int = 10512000):  # BSC blocks per year
        self.BLOCKS_PER_YEAR = blocks_per_year
        self.PRECISION = Decimal('1e18')

    async def calculate_pancake_apr(self, 
        pid: int, 
        pool_info: tuple, 
        masterchef: Contract,
        cake_price: float,
        pool_tvl: float
    ) -> float:
        """
        Calculate PancakeSwap farm APR including CAKE rewards
        Formula: (CAKE_per_block * blocks_per_year * CAKE_price * pool_alloc_points) / (total_alloc_points * pool_tvl)
        """
        try:
            # Get CAKE emissions per block
            cake_per_block = Decimal(str(masterchef.functions.cakePerBlock().call()))
            total_alloc_points = Decimal(str(masterchef.functions.totalAllocPoint().call()))
            pool_alloc_points = Decimal(str(pool_info[1]))  # allocPoint from poolInfo

            if total_alloc_points == 0 or pool_tvl == 0:
                return 0

            yearly_cake_rewards = (cake_per_block * self.BLOCKS_PER_YEAR * pool_alloc_points) / total_alloc_points
            yearly_cake_usd = yearly_cake_rewards * Decimal(str(cake_price)) / self.PRECISION
            
            apr = (yearly_cake_usd / Decimal(str(pool_tvl))) * 100
            return float(apr)

        except Exception as e:
            raise Exception(f"Error calculating PancakeSwap APR: {str(e)}")

    async def calculate_venus_rates(self, market_contract: Contract) -> Tuple[float, float]:
        """
        Calculate Venus supply and borrow APY
        Formula: ((1 + rate_per_block * blocks_per_year) - 1) * 100
        """
        try:
            supply_rate = Decimal(str(market_contract.functions.supplyRatePerBlock().call()))
            borrow_rate = Decimal(str(market_contract.functions.borrowRatePerBlock().call()))

            # Convert per-block rates to APY
            supply_apy = ((Decimal('1') + supply_rate * self.BLOCKS_PER_YEAR / self.PRECISION) - Decimal('1')) * 100
            borrow_apy = ((Decimal('1') + borrow_rate * self.BLOCKS_PER_YEAR / self.PRECISION) - Decimal('1')) * 100

            return float(supply_apy), float(borrow_apy)

        except Exception as e:
            raise Exception(f"Error calculating Venus rates: {str(e)}")

    async def calculate_alpaca_base_apy(self, vault_contract: Contract) -> float:
        """
        Calculate Alpaca vault base APY from lending activities
        Formula: (total_token - total_debt) / total_token * lending_apr
        """
        try:
            total_token = Decimal(str(vault_contract.functions.totalToken().call()))
            total_debt = Decimal(str(vault_contract.functions.vaultDebtVal().call()))
            
            if total_token == 0:
                return 0

            utilization = total_debt / total_token
            # Lending APR increases with utilization (simplified model)
            lending_apr = utilization * Decimal('0.15') * 100  # 15% max lending APR
            
            return float(lending_apr)

        except Exception as e:
            raise Exception(f"Error calculating Alpaca base APY: {str(e)}")

    async def calculate_alpaca_reward_apy(self, 
        pid: int, 
        fairlaunch: Contract,
        tvl: float,
        alpaca_price: float
    ) -> float:
        """
        Calculate Alpaca ALPACA token reward APY
        Formula: (ALPACA_per_block * blocks_per_year * ALPACA_price * pool_alloc_points) / (total_alloc_points * pool_tvl)
        """
        try:
            pool_info = fairlaunch.functions.poolInfo(pid).call()
            total_alloc_point = Decimal(str(fairlaunch.functions.totalAllocPoint().call()))
            alpaca_per_block = Decimal(str(fairlaunch.functions.alpacaPerBlock().call()))
            pool_alloc_points = Decimal(str(pool_info[1]))

            if total_alloc_point == 0 or tvl == 0:
                return 0

            yearly_alpaca = (alpaca_per_block * self.BLOCKS_PER_YEAR * pool_alloc_points) / total_alloc_point
            yearly_alpaca_usd = yearly_alpaca * Decimal(str(alpaca_price)) / self.PRECISION
            
            apy = (yearly_alpaca_usd / Decimal(str(tvl))) * 100
            return float(apy)

        except Exception as e:
            raise Exception(f"Error calculating Alpaca reward APY: {str(e)}")

    async def calculate_biswap_apr(self, 
        pid: int, 
        pool_info: tuple, 
        masterchef: Contract,
        bsw_price: float,
        pool_tvl: float
    ) -> float:
        """
        Calculate Biswap farm APR including BSW rewards
        Similar to PancakeSwap but with BSW token
        """
        try:
            bsw_per_block = Decimal(str(masterchef.functions.bswPerBlock().call()))
            total_alloc_points = Decimal(str(masterchef.functions.totalAllocPoint().call()))
            pool_alloc_points = Decimal(str(pool_info[1]))

            if total_alloc_points == 0 or pool_tvl == 0:
                return 0

            yearly_bsw = (bsw_per_block * self.BLOCKS_PER_YEAR * pool_alloc_points) / total_alloc_points
            yearly_bsw_usd = yearly_bsw * Decimal(str(bsw_price)) / self.PRECISION
            
            apr = (yearly_bsw_usd / Decimal(str(pool_tvl))) * 100
            return float(apr)

        except Exception as e:
            raise Exception(f"Error calculating Biswap APR: {str(e)}") 