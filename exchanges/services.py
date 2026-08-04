"""
Единственное место в проекте, которое разговаривает с биржей.

Зачем отдельный слой, а не вызовы ccxt из задач Celery напрямую:
- логика ботов не зависит от конкретной библиотеки — если завтра меняем ccxt
  на прямые запросы к API, правим один файл;
- все ошибки бирж приводятся к своим исключениям, и остальной код ловит их,
  не зная о внутренностях ccxt;
- секреты расшифровываются только здесь и живут в памяти процесса, не утекая
  в остальной код.
"""
import logging
from decimal import Decimal

import ccxt

from .models import Exchange, ExchangeAccount, MarketType

logger = logging.getLogger(__name__)


class ExchangeError(Exception):
    """Базовая ошибка работы с биржей."""


class AuthError(ExchangeError):
    """Неверные ключи или недостаточно прав."""


class InsufficientFunds(ExchangeError):
    """Не хватает средств на балансе."""


class TemporaryError(ExchangeError):
    """Временный сбой: сеть, таймаут, rate limit. Есть смысл повторить."""


# У ccxt для каждой биржи свой класс. Собираем соответствие один раз.
_CLASSES = {
    Exchange.OKX: ccxt.okx,
    Exchange.BYBIT: ccxt.bybit,
    Exchange.BINANCE: ccxt.binance,
}


class ExchangeClient:
    """Обёртка над ccxt для одного ExchangeAccount."""

    def __init__(self, account: ExchangeAccount):
        self.account = account
        self._client = self._build()

    def _build(self):
        cls = _CLASSES.get(self.account.exchange)
        if cls is None:
            raise ExchangeError(f'Биржа {self.account.exchange} не поддерживается')

        params = {
            'apiKey': self.account.api_key,
            'secret': self.account.api_secret,
            'enableRateLimit': True,  # ccxt сам притормозит запросы под лимиты биржи
            'options': {
                # ccxt называет фьючерсы 'swap' (бессрочные контракты)
                'defaultType': (
                    'swap' if self.account.market_type == MarketType.FUTURES else 'spot'
                ),
            },
        }
        # У OKX третий обязательный параметр — passphrase, у большинства бирж его нет
        if self.account.passphrase_encrypted:
            params['password'] = self.account.passphrase

        client = cls(params)
        if self.account.is_testnet:
            client.set_sandbox_mode(True)
        return client

    # --- Внутреннее: единая обработка ошибок ccxt --------------------------

    def _call(self, method_name: str, *args, **kwargs):
        """Вызывает метод ccxt и переводит его ошибки в наши.

        Важно: сюда НИКОГДА не логируем args целиком — в них могут быть
        чувствительные данные. Логируем только имя метода и текст ошибки.
        """
        method = getattr(self._client, method_name)
        try:
            return method(*args, **kwargs)
        except ccxt.AuthenticationError as exc:
            raise AuthError(f'Ошибка авторизации на бирже: {exc}') from exc
        except ccxt.InsufficientFunds as exc:
            raise InsufficientFunds(f'Недостаточно средств: {exc}') from exc
        except (ccxt.NetworkError, ccxt.RateLimitExceeded, ccxt.ExchangeNotAvailable) as exc:
            raise TemporaryError(f'Временная ошибка биржи: {exc}') from exc
        except ccxt.BaseError as exc:
            logger.warning('Ошибка биржи в %s: %s', method_name, exc)
            raise ExchangeError(str(exc)) from exc

    # --- Публичные методы --------------------------------------------------

    def check_connection(self) -> bool:
        """Проверка ключей: если баланс отдался — ключи рабочие."""
        self._call('fetch_balance')
        return True

    def get_balance(self, currency: str = 'USDT') -> Decimal:
        """Свободный (не занятый в ордерах) баланс по валюте."""
        data = self._call('fetch_balance')
        free = data.get('free', {}).get(currency, 0)
        return Decimal(str(free))

    def get_price(self, symbol: str) -> Decimal:
        """Текущая цена последней сделки по паре."""
        ticker = self._call('fetch_ticker', symbol)
        return Decimal(str(ticker['last']))

    def get_ohlcv(self, symbol: str, timeframe: str = '15m', limit: int = 200):
        """Свечи для расчёта индикаторов.

        Возвращает список [timestamp, open, high, low, close, volume].
        """
        return self._call('fetch_ohlcv', symbol, timeframe, None, limit)

    def create_limit_order(self, symbol: str, side: str, amount: Decimal, price: Decimal):
        """Лимитный ордер. side: 'buy' | 'sell'."""
        return self._call(
            'create_order', symbol, 'limit', side, float(amount), float(price)
        )

    def create_market_order(self, symbol: str, side: str, amount: Decimal):
        """Рыночный ордер — исполняется сразу по текущей цене."""
        return self._call('create_order', symbol, 'market', side, float(amount))

    def cancel_order(self, order_id: str, symbol: str):
        return self._call('cancel_order', order_id, symbol)

    def fetch_order(self, order_id: str, symbol: str):
        """Статус ордера: исполнен, частично, отменён."""
        return self._call('fetch_order', order_id, symbol)

    def get_market_limits(self, symbol: str) -> dict:
        """Ограничения биржи по паре: минимальный объём, шаг цены и количества.

        Без этого ордер может быть отклонён: биржи не принимают
        произвольную точность и слишком мелкие объёмы.
        """
        self._call('load_markets')
        market = self._client.market(symbol)
        return {
            'min_amount': Decimal(str(market['limits']['amount']['min'] or 0)),
            'min_cost': Decimal(str((market['limits'].get('cost') or {}).get('min') or 0)),
            'amount_precision': market['precision']['amount'],
            'price_precision': market['precision']['price'],
        }

    def round_amount(self, symbol: str, amount: Decimal) -> Decimal:
        """Округляет объём под требования биржи."""
        return Decimal(str(self._client.amount_to_precision(symbol, float(amount))))

    def round_price(self, symbol: str, price: Decimal) -> Decimal:
        """Округляет цену под шаг котировки биржи."""
        return Decimal(str(self._client.price_to_precision(symbol, float(price))))