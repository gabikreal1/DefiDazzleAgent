from typing import Dict, List, Optional, Tuple
from web3.contract import Contract
from decimal import Decimal
import asyncio
import aiohttp
from datetime import datetime, timedelta
import logging

class PriceCalculator:
    def __init__(self, w3, factory_contract: Contract, router_contract: Contract):
        self.w3 = w3
        self.factory = factory_contract
        self.router = router_contract
        self.WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
        self.BUSD = "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"
        self.PRECISION = Decimal('1e18')
        
        # Setup logging
        self.logger = logging.getLogger(__name__)

    async def get_token_price(self, token_address: str) -> float:
        """
        Get token price in USD using PancakeSwap pairs
        First tries direct BUSD pair, then tries through WBNB
        """
        try:
            # Try direct BUSD pair first
            busd_price = await self._get_token_price_from_pair(token_address, self.BUSD)
            if busd_price is not None:
                return float(busd_price)

            # If no BUSD pair, try through WBNB
            bnb_price = await self._get_token_price_from_pair(token_address, self.WBNB)
            if bnb_price is not None:
                bnb_busd_price = await self._get_token_price_from_pair(self.WBNB, self.BUSD)
                if bnb_busd_price is not None:
                    return float(bnb_price * bnb_busd_price)

            raise Exception(f"No valid price path found for token {token_address}")

        except Exception as e:
            self.logger.error(f"Error getting token price: {str(e)}")
            raise

    async def _get_token_price_from_pair(self, token0_address: str, token1_address: str) -> Optional[Decimal]:
        """Get token price from a specific pair"""
        try:
            pair_address = self.factory.functions.getPair(token0_address, token1_address).call()
            if pair_address == "0x0000000000000000000000000000000000000000":
                return None

            pair_contract = self.w3.eth.contract(
                address=pair_address,
                abi=[{
                    "inputs": [],
                    "name": "getReserves",
                    "outputs": [
                        {"internalType": "uint112", "name": "_reserve0", "type": "uint112"},
                        {"internalType": "uint112", "name": "_reserve1", "type": "uint112"},
                        {"internalType": "uint32", "name": "_blockTimestampLast", "type": "uint32"}
                    ],
                    "stateMutability": "view",
                    "type": "function"
                }]
            )

            reserves = pair_contract.functions.getReserves().call()
            token0 = self.w3.eth.contract(
                address=token0_address,
                abi=[{
                    "inputs": [],
                    "name": "decimals",
                    "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
                    "stateMutability": "view",
                    "type": "function"
                }]
            )
            token1 = self.w3.eth.contract(
                address=token1_address,
                abi=[{
                    "inputs": [],
                    "name": "decimals",
                    "outputs": [{"internalType": "uint8", "name": "", "type": "uint8"}],
                    "stateMutability": "view",
                    "type": "function"
                }]
            )

            decimals0 = token0.functions.decimals().call()
            decimals1 = token1.functions.decimals().call()

            reserve0 = Decimal(str(reserves[0])) / Decimal(str(10 ** decimals0))
            reserve1 = Decimal(str(reserves[1])) / Decimal(str(10 ** decimals1))

            if reserve0 == 0 or reserve1 == 0:
                return None

            return reserve1 / reserve0

        except Exception as e:
            self.logger.error(f"Error getting price from pair: {str(e)}")
            return None

    async def get_token_price_history(self, token_address: str, days: int = 30) -> List[float]:
        """
        Get token price history using TheGraph API
        Returns list of daily prices for specified number of days
        """
        try:
            # TheGraph endpoint for PancakeSwap
            url = "https://api.thegraph.com/subgraphs/name/pancakeswap/exchange-v2"
            
            # Query for historical data
            query = """
            query ($token: String!, $days: Int!) {
                tokenDayDatas(
                    first: $days,
                    orderBy: date,
                    orderDirection: desc,
                    where: {token: $token}
                ) {
                    priceUSD
                    date
                }
            }
            """
            
            variables = {
                "token": token_address.lower(),
                "days": days
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={'query': query, 'variables': variables}) as response:
                    data = await response.json()
                    
                    if 'errors' in data:
                        raise Exception(f"GraphQL error: {data['errors']}")
                        
                    prices = [float(day['priceUSD']) for day in reversed(data['data']['tokenDayDatas'])]
                    return prices

        except Exception as e:
            self.logger.error(f"Error getting price history: {str(e)}")
            raise

    async def get_pool_volume(self, pool_address: str, timeframe: int = 24) -> float:
        """Get pool trading volume for specified timeframe in hours"""
        try:
            # TheGraph endpoint for PancakeSwap
            url = "https://api.thegraph.com/subgraphs/name/pancakeswap/exchange-v2"
            
            # Calculate timestamp for timeframe
            current_time = int(datetime.now().timestamp())
            timeframe_start = current_time - (timeframe * 3600)
            
            query = """
            query ($pool: String!, $timestamp: Int!) {
                pairHourDatas(
                    where: {
                        pair: $pool,
                        hourStartUnix_gt: $timestamp
                    }
                ) {
                    hourlyVolumeUSD
                }
            }
            """
            
            variables = {
                "pool": pool_address.lower(),
                "timestamp": timeframe_start
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json={'query': query, 'variables': variables}) as response:
                    data = await response.json()
                    
                    if 'errors' in data:
                        raise Exception(f"GraphQL error: {data['errors']}")
                        
                    volume = sum(float(hour['hourlyVolumeUSD']) for hour in data['data']['pairHourDatas'])
                    return volume

        except Exception as e:
            self.logger.error(f"Error getting pool volume: {str(e)}")
            raise

    async def calculate_price_impact(self, 
        token_in: str, 
        token_out: str, 
        amount_in: int
    ) -> float:
        """Calculate price impact for a potential swap"""
        try:
            # Get amounts out for both small and actual amount
            small_amount = 1000  # Small amount to compare against
            
            amounts_out_small = await self.router.functions.getAmountsOut(
                small_amount,
                [token_in, token_out]
            ).call()
            
            amounts_out_actual = await self.router.functions.getAmountsOut(
                amount_in,
                [token_in, token_out]
            ).call()
            
            # Calculate effective prices
            price_small = amounts_out_small[1] / small_amount
            price_actual = amounts_out_actual[1] / amount_in
            
            # Calculate price impact
            price_impact = abs(price_actual - price_small) / price_small * 100
            
            return float(price_impact)

        except Exception as e:
            self.logger.error(f"Error calculating price impact: {str(e)}")
            raise 