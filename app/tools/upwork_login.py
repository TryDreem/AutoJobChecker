"""Одноразовая авторизация Upwork: получение refresh_token.

Запуск:
    python -m app.tools.upwork_login

Ключ и секрет (UPWORK_CLIENT_ID / UPWORK_CLIENT_SECRET) уже должны лежать
в .env — их выдают сразу при создании заявки в API Center. Но пока статус
ключа "Disabled" (Upwork рассматривает заявку — обычно около недели), этот
скрипт будет падать с ошибкой авторизации: это нормально, просто рано.

Сам refresh_token эти два поля не заменяют — client_id/client_secret
подтверждают личность приложения, а refresh_token появляется только после
того, как ты лично разрешишь приложению доступ к своему аккаунту Upwork.
Это разовое действие через браузер, дальше бот обновляет токен сам.
"""

from __future__ import annotations

import sys
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config import get_settings

AUTHORIZE_URL = "https://www.upwork.com/ab/account-security/oauth2/authorize"
TOKEN_URL = "https://www.upwork.com/api/v3/oauth2/token"
# Тот же адрес, что указан как Callback URL в самой заявке на ключ.
REDIRECT_URI = "https://localhost"


def _extract_code(pasted: str) -> str | None:
    """Достаёт code= из вставленной ссылки — целиком или уже готовым значением."""
    pasted = pasted.strip()
    if not pasted:
        return None
    if pasted.startswith("http"):
        query = parse_qs(urlparse(pasted).query)
        codes = query.get("code")
        return codes[0] if codes else None
    return pasted


def main() -> int:
    settings = get_settings()

    print()
    print("═" * 62)
    print("  Авторизация Upwork — получение refresh_token")
    print("═" * 62)
    print()

    if not (settings.upwork_client_id and settings.upwork_client_secret):
        print("❌ В .env нет UPWORK_CLIENT_ID / UPWORK_CLIENT_SECRET.")
        print("   Сначала создай ключ: upwork.com → Settings → API Access.")
        return 1

    auth_url = f"{AUTHORIZE_URL}?" + urlencode(
        {"response_type": "code", "client_id": settings.upwork_client_id}
    )

    print("1. Открой эту ссылку в браузере и войди под своим аккаунтом Upwork:")
    print()
    print(f"   {auth_url}")
    print()
    print("2. Разреши приложению доступ (Authorize).")
    print("3. Браузер перекинет на https://localhost/... — страница НЕ откроется,")
    print("   это нормально: нужен просто адрес из адресной строки.")
    print("4. Скопируй эту ссылку целиком (или только значение после code=) и")
    print("   вставь ниже.")
    print()

    pasted = input("Вставь ссылку или code: ").strip()
    code = _extract_code(pasted)
    if not code:
        print("❌ Не нашёл code= в том, что ты вставил. Попробуй ещё раз.")
        return 1

    print()
    print("Обмениваю code на токены…")

    try:
        response = httpx.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.upwork_client_id,
                "client_secret": settings.upwork_client_secret,
                "redirect_uri": REDIRECT_URI,
            },
            timeout=30,
        )
    except httpx.HTTPError as exc:
        print(f"❌ Сеть недоступна: {exc}")
        return 1

    if response.status_code != 200:
        print(f"❌ Upwork ответил HTTP {response.status_code}: {response.text[:300]}")
        print()
        print("   Частые причины:")
        print("   • ключ ещё Disabled — Upwork не одобрил заявку (жди письмо, ~неделя)")
        print("   • code уже использован или истёк — ссылки одноразовые и живут минуты")
        print("   • redirect_uri в запросе не совпадает с Callback URL ключа")
        return 1

    payload = response.json()
    refresh_token = payload.get("refresh_token", "")
    if not refresh_token:
        print(f"❌ В ответе нет refresh_token: {payload}")
        return 1

    print()
    print("✅ Готово. Впиши это значение в .env:")
    print()
    print(f"   UPWORK_REFRESH_TOKEN={refresh_token}")
    print()
    print("   Дальше бот сам обновляет access_token по мере надобности —")
    print("   этот шаг больше повторять не нужно.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
