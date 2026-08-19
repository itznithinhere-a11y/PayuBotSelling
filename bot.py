import asyncio
import io
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta
from typing import Optional

import aiohttp
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# CONFIG & ENV
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PAYU_CLIENT_ID = os.getenv("PAYU_CLIENT_ID", "").strip()
PAYU_CLIENT_SECRET = os.getenv("PAYU_CLIENT_SECRET", "").strip()
PAYU_MERCHANT_ID = os.getenv("PAYU_MERCHANT_ID", "").strip()

# URLs (Default to Production)
PAYU_TOKEN_URL = "https://accounts.payu.in/oauth/token"
PAYU_PAYMENT_LINK_URL = "https://oneapi.payu.in/payment-links"

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0").strip()
WEBHOOK_PORT = int(os.getenv("WEBHOOK_PORT", "8080"))
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "").strip()
DB_PATH = os.getenv("DB_PATH", "orders.db").strip()

# Plans Configuration
PLANS = {
    "gold": {"name": "⚡ Gold Dark (CH 1)", "price": 1499, "description": "Gold Access"},
    "silver": {"name": "⚡ Silver Dark (CH 2)", "price": 1499, "description": "Silver Access"},
    "bronze": {"name": "⚡ Bronze Dark (CH 3)", "price": 1499, "description": "Bronze Access"},
    "iron": {"name": "⚡ Iron Dark (CH 4)", "price": 1499, "description": "Iron Access"},
}

# Globals
router = Router()
bot: Optional[Bot] = None
_payu_token = None
_payu_token_expires_at = 0

