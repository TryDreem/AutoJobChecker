"""Первичный вход в Telegram-аккаунт для парсера.

Запуск:
    python -m app.tools.login

Создаёт файл сессии в data/. После этого бот подключается сам, без кода.
Файл сессии — это полный доступ к аккаунту: не коммить его и не пересылай.
"""

from __future__ import annotations

import asyncio
import sys

from telethon import TelegramClient
from telethon.errors import (
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from app.config import get_settings


async def main() -> int:
    settings = get_settings()

    print()
    print("═" * 62)
    print("  Вход в Telegram для парсера каналов")
    print("═" * 62)
    print()
    print("Нужен обычный аккаунт, а не бот: боты не могут читать чужие каналы.")
    print(f"Файл сессии: {settings.session_path}.session")
    print()

    client = TelegramClient(
        str(settings.session_path), settings.tg_api_id, settings.tg_api_hash
    )
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"✅ Уже авторизован как @{me.username or me.first_name} (id {me.id})")
        print("   Ничего делать не нужно — запускай python main.py")
        await client.disconnect()
        return 0

    phone = settings.tg_phone or input("Номер телефона (например +48123456789): ").strip()
    if not phone:
        print("❌ Без номера войти нельзя.")
        await client.disconnect()
        return 1

    try:
        await client.send_code_request(phone)
    except Exception as exc:  # noqa: BLE001 - причина нужна пользователю целиком
        print(f"❌ Не удалось отправить код: {exc}")
        print("   Проверь TG_API_ID и TG_API_HASH в .env — их берут на my.telegram.org")
        await client.disconnect()
        return 1

    print()
    print("📨 Код отправлен в Telegram (ищи сообщение от «Telegram»).")
    code = input("   Введи код: ").strip()

    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        print()
        print("🔐 Включена двухфакторная защита.")
        password = input("   Введи облачный пароль: ").strip()
        try:
            await client.sign_in(password=password)
        except Exception as exc:  # noqa: BLE001
            print(f"❌ Пароль не подошёл: {exc}")
            await client.disconnect()
            return 1
    except PhoneCodeInvalidError:
        print("❌ Неверный код.")
        await client.disconnect()
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"❌ Не удалось войти: {exc}")
        await client.disconnect()
        return 1

    me = await client.get_me()
    print()
    print(f"✅ Готово. Вошёл как @{me.username or me.first_name} (id {me.id})")
    print(f"   Сессия сохранена: {settings.session_path}.session")
    print()
    print("   Дальше: python main.py")
    print()
    await client.disconnect()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\nОтменено.")
        sys.exit(1)
