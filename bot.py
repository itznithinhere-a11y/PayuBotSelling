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
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    CallbackQuery,
)

from dotenv import load_dotenv


# ============================================================
# ENV
# ============================================================

load_dotenv()


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "",
).strip()


# ------------------------------------------------------------
# PAYU PAYMENT LINK API
# ------------------------------------------------------------

PAYU_CLIENT_ID = os.getenv(
    "PAYU_CLIENT_ID",
    "",
).strip()

PAYU_CLIENT_SECRET = os.getenv(
    "PAYU_CLIENT_SECRET",
    "",
).strip()

PAYU_MERCHANT_ID = os.getenv(
    "PAYU_MERCHANT_ID",
    "",
).strip()


# Production OAuth endpoint
PAYU_TOKEN_URL = os.getenv(
    "PAYU_TOKEN_URL",
    "https://accounts.payu.in/oauth/token",
).strip()


# Production Payment Link endpoint
PAYU_PAYMENT_LINK_URL = os.getenv(
    "PAYU_PAYMENT_LINK_URL",
    "https://partner.payu.in/payment-links",
).strip()


# ------------------------------------------------------------
# WEB SERVER
# ------------------------------------------------------------

WEBHOOK_HOST = os.getenv(
    "WEBHOOK_HOST",
    "0.0.0.0",
).strip()


WEBHOOK_PORT = int(
    os.getenv(
        "WEBHOOK_PORT",
        "8080",
    )
)


PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "",
).rstrip("/")


# ------------------------------------------------------------
# SUPPORT
# ------------------------------------------------------------

SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "",
).strip()


# ------------------------------------------------------------
# DATABASE
# ------------------------------------------------------------

DB_PATH = os.getenv(
    "DB_PATH",
    "orders.db",
).strip()


# ============================================================
# PLANS
# ============================================================

PLANS = {

    "gold": {
        "name": "⚡ Gold Dark (Channel 1)",

        "price": 1499,

        "description": (
            "Gold Dark — Lifetime Access"
        ),

        "channel_id": os.getenv(
            "GOLD_CHANNEL_ID",
            "",
        ).strip(),

        "access_link": os.getenv(
            "GOLD_ACCESS_LINK",
            "",
        ).strip(),
    },


    "silver": {
        "name": "⚡ Silver Dark (Channel 2)",

        "price": 1499,

        "description": (
            "Silver Dark — Lifetime Access"
        ),

        "channel_id": os.getenv(
            "SILVER_CHANNEL_ID",
            "",
        ).strip(),

        "access_link": os.getenv(
            "SILVER_ACCESS_LINK",
            "",
        ).strip(),
    },


    "bronze": {
        "name": "⚡ Bronze Dark (Channel 3)",

        "price": 1499,

        "description": (
            "Bronze Dark — Lifetime Access"
        ),

        "channel_id": os.getenv(
            "BRONZE_CHANNEL_ID",
            "",
        ).strip(),

        "access_link": os.getenv(
            "BRONZE_ACCESS_LINK",
            "",
        ).strip(),
    },


    "iron": {
        "name": "⚡ Iron Dark (Channel 4)",

        "price": 1499,

        "description": (
            "Iron Dark — Lifetime Access"
        ),

        "channel_id": os.getenv(
            "IRON_CHANNEL_ID",
            "",
        ).strip(),

        "access_link": os.getenv(
            "IRON_ACCESS_LINK",
            "",
        ).strip(),
    },
}


# ============================================================
# GLOBALS
# ============================================================

router = Router()

bot: Optional[Bot] = None


# ============================================================
# ERROR ID
# ============================================================

def generate_error_id() -> str:

    return (
        f"E-{int(time.time())}-"
        f"{secrets.token_hex(2).upper()}"
    )


def log_error(
    error_id: str,
    title: str,
    details=None,
):

    print()
    print("=" * 70)
    print(f"ERROR [{error_id}]")
    print("=" * 70)

    print(
        "TITLE:",
        title,
    )

    if details is not None:

        print(
            "DETAILS:"
        )

        if isinstance(
            details,
            (dict, list),
        ):

            try:

                print(
                    json.dumps(
                        details,
                        indent=2,
                        ensure_ascii=False,
                    )
                )

            except Exception:

                print(
                    repr(details)
                )

        else:

            print(
                details
            )

    print("=" * 70)
    print()


# ============================================================
# DATABASE
# ============================================================

def db():

    conn = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    with closing(db()) as conn:

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (

                reference_id TEXT PRIMARY KEY,

                user_id INTEGER NOT NULL,

                plan_key TEXT NOT NULL,

                amount_paise INTEGER NOT NULL,

                txnid TEXT UNIQUE NOT NULL,

                status TEXT NOT NULL DEFAULT 'created',

                payment_id TEXT,

                mihpayid TEXT,

                payment_link_id TEXT,

                payment_link_url TEXT,

                created_at INTEGER NOT NULL,

                paid_at INTEGER,

                access_sent INTEGER NOT NULL DEFAULT 0
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (

                event_id TEXT PRIMARY KEY,

                created_at INTEGER NOT NULL
            )
            """
        )

        conn.commit()


# ============================================================
# SAVE ORDER
# ============================================================

def save_order(
    reference_id: str,
    user_id: int,
    plan_key: str,
    amount_paise: int,
    txnid: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            INSERT INTO orders
            (
                reference_id,
                user_id,
                plan_key,
                amount_paise,
                txnid,
                status,
                created_at
            )

            VALUES
            (
                ?,
                ?,
                ?,
                ?,
                ?,
                'created',
                ?
            )
            """,

            (
                reference_id,
                user_id,
                plan_key,
                amount_paise,
                txnid,
                int(time.time()),
            ),
        )

        conn.commit()


