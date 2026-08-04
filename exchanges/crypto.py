"""
Шифрование секретов (API-ключей бирж) перед сохранением в БД.

Принцип: в базе НИКОГДА не лежит открытый ключ. Шифруем симметрично (Fernet),
ключ шифрования живёт только в переменных окружения (.env), не в коде и не в git.

Если ключ шифрования утечёт вместе с дампом базы — защита бесполезна, поэтому
на проде FERNET_KEY хранится отдельно от БД (секрет-менеджер / переменные окружения
сервера), а не в том же бэкапе.
"""
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from cryptography.fernet import Fernet, InvalidToken


def _get_fernet() -> Fernet:
    """Собирает объект шифрования из ключа в настройках.

    Вынесено в функцию, а не в глобальную переменную, чтобы приложение
    не падало на импорте, если ключ не задан, — упадёт только при реальном
    обращении к шифрованию, с понятной ошибкой.
    """
    key = getattr(settings, 'FERNET_KEY', None)
    if not key:
        raise ImproperlyConfigured(
            'FERNET_KEY не задан. Сгенерируй: '
            'python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())" и положи в .env'
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(value: str) -> str:
    """Шифрует строку. Пустое значение остаётся пустым (не шифруем None/'')."""
    if not value:
        return ''
    return _get_fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    """Расшифровывает строку.

    InvalidToken означает, что данные повреждены или FERNET_KEY сменили.
    Пробрасываем понятную ошибку, а не молча возвращаем мусор, — иначе
    бот попытается торговать с битым ключом и получит отказ от биржи.
    """
    if not token:
        return ''
    try:
        return _get_fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError(
            'Не удалось расшифровать секрет: данные повреждены '
            'или изменился FERNET_KEY'
        ) from exc


def mask(value: str, visible: int = 4) -> str:
    """Маскирует секрет для показа в интерфейсе и логах: 'abcd...wxyz'.

    Нужно, чтобы пользователь узнавал свой ключ, но полный секрет
    никогда не попадал ни на экран, ни в логи.
    """
    if not value:
        return ''
    if len(value) <= visible * 2:
        return '*' * len(value)
    return f'{value[:visible]}...{value[-visible:]}'