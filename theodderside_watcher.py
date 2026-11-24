#!/usr/bin/env python3
"""
Monitor dostępności: The Odder Side – KOSZULA FLANELOWA W KRATĘ RANCH BLUE, rozmiar S.

Sklep stoi na Shopify, więc stan wariantów jest w publicznym JSON-ie:
  https://theodderside.com/products/13503.js  ->  variants[].available

Tylko biblioteka standardowa Pythona – nic nie trzeba instalować.

Powiadomienia (działa każdy kanał, który ma ustawione zmienne środowiskowe):
  Telegram : TELEGRAM_TOKEN, TELEGRAM_CHAT_ID          (darmowe)
  SMS      : SMSAPI_TOKEN, SMS_TO (np. 48600111222), opcjonalnie SMSAPI_SENDER

Użycie:
  python3 theodderside_watcher.py                # jedno sprawdzenie (tryb dla GitHub Actions)
  python3 theodderside_watcher.py --test-notify  # wysyła testowe powiadomienie
"""
import json
import os
import sys
import urllib.parse
import urllib.request

PRODUCT_URL = "https://theodderside.com/products/13503"
VARIANT_ID = 57460917600631   # rozmiar S
SIZE = "S"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def log(msg):
    print(msg, flush=True)


def http(url, data=None, headers=None):
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read().decode("utf-8", "replace")


# ---------- powiadomienia ----------

def notify_telegram(text):
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return None
    http(f"https://api.telegram.org/bot{token}/sendMessage",
         {"chat_id": chat, "text": text, "disable_web_page_preview": "false"})
    return "Telegram"


def notify_sms(text):
    token, to = os.getenv("SMSAPI_TOKEN"), os.getenv("SMS_TO")
    if not (token and to):
        return None
    data = {"to": to, "message": text, "format": "json"}
    if os.getenv("SMSAPI_SENDER"):
        data["from"] = os.getenv("SMSAPI_SENDER")
    _, resp = http("https://api.smsapi.pl/sms.do", data,
                   {"Authorization": f"Bearer {token}"})
    if '"error"' in resp:
        raise RuntimeError(f"SMSAPI odrzuciło wiadomość: {resp}")
    return "SMS"


def notify(text, sms_text):
    sent, errors = [], []
    for fn, msg in ((notify_telegram, text), (notify_sms, sms_text)):
        try:
            ch = fn(msg)
            if ch:
                sent.append(ch)
        except Exception as exc:  # jeden kanał padł – próbujemy pozostałe
            errors.append(f"{fn.__name__}: {exc}")
    for e in errors:
        log(f"BŁĄD powiadomienia – {e}")
    if not sent:
        log("UWAGA: żaden kanał powiadomień nie zadziałał / nie jest skonfigurowany.")
    else:
        log(f"Powiadomienie wysłane: {', '.join(sent)}")
    return bool(sent)


def set_output(key, value):
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a") as f:
            f.write(f"{key}={value}\n")


# ---------- główna logika ----------

def main():
    if "--test-notify" in sys.argv:
        ok = notify("Test: monitor koszuli Odder Side działa ✅",
                    "Test: monitor koszuli Odder Side dziala.")
        return 0 if ok else 1

    status, body = http(PRODUCT_URL + ".js", headers={"Accept": "application/json"})
    product = json.loads(body)

    variants = product.get("variants", [])
    variant = next((v for v in variants if v.get("id") == VARIANT_ID), None) \
        or next((v for v in variants if str(v.get("title")).strip().upper() == SIZE), None)

    summary = ", ".join(f"{v.get('title')}={'TAK' if v.get('available') else 'nie'}" for v in variants)
    log(f"{product.get('title')} | dostępność: {summary}")

    if variant is None:
        # sklep zmienił warianty – lepiej się dowiedzieć niż czekać w nieskończoność
        raise SystemExit(f"Nie znaleziono wariantu {SIZE} (id {VARIANT_ID}). Sprawdź stronę ręcznie.")

    if not variant.get("available"):
        log(f"Rozmiar {SIZE}: niedostępny.")
        set_output("available", "false")
        return 0

    price = variant.get("price")
    price_txt = f"{price / 100:.0f} zł" if isinstance(price, (int, float)) else ""
    link = f"{PRODUCT_URL}?variant={variant['id']}"
    log(f"Rozmiar {SIZE}: DOSTĘPNY! {price_txt}")
    notify(f"🎉 Koszula Ranch Blue w rozmiarze {SIZE} jest dostępna! {price_txt}\n{link}",
           f"Koszula Ranch Blue rozmiar {SIZE} jest dostepna! {price_txt.replace('zł', 'zl')} {link}")
    set_output("available", "true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