# ============================================================
# GET ORDER
# ============================================================

def get_order(
    reference_id: str,
):

    with closing(db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE reference_id = ?
            """,

            (
                reference_id,
            ),
        ).fetchone()

        return (
            dict(row)
            if row
            else None
        )


# ============================================================
# GET ORDER BY TXNID
# ============================================================

def get_order_by_txnid(
    txnid: str,
):

    with closing(db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE txnid = ?
            """,

            (
                txnid,
            ),
        ).fetchone()

        return (
            dict(row)
            if row
            else None
        )


# ============================================================
# LATEST ORDER
# ============================================================

def get_latest_order(
    user_id: int,
):

    with closing(db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE user_id = ?

            ORDER BY created_at DESC

            LIMIT 1
            """,

            (
                user_id,
            ),
        ).fetchone()

        return (
            dict(row)
            if row
            else None
        )


# ============================================================
# MARK PAID
# ============================================================

def mark_paid(
    reference_id: str,
    payment_id: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET
                status = 'paid',
                payment_id = ?,
                mihpayid = ?,
                paid_at = ?

            WHERE reference_id = ?
            """,

            (
                payment_id,
                payment_id,
                int(time.time()),
                reference_id,
            ),
        )

        conn.commit()


# ============================================================
# MARK FAILED
# ============================================================

def mark_failed(
    reference_id: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET status = 'failed'

            WHERE reference_id = ?
            """,

            (
                reference_id,
            ),
        )

        conn.commit()


# ============================================================
# MARK ACCESS SENT
# ============================================================

def mark_access_sent(
    reference_id: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET access_sent = 1

            WHERE reference_id = ?
            """,

            (
                reference_id,
            ),
        )

        conn.commit()


# ============================================================
# SAVE PAYMENT LINK
# ============================================================

def save_payment_link(
    reference_id: str,
    payment_link_id: str,
    payment_link_url: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET
                payment_link_id = ?,
                payment_link_url = ?

            WHERE reference_id = ?
            """,

            (
                payment_link_id,
                payment_link_url,
                reference_id,
            ),
        )

        conn.commit()


# ============================================================
# EVENTS
# ============================================================

def event_already_processed(
    event_id: str,
) -> bool:

    if not event_id:

        return False

    with closing(db()) as conn:

        row = conn.execute(
            """
            SELECT 1

            FROM processed_events

            WHERE event_id = ?
            """,

            (
                event_id,
            ),
        ).fetchone()

        return bool(row)


def save_event(
    event_id: str,
):

    if not event_id:

        return

    with closing(db()) as conn:

        conn.execute(
            """
            INSERT OR IGNORE INTO processed_events
            (
                event_id,
                created_at
            )

            VALUES (?, ?)
            """,

            (
                event_id,
                int(time.time()),
            ),
        )

        conn.commit()


# ============================================================
# PAYU OAUTH TOKEN
# ============================================================

async def get_payu_access_token():

    error_id = generate_error_id()

    if not PAYU_CLIENT_ID:

        raise RuntimeError(
            f"{error_id}|PAYU_CLIENT_ID missing."
        )

    if not PAYU_CLIENT_SECRET:

        raise RuntimeError(
            f"{error_id}|PAYU_CLIENT_SECRET missing."
        )

    payload = {

        "client_id":
            PAYU_CLIENT_ID,

        "client_secret":
            PAYU_CLIENT_SECRET,

        "grant_type":
            "client_credentials",

        "scope":
            "create_payment_links",
    }


    timeout = aiohttp.ClientTimeout(
        total=30
    )


    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                PAYU_TOKEN_URL,

                data=payload,

                headers={
                    "Content-Type":
                        "application/x-www-form-urlencoded",

                    "Accept":
                        "application/json",
                },
            ) as response:

                text = await response.text()

                print(
                    "PAYU TOKEN HTTP:",
                    response.status,
                )

                print(
                    "PAYU TOKEN RESPONSE:",
                    text[:5000],
                )


                if response.status >= 400:

                    raise RuntimeError(
                        f"{error_id}|"
                        f"PayU token HTTP "
                        f"{response.status}: "
                        f"{text[:1000]}"
                    )


                try:

                    result = json.loads(
                        text
                    )

                except Exception:

                    raise RuntimeError(
                        f"{error_id}|"
                        "PayU token returned invalid JSON."
                    )


                access_token = result.get(
                    "access_token"
                )


                if not access_token:

                    raise RuntimeError(
                        f"{error_id}|"
                        "PayU access token missing."
                    )


                return access_token


    except asyncio.TimeoutError as e:

        log_error(
            error_id,
            "PayU token timeout",
            repr(e),
        )

        raise RuntimeError(
            f"{error_id}|"
            "PayU token request timed out."
        ) from e


    except aiohttp.ClientError as e:

        log_error(
            error_id,
            "PayU token network error",
            repr(e),
        )

        raise RuntimeError(
            f"{error_id}|"
            "PayU token network error."
        ) from e


# ============================================================
# CREATE PAYU PAYMENT LINK
# ============================================================

async def create_payu_payment_link(
    user_id: int,
    plan_key: str,
    firstname: str,
):

    error_id = generate_error_id()


    if plan_key not in PLANS:

        raise RuntimeError(
            f"{error_id}|Invalid plan."
        )


    if not PAYU_MERCHANT_ID:

        raise RuntimeError(
            f"{error_id}|PAYU_MERCHANT_ID missing."
        )


    plan = PLANS[
        plan_key
    ]


    # --------------------------------------------------------
    # UNIQUE ORDER IDs
    # --------------------------------------------------------

    txnid = (
        f"TG"
        f"{user_id}"
        f"{int(time.time())}"
        f"{secrets.token_hex(5)}"
    )


    reference_id = (
        f"ORD_"
        f"{secrets.token_hex(12)}"
    )


    invoice_number = (
        f"INV"
        f"{int(time.time())}"
        f"{secrets.token_hex(4).upper()}"
    )


    # --------------------------------------------------------
    # SAVE ORDER FIRST
    # --------------------------------------------------------

    save_order(
        reference_id=reference_id,

        user_id=user_id,

        plan_key=plan_key,

        amount_paise=(
            plan["price"] * 100
        ),

        txnid=txnid,
    )


    # --------------------------------------------------------
    # GET TOKEN
    # --------------------------------------------------------

    access_token = (
        await get_payu_access_token()
    )


    # --------------------------------------------------------
    # PAYMENT LINK EXPIRY
    # --------------------------------------------------------

    expiry = (
        datetime.now()
        + timedelta(minutes=30)
    )

    expiry_date = expiry.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    firstname = (
        firstname
        or "Customer"
    )


    # --------------------------------------------------------
    # PAYMENT LINK PAYLOAD
    # --------------------------------------------------------

    payload = {

        "invoiceNumber":
            invoice_number,

        "isAmountFilledByCustomer":
            False,

        "subAmount":
            plan["price"],

        "description":
            plan["description"],

        "source":
            "API",

        "isPartialPaymentAllowed":
            False,

        "currency":
            "INR",

        "maxPaymentsAllowed":
            1,

        "expiryDate":
            expiry_date,

        "isActive":
            True,

        "customer": {

            "name":
                firstname,

            "phone":
                "9999999999",

            "email":
                f"telegram{user_id}@example.com",
        },

        "udf": {

            "udf1":
                str(user_id),

            "udf2":
                plan_key,

            "udf3":
                reference_id,

            "udf4":
                txnid,

            "udf5":
                "",
        },

        "notes":
            f"Telegram User: {user_id}",

        "viaEmail":
            False,

        "viaSms":
            False,
    }


    # --------------------------------------------------------
    # DEBUG
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("PAYU PAYMENT LINK REQUEST")
    print("=" * 70)

    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
    )

    print("=" * 70)
    print()


    timeout = aiohttp.ClientTimeout(
        total=30
    )


    # --------------------------------------------------------
    # API REQUEST
    # --------------------------------------------------------

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                PAYU_PAYMENT_LINK_URL,

                json=payload,

                headers={

                    "merchantId":
                        PAYU_MERCHANT_ID,

                    "Authorization":
                        f"Bearer {access_token}",

                    "Content-Type":
                        "application/json",

                    "Accept":
                        "application/json",
                },
            ) as response:

                http_status = response.status

                raw_text = await response.text()


    except asyncio.TimeoutError as e:

        log_error(
            error_id,
            "PayU payment link timeout",
            repr(e),
        )

        mark_failed(
            reference_id
        )

        raise RuntimeError(
            f"{error_id}|"
            "PayU payment link request timed out."
        ) from e


    except aiohttp.ClientError as e:

        log_error(
            error_id,
            "PayU payment link network error",
            repr(e),
        )

        mark_failed(
            reference_id
        )

        raise RuntimeError(
            f"{error_id}|"
            "Network error while connecting to PayU."
        ) from e


    # --------------------------------------------------------
    # RAW RESPONSE
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("PAYU PAYMENT LINK RESPONSE")
    print("=" * 70)

    print(
        "HTTP STATUS:",
        http_status,
    )

    print(
        raw_text[:10000]
    )

    print("=" * 70)
    print()


    if http_status >= 400:

        log_error(
            error_id,
            f"PayU HTTP {http_status}",
            raw_text[:5000],
        )

        mark_failed(
            reference_id
        )

        raise RuntimeError(
            f"{error_id}|"
            f"PayU HTTP {http_status}: "
            f"{raw_text[:1000]}"
        )


    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    try:

        result = json.loads(
            raw_text
        )

    except Exception as e:

        mark_failed(
            reference_id
        )

        raise RuntimeError(
            f"{error_id}|"
            "PayU returned invalid JSON."
        ) from e


    print(
        "PAYU JSON:"
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )


    # --------------------------------------------------------
    # RESULT
    # --------------------------------------------------------

    result_data = result.get(
        "result"
    )


    if not isinstance(
        result_data,
        dict,
    ):

        result_data = {}


    payment_link = result_data.get(
        "paymentLink"
    )


    if not payment_link:

        message = (
            result.get("message")
            or "PayU payment link was not generated."
        )


        log_error(
            error_id,
            "Payment link missing",
            result,
        )


        mark_failed(
            reference_id
        )


        raise RuntimeError(
            f"{error_id}|{message}"
        )


    payment_link_id = (
        result_data.get(
            "invoiceNumber"
        )
        or invoice_number
    )


    # --------------------------------------------------------
    # SAVE LINK
    # --------------------------------------------------------

    save_payment_link(
        reference_id=reference_id,

        payment_link_id=payment_link_id,

        payment_link_url=payment_link,
    )


    return {

        "reference_id":
            reference_id,

        "txnid":
            txnid,

        "invoice_number":
            invoice_number,

        "payment_link":
            payment_link,

        "payment_link_id":
            payment_link_id,

        "amount":
            plan["price"],
    }


# ============================================================
# TELEGRAM UI
# ============================================================

def support_url():

    username = (
        SUPPORT_USERNAME
        .lstrip("@")
        .strip()
    )


    if not username:

        return None


    return (
        "https://t.me/"
        + username
    )


# ============================================================
# MAIN KEYBOARD
# ============================================================

def main_keyboard():

    buttons = [

        [
            InlineKeyboardButton(
                text="⚡ Gold Dark (Channel 1)",
                callback_data="plan:gold",
            )
        ],

        [
            InlineKeyboardButton(
                text="⚡ Silver Dark (Channel 2)",
                callback_data="plan:silver",
            )
        ],

        [
            InlineKeyboardButton(
                text="⚡ Bronze Dark (Channel 3)",
                callback_data="plan:bronze",
            )
        ],

        [
            InlineKeyboardButton(
                text="⚡ Iron Dark (Channel 4)",
                callback_data="plan:iron",
            )
        ],

        [
            InlineKeyboardButton(
                text="📋 My Plan",
                callback_data="myplan",
            )
        ],
    ]


    support = support_url()


    if support:

        buttons.append(

            [
                InlineKeyboardButton(
                    text="📞 Support",
                    url=support,
                )
            ]

        )


    return InlineKeyboardMarkup(
        inline_keyboard=buttons
    )


# ============================================================
# PLAN KEYBOARD
# ============================================================

def plan_keyboard(
    plan_key: str,
):

    plan = PLANS[
        plan_key
    ]


    return InlineKeyboardMarkup(

        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text=(
                        f"💳 Pay ₹"
                        f"{plan['price']}"
                        f" with PayU"
                    ),

                    callback_data=(
                        f"buy:{plan_key}"
                    ),
                )
            ],

            [
                InlineKeyboardButton(
                    text="↩️ Back",
                    callback_data="home",
                )
            ],
        ]

    )


# ============================================================
# HOME
# ============================================================

async def send_home(
    chat_id: int,
):

    support = (
        SUPPORT_USERNAME
        if SUPPORT_USERNAME
        else "Contact admin"
    )


    text = (

        "👋 <b>Welcome to DARK STORE!</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        "<blockquote>"

        "<b>Available Channels:</b>\n"

        "⚡ Gold Dark (Channel 1)\n"

        "⚡ Silver Dark (Channel 2)\n"

        "⚡ Bronze Dark (Channel 3)\n"

        "⚡ Iron Dark (Channel 4)"

        "</blockquote>\n\n"

        "💳 Secure payment powered by PayU\n\n"

        "━━━━━━━━━━━━━━━━━━\n"

        f"💬 Support: {support}"
    )


    await bot.send_message(

        chat_id,

        text,

        reply_markup=main_keyboard(),
    )


# ============================================================
# START
# ============================================================

@router.message(
    CommandStart()
)
async def start_handler(
    message: Message,
):

    await send_home(
        message.chat.id
    )


# ============================================================
# HOME CALLBACK
# ============================================================

@router.callback_query(
    F.data == "home"
)
async def home_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    await send_home(
        callback.message.chat.id
    )


# ============================================================
# PLAN
# ============================================================

@router.callback_query(
    F.data.startswith("plan:")
)
async def plan_callback(
    callback: CallbackQuery,
):

    await callback.answer()


    plan_key = callback.data.split(
        ":",
        1,
    )[1]


    if plan_key not in PLANS:

        await callback.message.answer(
            "❌ Invalid plan."
        )

        return


    plan = PLANS[
        plan_key
    ]


    text = (

        f"<b>{plan['name']}</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 Price: "
        f"<b>₹{plan['price']}</b>\n\n"

        "🔐 Lifetime Access\n"

        "💳 Secure PayU Payment Link\n\n"

        "Click below to continue."
    )


    await callback.message.answer(

        text,

        reply_markup=plan_keyboard(
            plan_key
        ),
    )


# ============================================================
# BUY / CREATE PAYMENT LINK
# ============================================================

@router.callback_query(
    F.data.startswith("buy:")
)
async def buy_callback(
    callback: CallbackQuery,
):

    await callback.answer(
        "Creating PayU payment link..."
    )


    plan_key = callback.data.split(
        ":",
        1,
    )[1]


    if plan_key not in PLANS:

        await callback.message.answer(
            "❌ Invalid plan."
        )

        return


    try:

        result = (
            await create_payu_payment_link(

                user_id=(
                    callback.from_user.id
                ),

                plan_key=plan_key,

                firstname=(
                    callback.from_user.first_name
                    or "Customer"
                ),
            )
        )


    except Exception as e:

        error_text = str(e)


        print(
            "FINAL PAYMENT LINK ERROR:",
            repr(e),
        )


        if "|" in error_text:

            error_id, reason = (
                error_text.split(
                    "|",
                    1,
                )
            )

        else:

            error_id = (
                generate_error_id()
            )

            reason = (
                "Unexpected payment link error."
            )


            log_error(
                error_id,
                "Unhandled payment link exception",
                repr(e),
            )


        await callback.message.answer(

            "❌ <b>Payment Link generate nahi ho paya.</b>\n\n"

            "PayU se payment link create karte waqt "
            "problem aayi.\n\n"

            f"🔎 Reason: "
            f"<code>{reason}</code>\n\n"

            f"🧾 Error ID: "
            f"<code>{error_id}</code>\n\n"

            "Agar problem baar-baar aa rahi hai "
            "to ye Error ID support ko bhejein."
        )

        return


    plan = PLANS[
        plan_key
    ]


    # --------------------------------------------------------
    # PAYMENT LINK UI
    # --------------------------------------------------------

    text = (

        "💳 <b>PAYU PAYMENT</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Amount: "
        f"<b>₹{plan['price']}</b>\n\n"

        "🔐 <b>Secure PayU Checkout</b>\n\n"

        "Neeche <b>Pay Now</b> button dabakar "
        "PayU checkout page open karein.\n\n"

        f"🧾 Order ID:\n"
        f"<code>{result['txnid']}</code>\n\n"

        "⏱️ Payment link validity: "
        "<b>30 minutes</b>\n\n"

        "Payment complete hone ke baad "
        "<b>Verify Payment</b> dabayein."
    )


    keyboard = InlineKeyboardMarkup(

        inline_keyboard=[

            [
                InlineKeyboardButton(

                    text=(
                        f"💳 Pay ₹"
                        f"{plan['price']}"
                        f" Now"
                    ),

                    url=(
                        result[
                            "payment_link"
                        ]
                    ),
                )
            ],

            [
                InlineKeyboardButton(

                    text="🔄 Verify Payment",

                    callback_data=(
                        "verify:"
                        + result[
                            "reference_id"
                        ]
                    ),
                )
            ],

            [
                InlineKeyboardButton(
                    text="📋 My Plan",
                    callback_data="myplan",
                )
            ],

            [
                InlineKeyboardButton(
                    text="↩️ Back",
                    callback_data="home",
                )
            ],
        ]
    )


    await callback.message.answer(
        text,
        reply_markup=keyboard,
    )


# ============================================================
# MAKE ACCESS LINK
# ============================================================

async def make_access_link(
    plan_key: str,
) -> Optional[str]:

    plan = PLANS[
        plan_key
    ]


    # --------------------------------------------------------
    # ONE TIME TELEGRAM INVITE
    # --------------------------------------------------------

    if plan["channel_id"]:

        try:

            invite = (
                await bot.create_chat_invite_link(

                    chat_id=(
                        plan["channel_id"]
                    ),

                    member_limit=1,
                )
            )


            return invite.invite_link


        except Exception as e:

            print(
                "Invite link creation failed:",
                repr(e),
            )


    # --------------------------------------------------------
    # STATIC FALLBACK
    # --------------------------------------------------------

    if plan["access_link"]:

        return plan[
            "access_link"
        ]


    return None


# ============================================================
# DELIVER ACCESS
# ============================================================

async def deliver_access(
    order: dict,
):

    if order.get(
        "access_sent"
    ):

        return True


    access_link = (
        await make_access_link(
            order["plan_key"]
        )
    )


    if not access_link:

        support = (
            SUPPORT_USERNAME
            or "admin"
        )


        await bot.send_message(

            order["user_id"],

            "✅ <b>Payment confirmed!</b>\n\n"

            "Lekin access link configure nahi hai.\n\n"

            f"📞 Support: {support}",
        )


        return False


    plan = PLANS[
        order["plan_key"]
    ]


    await bot.send_message(

        order["user_id"],

        "🎉 <b>Payment Confirmed!</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Paid: "
        f"<b>₹{plan['price']}</b>\n"

        f"🧾 Transaction ID:\n"
        f"<code>{order['txnid']}</code>\n\n"

        "🔗 <b>Your Access Link:</b>\n"

        f"{access_link}\n\n"

        "⚠️ Link ko kisi ke saath share mat karein."
    )


    mark_access_sent(
        order["reference_id"]
    )


    return True


# ============================================================
# VERIFY PAYMENT LINK
# ============================================================

async def get_payment_link_status(
    invoice_number: str,
):

    error_id = generate_error_id()


    # --------------------------------------------------------
    # GET TOKEN WITH READ SCOPE
    # --------------------------------------------------------

    payload = {

        "client_id":
            PAYU_CLIENT_ID,

        "client_secret":
            PAYU_CLIENT_SECRET,

        "grant_type":
            "client_credentials",

        "scope":
            "read_payment_links",
    }


    timeout = aiohttp.ClientTimeout(
        total=30
    )


    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(

                PAYU_TOKEN_URL,

                data=payload,

                headers={
                    "Content-Type":
                        "application/x-www-form-urlencoded",

                    "Accept":
                        "application/json",
                },

            ) as response:

                token_text = (
                    await response.text()
                )


                if response.status >= 400:

                    raise RuntimeError(
                        f"{error_id}|"
                        f"PayU token HTTP "
                        f"{response.status}: "
                        f"{token_text[:1000]}"
                    )


                token_data = json.loads(
                    token_text
                )


                access_token = (
                    token_data.get(
                        "access_token"
                    )
                )


                if not access_token:

                    raise RuntimeError(
                        f"{error_id}|"
                        "PayU read token missing."
                    )


            # ------------------------------------------------
            # GET PAYMENT LINK
            # ------------------------------------------------

            url = (
                PAYU_PAYMENT_LINK_URL.rstrip("/")
                + "/"
                + invoice_number
            )


            async with session.get(

                url,

                headers={

                    "merchantId":
                        PAYU_MERCHANT_ID,

                    "Authorization":
                        f"Bearer {access_token}",

                    "Accept":
                        "application/json",
                },

            ) as response:

                text = (
                    await response.text()
                )


                print(
                    "PAYU LINK STATUS HTTP:",
                    response.status,
                )


                print(
                    "PAYU LINK STATUS:",
                    text[:10000],
                )


                if response.status >= 400:

                    raise RuntimeError(
                        f"{error_id}|"
                        f"PayU status HTTP "
                        f"{response.status}: "
                        f"{text[:1000]}"
                    )


                return json.loads(
                    text
                )


    except asyncio.TimeoutError as e:

        raise RuntimeError(
            f"{error_id}|"
            "PayU status request timed out."
        ) from e


    except aiohttp.ClientError as e:

        raise RuntimeError(
            f"{error_id}|"
            f"PayU status network error: {e}"
        ) from e


# ============================================================
# PROCESS PAYMENT
# ============================================================

async def process_payment_link_success(
    order: dict,
    payment_details: dict,
):

    # --------------------------------------------------------
    # AMOUNT VALIDATION
    # --------------------------------------------------------

    received_amount = (
        payment_details.get(
            "totalAmount"
        )
        or payment_details.get(
            "subAmount"
        )
    )


    if received_amount is not None:

        try:

            expected_amount = (
                order["amount_paise"]
                / 100
            )


            received_amount = float(
                received_amount
            )


            if abs(
                received_amount
                - expected_amount
            ) > 0.01:

                print(
                    "PAYMENT LINK AMOUNT MISMATCH:",
                    received_amount,
                    expected_amount,
                )

                return False


        except Exception:

            return False


    # --------------------------------------------------------
    # MARK PAID
    # --------------------------------------------------------

    payment_id = (

        payment_details.get(
            "mihpayid"
        )

        or payment_details.get(
            "paymentId"
        )

        or payment_details.get(
            "transactionId"
        )

        or order["txnid"]
    )


    if order["status"] != "paid":

        mark_paid(

            order["reference_id"],

            str(payment_id),
        )


    updated = get_order(
        order["reference_id"]
    )


    if updated:

        try:

            await deliver_access(
                updated
            )

        except Exception as e:

            print(
                "Access delivery failed:",
                repr(e),
            )


    return True


# ============================================================
# VERIFY BUTTON
# ============================================================

@router.callback_query(
    F.data.startswith("verify:")
)
async def verify_callback(
    callback: CallbackQuery,
):

    await callback.answer(
        "Checking PayU payment..."
    )


    reference_id = (
        callback.data.split(
            ":",
            1,
        )[1]
    )


    order = get_order(
        reference_id
    )


    if not order:

        await callback.message.answer(
            "❌ Order not found."
        )

        return


    if (
        order["user_id"]
        != callback.from_user.id
    ):

        await callback.message.answer(
            "❌ Invalid order."
        )

        return


    if order["status"] == "paid":

        await callback.message.answer(
            "✅ Payment already confirmed.\n\n"
            "Use /myplan for access."
        )

        return


    if not order.get(
        "payment_link_id"
    ):

        await callback.message.answer(
            "❌ Payment link information missing."
        )

        return


    try:

        result = (
            await get_payment_link_status(

                order[
                    "payment_link_id"
                ]
            )
        )


        print(
            "PAYU PAYMENT LINK VERIFY:",
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            ),
        )


        result_data = result.get(
            "result"
        )


        if not isinstance(
            result_data,
            dict,
        ):

            result_data = {}


        # ----------------------------------------------------
        # PAYMENT LINK STATUS
        # ----------------------------------------------------

        status = (

            result_data.get(
                "status"
            )

            or result_data.get(
                "paymentStatus"
            )

            or result.get(
                "status"
            )
        )


        status_text = str(
            status
            or ""
        ).lower()


        # ----------------------------------------------------
        # AMOUNT
        # ----------------------------------------------------

        total_collected = (
            result_data.get(
                "totalAmountCollected"
            )
        )


        if total_collected is not None:

            try:

                expected = (
                    order["amount_paise"]
                    / 100
                )

                collected = float(
                    total_collected
                )


                if (
                    collected > 0
                    and abs(
                        collected
                        - expected
                    ) > 0.01
                ):

                    await callback.message.answer(

                        "❌ <b>Amount mismatch detected.</b>\n\n"

                        f"Expected: ₹{expected:.2f}\n"

                        f"Received: ₹{collected:.2f}\n\n"

                        "Payment manually verify karwana padega."
                    )

                    return


            except Exception:

                pass


        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        success_statuses = {

            "paid",
            "success",
            "successful",
            "completed",
            "complete",
        }


        if status_text in success_statuses:

            success = (
                await process_payment_link_success(

                    order,

                    result_data,
                )
            )


            if success:

                await callback.message.answer(

                    "🎉 <b>Payment Confirmed!</b>\n\n"

                    "Aapka payment successfully verify ho gaya hai.\n\n"

                    "🔗 Access link Telegram par "
                    "send kar diya gaya hai.\n\n"

                    "Use <b>/myplan</b> anytime."
                )

            else:

                await callback.message.answer(

                    "❌ Payment verification mein "
                    "amount validation failed."
                )


            return


        # ----------------------------------------------------
        # FAILED
        # ----------------------------------------------------

        if status_text in {

            "failed",
            "failure",
            "cancelled",
            "canceled",
            "expired",
            "inactive",
        }:

            mark_failed(
                order[
                    "reference_id"
                ]
            )


            await callback.message.answer(

                "❌ <b>Payment failed/expired.</b>\n\n"

                "Naya payment link generate karne ke "
                "liye plan dobara select karein."
            )


            return


        # ----------------------------------------------------
        # PENDING
        # ----------------------------------------------------

        await callback.message.answer(

            "⏳ <b>Payment abhi confirm nahi hua.</b>\n\n"

            f"PayU Status: "
            f"<code>{status or 'pending'}</code>\n\n"

            "Agar payment abhi-abhi kiya hai "
            "to 10–30 seconds wait karke "
            "dobara Verify Payment dabayein."
        )


    except Exception as e:

        error_id = generate_error_id()


        log_error(
            error_id,
            "Payment verification failed",
            repr(e),
        )


        await callback.message.answer(

            "❌ <b>PayU verification failed.</b>\n\n"

            f"🧾 Error ID: "
            f"<code>{error_id}</code>\n\n"

            "Thodi der baad dobara try karein."
        )


# ============================================================
# MY PLAN
# ============================================================

@router.callback_query(
    F.data == "myplan"
)
async def myplan_callback(
    callback: CallbackQuery,
):

    await callback.answer()

    await send_my_plan(
        callback.message
    )


@router.message(
    Command("myplan")
)
async def myplan_message(
    message: Message,
):

    await send_my_plan(
        message
    )


async def send_my_plan(
    message: Message,
):

    order = get_latest_order(
        message.from_user.id
    )


    if not order:

        await message.answer(

            "📋 <b>My Plan</b>\n\n"

            "Aapka koi order nahi mila.",

            reply_markup=main_keyboard(),
        )

        return


    plan = PLANS.get(
        order["plan_key"],
        {},
    )


    status = order[
        "status"
    ].upper()


    # --------------------------------------------------------
    # PAID
    # --------------------------------------------------------

    if order["status"] == "paid":

        text = (

            "📋 <b>My Plan</b>\n"

            "━━━━━━━━━━━━━━━━━━\n\n"

            f"📦 Plan: "
            f"<b>"
            f"{plan.get(
                'name',
                order['plan_key']
            )}"
            f"</b>\n"

            f"💰 Amount: "
            f"<b>"
            f"₹{order['amount_paise'] // 100}"
            f"</b>\n"

            "📌 Status: <b>PAID</b>\n"

            f"🧾 Transaction: "
            f"<code>{order['txnid']}</code>\n\n"

            "Access dobara lene ke liye "
            "button dabayein."
        )


        keyboard = InlineKeyboardMarkup(

            inline_keyboard=[

                [
                    InlineKeyboardButton(

                        text="🔗 Send Access Link",

                        callback_data=(
                            "access:"
                            + order[
                                "reference_id"
                            ]
                        ),
                    )
                ],

                [
                    InlineKeyboardButton(
                        text="↩️ Back",
                        callback_data="home",
                    )
                ],
            ]
        )


        await message.answer(

            text,

            reply_markup=keyboard,
        )


        return


    # --------------------------------------------------------
    # UNPAID
    # --------------------------------------------------------

    keyboard_buttons = []


    if order.get(
        "payment_link_url"
    ):

        keyboard_buttons.append(

            [
                InlineKeyboardButton(

                    text="💳 Open Payment Link",

                    url=(
                        order[
                            "payment_link_url"
                        ]
                    ),
                )
            ]
        )


    keyboard_buttons.append(

        [
            InlineKeyboardButton(

                text="🔄 Verify Payment",

                callback_data=(
                    "verify:"
                    + order[
                        "reference_id"
                    ]
                ),
            )
        ]
    )


    keyboard_buttons.append(

        [
            InlineKeyboardButton(
                text="↩️ Back",
                callback_data="home",
            )
        ]
    )


    keyboard = InlineKeyboardMarkup(
        inline_keyboard=keyboard_buttons
    )


    await message.answer(

        "📋 <b>My Plan</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>"
        f"{plan.get(
            'name',
            order['plan_key']
        )}"
        f"</b>\n"

        f"💰 Amount: "
        f"<b>"
        f"₹{order['amount_paise'] // 100}"
        f"</b>\n"

        f"📌 Status: "
        f"<b>{status}</b>\n\n"

        "Agar payment kar diya hai to "
        "<b>Verify Payment</b> dabayein.",

        reply_markup=keyboard,
    )


# ============================================================
# ACCESS
# ============================================================

@router.callback_query(
    F.data.startswith("access:")
)
async def access_callback(
    callback: CallbackQuery,
):

    await callback.answer(
        "Checking..."
    )


    reference_id = (
        callback.data.split(
            ":",
            1,
        )[1]
    )


    order = get_order(
        reference_id
    )


    if not order:

        await callback.message.answer(
            "❌ Order not found."
        )

        return


    if (
        order["user_id"]
        != callback.from_user.id
    ):

        await callback.message.answer(
            "❌ This order does not belong to you."
        )

        return


    if order["status"] != "paid":

        await callback.message.answer(
            "❌ Payment confirmed nahi hai."
        )

        return


    try:

        access_link = (
            await make_access_link(
                order["plan_key"]
            )
        )


        if not access_link:

            await callback.message.answer(

                "❌ Access link unavailable.\n"

                f"Contact {SUPPORT_USERNAME}."
            )

            return


        await callback.message.answer(

            "🔗 <b>Your Access Link</b>\n\n"

            f"{access_link}"
        )


    except Exception as e:

        print(
            "Manual access failed:",
            repr(e),
        )


        await callback.message.answer(

            "❌ Access send nahi ho paya.\n"

            f"Contact {SUPPORT_USERNAME}."
        )


# ============================================================
# HEALTH
# ============================================================

async def health(
    request: web.Request,
):

    return web.json_response(

        {

            "ok": True,

            "service":
                "telegram-store-bot-payu-payment-link",

            "time":
                int(time.time()),
        }
    )


# ============================================================
# WEB SERVER
# ============================================================

async def start_web_server():

    app = web.Application()


    app.router.add_get(
        "/health",
        health,
    )


    runner = web.AppRunner(
        app
    )


    await runner.setup()


    site = web.TCPSite(

        runner,

        WEBHOOK_HOST,

        WEBHOOK_PORT,
    )


    await site.start()


    print()
    print(
        "Web server running:"
    )

    print(
        f"{WEBHOOK_HOST}:"
        f"{WEBHOOK_PORT}"
    )

    print(
        "Health:",
        f"{PUBLIC_BASE_URL}/health",
    )

    print()


    return runner


# ============================================================
# CONFIG VALIDATION
# ============================================================

def validate_config():

    errors = []


    if not BOT_TOKEN:

        errors.append(
            "BOT_TOKEN missing"
        )


    if not PAYU_CLIENT_ID:

        errors.append(
            "PAYU_CLIENT_ID missing"
        )


    if not PAYU_CLIENT_SECRET:

        errors.append(
            "PAYU_CLIENT_SECRET missing"
        )


    if not PAYU_MERCHANT_ID:

        errors.append(
            "PAYU_MERCHANT_ID missing"
        )


    if not PUBLIC_BASE_URL:

        errors.append(
            "PUBLIC_BASE_URL missing"
        )


    if (
        "localhost"
        in PUBLIC_BASE_URL.lower()
    ):

        errors.append(
            "PUBLIC_BASE_URL cannot be localhost"
        )


    if (
        "your-domain.com"
        in PUBLIC_BASE_URL.lower()
    ):

        errors.append(
            "Replace example PUBLIC_BASE_URL"
        )


    if errors:

        raise RuntimeError(

            "Configuration errors:\n"

            + "\n".join(

                f"- {x}"

                for x in errors
            )
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global bot


    validate_config()


    init_db()


    bot = Bot(

        token=BOT_TOKEN,

        default=(
            DefaultBotProperties(
                parse_mode=ParseMode.HTML
            )
        ),
    )


    dp = Dispatcher()


    dp.include_router(
        router
    )


    runner = (
        await start_web_server()
    )


    try:

        print(
            "======================================"
        )

        print(
            "Telegram bot started."
        )

        print(
            "Payment gateway: PayU Payment Links"
        )

        print(
            "Environment: PRODUCTION"
        )

        print(
            "PayU Token URL:",
            PAYU_TOKEN_URL,
        )

        print(
            "PayU Payment Link URL:",
            PAYU_PAYMENT_LINK_URL,
        )

        print(
            "PayU Merchant ID:",
            PAYU_MERCHANT_ID,
        )

        print(
            "Public Base URL:",
            PUBLIC_BASE_URL,
        )

        print(
            "======================================"
        )


        await dp.start_polling(
            bot
        )


    finally:

        await runner.cleanup()


        if bot:

            await bot.session.close()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "Bot stopped."
        )
