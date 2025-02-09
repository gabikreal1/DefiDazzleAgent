from typing import Dict, List, Optional, Tuple
import aiohttp
import asyncio
from datetime import datetime, timedelta
import logging
from web3 import Web3
from web3.contract import Contract
import json
from cachetools import TTLCache, cached

class DataFetcher:
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.logger = logging.getLogger(__name__)
        
        # Cache for API responses
        self.cache = TTLCache(maxsize=100, ttl=300)  # 5 minutes cache
        
        # TheGraph endpoints
        self.ENDPOINTS = {
            'pancakeswap': 'https://api.thegraph.com/subgraphs/name/pancakeswap/exchange-v2',
            'venus': 'https://api.thegraph.com/subgraphs/name/venusprotocol/venus-subgraph',
            'alpaca': 'https://api.thegraph.com/subgraphs/name/alpaca-finance/alpaca-finance',
            'biswap': 'https://api.thegraph.com/subgraphs/name/biswap-dex/exchange'
        }
        
        # DefiLlama API endpoint
        self.DEFILLAMA_API = 'https://api.llama.fi'

    async def get_total_value_locked_history(self, pool_address: str, days: int = 30) -> List[float]:
        """Get TVL history for a specific pool"""
        try:
            query = """
            query ($pool: String!, $days: Int!) {
                pairDayDatas(
                    first: $days,
                    orderBy: date,
                    orderDirection: desc,
                    where: {pairAddress: $pool}
                ) {
                    reserveUSD
                    date
                }
            }
            """
            
            variables = {
                "pool": pool_address.lower(),
                "days": days
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.ENDPOINTS['pancakeswap'],
                    json={'query': query, 'variables': variables}
                ) as response:
                    data = await response.json()
                    
                    if 'errors' in data:
                        raise Exception(f"GraphQL error: {data['errors']}")
                    
                    tvl_history = [
                        float(day['reserveUSD']) 
                        for day in reversed(data['data']['pairDayDatas'])
                    ]
                    return tvl_history

        except Exception as e:
            self.logger.error(f"Error fetching TVL history: {str(e)}")
            raise

    async def get_protocol_metrics(self, protocol: str) -> Dict:
        """Get protocol-wide metrics from DefiLlama"""
        try:
            protocol_ids = {
                'pancakeswap': 'pancakeswap',
                'venus': 'venus',
                'alpaca': 'alpaca-finance',
                'biswap': 'biswap'
            }
            
            protocol_id = protocol_ids.get(protocol.lower())
            if not protocol_id:
                raise ValueError(f"Unknown protocol: {protocol}")
            
            async with aiohttp.ClientSession() as session:
                # Get TVL data
                async with session.get(
                    f"{self.DEFILLAMA_API}/protocol/{protocol_id}"
                ) as response:
                    data = await response.json()
                    
                    metrics = {
                        'tvl': data['tvl'][-1]['totalLiquidityUSD'],
                        'tvl_change_24h': self._calculate_change(
                            data['tvl'][-2]['totalLiquidityUSD'],
                            data['tvl'][-1]['totalLiquidityUSD']
                        ),
                        'mcap_tvl_ratio': data.get('mcap', 0) / data['tvl'][-1]['totalLiquidityUSD']
                        if data.get('mcap') else 0
                    }
                    
                    return metrics

        except Exception as e:
            self.logger.error(f"Error fetching protocol metrics: {str(e)}")
            raise

    async def get_pool_creation_block(self, pool_address: str) -> int:
        """Get the block number when the pool was created"""
        try:
            # Get the first transfer event (pool creation)
            creation_filter = self.w3.eth.filter({
                'fromBlock': 0,
                'toBlock': 'latest',
                'address': pool_address,
                'topics': [
                    self.w3.keccak(text='Transfer(address,address,uint256)').hex(),
                    '0x0000000000000000000000000000000000000000000000000000000000000000'
                ]
            })
            
            logs = creation_filter.get_all_entries()
            if logs:
                return logs[0]['blockNumber']
            return 0

        except Exception as e:
            self.logger.error(f"Error fetching pool creation block: {str(e)}")
            raise

    async def get_user_count(self, protocol: str) -> int:
        """Get unique user count for protocol"""
        try:
            query = """
            query {
                pancakeFactory(id: "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73") {
                    totalUsers
                }
            }
            """
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.ENDPOINTS[protocol.lower()],
                    json={'query': query}
                ) as response:
                    data = await response.json()
                    
                    if 'errors' in data:
                        raise Exception(f"GraphQL error: {data['errors']}")
                    
                    return int(data['data']['pancakeFactory']['totalUsers'])

        except Exception as e:
            self.logger.error(f"Error fetching user count: {str(e)}")
            raise

    async def get_pool_metrics(self, pool_address: str) -> Dict:
        """Get comprehensive pool metrics"""
        try:
            query = """
            query ($pool: String!) {
                pair(id: $pool) {
                    token0Price
                    token1Price
                    volumeUSD
                    txCount
                    liquidityProviderCount
                    untrackedVolumeUSD
                    trackedReserveUSD
                }
            }
            """
            
            variables = {
                "pool": pool_address.lower()
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.ENDPOINTS['pancakeswap'],
                    json={'query': query, 'variables': variables}
                ) as response:
                    data = await response.json()
                    
                    if 'errors' in data:
                        raise Exception(f"GraphQL error: {data['errors']}")
                    
                    pair_data = data['data']['pair']
                    return {
                        'price_token0': float(pair_data['token0Price']),
                        'price_token1': float(pair_data['token1Price']),
                        'volume_usd': float(pair_data['volumeUSD']),
                        'tx_count': int(pair_data['txCount']),
                        'lp_count': int(pair_data['liquidityProviderCount']),
                        'untracked_volume_usd': float(pair_data['untrackedVolumeUSD']),
                        'tracked_reserve_usd': float(pair_data['trackedReserveUSD'])
                    }

        except Exception as e:
            self.logger.error(f"Error fetching pool metrics: {str(e)}")
            raise

    def _calculate_change(self, old_value: float, new_value: float) -> float:
        """Calculate percentage change between two values"""
        if old_value == 0:
            return 0
        return ((new_value - old_value) / old_value) * 100

    async def get_lending_borrow_rates(self, market_contract: Contract) -> Tuple[float, float]:
        """Get current lending and borrowing rates from Venus market"""
        try:
            supply_rate = await market_contract.functions.supplyRatePerBlock().call()
            borrow_rate = await market_contract.functions.borrowRatePerBlock().call()
            
            # Convert to APY (BSC blocks per year = 10512000)
            blocks_per_year = 10512000
            supply_apy = ((1 + supply_rate / 1e18) ** blocks_per_year - 1) * 100
            borrow_apy = ((1 + borrow_rate / 1e18) ** blocks_per_year - 1) * 100
            
            return supply_apy, borrow_apy

        except Exception as e:
            self.logger.error(f"Error fetching lending/borrow rates: {str(e)}")
            raise 