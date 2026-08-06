#!/usr/bin/env python3
"""
salomon_stock_watcher.py  (v2 — bez przeglądarki)
-------------------------------------------------
Odpytuje API stanów magazynowych Salomon i powiadamia, gdy wybrany rozmiar
wróci do sprzedaży.

Endpoint:  GET https://www.salomon.com/api/pdp/stock?sizeIds=SKU1,SKU2,...
Odpowiedź: [{"sku_code": "L47971100024", "stock": 0}, ...]

Wymagania:
    pip install requests

Użycie:
    python salomon_stock_watcher.py              # pętla co 5 min
    python salomon_stock_watcher.py --once       # jedno sprawdzenie
    python salomon_stock_watcher.py --list       # stany WSZYSTKICH rozmiarów
    python salomon_stock_watcher.py --raw        # surowa odpowiedź API
    python salomon_stock_watcher.py --test-notify
"""

from __future__ import annotations

import argparse
import os
import random
import sys
import time
import warnings
from datetime import datetime

warnings.filterwarnings("ignore", message=".*OpenSSL.*")  # nieszkodliwe na macOS/LibreSSL

import requests

# --------------------------------------------------------------------------
# KONFIGURACJA
# --------------------------------------------------------------------------

TARGET_SKU = "L47971100024"          # 38 2/3
PRODUCT_URL = f"https://www.salomon.com/pl-pl/product/x-alp-woven-li8936/L47971100?sizeId={TARGET_SKU}"
STOCK_API = "https://www.salomon.com/api/pdp/stock"

# SKU-e rosną sekwencyjnie od rozmiaru 36 (…020) do 48 (…038)
SIZE_LABELS = [
    "36", "36 2/3", "37 1/3", "38", "38 2/3", "39 1/3", "40", "40 2/3", "41 1/3",
    "42", "42 2/3", "43 1/3", "44", "44 2/3", "45 1/3", "46", "46 2/3", "47 1/3", "48",
]
ALL_SKUS = {f"L47971100{20 + i:03d}"[:13]: label for i, label in enumerate(SIZE_LABELS)}
ALL_SKUS = {f"L479711000{20 + i}": label for i, label in enumerate(SIZE_LABELS)}

INTERVAL_SECONDS = 300
JITTER_SECONDS = 45
STATE_FILE = "salomon_watcher_state.txt"
LOG_FILE = "salomon_watcher.log"
REMIND_EVERY_HOURS = 0               # 0 = powiadom raz i zakończ

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Referer": PRODUCT_URL,
}

STOCK_KEYS = ("stock", "quantity", "stockLevel", "available_quantity", "availableQuantity")
SKU_KEYS = ("sku_code", "skuCode", "sku", "id", "sizeId")


# --------------------------------------------------------------------------
# NARZĘDZIA
# --------------------------------------------------------------------------

def log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


LOCALE_CANDIDATES = ["pl-PL", "pl_PL", "pl-pl", "pl", "pl-PL@PLN"]
_working_locale: dict[str, str | None] = {"value": None}


def build_url(skus: list[str], locale: str) -> str:
    """Przecinki MUSZĄ zostać niezakodowane — API zwraca 400 na %2C."""
    return f"{STOCK_API}?sizeIds=" + ",".join(skus) + f"&locale={locale}"


def _get(skus: list[str], locale: str):
    return requests.get(build_url(skus, locale), headers=HEADERS, timeout=25)


def fetch_stock(skus: list[str]) -> list[dict]:
    # Za pierwszym razem sprawdzamy, który format locale akceptuje API; potem trzymamy się go.
    candidates = [_working_locale["value"]] if _working_locale["value"] else LOCALE_CANDIDATES
    response = None
    for locale in candidates:
        response = _get(skus, locale)
        if response.ok:
            if _working_locale["value"] != locale:
                _working_locale["value"] = locale
                log(f"Działający parametr locale: {locale}")
            break

    # Gdyby API nie lubiło długich list — spróbuj pojedynczo.
    if response is not None and response.status_code == 400 and len(skus) > 1:
        log("API odrzuciło zbiorcze zapytanie (400) — przechodzę na pojedyncze SKU.")
        collected: list[dict] = []
        for sku in skus:
            collected.extend(fetch_stock([sku]))
            time.sleep(0.4)
        return collected

    if response is None or not response.ok:
        raise RuntimeError(
            f"HTTP {response.status_code} dla {response.url}\nOdpowiedź: {response.text[:500]}"
        )

    payload = response.json()

    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for value in payload.values():           # np. {"data": [...]} albo {"stocks": [...]}
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        return [payload]
    raise ValueError(f"Nieoczekiwany format odpowiedzi: {type(payload)}")


def read_entry(entry: dict) -> tuple[str | None, int | None]:
    sku = next((entry[k] for k in SKU_KEYS if k in entry), None)
    stock = next((entry[k] for k in STOCK_KEYS if k in entry), None)
    if isinstance(stock, bool):
        stock = int(stock)
    if isinstance(stock, str) and stock.isdigit():
        stock = int(stock)
    return sku, stock if isinstance(stock, int) else None


def stock_for(sku: str) -> int:
    for entry in fetch_stock([sku]):
        found_sku, stock = read_entry(entry)
        if found_sku == sku:
            if stock is None:
                raise ValueError(f"Brak pola ze stanem w odpowiedzi: {entry}")
            return stock
    raise ValueError(f"API nie zwróciło wpisu dla {sku}")


