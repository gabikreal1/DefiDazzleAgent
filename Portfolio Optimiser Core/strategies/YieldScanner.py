from web3 import Web3, AsyncWeb3
from web3.middleware import geth_poa_middleware
from typing import List, Dict, Tuple, Optional
import asyncio
import aiohttp
from datetime import datetime, timedelta
import json
import logging
import os
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import time
from cachetools import TTLCache, cached
from dataclasses import dataclass
from eth_typing import Address
from web3.contract import Contract

from calculations.apr_calculator import APRCalculator
from calculations.risk_calculator import RiskCalculator
from calculations.price_calculator import PriceCalculator
from calculations.data_fetcher import DataFetcher

@dataclass
class TokenInfo:
    address: str
    symbol: str
    decimals: int
    price: float
    total_supply: float

@dataclass
class PoolInfo:
    address: str
    token0: TokenInfo
    token1: TokenInfo
    reserves: Tuple[float, float]
    tvl: float
    apr: float
    protocol: str
    risk_score: float

class YieldScanner:
    def __init__(self):
        # Initialize Web3 connections
        self.w3 = Web3(Web3.HTTPProvider('https://bsc-dataseed.binance.org/'))
        self.w3.middleware_onion.inject(geth_poa_middleware, layer=0)
        self.async_w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider('https://bsc-dataseed.binance.org/'))
        
        # Initialize addresses
        self.ADDRESSES = {
            'PANCAKESWAP_ROUTER': '0x10ED43C718714eb63d5aA57B78B54704E256024E',
            'PANCAKESWAP_FACTORY': '0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73',
            'PANCAKESWAP_MASTERCHEF': '0x73feaa1eE314F8c655E354234017bE2193C9E24E',
            'VENUS_COMPTROLLER': '0xfD36E2c2a6789Db23113685031d7F16329158384',
            'ALPACA_FAIRLAUNCH': '0xA625AB01B08ce023B2a342Dbb12a16f2C8489A8F',
            'BISWAP_MASTERCHEF': '0xDbc1A13490deeF9c3C12b44FE77b503c1B061739',
            'BUSD': '0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56',
            'WBNB': '0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c',
            'CAKE': '0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82',
            'BSW': '0xYourBswTokenAddressHere'
        }
        
        # Load ABIs
        self.ABIS = {
            'PAIR': self._load_abi('pair_abi.json'),
            'FARM': self._load_abi('farm_abi.json'),
            'ERC20': self._load_abi('erc20_abi.json'),
            'FACTORY': self._load_abi('factory_abi.json'),
            'VENUS_COMPTROLLER': self._load_abi('venus_comptroller_abi.json'),
            'VENUS_MARKET': self._load_abi('venus_market_abi.json'),
            'ALPACA_FAIRLAUNCH': self._load_abi('alpaca_fairlaunch_abi.json'),
            'ALPACA_VAULT': self._load_abi('alpaca_vault_abi.json'),
            'BISWAP_MASTERCHEF': self._load_abi('biswap_masterchef_abi.json')
        }
        
        # Initialize contracts
        self._init_contracts()
        
        # Initialize calculation modules
        self.apr_calculator = APRCalculator()
        self.risk_calculator = RiskCalculator()
        self.price_calculator = PriceCalculator(
            self.w3,
            self.contracts['pancake_factory'],
            self.contracts['pancake_router']
        )
        self.data_fetcher = DataFetcher(self.w3)
        
        # Setup logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)

    def _init_contracts(self):
        """Initialize commonly used contracts"""
        self.contracts = {
            'pancake_factory': self.w3.eth.contract(
                address=self.ADDRESSES['PANCAKESWAP_FACTORY'],
                abi=self.ABIS['FACTORY']
            ),
            'pancake_router': self.w3.eth.contract(
                address=self.ADDRESSES['PANCAKESWAP_ROUTER'],
                abi=self.ABIS['ROUTER']
            ),
            'pancake_masterchef': self.w3.eth.contract(
                address=self.ADDRESSES['PANCAKESWAP_MASTERCHEF'],
                abi=self.ABIS['FARM']
            ),
            'venus_comptroller': self.w3.eth.contract(
                address=self.ADDRESSES['VENUS_COMPTROLLER'],
                abi=self.ABIS['VENUS_COMPTROLLER']
            ),
            'alpaca_fairlaunch': self.w3.eth.contract(
                address=self.ADDRESSES['ALPACA_FAIRLAUNCH'],
                abi=self.ABIS['ALPACA_FAIRLAUNCH']
            ),
            'biswap_masterchef': self.w3.eth.contract(
                address=self.ADDRESSES['BISWAP_MASTERCHEF'],
                abi=self.ABIS['BISWAP_MASTERCHEF']
            )
        }

    def _load_abi(self, filename: str) -> dict:
        """Load ABI from json file"""
        try:
            path = os.path.join(os.path.dirname(__file__), 'abis', filename)
            with open(path, 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading ABI {filename}: {str(e)}")
            return {}

    @cached(cache=TTLCache(maxsize=100, ttl=300))
    async def _get_token_info(self, token_address: str) -> TokenInfo:
        """Get token information including price"""
        try:
            token_contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(token_address),
                abi=self.ABIS['ERC20']
            )
            
            # Get basic token info
            symbol = token_contract.functions.symbol().call()
            decimals = token_contract.functions.decimals().call()
            total_supply = token_contract.functions.totalSupply().call() / (10 ** decimals)
            
            # Get price
            price = await self.get_token_price(token_address)
            
            return TokenInfo(
                address=token_address,
                symbol=symbol,
                decimals=decimals,
                price=price,
                total_supply=total_supply
            )
        except Exception as e:
            self.logger.error(f"Error getting token info for {token_address}: {str(e)}")
            raise

    async def get_token_price(self, token_address: str) -> float:
        """Get token price in USD using PancakeSwap"""
        if token_address in self.price_cache:
            return self.price_cache[token_address]
            
        try:
            if token_address.lower() == self.ADDRESSES['BUSD'].lower():
                return 1.0
                
            pair_address = self.contracts['pancake_factory'].functions.getPair(
                token_address,
                self.ADDRESSES['BUSD']
            ).call()
            
            if pair_address == '0x0000000000000000000000000000000000000000':
                # Try with WBNB if BUSD pair doesn't exist
                wbnb_pair = self.contracts['pancake_factory'].functions.getPair(
                    token_address,
                    self.ADDRESSES['WBNB']
                ).call()
                
                if wbnb_pair == '0x0000000000000000000000000000000000000000':
                    return 0
                    
                # Get price through WBNB
                wbnb_price = await self.get_token_price(self.ADDRESSES['WBNB'])
                pair_contract = self.w3.eth.contract(
                    address=wbnb_pair,
                    abi=self.ABIS['PAIR']
                )
                reserves = pair_contract.functions.getReserves().call()
                token0 = pair_contract.functions.token0().call()
                
                if token_address.lower() == token0.lower():
                    price = (reserves[1] / reserves[0]) * wbnb_price
                else:
                    price = (reserves[0] / reserves[1]) * wbnb_price
            else:
                # Get price directly through BUSD pair
                pair_contract = self.w3.eth.contract(
                    address=pair_address,
                    abi=self.ABIS['PAIR']
                )
                reserves = pair_contract.functions.getReserves().call()
                token0 = pair_contract.functions.token0().call()
                
                if token_address.lower() == token0.lower():
                    price = reserves[1] / reserves[0]
                else:
                    price = reserves[0] / reserves[1]
            
            self.price_cache[token_address] = price
            return price
            
        except Exception as e:
            self.logger.error(f"Error getting price for {token_address}: {str(e)}")
            return 0

    def _calculate_risk_score(self, pool_info: Dict) -> float:
        """Calculate risk score based on various metrics"""
        try:
            risk_factors = {
                'tvl': self._calculate_tvl_risk(pool_info['tvl']),
                'protocol': self._calculate_protocol_risk(pool_info['protocol']),
                'apr': self._calculate_apr_risk(pool_info['apr']),
                'age': self._calculate_age_risk(self._get_pool_age(pool_info.get('address', ''))),
                'liquidity': self._calculate_liquidity_risk(pool_info['tvl'])
            }
            
            # Weighted average of risk factors
            weights = {
                'tvl': 0.3,
                'protocol': 0.2,
                'apr': 0.2,
                'age': 0.15,
                'liquidity': 0.15
            }
            
            risk_score = sum(score * weights[factor] for factor, score in risk_factors.items())
            
            # Normalize to 0-1 range where 1 is highest risk
            return min(max(risk_score, 0), 1)
            
        except Exception as e:
            self.logger.error(f"Error calculating risk score: {str(e)}")
            return 1  # Return highest risk on error

    def _calculate_tvl_risk(self, tvl: float) -> float:
        """Calculate risk based on TVL"""
        if tvl >= 10_000_000:  # $10M+
            return 0.1
        elif tvl >= 1_000_000:  # $1M+
            return 0.3
        elif tvl >= 100_000:    # $100k+
            return 0.6
        else:
            return 0.9

    def _calculate_protocol_risk(self, protocol: str) -> float:
        """Calculate risk based on protocol reputation"""
        protocol_risks = {
            'pancakeswap': 0.2,
            'venus': 0.3,
            'alpaca': 0.4,
            'biswap': 0.5
        }
        return protocol_risks.get(protocol.lower(), 0.9)

    def _calculate_apr_risk(self, apr: float) -> float:
        """Calculate risk based on APR"""
        if apr <= 15:  # 0-15%
            return 0.2
        elif apr <= 50:  # 15-50%
            return 0.4
        elif apr <= 100:  # 50-100%
            return 0.6
        elif apr <= 1000:  # 100-1000%
            return 0.8
        else:  # >1000%
            return 1.0

    def _calculate_age_risk(self, age_in_days: int) -> float:
        """Calculate risk based on pool age"""
        if age_in_days >= 365:  # 1+ year
            return 0.2
        elif age_in_days >= 180:  # 6+ months
            return 0.4
        elif age_in_days >= 90:  # 3+ months
            return 0.6
        elif age_in_days >= 30:  # 1+ month
            return 0.8
        else:
            return 1.0

    def _calculate_liquidity_risk(self, tvl: float) -> float:
        """Calculate risk based on liquidity"""
        if tvl >= 5_000_000:  # $5M+
            return 0.1
        elif tvl >= 1_000_000:  # $1M+
            return 0.3
        elif tvl >= 500_000:    # $500k+
            return 0.5
        elif tvl >= 100_000:    # $100k+
            return 0.7
        else:
            return 0.9

    async def _scan_pancakeswap(self) -> List[Dict]:
        """Scan PancakeSwap farms"""
        opportunities = []
        try:
            masterchef = self.contracts['pancake_masterchef']
            pool_length = masterchef.functions.poolLength().call()
            
            tasks = []
            for pid in range(pool_length):
                tasks.append(self._get_pancake_pool_info(pid))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            opportunities.extend([r for r in results if r is not None])
            
        except Exception as e:
            self.logger.error(f"Error scanning PancakeSwap: {str(e)}")
        
        return opportunities

    async def _get_pancake_pool_info(self, pid: int) -> Optional[Dict]:
        """Get detailed information about a PancakeSwap pool"""
        try:
            masterchef = self.contracts['pancake_masterchef']
            pool_info = masterchef.functions.poolInfo(pid).call()
            
            # Get pool contract
            pool_contract = self.w3.eth.contract(
                address=pool_info[0],
                abi=self.ABIS['PAIR']
            )
            
            # Get token addresses
            token0_address = pool_contract.functions.token0().call()
            token1_address = pool_contract.functions.token1().call()
            
            # Get token prices
            token0_price = await self.price_calculator.get_token_price(token0_address)
            token1_price = await self.price_calculator.get_token_price(token1_address)
            
            # Get reserves and calculate TVL
            reserves = pool_contract.functions.getReserves().call()
            tvl = (
                reserves[0] * token0_price / (10 ** 18) +
                reserves[1] * token1_price / (10 ** 18)
            )
            
            # Calculate APR
            apr = await self.apr_calculator.calculate_pancake_apr(
                pid,
                pool_info,
                masterchef,
                await self.price_calculator.get_token_price(self.ADDRESSES['CAKE']),
                tvl
            )
            
            # Get price histories for IL calculation
            token0_history = await self.price_calculator.get_token_price_history(token0_address)
            token1_history = await self.price_calculator.get_token_price_history(token1_address)
            
            # Calculate risk metrics
            il_risk = self.risk_calculator.calculate_impermanent_loss_risk(
                token0_history,
                token1_history
            )
            
            creation_block = await self.data_fetcher.get_pool_creation_block(pool_info[0])
            creation_timestamp = self.w3.eth.getBlock(creation_block)['timestamp']
            age_in_days = (datetime.now().timestamp() - creation_timestamp) / 86400
            age_risk = self.risk_calculator.calculate_age_risk(int(age_in_days))
            
            tvl_risk = self.risk_calculator.calculate_tvl_risk(tvl)
            
            # Get protocol metrics
            protocol_metrics = await self.data_fetcher.get_protocol_metrics('pancakeswap')
            
            # Calculate final risk score
            risk_score = self.risk_calculator.calculate_composite_risk_score(
                tvl_risk,
                self.risk_calculator.calculate_pool_volatility(token0_history),
                age_risk,
                il_risk,
                protocol_metrics['tvl_change_24h']
            )
            
            return {
                'protocol': 'pancakeswap',
                'type': 'farm',
                'pid': pid,
                'address': pool_info[0],
                'token0_address': token0_address,
                'token1_address': token1_address,
                'token0_price': token0_price,
                'token1_price': token1_price,
                'tvl': tvl,
                'apr': apr,
                'risk_score': risk_score,
                'il_risk': il_risk,
                'age_days': age_in_days,
                'volume_24h': await self.data_fetcher.get_pool_metrics(pool_info[0])['volume_usd'],
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting PancakeSwap pool {pid} info: {str(e)}")
            return None

    async def _scan_venus(self) -> List[Dict]:
        """Scan Venus protocol for lending/borrowing opportunities"""
        opportunities = []
        try:
            comptroller = self.contracts['venus_comptroller']
            markets = comptroller.functions.getAllMarkets().call()
            
            tasks = []
            for market_address in markets:
                tasks.append(self._get_venus_market_info(market_address))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            opportunities.extend([r for r in results if r is not None])
            
        except Exception as e:
            self.logger.error(f"Error scanning Venus: {str(e)}")
        
        return opportunities

    async def _get_venus_market_info(self, market_address: str) -> Optional[Dict]:
        """Get detailed information about a Venus market"""
        try:
            market_contract = self.w3.eth.contract(
                address=market_address,
                abi=self.ABIS['VENUS_MARKET']
            )
            
            # Get underlying token
            underlying_address = market_contract.functions.underlying().call()
            underlying_price = await self.price_calculator.get_token_price(underlying_address)
            
            # Get market data
            supply_rate, borrow_rate = await self.data_fetcher.get_lending_borrow_rates(market_contract)
            
            # Get total supply and borrow
            total_supply = market_contract.functions.totalSupply().call()
            total_borrows = market_contract.functions.totalBorrows().call()
            exchange_rate = market_contract.functions.exchangeRateStored().call()
            
            # Calculate TVL
            tvl = (total_supply * exchange_rate / 1e36) * underlying_price
            
            # Get price history for volatility calculation
            price_history = await self.price_calculator.get_token_price_history(underlying_address)
            
            # Calculate risk metrics
            volatility_score = self.risk_calculator.calculate_pool_volatility(price_history)
            tvl_risk = self.risk_calculator.calculate_tvl_risk(tvl)
            
            # Get protocol metrics
            protocol_metrics = await self.data_fetcher.get_protocol_metrics('venus')
            
            # Calculate utilization rate
            utilization = self._calculate_utilization_rate(total_borrows, total_supply)
            
            # Calculate final risk scores
            supply_risk = self.risk_calculator.calculate_composite_risk_score(
                tvl_risk,
                volatility_score,
                self.risk_calculator.calculate_age_risk(await self.data_fetcher.get_pool_creation_block(market_address)),
                0,  # No IL risk for lending
                protocol_metrics['tvl_change_24h']
            )
            
            borrow_risk = supply_risk * 1.2  # Borrowing inherently riskier than lending
            
            return {
                'protocol': 'venus',
                'type': 'lending',
                'address': market_address,
                'underlying_address': underlying_address,
                'underlying_price': underlying_price,
                'tvl': tvl,
                'supply_apy': supply_rate,
                'borrow_apy': borrow_rate,
                'supply_risk_score': supply_risk,
                'borrow_risk_score': borrow_risk,
                'utilization_rate': utilization,
                'total_supply': total_supply,
                'total_borrows': total_borrows,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting Venus market info: {str(e)}")
            return None

    async def _scan_alpaca(self) -> List[Dict]:
        """Scan Alpaca Finance for vault opportunities"""
        opportunities = []
        try:
            fairlaunch = self.contracts['alpaca_fairlaunch']
            pool_length = fairlaunch.functions.poolLength().call()
            
            tasks = []
            for pid in range(pool_length):
                tasks.append(self._get_alpaca_pool_info(pid))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            opportunities.extend([r for r in results if r is not None])
            
        except Exception as e:
            self.logger.error(f"Error scanning Alpaca: {str(e)}")
        
        return opportunities

    async def _get_alpaca_pool_info(self, pid: int) -> Optional[Dict]:
        """Get detailed information about an Alpaca vault"""
        try:
            fairlaunch = self.contracts['alpaca_fairlaunch']
            pool_info = fairlaunch.functions.poolInfo(pid).call()
            
            # Get vault contract
            vault_contract = self.w3.eth.contract(
                address=pool_info[0],
                abi=self.ABIS['ALPACA_VAULT']
            )
            
            # Get underlying token
            token_address = vault_contract.functions.token().call()
            token_price = await self.price_calculator.get_token_price(token_address)
            
            # Calculate TVL
            total_token = vault_contract.functions.totalToken().call()
            tvl = total_token * token_price / 1e18
            
            # Calculate APYs
            base_apy = await self.apr_calculator.calculate_alpaca_base_apy(vault_contract)
            reward_apy = await self.apr_calculator.calculate_alpaca_reward_apy(
                pid,
                fairlaunch,
                tvl,
                await self.price_calculator.get_token_price(self.ADDRESSES['ALPACA'])
            )
            
            total_apy = base_apy + reward_apy
            
            # Get price history for volatility calculation
            price_history = await self.price_calculator.get_token_price_history(token_address)
            
            # Calculate risk metrics
            volatility_score = self.risk_calculator.calculate_pool_volatility(price_history)
            tvl_risk = self.risk_calculator.calculate_tvl_risk(tvl)
            
            # Get protocol metrics
            protocol_metrics = await self.data_fetcher.get_protocol_metrics('alpaca')
            
            # Calculate final risk score
            risk_score = self.risk_calculator.calculate_composite_risk_score(
                tvl_risk,
                volatility_score,
                self.risk_calculator.calculate_age_risk(await self.data_fetcher.get_pool_creation_block(pool_info[0])),
                0,  # No IL risk for vaults
                protocol_metrics['tvl_change_24h']
            )
            
            return {
                'protocol': 'alpaca',
                'type': 'vault',
                'pid': pid,
                'address': pool_info[0],
                'token_address': token_address,
                'token_price': token_price,
                'tvl': tvl,
                'base_apy': base_apy,
                'reward_apy': reward_apy,
                'total_apy': total_apy,
                'risk_score': risk_score,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting Alpaca pool {pid} info: {str(e)}")
            return None

    async def _scan_biswap(self) -> List[Dict]:
        """Scan Biswap for farming opportunities"""
        opportunities = []
        try:
            masterchef = self.contracts['biswap_masterchef']
            pool_length = masterchef.functions.poolLength().call()
            
            tasks = []
            for pid in range(pool_length):
                tasks.append(self._get_biswap_pool_info(pid))
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            opportunities.extend([r for r in results if r is not None])
            
        except Exception as e:
            self.logger.error(f"Error scanning Biswap: {str(e)}")
        
        return opportunities

    async def _get_biswap_pool_info(self, pid: int) -> Optional[Dict]:
        """Get detailed information about a Biswap pool"""
        try:
            masterchef = self.contracts['biswap_masterchef']
            pool_info = masterchef.functions.poolInfo(pid).call()
            
            # Get pool contract
            pool_contract = self.w3.eth.contract(
                address=pool_info[0],
                abi=self.ABIS['PAIR']
            )
            
            # Get token addresses
            token0_address = pool_contract.functions.token0().call()
            token1_address = pool_contract.functions.token1().call()
            
            # Get token prices
            token0_price = await self.price_calculator.get_token_price(token0_address)
            token1_price = await self.price_calculator.get_token_price(token1_address)
            
            # Get reserves and calculate TVL
            reserves = pool_contract.functions.getReserves().call()
            tvl = (
                reserves[0] * token0_price / (10 ** 18) +
                reserves[1] * token1_price / (10 ** 18)
            )
            
            # Calculate APR
            apr = await self.apr_calculator.calculate_biswap_apr(
                pid,
                pool_info,
                masterchef,
                await self.price_calculator.get_token_price(self.ADDRESSES['BSW']),
                tvl
            )
            
            # Get price histories for IL calculation
            token0_history = await self.price_calculator.get_token_price_history(token0_address)
            token1_history = await self.price_calculator.get_token_price_history(token1_address)
            
            # Calculate risk metrics
            il_risk = self.risk_calculator.calculate_impermanent_loss_risk(
                token0_history,
                token1_history
            )
            
            volatility_score = self.risk_calculator.calculate_pool_volatility(token0_history)
            tvl_risk = self.risk_calculator.calculate_tvl_risk(tvl)
            
            # Get protocol metrics
            protocol_metrics = await self.data_fetcher.get_protocol_metrics('biswap')
            
            # Calculate final risk score
            risk_score = self.risk_calculator.calculate_composite_risk_score(
                tvl_risk,
                volatility_score,
                self.risk_calculator.calculate_age_risk(await self.data_fetcher.get_pool_creation_block(pool_info[0])),
                il_risk,
                protocol_metrics['tvl_change_24h']
            )
            
            return {
                'protocol': 'biswap',
                'type': 'farm',
                'pid': pid,
                'address': pool_info[0],
                'token0_address': token0_address,
                'token1_address': token1_address,
                'token0_price': token0_price,
                'token1_price': token1_price,
                'tvl': tvl,
                'apr': apr,
                'risk_score': risk_score,
                'il_risk': il_risk,
                'timestamp': datetime.now().isoformat()
            }
            
        except Exception as e:
            self.logger.error(f"Error getting Biswap pool {pid} info: {str(e)}")
            return None

    def _calculate_utilization_rate(self, total_borrows: int, total_supply: int) -> float:
        """Calculate the utilization rate of a lending pool"""
        if total_supply == 0:
            return 0
        return (total_borrows / total_supply) * 100

    def _load_abi(self, filename: str) -> Dict:
        """Load ABI from file"""
        try:
            with open(f"abis/{filename}", 'r') as f:
                return json.load(f)
        except Exception as e:
            self.logger.error(f"Error loading ABI {filename}: {str(e)}")
            raise
