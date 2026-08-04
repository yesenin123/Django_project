"""
Расчёт сетки ордеров.

Здесь нет ни базы, ни биржи — только чистые функции. Благодаря этому:
- сетку можно посчитать и показать на графике ДО запуска бота (предпросмотр);
- ту же функцию использует бэктест и реальная торговля — расхождений не будет;
- логику легко покрыть тестами.

Как работает стратегия (одна, как у Veles):
1. Бот ждёт сигнала входа (индикаторы). Дождавшись — выставляет сетку лимитных
   ордеров в сторону против позиции: для LONG — вниз, для SHORT — вверх.
2. Первый ордер стоит на расстоянии indent_percent от рынка (или по рынку).
3. Вся сетка укладывается в диапазон overlap_percent — это «перекрытие»,
   то есть насколько бот выдержит движение против себя.
4. Объём каждого следующего ордера больше предыдущего на martingale_percent —
   так средняя цена быстрее подтягивается к текущей.
5. После каждого исполнения пересчитывается средняя цена, и тейк-профит
   переставляется относительно НЕЁ, а не относительно первого входа.
6. Сработал тейк-профит — позиция закрыта целиком, цикл начинается заново.
"""
from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal

HUNDRED = Decimal('100')


@dataclass(frozen=True)
class GridOrder:
    """Один ордер сетки со всеми пересчитанными на его момент величинами."""

    index: int                      # порядковый номер, 0 — первый (базовый)
    price: Decimal                  # цена ордера
    quote_amount: Decimal           # сколько денег (USDT) вложим этим ордером
    base_amount: Decimal            # сколько монеты купим/продадим
    cumulative_quote: Decimal       # всего вложено после этого ордера
    cumulative_base: Decimal        # всего монеты в позиции после этого ордера
    average_price: Decimal          # средняя цена входа после этого ордера
    take_profit_price: Decimal      # куда переедет тейк-профит после исполнения


def _price_steps(overlap: Decimal, count: int, log_ratio: Decimal) -> list[Decimal]:
    """Расстояния (в %) каждого ордера от первого.

    Линейное распределение (log_ratio == 1): шаги одинаковые.
    Логарифмическое (log_ratio > 1): каждый следующий шаг больше предыдущего
    в log_ratio раз — ордера гуще у цены входа и реже на удалении.
    Смысл: чаще ловим мелкие откаты, но остаёмся защищены на глубокой просадке.
    """
    if count <= 1:
        return [Decimal('0')]

    gaps = count - 1  # промежутков между ордерами на один меньше, чем ордеров

    if log_ratio == Decimal('1'):
        step = overlap / Decimal(gaps)
        return [step * Decimal(i) for i in range(count)]

    # Геометрическая прогрессия шагов: s, s*r, s*r^2, ...
    # Сумма всех шагов должна равняться overlap, отсюда находим первый шаг s.
    ratios = [log_ratio ** i for i in range(gaps)]
    first_step = overlap / sum(ratios)

    offsets = [Decimal('0')]
    acc = Decimal('0')
    for r in ratios:
        acc += first_step * r
        offsets.append(acc)
    return offsets


def _volume_weights(count: int, martingale: Decimal) -> list[Decimal]:
    """Доли депозита на каждый ордер с учётом мартингейла.

    Объём растёт на martingale % с каждым шагом: w_i = (1 + m/100)^i.
    Нормируем так, чтобы сумма долей была равна 1 — тогда сетка
    ровно укладывается в депозит, ни больше ни меньше.
    """
    factor = Decimal('1') + martingale / HUNDRED
    weights = [factor ** i for i in range(count)]
    total = sum(weights)
    return [w / total for w in weights]


def calculate_grid(
    *,
    market_price: Decimal,
    deposit: Decimal,
    direction: str,
    orders_count: int,
    overlap_percent: Decimal,
    martingale_percent: Decimal,
    indent_percent: Decimal,
    take_profit_percent: Decimal,
    logarithmic: bool = False,
    log_ratio: Decimal = Decimal('1.2'),
) -> list[GridOrder]:
    """Считает всю сетку целиком.

    Все аргументы именованные (звёздочка в сигнатуре) — при таком количестве
    параметров позиционный вызов слишком легко перепутать местами.

    direction: 'long' — сетка вниз, покупаем; 'short' — сетка вверх, продаём.
    """
    if orders_count < 1:
        raise ValueError('Количество ордеров должно быть не меньше 1')
    if deposit <= 0:
        raise ValueError('Депозит должен быть больше нуля')
    if market_price <= 0:
        raise ValueError('Цена должна быть больше нуля')

    is_long = direction == 'long'
    # Знак: для LONG сетка идёт вниз от цены, для SHORT — вверх
    sign = Decimal('-1') if is_long else Decimal('1')

    # Первый ордер отступает от рынка на indent_percent в ту же сторону, что и сетка
    start_price = market_price * (Decimal('1') + sign * indent_percent / HUNDRED)

    ratio = log_ratio if logarithmic else Decimal('1')
    offsets = _price_steps(overlap_percent, orders_count, ratio)
    weights = _volume_weights(orders_count, martingale_percent)

    orders: list[GridOrder] = []
    cum_quote = Decimal('0')
    cum_base = Decimal('0')

    for i in range(orders_count):
        price = start_price * (Decimal('1') + sign * offsets[i] / HUNDRED)
        quote = deposit * weights[i]
        base = quote / price

        cum_quote += quote
        cum_base += base
        average = cum_quote / cum_base

        # Тейк-профит считается от СРЕДНЕЙ цены: для LONG выше неё, для SHORT ниже
        tp = average * (Decimal('1') - sign * take_profit_percent / HUNDRED)

        orders.append(
            GridOrder(
                index=i,
                price=price,
                quote_amount=quote,
                base_amount=base,
                cumulative_quote=cum_quote,
                cumulative_base=cum_base,
                average_price=average,
                take_profit_price=tp,
            )
        )
    return orders


def average_price(filled: list[tuple[Decimal, Decimal]]) -> Decimal:
    """Средняя цена по фактически исполненным ордерам.

    filled — список пар (цена, объём в монете). Используется в реальной торговле,
    где исполниться могло меньше ордеров, чем запланировано, и по чуть другим ценам
    (проскальзывание), поэтому пересчитываем по факту, а не по плану.
    """
    total_base = sum(amount for _, amount in filled)
    if not total_base:
        return Decimal('0')
    total_quote = sum(price * amount for price, amount in filled)
    return total_quote / total_base


def take_profit_price(avg: Decimal, percent: Decimal, direction: str) -> Decimal:
    """Цена тейк-профита от средней цены входа."""
    sign = Decimal('1') if direction == 'long' else Decimal('-1')
    return avg * (Decimal('1') + sign * percent / HUNDRED)


def liquidation_distance_percent(orders: list[GridOrder]) -> Decimal:
    """Насколько процентов цена может уйти против позиции, пока сетка не исчерпана.

    Простой, но важный показатель риска для пользователя: если рынок пройдёт
    дальше последнего ордера, бот больше не усредняет и сидит в убытке.
    """
    if not orders:
        return Decimal('0')
    first, last = orders[0].price, orders[-1].price
    return abs(last - first) / first * HUNDRED


def quantize(value: Decimal, places: int = 8) -> Decimal:
    """Округление вниз до нужной точности.

    Вниз (ROUND_DOWN), а не по правилам арифметики: при работе с деньгами
    лучше запросить у биржи чуть меньше, чем чуть больше доступного —
    иначе ордер отклонят из-за нехватки средств.
    """
    return value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN)