"""Доктор конфигурации: проверяет боевые ключи и помогает настроить YouGile.

Использование:
  python -m dirizher.cli.doctor               # проверить всё из .env
  python -m dirizher.cli.doctor yougile-key   # создать API-ключ YouGile (логин/пароль)

Каждая проверка независима: что настроено — то и тестируется; остальное помечается
как mock/пропущено. Скрипт делает минимальные безопасные запросы к API.
"""

from __future__ import annotations

import sys
import uuid

import httpx

from ..config import get_settings

OK = "✅"
FAIL = "❌"
SKIP = "⚪"


def _line(mark: str, name: str, msg: str) -> None:
    print(f"  {mark} {name:<12} {msg}")


# ── Telegram ─────────────────────────────────────────────────────────────────
def check_telegram(token: str) -> None:
    if not token:
        _line(SKIP, "Telegram", "токен не задан → mock-режим")
        return
    try:
        r = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15)
        data = r.json()
        if r.status_code == 200 and data.get("ok"):
            u = data["result"]
            _line(OK, "Telegram", f"@{u.get('username')} (id {u.get('id')})")
        else:
            _line(FAIL, "Telegram", f"ответ: {data.get('description', r.text)[:120]}")
    except Exception as e:  # noqa: BLE001
        _line(FAIL, "Telegram", f"ошибка сети: {e}")


# ── Groq ─────────────────────────────────────────────────────────────────────
def check_groq(keys: list[str], model: str) -> None:
    if not keys:
        _line(SKIP, "Groq", "ключ не задан")
        return
    ok = 0
    for i, key in enumerate(keys, 1):
        try:
            r = httpx.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {key}"},
                timeout=20,
            )
            if r.status_code == 200:
                ok += 1
            else:
                _line(FAIL, "Groq", f"ключ #{i}: HTTP {r.status_code}: {r.text[:80]}")
        except Exception as e:  # noqa: BLE001
            _line(FAIL, "Groq", f"ключ #{i}: ошибка: {e}")
    if ok:
        _line(OK, "Groq", f"рабочих ключей: {ok}/{len(keys)}; модель {model}; ротация при лимите")


# ── GigaChat ─────────────────────────────────────────────────────────────────
def check_gigachat(creds: str, scope: str) -> None:
    if not creds:
        _line(SKIP, "GigaChat", "креды не заданы")
        return
    try:
        r = httpx.post(
            "https://ngw.devices.sberbank.ru:9443/api/v2/oauth",
            headers={
                "Authorization": f"Basic {creds}",
                "RqUID": str(uuid.uuid4()),
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"scope": scope},
            timeout=20,
            verify=False,  # self-signed цепочка НУЦ Минцифры
        )
        if r.status_code == 200 and r.json().get("access_token"):
            _line(OK, "GigaChat", "токен получен, ключ рабочий")
        else:
            _line(FAIL, "GigaChat", f"HTTP {r.status_code}: {r.text[:120]}")
    except Exception as e:  # noqa: BLE001
        _line(FAIL, "GigaChat", f"ошибка: {e}")


# ── YouGile ──────────────────────────────────────────────────────────────────
def check_yougile(key: str, base: str) -> None:
    if not key:
        _line(SKIP, "YouGile", "ключ не задан → mock-доска")
        return
    headers = {"Authorization": f"Bearer {key}"}
    try:
        r = httpx.get(f"{base}/columns", headers=headers, params={"limit": 1000}, timeout=20)
        if r.status_code != 200:
            _line(FAIL, "YouGile", f"HTTP {r.status_code}: {r.text[:120]}")
            return
        cols = r.json().get("content", [])
        _line(OK, "YouGile", f"ключ рабочий; колонок найдено: {len(cols)}")
        if cols:
            print("\n     Колонки (вставьте нужные id в .env):")
            for col in cols:
                print(f"       • {col.get('title','?'):<22} id = {col.get('id')}")
            print("       DIRIZHER_YOUGILE__COLUMN_TODO / _IN_PROGRESS / _DONE")
    except Exception as e:  # noqa: BLE001
        _line(FAIL, "YouGile", f"ошибка: {e}")


def yougile_create_key(base: str) -> None:
    """Интерактивно создать API-ключ YouGile из логина/пароля."""
    print("Создание API-ключа YouGile (данные никуда не сохраняются).")
    login = input("  Логин (email): ").strip()
    import getpass

    password = getpass.getpass("  Пароль: ").strip()
    try:
        rc = httpx.post(f"{base}/auth/companies", json={"login": login, "password": password}, timeout=20)
        companies = rc.json().get("content", []) if rc.status_code == 200 else []
        if not companies:
            print(f"  {FAIL} Не удалось получить компании: HTTP {rc.status_code} {rc.text[:160]}")
            return
        company = companies[0]
        cid = company["id"]
        print(f"  Компания: {company.get('name','?')} (id {cid})")

        rk = httpx.post(
            f"{base}/auth/keys",
            json={"login": login, "password": password, "companyId": cid},
            timeout=20,
        )
        if rk.status_code in (200, 201):
            key = rk.json().get("key")
            print(f"\n  {OK} API-ключ создан:\n     {key}\n")
            print("  Вставьте его в .env: DIRIZHER_YOUGILE__API_KEY=" + (key or ""))
            print("  Затем: python -m dirizher.cli.doctor  (покажет id колонок)")
        else:
            print(f"  {FAIL} Создание ключа: HTTP {rk.status_code} {rk.text[:160]}")
    except Exception as e:  # noqa: BLE001
        print(f"  {FAIL} Ошибка: {e}")


def main() -> None:
    s = get_settings()
    if len(sys.argv) > 1 and sys.argv[1] == "yougile-key":
        yougile_create_key(s.yougile.base_url)
        return

    print("\n🩺 Дирижёр · проверка боевых ключей\n")
    print(f"  Текущие режимы: {s.mode_banner()}\n")
    check_telegram(s.telegram.bot_token)
    check_groq(s.llm.groq_key_list, s.llm.groq_model)
    check_gigachat(s.llm.gigachat_credentials, s.llm.gigachat_scope)
    check_yougile(s.yougile.api_key, s.yougile.base_url)
    print(
        "\n  Подсказки:\n"
        "   • нет ключа YouGile?  →  python -m dirizher.cli.doctor yougile-key\n"
        "   • всё ✅?            →  python -m dirizher.main\n"
    )


if __name__ == "__main__":
    main()