# ============================================================
# DATABASE HELPERS
# ============================================================
def db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with closing(db()) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS orders (
            reference_id TEXT PRIMARY KEY, user_id INTEGER, plan_key TEXT, 
            amount_paise INTEGER, txnid TEXT UNIQUE, status TEXT DEFAULT 'created',
            payment_id TEXT, invoice_number TEXT, payment_link_url TEXT,
            created_at INTEGER, paid_at INTEGER, access_sent INTEGER DEFAULT 0)""")
        conn.commit()

def save_order(ref_id, uid, pk, amt, tid):
    with closing(db()) as conn:
        conn.execute("INSERT INTO orders (reference_id, user_id, plan_key, amount_paise, txnid, created_at) VALUES (?,?,?,?,?,?)",
                     (ref_id, uid, pk, amt, tid, int(time.time())))
        conn.commit()

def update_order_link(ref_id, inv, url):
    with closing(db()) as conn:
        conn.execute("UPDATE orders SET invoice_number=?, payment_link_url=? WHERE reference_id=?", (inv, url, ref_id))
        conn.commit()

def get_order(ref_id):
    with closing(db()) as conn:
        row = conn.execute("SELECT * FROM orders WHERE reference_id = ?", (ref_id,)).fetchone()
        return dict(row) if row else None

# ============================================================
# PAYU API CORE
# ============================================================

async def get_payu_access_token():
    global _payu_token, _payu_token_expires_at
    if _payu_token and time.time() < _payu_token_expires_at - 60:
        return _payu_token

    payload = {
        "client_id": PAYU_CLIENT_ID,
        "client_secret": PAYU_CLIENT_SECRET,
        "grant_type": "client_credentials",
        "scope": "create_payment_links read_payment_links"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(PAYU_TOKEN_URL, data=payload) as resp:
            data = await resp.json()
            if resp.status != 200:
                print(f"Token Error: {data}")
                return None
            _payu_token = data['access_token']
            _payu_token_expires_at = time.time() + int(data['expires_in'])
            return _payu_token

async def create_payu_payment_link(user_id: int, plan_key: str, name: str):
    token = await get_payu_access_token()
    if not token: raise RuntimeError("Could not fetch PayU Token")

    plan = PLANS[plan_key]
    txnid = f"TXN{user_id}{int(time.time())}"
    ref_id = f"ORD{secrets.token_hex(6).upper()}"
    inv_num = f"INV{secrets.token_hex(5).upper()}"

    # Important: PayU OneAPI payload structure
    payload = {
        "subAmount": float(plan["price"]),
        "description": plan["description"],
        "invoiceNumber": inv_num,
        "expiryDate": (datetime.now() + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S"),
        "source": "API",
        "currency": "INR",
        "customer": {
            "name": name[:50],
            "email": f"user{user_id}@tgstore.com",
            "phone": "9876543210" # Placeholder required by some PayU accounts
        },
        "udf": {"udf1": str(user_id), "udf2": plan_key, "udf3": ref_id},
        "successURL": f"{PUBLIC_BASE_URL}/payu/success",
        "failureURL": f"{PUBLIC_BASE_URL}/payu/failure"
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "merchantId": PAYU_MERCHANT_ID,
        "Content-Type": "application/json"
    }

    async with aiohttp.ClientSession() as session:
        async with session.post(PAYU_PAYMENT_LINK_URL, json=payload, headers=headers) as resp:
            res_data = await resp.json()
            if resp.status != 200 or res_data.get('status') != 0:
                print(f"Link Creation Error: {res_data}")
                raise RuntimeError(res_data.get('message', 'API Error'))
            
            pay_link = res_data['result']['paymentLink']
            save_order(ref_id, user_id, plan_key, plan['price']*100, txnid)
            update_order_link(ref_id, inv_num, pay_link)
            return {"link": pay_link, "ref_id": ref_id, "price": plan['price']}

async def verify_payu_payment(invoice_num):
    token = await get_payu_access_token()
    url = f"{PAYU_PAYMENT_LINK_URL}/{invoice_num}/txns"
    headers = {"Authorization": f"Bearer {token}", "merchantId": PAYU_MERCHANT_ID}
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            if resp.status == 200 and data.get('result', {}).get('data'):
                # Check the first transaction in the list
                txn = data['result']['data'][0]
                if txn.get('status', '').lower() in ['success', 'captured']:
                    return True, txn.get('paymentId')
            return False, None

# ============================================================
# BOT HANDLERS
# ============================================================

@router.message(CommandStart())
async def cmd_start(msg: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=v['name'], callback_data=f"buy:{k}")] for k,v in PLANS.items()
    ])
    await msg.answer("👋 **Welcome to Dark Store!**\nChoose a plan to continue:", reply_markup=kb)

@router.callback_query(F.data.startswith("buy:"))
async def handle_buy(call: CallbackQuery):
    plan_key = call.data.split(":")[1]
    await call.answer("Generating payment link...")
    try:
        res = await create_payu_payment_link(call.from_user.id, plan_key, call.from_user.first_name or "User")
        text = (f"💳 **Payment details**\n\nPlan: {PLANS[plan_key]['name']}\nAmount: ₹{res['price']}\n\n"
                "Pay using the button below and then click Verify.")
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Pay Now", url=res['link'])],
            [InlineKeyboardButton(text="🔄 Verify Payment", callback_data=f"verify:{res['ref_id']}")]
        ])
        await call.message.edit_text(text, reply_markup=kb)
    except Exception as e:
        await call.message.answer(f"❌ Error: {str(e)}")

@router.callback_query(F.data.startswith("verify:"))
async def handle_verify(call: CallbackQuery):
    ref_id = call.data.split(":")[1]
    order = get_order(ref_id)
    if not order: return await call.answer("Order not found!")
    
    await call.answer("Checking status...")
    is_paid, pay_id = await verify_payu_payment(order['invoice_number'])
    
    if is_paid:
        with closing(db()) as conn:
            conn.execute("UPDATE orders SET status='paid', payment_id=? WHERE reference_id=?", (pay_id, ref_id))
            conn.commit()
        await call.message.edit_text(f"✅ **Payment Confirmed!**\nTxn ID: `{pay_id}`\n\nEnjoy your access!")
    else:
        await call.answer("❌ Payment not found yet. Try after 1 minute.", show_alert=True)

# ============================================================
# RUNNER
# ============================================================

async def main():
    global bot
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)
    
    # Optional Web Server for redirects
    app = web.Application()
    app.router.add_get("/payu/success", lambda r: web.Response(text="Success! Return to Telegram."))
    app.router.add_get("/payu/failure", lambda r: web.Response(text="Failed! Try again."))
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBHOOK_HOST, WEBHOOK_PORT)
    await site.start()

    print("Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
