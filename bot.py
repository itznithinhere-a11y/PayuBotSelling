import asyncio
import hashlib
import hmac
import io
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from typing import Optional

import aiohttp
import qrcode
from aiohttp import web

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from dotenv import load_dotenv


# ============================================================
# ENV
# ============================================================

load_dotenv()


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

PAYU_KEY = os.getenv("PAYU_KEY", "").strip()
PAYU_SALT = os.getenv("PAYU_SALT", "").strip()

PAYU_QR_URL = os.getenv(
    "PAYU_QR_URL",
    "https://secure.payu.in/_payment",
).strip()

PAYU_VERIFY_URL = os.getenv(
    "PAYU_VERIFY_URL",
    "",
).strip()

WEBHOOK_HOST = os.getenv(
    "WEBHOOK_HOST",
    "0.0.0.0",
).strip()

WEBHOOK_PORT = int(
    os.getenv(
        "WEBHOOK_PORT",
        "10000",
    )
)

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "",
).strip().rstrip("/")

SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "",
).strip()

DB_PATH = os.getenv(
    "DB_PATH",
    "bot.db",
).strip()


# ============================================================
# PLANS
# ============================================================

PLANS = {
    "gold": {
        "name": "⚡ Gold Dark (Channel 1)",
        "price": 1499,
        "description": "Gold Dark — Lifetime Access",
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
        "description": "Silver Dark — Lifetime Access",
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
        "description": "Bronze Dark — Lifetime Access",
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
        "description": "Iron Dark — Lifetime Access",
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


router = Router()
bot: Optional[Bot] = None


# ============================================================
# LOGGING HELPERS
# ============================================================

def log_section(title: str):
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def log_error(title: str, error: Exception):
    log_section(title)

    print(
        "ERROR TYPE:",
        type(error).__name__,
    )

    print(
        "ERROR:",
        str(error),
    )

    print("=" * 70)


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

    # Create database directory if needed.
    db_dir = os.path.dirname(
        os.path.abspath(DB_PATH)
    )

    if db_dir:
        os.makedirs(
            db_dir,
            exist_ok=True,
        )

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
            VALUES (?, ?, ?, ?, ?, 'created', ?)
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


def get_order(reference_id: str):

    with closing(db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE reference_id = ?
            """,
            (reference_id,),
        ).fetchone()

        return dict(row) if row else None


def get_order_by_txnid(txnid: str):

    with closing(db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE txnid = ?
            """,
            (txnid,),
        ).fetchone()

        return dict(row) if row else None


def get_latest_order(user_id: int):

    with closing(db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()

        return dict(row) if row else None


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


def mark_failed(
    reference_id: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders
            SET status = 'failed'
            WHERE reference_id = ?
            AND status != 'paid'
            """,
            (reference_id,),
        )

        conn.commit()


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
            (reference_id,),
        )

        conn.commit()


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
            (event_id,),
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
# PAYU HASH
# ============================================================

def sha512(
    value: str,
) -> str:

    return hashlib.sha512(
        value.encode("utf-8")
    ).hexdigest()


def generate_payment_hash(
    txnid: str,
    amount: str,
    productinfo: str,
    firstname: str,
    email: str,
    udf1: str = "",
    udf2: str = "",
    udf3: str = "",
    udf4: str = "",
    udf5: str = "",
):

    hash_string = (
        f"{PAYU_KEY}|"
        f"{txnid}|"
        f"{amount}|"
        f"{productinfo}|"
        f"{firstname}|"
        f"{email}|"
        f"{udf1}|"
        f"{udf2}|"
        f"{udf3}|"
        f"{udf4}|"
        f"{udf5}"
        f"||||||"
        f"{PAYU_SALT}"
    )

    return sha512(
        hash_string
    )


def generate_reverse_hash(
    data: dict,
) -> str:

    status = data.get(
        "status",
        "",
    )

    udf5 = data.get(
        "udf5",
        "",
    )

    udf4 = data.get(
        "udf4",
        "",
    )

    udf3 = data.get(
        "udf3",
        "",
    )

    udf2 = data.get(
        "udf2",
        "",
    )

    udf1 = data.get(
        "udf1",
        "",
    )

    email = data.get(
        "email",
        "",
    )

    firstname = data.get(
        "firstname",
        "",
    )

    productinfo = data.get(
        "productinfo",
        "",
    )

    amount = data.get(
        "amount",
        "",
    )

    txnid = data.get(
        "txnid",
        "",
    )

    key = data.get(
        "key",
        "",
    )

    additional_charges = data.get(
        "additionalCharges",
        data.get(
            "additional_charges",
            "",
        ),
    )

    reverse_string = (
        f"{PAYU_SALT}|"
        f"{status}|"
        f"{additional_charges}|"
        f"||||||"
        f"{udf5}|"
        f"{udf4}|"
        f"{udf3}|"
        f"{udf2}|"
        f"{udf1}|"
        f"{email}|"
        f"{firstname}|"
        f"{productinfo}|"
        f"{amount}|"
        f"{txnid}|"
        f"{key}"
    )

    return sha512(
        reverse_string
    )


def verify_payu_response(
    data: dict,
) -> bool:

    received_hash = data.get(
        "hash",
        "",
    )

    if not received_hash:
        return False

    calculated_hash = generate_reverse_hash(
        data
    )

    return hmac.compare_digest(
        calculated_hash.lower(),
        received_hash.lower(),
    )


# ============================================================
# CREATE PAYU DYNAMIC QR
# ============================================================

async def create_payu_dynamic_qr(
    user_id: int,
    plan_key: str,
    firstname: str,
):

    if plan_key not in PLANS:
        raise ValueError(
            f"Invalid plan: {plan_key}"
        )

    plan = PLANS[plan_key]

    # --------------------------------------------------------
    # Generate unique transaction ID
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

    amount = f"{plan['price']:.2f}"

    email = (
        f"telegram"
        f"{user_id}"
        f"@example.com"
    )

    productinfo = plan["description"]

    # --------------------------------------------------------
    # UDF DATA
    # --------------------------------------------------------

    udf1 = str(user_id)
    udf2 = plan_key
    udf3 = reference_id
    udf4 = ""
    udf5 = ""

    # --------------------------------------------------------
    # Generate PayU hash
    # --------------------------------------------------------

    payment_hash = generate_payment_hash(
        txnid=txnid,
        amount=amount,
        productinfo=productinfo,
        firstname=firstname or "Customer",
        email=email,
        udf1=udf1,
        udf2=udf2,
        udf3=udf3,
        udf4=udf4,
        udf5=udf5,
    )

    # --------------------------------------------------------
    # Save order BEFORE PayU request
    # --------------------------------------------------------

    save_order(
        reference_id=reference_id,
        user_id=user_id,
        plan_key=plan_key,
        amount_paise=plan["price"] * 100,
        txnid=txnid,
    )

    # --------------------------------------------------------
    # PayU payload
    # --------------------------------------------------------

    payload = {

        "key": PAYU_KEY,

        "txnid": txnid,

        "amount": amount,

        "productinfo": productinfo,

        "firstname": firstname or "Customer",

        "lastname": "",

        "email": email,

        "phone": "9999999999",

        "surl": (
            f"{PUBLIC_BASE_URL}"
            f"/payu/success"
        ),

        "furl": (
            f"{PUBLIC_BASE_URL}"
            f"/payu/failure"
        ),

        "pg": "DBQR",

        "bankcode": "UPIDBQR",

        "hash": payment_hash,

        "s2s_client_ip": "127.0.0.1",

        "s2s_device_info": "Telegram Bot",

        "txn_s2s_flow": "4",

        # 30 minutes
        "expiry_time": "1800",

        "udf1": udf1,
        "udf2": udf2,
        "udf3": udf3,
        "udf4": udf4,
        "udf5": udf5,
    }

    # --------------------------------------------------------
    # LOG REQUEST
    # --------------------------------------------------------

    log_section(
        "PAYU DYNAMIC QR REQUEST"
    )

    print(
        "TXNID:",
        txnid,
    )

    print(
        "REFERENCE ID:",
        reference_id,
    )

    print(
        "PLAN:",
        plan_key,
    )

    print(
        "AMOUNT:",
        amount,
    )

    print(
        "PAYU URL:",
        PAYU_QR_URL,
    )

    print(
        "SUCCESS URL:",
        payload["surl"],
    )

    print(
        "FAILURE URL:",
        payload["furl"],
    )

    # Do not print salt/hash in production logs.

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                PAYU_QR_URL,
                data=payload,
                headers={
                    "Accept": "application/json",
                },
            ) as response:

                text = await response.text()

                # ------------------------------------------------
                # RAW PAYU RESPONSE
                # ------------------------------------------------

                log_section(
                    "PAYU RAW RESPONSE"
                )

                print(
                    "HTTP STATUS:",
                    response.status,
                )

                print(
                    "CONTENT TYPE:",
                    response.headers.get(
                        "Content-Type"
                    ),
                )

                print(
                    "RESPONSE:"
                )

                print(
                    text[:10000]
                )

                print(
                    "=" * 70
                )

                if response.status >= 400:

                    raise RuntimeError(
                        f"PayU HTTP {response.status}: "
                        f"{text[:3000]}"
                    )

                try:

                    result = json.loads(
                        text
                    )

                except json.JSONDecodeError as e:

                    raise RuntimeError(
                        "PayU returned non-JSON response. "
                        f"Content-Type="
                        f"{response.headers.get('Content-Type')}. "
                        f"Body={text[:3000]}"
                    ) from e

    except asyncio.TimeoutError as e:

        raise RuntimeError(
            "PayU request timed out after 30 seconds."
        ) from e

    except aiohttp.ClientError as e:

        raise RuntimeError(
            f"PayU network error: {e}"
        ) from e

    # ------------------------------------------------------------
    # Print parsed response
    # ------------------------------------------------------------

    log_section(
        "PAYU PARSED RESPONSE"
    )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )[:15000]
    )

    print(
        "=" * 70
    )

    # ------------------------------------------------------------
    # Extract QR
    # ------------------------------------------------------------

    meta = result.get(
        "metaData",
        {},
    )

    qr_result = result.get(
        "result",
        {},
    )

    qr_string = qr_result.get(
        "qrString"
    )

    if not qr_string:

        # Try common alternate fields too.
        qr_string = (
            result.get("qrString")
            or result.get("qr_string")
            or qr_result.get("qr_string")
        )

    if not qr_string:

        error_message = (
            result.get("message")
            or result.get("error")
            or result.get("errorMessage")
            or meta.get("message")
            or "qrString missing"
        )

        raise RuntimeError(
            "PayU QR generation failed. "
            f"Reason: {error_message}. "
            f"Full response: "
            f"{json.dumps(result, ensure_ascii=False)[:5000]}"
        )

    return {
        "txnid": txnid,
        "reference_id": reference_id,
        "qr_string": qr_string,
        "payment_id": qr_result.get(
            "paymentId"
        ),
        "merchant_vpa": qr_result.get(
            "merchantVpa"
        ),
        "amount": qr_result.get(
            "amount"
        ),
        "status": meta.get(
            "txnStatus"
        ),
    }


# ============================================================
# PAYU VERIFY API
# ============================================================

async def verify_payment_with_payu(
    txnid: str,
):

    if not PAYU_VERIFY_URL:

        raise RuntimeError(
            "PAYU_VERIFY_URL is not configured."
        )

    verify_hash_string = (
        f"{PAYU_KEY}|"
        f"verify_payment|"
        f"{txnid}|"
        f"{PAYU_SALT}"
    )

    verify_hash = sha512(
        verify_hash_string
    )

    data = {

        "key": PAYU_KEY,

        "command": "verify_payment",

        "var1": txnid,

        "hash": verify_hash,
    }

    timeout = aiohttp.ClientTimeout(
        total=20
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                PAYU_VERIFY_URL,
                data=data,
            ) as response:

                text = await response.text()

                log_section(
                    "PAYU VERIFY RESPONSE"
                )

                print(
                    "HTTP STATUS:",
                    response.status,
                )

                print(
                    "RESPONSE:",
                    text[:10000],
                )

                print(
                    "=" * 70
                )

                if response.status >= 400:

                    raise RuntimeError(
                        f"PayU verification HTTP "
                        f"{response.status}: "
                        f"{text[:3000]}"
                    )

                try:

                    return json.loads(
                        text
                    )

                except json.JSONDecodeError:

                    return {
                        "raw": text
                    }

    except asyncio.TimeoutError as e:

        raise RuntimeError(
            "PayU verification request timed out."
        ) from e

    except aiohttp.ClientError as e:

        raise RuntimeError(
            f"PayU verification network error: {e}"
        ) from e


# ============================================================
# TELEGRAM UI
# ============================================================

def main_keyboard():

    support_url = (
        "https://t.me/"
        + SUPPORT_USERNAME.lstrip("@")
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[

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

            [
                InlineKeyboardButton(
                    text="📞 Support",
                    url=support_url,
                )
            ],
        ]
    )


def plan_keyboard(
    plan_key: str,
):

    plan = PLANS[plan_key]

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

        f"💬 Support: "
        f"{SUPPORT_USERNAME}"
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
# PLAN CALLBACK
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

    plan = PLANS[plan_key]

    text = (

        f"<b>{plan['name']}</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 Price: "
        f"<b>₹{plan['price']}</b>\n\n"

        "🔐 Lifetime Access\n"

        "💳 Pay securely using PayU\n\n"

        "Click below to continue."
    )

    await callback.message.answer(
        text,
        reply_markup=plan_keyboard(
            plan_key
        ),
    )


# ============================================================
# BUY
# ============================================================

@router.callback_query(
    F.data.startswith("buy:")
)
async def buy_callback(
    callback: CallbackQuery,
):

    await callback.answer(
        "Generating dynamic QR..."
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

        result = await create_payu_dynamic_qr(

            user_id=callback.from_user.id,

            plan_key=plan_key,

            firstname=(
                callback.from_user.first_name
                or "Customer"
            ),
        )

    except Exception as e:

        log_error(
            "PAYU DYNAMIC QR GENERATION FAILED",
            e,
        )

        await callback.message.answer(

            "❌ <b>Dynamic QR generate nahi ho paya.</b>\n\n"

            "PayU se QR create karte waqt problem aayi.\n"

            "Please thodi der baad dobara try karein.\n\n"

            f"🧾 Error ID: "
            f"<code>{int(time.time())}</code>"
        )

        return

    # ========================================================
    # CREATE PNG FROM PAYU QR STRING
    # ========================================================

    try:

        qr = qrcode.QRCode(

            version=None,

            error_correction=(
                qrcode.constants
                .ERROR_CORRECT_M
            ),

            box_size=10,

            border=4,
        )

        qr.add_data(
            result["qr_string"]
        )

        qr.make(
            fit=True
        )

        img = qr.make_image(
            fill_color="black",
            back_color="white",
        )

        buffer = io.BytesIO()

        img.save(
            buffer,
            format="PNG",
        )

        buffer.seek(0)

        qr_bytes = buffer.getvalue()

        qr_file = BufferedInputFile(
            qr_bytes,
            filename=(
                f"{result['txnid']}.png"
            ),
        )

    except Exception as e:

        log_error(
            "LOCAL QR PNG GENERATION FAILED",
            e,
        )

        await callback.message.answer(
            "❌ PayU QR data mil gaya, "
            "lekin PNG QR create nahi ho paya."
        )

        return

    plan = PLANS[plan_key]

    text = (

        "💳 <b>PAYU UPI PAYMENT</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Amount: "
        f"<b>₹{plan['price']}</b>\n\n"

        "📱 <b>Scan this QR using any UPI app</b>\n"

        "GPay • PhonePe • Paytm • BHIM • Any UPI App\n\n"

        "⚡ This is a <b>dynamic QR</b>.\n"

        "Amount is already fixed in the QR.\n\n"

        f"🧾 Txn ID:\n"
        f"<code>{result['txnid']}</code>\n\n"

        "⏱️ QR validity: <b>30 minutes</b>\n\n"

        "After payment, press "
        "<b>Verify Payment</b>."
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🔄 Verify Payment",
                    callback_data=(
                        "verify:"
                        + result["reference_id"]
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

    try:

        await callback.message.answer_photo(
            photo=qr_file,
            caption=text,
            reply_markup=keyboard,
        )

    except Exception as e:

        log_error(
            "TELEGRAM QR SEND FAILED",
            e,
        )

        await callback.message.answer(
            "❌ QR generate ho gaya tha, "
            "lekin Telegram par send nahi ho paya."
        )


# ============================================================
# ACCESS LINK
# ============================================================

async def make_access_link(
    plan_key: str,
) -> Optional[str]:

    plan = PLANS[plan_key]

    # --------------------------------------------------------
    # Preferred: one-time Telegram invite link
    # --------------------------------------------------------

    if plan["channel_id"]:

        try:

            invite = await bot.create_chat_invite_link(

                chat_id=plan["channel_id"],

                member_limit=1,
            )

            return invite.invite_link

        except Exception as e:

            log_error(
                f"INVITE LINK CREATION FAILED: {plan_key}",
                e,
            )

    # --------------------------------------------------------
    # Fallback static access link
    # --------------------------------------------------------

    if plan["access_link"]:

        return plan["access_link"]

    return None


# ============================================================
# DELIVER ACCESS
# ============================================================

async def deliver_access(
    order: dict,
):

    # Prevent duplicate automatic delivery.
    if order.get("access_sent"):

        return True

    access_link = await make_access_link(
        order["plan_key"]
    )

    if not access_link:

        await bot.send_message(

            order["user_id"],

            "✅ <b>Payment confirmed!</b>\n\n"

            "Lekin access link configure nahi hai.\n\n"

            f"📞 Support: "
            f"{SUPPORT_USERNAME}",
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
# PROCESS SUCCESSFUL PAYMENT
# ============================================================

async def process_successful_payment(
    data: dict,
):

    txnid = data.get(
        "txnid"
    )

    if not txnid:

        return False

    order = get_order_by_txnid(
        txnid
    )

    if not order:

        print(
            "Unknown transaction:",
            txnid,
        )

        return False

    # --------------------------------------------------------
    # User validation
    # --------------------------------------------------------

    try:

        if data.get("udf1"):

            if str(data["udf1"]) != str(
                order["user_id"]
            ):

                print(
                    "User ID mismatch:",
                    txnid,
                )

                return False

    except Exception:

        return False

    # --------------------------------------------------------
    # Amount validation
    # --------------------------------------------------------

    try:

        received_amount = float(
            data.get(
                "amount",
                "0",
            )
        )

        expected_amount = (
            order["amount_paise"]
            / 100
        )

        if abs(
            received_amount
            - expected_amount
        ) > 0.01:

            print(
                "Amount mismatch:",
                txnid,
                received_amount,
                expected_amount,
            )

            return False

    except Exception:

        return False

    # --------------------------------------------------------
    # Plan validation
    # --------------------------------------------------------

    if data.get("udf2"):

        if data["udf2"] != order["plan_key"]:

            print(
                "Plan mismatch:",
                txnid,
            )

            return False

    # --------------------------------------------------------
    # Payment ID
    # --------------------------------------------------------

    payment_id = (

        data.get("mihpayid")

        or data.get("payuMoneyId")

        or txnid
    )

    # --------------------------------------------------------
    # Mark paid
    # --------------------------------------------------------

    if order["status"] != "paid":

        mark_paid(
            order["reference_id"],
            payment_id,
        )

    updated = get_order(
        order["reference_id"]
    )

    # --------------------------------------------------------
    # Deliver access
    # --------------------------------------------------------

    if updated and not updated["access_sent"]:

        try:

            await deliver_access(
                updated
            )

        except Exception as e:

            log_error(
                "ACCESS DELIVERY FAILED",
                e,
            )

    return True


# ============================================================
# PAYU SUCCESS CALLBACK
# ============================================================

async def payu_success(
    request: web.Request,
):

    try:

        data = await request.post()

        data = dict(data)

    except Exception as e:

        log_error(
            "PAYU SUCCESS PARSE ERROR",
            e,
        )

        return web.Response(
            status=400,
            text="Invalid request",
        )

    log_section(
        "PAYU SUCCESS CALLBACK"
    )

    print(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str,
        )[:15000]
    )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # Reverse hash
    # --------------------------------------------------------

    if not verify_payu_response(
        data
    ):

        print(
            "PayU reverse hash verification failed."
        )

        return web.Response(
            status=400,
            text="Invalid hash",
        )

    # --------------------------------------------------------
    # Status
    # --------------------------------------------------------

    if data.get(
        "status"
    ) != "success":

        return web.Response(
            status=400,
            text="Payment not successful",
        )

    txnid = data.get(
        "txnid",
        "",
    )

    event_id = (
        "success_"
        + txnid
        + "_"
        + data.get(
            "mihpayid",
            "",
        )
    )

    if event_already_processed(
        event_id
    ):

        return web.Response(
            status=200,
            text="already processed",
        )

    save_event(
        event_id
    )

    success = await process_successful_payment(
        data
    )

    if not success:

        return web.Response(
            status=400,
            text="Payment verification failed",
        )

    return web.Response(
        status=200,
        text=(
            "Payment successful. "
            "You can return to Telegram."
        ),
    )


# ============================================================
# PAYU FAILURE CALLBACK
# ============================================================

async def payu_failure(
    request: web.Request,
):

    try:

        data = await request.post()

        data = dict(data)

    except Exception as e:

        log_error(
            "PAYU FAILURE PARSE ERROR",
            e,
        )

        return web.Response(
            status=400,
            text="Invalid request",
        )

    log_section(
        "PAYU FAILURE CALLBACK"
    )

    print(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str,
        )[:10000]
    )

    print(
        "=" * 70
    )

    # Verify callback hash.
    if data.get("hash"):

        if not verify_payu_response(
            data
        ):

            return web.Response(
                status=400,
                text="Invalid hash",
            )

    txnid = data.get(
        "txnid",
        "",
    )

    if txnid:

        order = get_order_by_txnid(
            txnid
        )

        if (
            order
            and order["status"] != "paid"
        ):

            mark_failed(
                order["reference_id"]
            )

    return web.Response(
        status=200,
        text="Payment failed",
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
            f"<b>{plan.get('name', order['plan_key'])}</b>\n"

            f"💰 Amount: "
            f"<b>₹{order['amount_paise'] // 100}</b>\n"

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

    # --------------------------------------------------------
    # NOT PAID
    # --------------------------------------------------------

    else:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[

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

            "📋 <b>My Plan</b>\n"

            "━━━━━━━━━━━━━━━━━━\n\n"

            f"📦 Plan: "
            f"<b>{plan.get('name', order['plan_key'])}</b>\n"

            f"💰 Amount: "
            f"<b>₹{order['amount_paise'] // 100}</b>\n"

            f"📌 Status: "
            f"<b>{status}</b>\n\n"

            "Agar payment kar diya hai to "
            "<b>Verify Payment</b> dabayein.",

            reply_markup=keyboard,
        )


# ============================================================
# ACCESS CALLBACK
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

    reference_id = callback.data.split(
        ":",
        1,
    )[1]

    order = get_order(
        reference_id
    )

    if not order:

        await callback.message.answer(
            "❌ Order not found."
        )

        return

    # Security check.
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

        access_link = await make_access_link(
            order["plan_key"]
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

        log_error(
            "MANUAL ACCESS FAILED",
            e,
        )

        await callback.message.answer(
            "❌ Access send nahi ho paya.\n"
            f"Contact {SUPPORT_USERNAME}."
        )


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
        "Verifying with PayU..."
    )

    reference_id = callback.data.split(
        ":",
        1,
    )[1]

    order = get_order(
        reference_id
    )

    if not order:

        await callback.message.answer(
            "❌ Order not found."
        )

        return

    # Security.
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
            "✅ Payment already confirmed.\n"
            "Use /myplan for access."
        )

        return

    try:

        result = await verify_payment_with_payu(
            order["txnid"]
        )

        print(
            "PAYU VERIFY:"
        )

        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
                default=str,
            )[:15000]
        )

        transaction_details = result.get(
            "transaction_details",
            {},
        )

        details = transaction_details.get(
            order["txnid"],
            {},
        )

        payment_status = (

            details.get("status")

            or result.get("status")
        )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        if (
            str(payment_status).lower()
            == "success"
        ):

            # Validate amount if available.
            verify_amount = (
                details.get("amt")
                or details.get("amount")
            )

            if verify_amount is not None:

                try:

                    received_amount = float(
                        verify_amount
                    )

                    expected_amount = (
                        order["amount_paise"]
                        / 100
                    )

                    if abs(
                        received_amount
                        - expected_amount
                    ) > 0.01:

                        await callback.message.answer(
                            "❌ Payment amount mismatch."
                        )

                        print(
                            "VERIFY AMOUNT MISMATCH:",
                            order["txnid"],
                            received_amount,
                            expected_amount,
                        )

                        return

                except Exception:

                    pass

            payment_id = (

                details.get("mihpayid")

                or details.get(
                    "payuMoneyId"
                )

                or order["txnid"]
            )

            mark_paid(
                order["reference_id"],
                payment_id,
            )

            updated = get_order(
                order["reference_id"]
            )

            try:

                delivered = await deliver_access(
                    updated
                )

                if delivered:

                    await callback.message.answer(
                        "✅ <b>Payment verified successfully!</b>\n\n"
                        "Access link Telegram par send kar diya gaya hai."
                    )

            except Exception as e:

                log_error(
                    "VERIFY ACCESS DELIVERY FAILED",
                    e,
                )

                await callback.message.answer(
                    "✅ Payment confirmed.\n\n"
                    "Access delivery mein problem aayi. "
                    f"Support: {SUPPORT_USERNAME}"
                )

        # ----------------------------------------------------
        # FAILURE
        # ----------------------------------------------------

        elif str(payment_status).lower() in (
            "failure",
            "failed",
        ):

            mark_failed(
                order["reference_id"]
            )

            await callback.message.answer(
                "❌ Payment failed."
            )

        # ----------------------------------------------------
        # PENDING / UNKNOWN
        # ----------------------------------------------------

        else:

            await callback.message.answer(

                "⏳ <b>Payment not confirmed yet.</b>\n\n"

                f"PayU status: "
                f"<code>{payment_status}</code>\n\n"

                "Agar payment abhi-abhi kiya hai "
                "to 10–30 seconds baad dobara verify karein."
            )

    except Exception as e:

        log_error(
            "PAYU VERIFICATION FAILED",
            e,
        )

        await callback.message.answer(

            "❌ <b>PayU verification failed.</b>\n\n"

            "Thodi der baad dobara try karein."
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
            "service": "telegram-store-bot-payu",
            "time": int(time.time()),
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

    app.router.add_post(
        "/payu/success",
        payu_success,
    )

    app.router.add_post(
        "/payu/failure",
        payu_failure,
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

    log_section(
        "PAYU CALLBACK SERVER"
    )

    print(
        "Host:",
        WEBHOOK_HOST,
    )

    print(
        "Port:",
        WEBHOOK_PORT,
    )

    print(
        "Health URL:",
        f"{PUBLIC_BASE_URL}/health",
    )

    print(
        "Success URL:",
        f"{PUBLIC_BASE_URL}/payu/success",
    )

    print(
        "Failure URL:",
        f"{PUBLIC_BASE_URL}/payu/failure",
    )

    print(
        "=" * 70
    )

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

    if not PAYU_KEY:
        errors.append(
            "PAYU_KEY missing"
        )

    if not PAYU_SALT:
        errors.append(
            "PAYU_SALT missing"
        )

    if not PAYU_VERIFY_URL:
        errors.append(
            "PAYU_VERIFY_URL missing"
        )

    if not PUBLIC_BASE_URL:

        errors.append(
            "PUBLIC_BASE_URL missing"
        )

    elif (
        "localhost"
        in PUBLIC_BASE_URL.lower()
        or "127.0.0.1"
        in PUBLIC_BASE_URL.lower()
        or "your-domain.com"
        in PUBLIC_BASE_URL.lower()
    ):

        errors.append(
            "PUBLIC_BASE_URL must be a real public HTTPS URL"
        )

    elif not PUBLIC_BASE_URL.startswith(
        "https://"
    ):

        errors.append(
            "PUBLIC_BASE_URL must start with https://"
        )

    if not SUPPORT_USERNAME:

        print(
            "WARNING: SUPPORT_USERNAME is empty."
        )

    if errors:

        raise RuntimeError(
            "Configuration errors:\n- "
            + "\n- ".join(errors)
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global bot

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    validate_config()

    # --------------------------------------------------------
    # Database
    # --------------------------------------------------------

    init_db()

    print(
        "Database initialized:",
        DB_PATH,
    )

    # --------------------------------------------------------
    # Telegram bot
    # --------------------------------------------------------

    bot = Bot(

        token=BOT_TOKEN,

        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML
        ),
    )

    dp = Dispatcher()

    dp.include_router(
        router
    )

    # --------------------------------------------------------
    # Web server
    # --------------------------------------------------------

    runner = await start_web_server()

    try:

        log_section(
            "TELEGRAM STORE BOT STARTED"
        )

        print(
            "Payment gateway: PayU"
        )

        print(
            "Environment: PRODUCTION"
        )

        print(
            "PayU QR URL:",
            PAYU_QR_URL,
        )

        print(
            "PayU Verify URL:",
            PAYU_VERIFY_URL,
        )

        print(
            "Public Base URL:",
            PUBLIC_BASE_URL,
        )

        print(
            "Database:",
            DB_PATH,
        )

        print(
            "=" * 70
        )

        # ----------------------------------------------------
        # Start polling
        # ----------------------------------------------------

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

    except Exception as e:

        log_error(
            "BOT STARTUP FAILED",
            e,
        )

        raise