# --------------------------------------------------------------------------
# POWIADOMIENIA
# --------------------------------------------------------------------------

def notify_smsapi(title: str, body: str) -> bool:
    token, to = os.getenv("SMSAPI_TOKEN"), os.getenv("SMS_TO")
    if not (token and to):
        return False
    r = requests.post(
        "https://api.smsapi.pl/sms.do",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "to": to,
            "message": f"{title} {body}",
            "from": os.getenv("SMSAPI_SENDER", "Info"),
            "format": "json",
            "encoding": "utf-8",
        },
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json()
    if "error" in payload:
        raise RuntimeError(f"SMSAPI: {payload}")
    return True


def notify_twilio(title: str, body: str) -> bool:
    sid, tok = os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN")
    frm, to = os.getenv("TWILIO_FROM"), os.getenv("SMS_TO")
    if not (sid and tok and frm and to):
        return False
    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        auth=(sid, tok),
        data={"From": frm, "To": to, "Body": f"{title} {body}"},
        timeout=20,
    )
    r.raise_for_status()
    return True


def notify_telegram(title: str, body: str) -> bool:
    token, chat_id = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat_id):
        return False
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data={"chat_id": chat_id, "text": f"{title}\n{body}"},
        timeout=20,
    )
    r.raise_for_status()
    return True


def notify_ntfy(title: str, body: str) -> bool:
    topic = os.getenv("NTFY_TOPIC")
    if not topic:
        return False
    r = requests.post(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Priority": "urgent",
            "Tags": "athletic_shoe",
            "Click": PRODUCT_URL,
        },
        timeout=20,
    )
    r.raise_for_status()
    return True


CHANNELS = (notify_smsapi, notify_twilio, notify_telegram, notify_ntfy)


def notify(title: str, body: str) -> None:
    sent = False
    for channel in CHANNELS:
        try:
            if channel(title, body):
                log(f"Powiadomienie wysłane: {channel.__name__}")
                sent = True
        except Exception as exc:
            log(f"BŁĄD kanału {channel.__name__}: {exc}")
    if not sent:
        log("UWAGA: żaden kanał nie jest skonfigurowany — ustaw zmienne środowiskowe.")


# --------------------------------------------------------------------------
# STAN / PĘTLA
# --------------------------------------------------------------------------

def last_notified() -> float | None:
    try:
        with open(STATE_FILE, encoding="utf-8") as fh:
            return float(fh.read().strip())
    except (OSError, ValueError):
        return None


def mark_notified() -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as fh:
        fh.write(str(time.time()))


def print_all_sizes() -> None:
    entries = fetch_stock(list(ALL_SKUS))
    log(f"Stany wszystkich rozmiarów ({len(entries)} wpisów):")
    for entry in entries:
        sku, stock = read_entry(entry)
        label = ALL_SKUS.get(sku, "?")
        mark = "✅" if (stock or 0) > 0 else "  "
        log(f"  {mark} {label:<8} {sku}  stock={stock}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--list", action="store_true", help="stany wszystkich rozmiarów")
    parser.add_argument("--raw", action="store_true", help="surowa odpowiedź API")
    parser.add_argument("--test-notify", action="store_true")
    args = parser.parse_args()

    if args.test_notify:
        notify("TEST", f"Testowe powiadomienie ({datetime.now():%H:%M}).")
        return 0

    if args.raw:
        for locale in LOCALE_CANDIDATES:
            url = build_url(list(ALL_SKUS), locale)
            r = requests.get(url, headers=HEADERS, timeout=25)
            print(f"\n--- locale={locale} -> HTTP {r.status_code}")
            print(r.text[:1500])
            if r.ok:
                print(f"\nDZIAŁA. Pełny adres:\n{url}")
                break
        return 0

    if args.list:
        print_all_sizes()
        return 0

    label = ALL_SKUS.get(TARGET_SKU, TARGET_SKU)
    log(f"Start. Rozmiar {label} ({TARGET_SKU}), co {INTERVAL_SECONDS // 60} min.")
    failures = 0

    while True:
        try:
            stock = stock_for(TARGET_SKU)
            failures = 0
            log(f"Rozmiar {label}: stock={stock} {'DOSTĘPNY ✅' if stock > 0 else ''}")

            if stock > 0:
                last = last_notified()
                due = last is None or (
                    REMIND_EVERY_HOURS and time.time() - last > REMIND_EVERY_HOURS * 3600
                )
                if due:
                    notify(
                        f"Salomon X-ALP WOVEN {label} dostepny!",
                        f"Sztuk w magazynie: {stock}. {PRODUCT_URL}",
                    )
                    mark_notified()
                    if not REMIND_EVERY_HOURS:
                        log("Powiadomienie wysłane — kończę.")
                        return 0
            elif os.path.exists(STATE_FILE):
                os.remove(STATE_FILE)

        except KeyboardInterrupt:
            log("Przerwano.")
            return 0
        except Exception as exc:
            failures += 1
            log(f"BŁĄD ({failures}): {exc}")
            if failures >= 12:
                notify("Salomon watcher: awaria", f"12 błędów z rzędu. Ostatni: {exc}")
                return 1
            time.sleep(min(60 * failures, 900))
            continue

        if args.once:
            return 0
        time.sleep(INTERVAL_SECONDS + random.randint(0, JITTER_SECONDS))


if __name__ == "__main__":
    sys.exit(main())
