"""
Индикаторы и проверка условий входа.

Считаем сами, без ta-lib: он требует компиляции C-библиотеки и усложняет
деплой. Формулы простые, а точность нам нужна ровно та же, что видит
пользователь на графике TradingView.

Все функции принимают список свечей ccxt: [ts, open, high, low, close, volume].
"""
from decimal import Decimal

from .models import EntryFilter


def closes(ohlcv: list) -> list[Decimal]:
    """Цены закрытия — база для большинства индикаторов."""
    return [Decimal(str(candle[4])) for candle in ohlcv]


def sma(values: list[Decimal], period: int) -> Decimal | None:
    """Простая скользящая средняя: среднее за N последних значений."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / Decimal(period)


def ema(values: list[Decimal], period: int) -> Decimal | None:
    """Экспоненциальная скользящая: свежие свечи весят больше старых.

    Стартуем от SMA первых period значений, дальше идём по рекуррентной
    формуле EMA = close * k + EMA_prev * (1 - k), где k = 2/(period+1).
    """
    if len(values) < period:
        return None
    k = Decimal('2') / Decimal(period + 1)
    result = sum(values[:period]) / Decimal(period)
    for value in values[period:]:
        result = value * k + result * (Decimal('1') - k)
    return result


def rsi(values: list[Decimal], period: int = 14) -> Decimal | None:
    """Индекс относительной силы, 0..100.

    Считает отношение среднего роста к среднему падению за период.
    Ниже 30 — принято считать перепроданностью (сигнал на покупку),
    выше 70 — перекупленностью.
    """
    if len(values) < period + 1:
        return None

    gains, losses = [], []
    for prev, curr in zip(values, values[1:]):
        diff = curr - prev
        gains.append(max(diff, Decimal('0')))
        losses.append(max(-diff, Decimal('0')))

    # Первое значение — простое среднее, дальше сглаживание Уайлдера
    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)

    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * Decimal(period - 1) + gain) / Decimal(period)
        avg_loss = (avg_loss * Decimal(period - 1) + loss) / Decimal(period)

    if avg_loss == 0:
        return Decimal('100')  # падений не было вовсе — максимум шкалы
    rs = avg_gain / avg_loss
    return Decimal('100') - Decimal('100') / (Decimal('1') + rs)


def stdev(values: list[Decimal], period: int) -> Decimal | None:
    """Стандартное отклонение — ширина полос Боллинджера."""
    if len(values) < period:
        return None
    window = values[-period:]
    mean = sum(window) / Decimal(period)
    variance = sum((v - mean) ** 2 for v in window) / Decimal(period)
    return variance.sqrt()


def bollinger(values: list[Decimal], period: int = 20, mult: Decimal = Decimal('2')):
    """Полосы Боллинджера: средняя ± N стандартных отклонений.

    Возвращает (нижняя, средняя, верхняя). Цена у нижней полосы —
    частый сигнал на вход в long.
    """
    middle = sma(values, period)
    deviation = stdev(values, period)
    if middle is None or deviation is None:
        return None, None, None
    return middle - deviation * mult, middle, middle + deviation * mult


def macd(values: list[Decimal], fast: int = 12, slow: int = 26, signal: int = 9):
    """MACD: разница быстрой и медленной EMA плюс сигнальная линия."""
    fast_ema, slow_ema = ema(values, fast), ema(values, slow)
    if fast_ema is None or slow_ema is None:
        return None, None
    macd_line = fast_ema - slow_ema
    # Упрощение: сигнальную считаем от истории MACD, здесь берём EMA закрытий
    signal_line = ema(values, signal)
    return macd_line, signal_line


def indicator_value(kind: str, values: list[Decimal], period: int) -> Decimal | None:
    """Единая точка получения значения любого индикатора по его коду."""
    match kind:
        case EntryFilter.Indicator.RSI:
            return rsi(values, period)
        case EntryFilter.Indicator.EMA:
            return ema(values, period)
        case EntryFilter.Indicator.SMA:
            return sma(values, period)
        case EntryFilter.Indicator.BOLLINGER_LOWER:
            return bollinger(values, period)[0]
        case EntryFilter.Indicator.BOLLINGER_UPPER:
            return bollinger(values, period)[2]
        case EntryFilter.Indicator.MACD:
            return macd(values)[0]
        case EntryFilter.Indicator.PRICE:
            return values[-1] if values else None
    return None


def check_filter(entry_filter: EntryFilter, ohlcv: list) -> bool:
    """Проверяет одно условие входа.

    Для пересечений (cross_up/cross_down) нужны два состояния — текущее
    и предыдущее: пересечение это не «выше», а «было ниже, стало выше».
    """
    values = closes(ohlcv)
    current = indicator_value(entry_filter.indicator, values, entry_filter.period)
    if current is None:
        return False

    target = entry_filter.value
    operator = entry_filter.operator

    if operator == EntryFilter.Operator.GT:
        return current > target
    if operator == EntryFilter.Operator.LT:
        return current < target

    previous = indicator_value(
        entry_filter.indicator, values[:-1], entry_filter.period
    )
    if previous is None:
        return False
    if operator == EntryFilter.Operator.CROSS_UP:
        return previous <= target < current
    if operator == EntryFilter.Operator.CROSS_DOWN:
        return previous >= target > current
    return False


def should_enter(bot, candles_by_timeframe: dict[str, list]) -> bool:
    """Итоговое решение о входе.

    Логика: внутри группы все фильтры должны быть истинны (И),
    достаточно одной истинной группы (ИЛИ).
    Если фильтров нет вообще — входим сразу, без условий.
    """
    filters = list(bot.filters.all())
    if not filters:
        return True

    groups: dict[int, list[EntryFilter]] = {}
    for item in filters:
        groups.setdefault(item.group, []).append(item)

    for group_filters in groups.values():
        if all(
            check_filter(f, candles_by_timeframe.get(f.timeframe, []))
            for f in group_filters
        ):
            return True
    return False


def required_timeframes(bot) -> set[str]:
    """Какие таймфреймы нужно запросить у биржи для этого бота.

    Собираем заранее, чтобы сделать по одному запросу на таймфрейм,
    а не по запросу на каждый фильтр.
    """
    return {f.timeframe for f in bot.filters.all()}