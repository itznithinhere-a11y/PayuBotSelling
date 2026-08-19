# ============================================================
# DARK STORE TELEGRAM BOT
# PAYU OAUTH PAYMENT LINKS
# ============================================================
#
# Features:
# - Telegram Bot / aiogram 3
# - PayU OAuth 2.0 Client Credentials
# - PayU Payment Links
# - Dynamic future expiry
# - Automatic payment verification
# - PayU transaction status API
# - SQLite orders database
# - Duplicate payment protection
# - Amount validation
# - Plan validation
# - My Plan
# - Manual Verify Payment
# - Automatic access delivery
# - One-time Telegram channel invite
# - Fallback static access link
# - Success / Failure redirect endpoints
# - Health endpoint
# - Background payment watcher
#
# IMPORTANT:
# PayU Payment Links API is different from PayU _payment/QR API.
# This code does NOT use PAYU_KEY / PAYU_SALT.
#
# ============================================================


import asyncio
import json
import os
import secrets
import sqlite3
import time
import uuid

from contextlib import closing
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import urlparse

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


# ============================================================
# LOAD ENV
# ============================================================

load_dotenv()


# ============================================================
# BASIC CONFIG
# ============================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "",
).strip()


# ============================================================
# PAYU OAUTH CONFIG
# ============================================================

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


PAYU_TOKEN_URL = os.getenv(
    "PAYU_TOKEN_URL",
    "https://accounts.payu.in/oauth/token",
).strip()


PAYU_PAYMENT_LINK_URL = os.getenv(
    "PAYU_PAYMENT_LINK_URL",
    "https://oneapi.payu.in/payment-links",
).strip()


PAYU_TRANSACTION_URL = os.getenv(
    "PAYU_TRANSACTION_URL",
    "https://oneapi.payu.in/payment-links",
).strip()


# ============================================================
# PAYMENT LINK SETTINGS
# ============================================================

PAYMENT_LINK_EXPIRY_MINUTES = int(
    os.getenv(
        "PAYMENT_LINK_EXPIRY_MINUTES",
        "30",
    )
)


PAYMENT_CHECK_INTERVAL = int(
    os.getenv(
        "PAYMENT_CHECK_INTERVAL",
        "20",
    )
)


# ============================================================
# WEB SERVER
# ============================================================

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


# ============================================================
# SUPPORT
# ============================================================

SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "",
).strip()


# ============================================================
# DATABASE
# ============================================================

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

        "description": "Gold Dark - Lifetime Access",

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

        "description": "Silver Dark - Lifetime Access",

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

        "description": "Bronze Dark - Lifetime Access",

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

        "description": "Iron Dark - Lifetime Access",

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

payment_watcher_task: Optional[asyncio.Task] = None

oauth_token_cache = {
    "access_token": "",
    "expires_at": 0,
}


# ============================================================
# LOGGING
# ============================================================

def error_id():
    return (
        f"E-{int(time.time())}-"
        f"{secrets.token_hex(3).upper()}"
    )


def log_error(
    title,
    details=None,
):
    print()
    print("=" * 80)
    print(title)

    if details is not None:
        print(
            json.dumps(
                details,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
            if isinstance(details, (dict, list))
            else details
        )

    print("=" * 80)
    print()


# ============================================================
# TIME
# ============================================================

def india_now():
    """
    Return current India time.

    ZoneInfo is used when available.
    """

    try:

        from zoneinfo import ZoneInfo

        return datetime.now(
            ZoneInfo("Asia/Kolkata")
        )

    except Exception:

        # Fallback: UTC + 5:30

        return datetime.utcnow() + timedelta(
            hours=5,
            minutes=30,
        )


def payu_expiry_datetime():
    """
    Generate a future PayU expiry datetime.

    PayU expects:
        YYYY-MM-DD HH:mm:ss

    Extra 60-second safety margin is added.
    """

    expiry = (
        india_now()
        + timedelta(
            minutes=PAYMENT_LINK_EXPIRY_MINUTES,
            seconds=60,
        )
    )

    return expiry.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


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

                invoice_number TEXT UNIQUE,

                payment_link TEXT,

                status TEXT NOT NULL DEFAULT 'created',

                payment_id TEXT,

                created_at INTEGER NOT NULL,

                expiry_at INTEGER,

                paid_at INTEGER,

                access_sent INTEGER NOT NULL DEFAULT 0,

                last_checked_at INTEGER,

                check_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )


        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_orders_user_id
            ON orders(user_id)
            """
        )


        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS
            idx_orders_status
            ON orders(status)
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
                status,
                created_at
            )
            VALUES
            (
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
                int(time.time()),
            ),
        )

        conn.commit()


# ============================================================
# UPDATE PAYMENT LINK
# ============================================================

def update_payment_link(
    reference_id: str,
    invoice_number: str,
    payment_link: str,
    expiry_at: int,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET
                invoice_number = ?,
                payment_link = ?,
                expiry_at = ?

            WHERE reference_id = ?
            """,
            (
                invoice_number,
                payment_link,
                expiry_at,
                reference_id,
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
# GET BY INVOICE
# ============================================================

def get_order_by_invoice(
    invoice_number: str,
):

    with closing(db()) as conn:

        row = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE invoice_number = ?
            """,
            (
                invoice_number,
            ),
        ).fetchone()

        return (
            dict(row)
            if row
            else None
        )


# ============================================================
# LATEST USER ORDER
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
# PAID ORDERS FOR WATCHER
# ============================================================

def get_pending_orders():

    with closing(db()) as conn:

        rows = conn.execute(
            """
            SELECT *
            FROM orders

            WHERE status IN (
                'created',
                'pending'
            )

            AND invoice_number IS NOT NULL

            ORDER BY created_at ASC

            LIMIT 100
            """
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]


# ============================================================
# MARK PENDING
# ============================================================

def mark_pending(
    reference_id: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET
                status = 'pending',
                last_checked_at = ?,
                check_count = check_count + 1

            WHERE reference_id = ?
            """,
            (
                int(time.time()),
                reference_id,
            ),
        )

        conn.commit()


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
                paid_at = ?,
                last_checked_at = ?

            WHERE reference_id = ?
            """,
            (
                payment_id,
                int(time.time()),
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

            SET
                status = 'failed',
                last_checked_at = ?

            WHERE reference_id = ?
            """,
            (
                int(time.time()),
                reference_id,
            ),
        )

        conn.commit()


# ============================================================
# MARK EXPIRED
# ============================================================

def mark_expired(
    reference_id: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET
                status = 'expired',
                last_checked_at = ?

            WHERE reference_id = ?
            """,
            (
                int(time.time()),
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
# EVENTS
# ============================================================

def event_already_processed(
    event_id: str,
):

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
            VALUES
            (
                ?,
                ?
            )
            """,
            (
                event_id,
                int(time.time()),
            ),
        )

        conn.commit()


# ============================================================
# HTTP HELPERS
# ============================================================

async def read_json_response(
    response,
):

    text = await response.text()

    try:
        return json.loads(text)

    except Exception:

        return {
            "raw": text
        }


# ============================================================
# PAYU OAUTH TOKEN
# ============================================================

async def get_payu_access_token(
    force_refresh=False,
):

    global oauth_token_cache

    now = int(
        time.time()
    )


    if (
        not force_refresh
        and oauth_token_cache.get(
            "access_token"
        )
        and now
        < oauth_token_cache.get(
            "expires_at",
            0,
        )
    ):

        return oauth_token_cache[
            "access_token"
        ]


    payload = {
        "client_id": PAYU_CLIENT_ID,

        "client_secret": PAYU_CLIENT_SECRET,

        "grant_type": "client_credentials",

        "scope": (
            "create_payment_links "
            "read_payment_links "
            "update_payment_links"
        ),
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

                result = await read_json_response(
                    response
                )


                if response.status >= 400:

                    log_error(
                        "PAYU OAUTH ERROR",
                        {
                            "http_status":
                                response.status,

                            "response":
                                result,
                        },
                    )

                    raise RuntimeError(
                        "PayU OAuth failed."
                    )


    except asyncio.TimeoutError:

        raise RuntimeError(
            "PayU OAuth request timed out."
        )


    except aiohttp.ClientError as e:

        raise RuntimeError(
            f"PayU OAuth network error: {e}"
        )


    access_token = result.get(
        "access_token"
    )


    if not access_token:

        log_error(
            "PAYU OAUTH TOKEN MISSING",
            result,
        )

        raise RuntimeError(
            "PayU did not return access_token."
        )


    expires_in = int(
        result.get(
            "expires_in",
            3600,
        )
    )


    # Keep 60 seconds safety margin.

    oauth_token_cache = {
        "access_token":
            access_token,

        "expires_at":
            now + max(
                60,
                expires_in - 60,
            ),
    }


    return access_token


# ============================================================
# PAYU API REQUEST
# ============================================================

async def payu_request(
    method: str,
    url: str,
    *,
    json_data=None,
    params=None,
    retry_auth=True,
):

    token = await get_payu_access_token()


    headers = {
        "Authorization":
            f"Bearer {token}",

        "merchantId":
            PAYU_MERCHANT_ID,

        "Content-Type":
            "application/json",

        "Accept":
            "application/json",
    }


    timeout = aiohttp.ClientTimeout(
        total=30
    )


    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.request(
                method,

                url,

                json=json_data,

                params=params,

                headers=headers,
            ) as response:

                result = await read_json_response(
                    response
                )


                # OAuth token may have expired.

                if (
                    response.status == 401
                    and retry_auth
                ):

                    await get_payu_access_token(
                        force_refresh=True
                    )

                    return await payu_request(
                        method,

                        url,

                        json_data=json_data,

                        params=params,

                        retry_auth=False,
                    )


                if response.status >= 400:

                    raise RuntimeError(
                        "PayU API HTTP "
                        f"{response.status}: "
                        f"{json.dumps(result, ensure_ascii=False)}"
                    )


                return result


    except asyncio.TimeoutError:

        raise RuntimeError(
            "PayU API request timed out."
        )


    except aiohttp.ClientError as e:

        raise RuntimeError(
            f"PayU API network error: {e}"
        )


# ============================================================
# CREATE PAYU PAYMENT LINK
# ============================================================

async def create_payu_payment_link(
    user_id: int,
    plan_key: str,
    firstname: str,
):

    if plan_key not in PLANS:

        raise RuntimeError(
            "Invalid plan."
        )


    plan = PLANS[
        plan_key
    ]


    reference_id = (
        "ORD_"
        + secrets.token_hex(12)
    )


    # PayU invoice number should be unique/alphanumeric.

    invoice_number = (
        "INV"
        + str(int(time.time()))
        + secrets.token_hex(4).upper()
    )


    firstname = (
        firstname
        or "Customer"
    )[:100]


    email = (
        f"telegram"
        f"{user_id}"
        f"@example.com"
    )


    phone = "9999999999"


    expiry_string = (
        payu_expiry_datetime()
    )


    # Convert expiry to unix for SQLite.

    try:

        expiry_dt = datetime.strptime(
            expiry_string,
            "%Y-%m-%d %H:%M:%S",
        )

        expiry_at = int(
            expiry_dt.timestamp()
        )

    except Exception:

        expiry_at = int(
            time.time()
            + PAYMENT_LINK_EXPIRY_MINUTES * 60
        )


    # --------------------------------------------------------
    # SAVE ORDER BEFORE API CALL
    # --------------------------------------------------------

    save_order(
        reference_id=reference_id,

        user_id=user_id,

        plan_key=plan_key,

        amount_paise=(
            plan["price"] * 100
        ),
    )


    # --------------------------------------------------------
    # PAYU SUCCESS / FAILURE URL
    # --------------------------------------------------------

    success_url = (
        f"{PUBLIC_BASE_URL}"
        "/payu/success"
    )


    failure_url = (
        f"{PUBLIC_BASE_URL}"
        "/payu/failure"
    )


    # --------------------------------------------------------
    # PAYMENT LINK PAYLOAD
    # --------------------------------------------------------
    #
    # PayU current Payment Links API.
    #
    # IMPORTANT:
    # successURL / failureURL
    # NOT surl / furl
    #
    # --------------------------------------------------------

    payload = {

        "isAmountFilledByCustomer": False,

        "subAmount": plan["price"],

        "description": plan[
            "description"
        ],

        "currency": "INR",

        "source": "API",

        "invoiceNumber":
            invoice_number,

        "isPartialPaymentAllowed":
            False,

        "maxPaymentsAllowed": 1,

        "expiryDate":
            expiry_string,

        "customer": {

            "name":
                firstname,

            "phone":
                phone,

            "email":
                email,
        },

        "udf": {

            "udf1":
                str(user_id),

            "udf2":
                plan_key,

            "udf3":
                reference_id,

            "udf4":
                "",

            "udf5":
                "",
        },

        "viaEmail": False,

        "viaSms": False,

        "successURL":
            success_url,

        "failureURL":
            failure_url,

        "notes":
            f"Telegram user {user_id} | "
            f"Plan {plan_key} | "
            f"Reference {reference_id}",
    }


    print()
    print("=" * 80)
    print("PAYU CREATE PAYMENT LINK")
    print("=" * 80)
    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
    )
    print("=" * 80)


    try:

        result = await payu_request(
            "POST",

            PAYU_PAYMENT_LINK_URL,

            json_data=payload,
        )

    except Exception as e:

        mark_failed(
            reference_id
        )

        raise RuntimeError(
            f"PayU payment link request failed: {e}"
        )


    print()
    print("PAYU CREATE RESPONSE:")
    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )


    # --------------------------------------------------------
    # API STATUS
    # --------------------------------------------------------

    api_status = result.get(
        "status"
    )


    if (
        api_status not in (
            0,
            "0",
            None,
        )
    ):

        mark_failed(
            reference_id
        )

        message = (
            result.get(
                "message"
            )
            or result.get(
                "errorCode"
            )
            or "Unknown PayU error"
        )


        raise RuntimeError(
            f"PayU rejected payment link: "
            f"{message}"
        )


    payment_result = result.get(
        "result"
    )


    if not isinstance(
        payment_result,
        dict,
    ):

        payment_result = {}


    payment_link = (
        payment_result.get(
            "paymentLink"
        )
        or payment_result.get(
            "payment_link"
        )
    )


    returned_invoice = (
        payment_result.get(
            "invoiceNumber"
        )
        or invoice_number
    )


    if not payment_link:

        mark_failed(
            reference_id
        )

        raise RuntimeError(
            "PayU did not return paymentLink."
        )


    # --------------------------------------------------------
    # SAVE GENERATED LINK
    # --------------------------------------------------------

    update_payment_link(
        reference_id=
            reference_id,

        invoice_number=
            returned_invoice,

        payment_link=
            payment_link,

        expiry_at=
            expiry_at,
    )


    return {
        "reference_id":
            reference_id,

        "invoice_number":
            returned_invoice,

        "payment_link":
            payment_link,

        "expiry":
            expiry_string,

        "amount":
            plan["price"],
    }


# ============================================================
# PAYU TRANSACTION DETAILS
# ============================================================

async def get_payu_transactions(
    invoice_number: str,
    created_at: int,
):

    created_date = datetime.fromtimestamp(
        created_at
    ).strftime(
        "%Y-%m-%d"
    )


    today = india_now().strftime(
        "%Y-%m-%d"
    )


    url = (
        f"{PAYU_TRANSACTION_URL.rstrip('/')}"
        f"/{invoice_number}/txns"
    )


    params = {

        "pageSize": 50,

        "dateFrom":
            created_date,

        "dateTo":
            today,
    }


    result = await payu_request(
        "GET",

        url,

        params=params,
    )


    return result


# ============================================================
# EXTRACT TRANSACTION
# ============================================================

def extract_transaction(
    result,
    order,
):

    if not isinstance(
        result,
        dict,
    ):
        return None


    api_result = result.get(
        "result"
    )


    if not isinstance(
        api_result,
        dict,
    ):

        return None


    transactions = api_result.get(
        "data"
    )


    if not isinstance(
        transactions,
        list,
    ):

        return None


    if not transactions:

        return None


    # Prefer success transaction.

    success_transactions = [

        tx
        for tx in transactions

        if str(
            tx.get(
                "status",
                "",
            )
        ).lower()
        == "success"
    ]


    candidates = (
        success_transactions
        or transactions
    )


    # --------------------------------------------------------
    # Find matching amount
    # --------------------------------------------------------

    expected_amount = (
        order["amount_paise"]
        / 100
    )


    for tx in candidates:

        amount = (

            tx.get(
                "settledAmount"
            )

            or tx.get(
                "amount"
            )

            or tx.get(
                "amt"
            )
        )


        if amount is None:

            return tx


        try:

            if abs(
                float(amount)
                - expected_amount
            ) <= 0.01:

                return tx

        except Exception:

            continue


    return candidates[0]


# ============================================================
# VERIFY ONE ORDER
# ============================================================

async def verify_order(
    order: dict,
):

    if not order:

        return False


    if order["status"] == "paid":

        return True


    if not order.get(
        "invoice_number"
    ):

        return False


    try:

        result = await get_payu_transactions(
            order[
                "invoice_number"
            ],

            order[
                "created_at"
            ],
        )


        print()
        print(
            "PAYU TRANSACTION CHECK:",
            order["invoice_number"],
        )
        print(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
                default=str,
            )
        )


    except Exception as e:

        print(
            "Transaction verification error:",
            repr(e),
        )

        return False


    transaction = extract_transaction(
        result,
        order,
    )


    if not transaction:

        mark_pending(
            order[
                "reference_id"
            ]
        )

        return False


    status = str(
        transaction.get(
            "status",
            "",
        )
    ).lower().strip()


    print(
        "PAYU PAYMENT STATUS:",
        status,
    )


    # ========================================================
    # SUCCESS
    # ========================================================

    if status == "success":

        # ----------------------------------------------------
        # Amount validation
        # ----------------------------------------------------

        expected_amount = (
            order[
                "amount_paise"
            ]
            / 100
        )


        received_amount = (

            transaction.get(
                "settledAmount"
            )

            or transaction.get(
                "amount"
            )

            or transaction.get(
                "amt"
            )
        )


        if received_amount is not None:

            try:

                if abs(
                    float(received_amount)
                    - expected_amount
                ) > 0.01:

                    print(
                        "AMOUNT MISMATCH:",
                        received_amount,
                        expected_amount,
                    )

                    return False

            except Exception:

                return False


        # ----------------------------------------------------
        # Payment ID
        # ----------------------------------------------------

        payment_id = (

            transaction.get(
                "transactionId"
            )

            or transaction.get(
                "paymentId"
            )

            or str(
                transaction.get(
                    "transaction_id",
                    "",
                )
            )

            or order[
                "invoice_number"
            ]
        )


        # ----------------------------------------------------
        # Mark paid
        # ----------------------------------------------------

        mark_paid(
            order[
                "reference_id"
            ],

            str(
                payment_id
            ),
        )


        updated_order = get_order(
            order[
                "reference_id"
            ]
        )


        if updated_order:

            try:

                await deliver_access(
                    updated_order
                )

            except Exception as e:

                print(
                    "Access delivery error:",
                    repr(e),
                )


        return True


    # ========================================================
    # FAILED
    # ========================================================

    if status in (
        "failed",
        "failure",
        "cancelled",
        "canceled",
    ):

        mark_failed(
            order[
                "reference_id"
            ]
        )

        return False


    # ========================================================
    # EXPIRED
    # ========================================================

    if status in (
        "expired",
    ):

        mark_expired(
            order[
                "reference_id"
            ]
        )

        return False


    # ========================================================
    # PENDING / OTHER
    # ========================================================

    mark_pending(
        order[
            "reference_id"
        ]
    )


    return False


# ============================================================
# PAYMENT WATCHER
# ============================================================

async def payment_watcher():

    print(
        "Payment watcher started."
    )


    while True:

        try:

            orders = get_pending_orders()


            for order in orders:

                try:

                    # Do not verify too aggressively.

                    last_checked = (
                        order.get(
                            "last_checked_at"
                        )
                        or 0
                    )


                    if (
                        time.time()
                        - last_checked
                        < PAYMENT_CHECK_INTERVAL
                    ):

                        continue


                    # If local expiry is reached,
                    # check once more and then expire.

                    expiry_at = (
                        order.get(
                            "expiry_at"
                        )
                        or 0
                    )


                    if (
                        expiry_at
                        and time.time()
                        > expiry_at
                    ):

                        # One final PayU verification.

                        success = (
                            await verify_order(
                                order
                            )
                        )


                        if not success:

                            latest = get_order(
                                order[
                                    "reference_id"
                                ]
                            )


                            if latest and latest[
                                "status"
                            ] != "paid":

                                mark_expired(
                                    order[
                                        "reference_id"
                                    ]
                                )

                        continue


                    await verify_order(
                        order
                    )


                except Exception as e:

                    print(
                        "Watcher order error:",
                        repr(e),
                    )


                await asyncio.sleep(
                    0.5
                )


        except asyncio.CancelledError:

            print(
                "Payment watcher stopped."
            )

            raise


        except Exception as e:

            print(
                "Payment watcher loop error:",
                repr(e),
            )


        await asyncio.sleep(
            PAYMENT_CHECK_INTERVAL
        )


# ============================================================
# SUPPORT URL
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
# PAYMENT LINK KEYBOARD
# ============================================================

def payment_link_keyboard(
    reference_id: str,
    payment_link: str,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="💳 Open PayU Payment",
                    url=payment_link,
                )
            ],

            [
                InlineKeyboardButton(
                    text="🔄 Verify Payment",
                    callback_data=(
                        "verify:"
                        + reference_id
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

        "💳 Secure payment powered by PayU\n"

        "🔗 Pay using a secure Payment Link\n\n"

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

        f"⏱️ Payment link validity: "
        f"<b>{PAYMENT_LINK_EXPIRY_MINUTES} minutes</b>\n\n"

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

                user_id=
                    callback.from_user.id,

                plan_key=
                    plan_key,

                firstname=(
                    callback.from_user.first_name
                    or "Customer"
                ),
            )
        )


    except Exception as e:

        eid = error_id()


        log_error(
            f"PAYMENT LINK ERROR [{eid}]",
            repr(e),
        )


        await callback.message.answer(

            "❌ <b>Payment Link generate nahi ho paya.</b>\n\n"

            "PayU se payment link create karte waqt "
            "problem aayi.\n\n"

            f"🔎 Reason:\n"
            f"<code>{str(e)[:1500]}</code>\n\n"

            f"🧾 Error ID: "
            f"<code>{eid}</code>\n\n"

            "Agar problem baar-baar aa rahi hai "
            "to ye Error ID support ko bhejein."
        )

        return


    plan = PLANS[
        plan_key
    ]


    text = (

        "💳 <b>PAYU PAYMENT LINK</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Amount: "
        f"<b>₹{plan['price']}</b>\n\n"

        "👇 <b>PayU payment page open karne ke "
        "liye button dabayein.</b>\n\n"

        "After payment, bot automatically payment "
        "status check karega.\n\n"

        f"⏱️ Link expiry: "
        f"<b>{PAYMENT_LINK_EXPIRY_MINUTES} minutes</b>\n\n"

        f"🧾 Invoice:\n"
        f"<code>{result['invoice_number']}</code>"
    )


    await callback.message.answer(

        text,

        reply_markup=payment_link_keyboard(
            result["reference_id"],
            result["payment_link"],
        ),
    )


# ============================================================
# MAKE ACCESS LINK
# ============================================================

async def make_access_link(
    plan_key: str,
):

    plan = PLANS[
        plan_key
    ]


    # --------------------------------------------------------
    # ONE-TIME TELEGRAM INVITE
    # --------------------------------------------------------

    if plan.get(
        "channel_id"
    ):

        try:

            invite = (
                await bot.create_chat_invite_link(

                    chat_id=
                        plan[
                            "channel_id"
                        ],

                    member_limit=1,
                )
            )


            return invite.invite_link


        except Exception as e:

            print(
                "Telegram invite creation failed:",
                repr(e),
            )


    # --------------------------------------------------------
    # STATIC FALLBACK
    # --------------------------------------------------------

    if plan.get(
        "access_link"
    ):

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
            order[
                "plan_key"
            ]
        )
    )


    if not access_link:

        support = (
            SUPPORT_USERNAME
            or "admin"
        )


        await bot.send_message(

            order["user_id"],

            "✅ <b>Payment Confirmed!</b>\n\n"

            "Lekin access link configure nahi hai.\n\n"

            f"📞 Support: {support}",
        )


        return False


    plan = PLANS[
        order[
            "plan_key"
        ]
    ]


    await bot.send_message(

        order["user_id"],

        "🎉 <b>Payment Confirmed!</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Paid: "
        f"<b>₹{plan['price']}</b>\n\n"

        f"🧾 Invoice:\n"
        f"<code>{order.get('invoice_number')}</code>\n\n"

        "🔗 <b>Your Access Link:</b>\n"

        f"{access_link}\n\n"

        "⚠️ Link ko kisi ke saath share mat karein."
    )


    mark_access_sent(
        order[
            "reference_id"
        ]
    )


    return True


# ============================================================
# VERIFY CALLBACK
# ============================================================

@router.callback_query(
    F.data.startswith("verify:")
)
async def verify_callback(
    callback: CallbackQuery,
):

    await callback.answer(
        "Checking PayU..."
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


    if (
        order["user_id"]
        != callback.from_user.id
    ):

        await callback.message.answer(
            "❌ This order does not belong to you."
        )

        return


    if order["status"] == "paid":

        await callback.message.answer(
            "✅ <b>Payment already confirmed.</b>\n\n"
            "Use /myplan for your access."
        )

        return


    try:

        success = await verify_order(
            order
        )


        updated = get_order(
            reference_id
        )


        if (
            success
            or (
                updated
                and updated["status"]
                == "paid"
            )
        ):

            await callback.message.answer(

                "🎉 <b>Payment Confirmed!</b>\n\n"

                "Aapka payment successfully verify "
                "ho gaya hai.\n\n"

                "🔗 Access link aapko send kar diya gaya hai.\n\n"

                "Use /myplan anytime."
            )

            return


        if updated:

            status = updated[
                "status"
            ]

        else:

            status = "pending"


        if status == "failed":

            await callback.message.answer(
                "❌ <b>Payment Failed.</b>\n\n"
                "PayU transaction failed."
            )

            return


        if status == "expired":

            await callback.message.answer(
                "⌛ <b>Payment Link Expired.</b>\n\n"
                "Please create a new payment link."
            )

            return


        await callback.message.answer(

            "⏳ <b>Payment abhi confirm nahi hua.</b>\n\n"

            "Agar payment abhi-abhi kiya hai to "
            "20–30 seconds wait karke dobara "
            "<b>Verify Payment</b> dabayein.\n\n"

            f"PayU status: "
            f"<code>{status}</code>"
        )


    except Exception as e:

        eid = error_id()


        log_error(
            f"MANUAL VERIFICATION ERROR [{eid}]",
            repr(e),
        )


        await callback.message.answer(

            "❌ <b>Payment verification failed.</b>\n\n"

            f"Error ID: "
            f"<code>{eid}</code>\n\n"

            "Thodi der baad dobara try karein."
        )


# ============================================================
# MY PLAN CALLBACK
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


# ============================================================
# MY PLAN COMMAND
# ============================================================

@router.message(
    Command("myplan")
)
async def myplan_message(
    message: Message,
):

    await send_my_plan(
        message
    )


# ============================================================
# SEND MY PLAN
# ============================================================

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


    status = str(
        order[
            "status"
        ]
    ).upper()


    # ========================================================
    # PAID
    # ========================================================

    if order[
        "status"
    ] == "paid":


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


        text = (

            "📋 <b>My Plan</b>\n"

            "━━━━━━━━━━━━━━━━━━\n\n"

            f"📦 Plan: "
            f"<b>{plan.get('name', order['plan_key'])}</b>\n"

            f"💰 Amount: "
            f"<b>₹{order['amount_paise'] // 100}</b>\n"

            "📌 Status: <b>PAID</b>\n"

            f"🧾 Invoice: "
            f"<code>{order.get('invoice_number')}</code>\n"

            f"💳 Payment ID: "
            f"<code>{order.get('payment_id') or '-'}</code>\n\n"

            "Your lifetime access is active."
        )


        await message.answer(
            text,
            reply_markup=keyboard,
        )

        return


    # ========================================================
    # UNPAID
    # ========================================================

    keyboard_rows = []


    if order.get(
        "payment_link"
    ):

        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text="💳 Open PayU Payment",

                    url=order[
                        "payment_link"
                    ],
                )
            ]
        )


    if order.get(
        "invoice_number"
    ):

        keyboard_rows.append(
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


    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="↩️ Back",

                callback_data="home",
            )
        ]
    )


    keyboard = InlineKeyboardMarkup(
        inline_keyboard=keyboard_rows
    )


    text = (

        "📋 <b>My Plan</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan.get('name', order['plan_key'])}</b>\n"

        f"💰 Amount: "
        f"<b>₹{order['amount_paise'] // 100}</b>\n"

        f"📌 Status: "
        f"<b>{status}</b>\n\n"

        "Payment complete karne ke baad "
        "<b>Verify Payment</b> dabayein."
    )


    await message.answer(
        text,
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
                order[
                    "plan_key"
                ]
            )
        )


        if not access_link:

            await callback.message.answer(

                "❌ Access link unavailable.\n\n"

                f"Contact {SUPPORT_USERNAME or 'admin'}."
            )

            return


        await callback.message.answer(

            "🔗 <b>Your Access Link</b>\n\n"

            f"{access_link}"
        )


    except Exception as e:

        print(
            "Manual access error:",
            repr(e),
        )


        await callback.message.answer(

            "❌ Access send nahi ho paya.\n\n"

            f"Contact {SUPPORT_USERNAME or 'admin'}."
        )


# ============================================================
# PAYU SUCCESS REDIRECT
# ============================================================

async def payu_success(
    request: web.Request,
):

    try:

        data = {}


        if request.method == "POST":

            post = await request.post()

            data.update(
                dict(post)
            )


        if request.query:

            data.update(
                dict(request.query)
            )


    except Exception as e:

        return web.Response(
            status=400,

            text=(
                "Invalid PayU response: "
                + str(e)
            ),
        )


    print()
    print(
        "PAYU SUCCESS REDIRECT:"
    )
    print(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )


    invoice = (

        data.get(
            "invoiceNumber"
        )

        or data.get(
            "invoice_number"
        )

        or data.get(
            "invoice"
        )
    )


    if invoice:

        order = get_order_by_invoice(
            invoice
        )


        if order:

            try:

                await verify_order(
                    order
                )

            except Exception as e:

                print(
                    "Success redirect verification error:",
                    repr(e),
                )


    return web.Response(

        status=200,

        content_type="text/html",

        text="""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
      content="width=device-width, initial-scale=1">
<title>Payment Successful</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>✅ Payment received</h2>
<p>Payment verification is being completed.</p>
<p>You can return to Telegram.</p>
</body>
</html>
""",
    )


# ============================================================
# PAYU FAILURE REDIRECT
# ============================================================

async def payu_failure(
    request: web.Request,
):

    try:

        data = {}


        if request.method == "POST":

            post = await request.post()

            data.update(
                dict(post)
            )


        if request.query:

            data.update(
                dict(request.query)
            )


    except Exception:

        data = {}


    print()
    print(
        "PAYU FAILURE REDIRECT:"
    )
    print(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )


    invoice = (

        data.get(
            "invoiceNumber"
        )

        or data.get(
            "invoice_number"
        )

        or data.get(
            "invoice"
        )
    )


    if invoice:

        order = get_order_by_invoice(
            invoice
        )


        if (
            order
            and order[
                "status"
            ] != "paid"
        ):

            mark_failed(
                order[
                    "reference_id"
                ]
            )


    return web.Response(

        status=200,

        content_type="text/html",

        text="""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
      content="width=device-width, initial-scale=1">
<title>Payment Failed</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>❌ Payment Failed</h2>
<p>Please return to Telegram and create a new payment link.</p>
</body>
</html>
""",
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
                "telegram-store-bot-payu-payment-links",

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


    app.router.add_get(
        "/payu/success",
        payu_success,
    )


    app.router.add_post(
        "/payu/success",
        payu_success,
    )


    app.router.add_get(
        "/payu/failure",
        payu_failure,
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


    print()
    print(
        "=============================================="
    )
    print(
        "PAYU WEB SERVER STARTED"
    )
    print(
        "=============================================="
    )


    print(
        "Health:",
        f"{PUBLIC_BASE_URL}/health",
    )


    print(
        "Success:",
        f"{PUBLIC_BASE_URL}/payu/success",
    )


    print(
        "Failure:",
        f"{PUBLIC_BASE_URL}/payu/failure",
    )


    print(
        "=============================================="
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
        "127.0.0.1"
        in PUBLIC_BASE_URL.lower()
    ):

        errors.append(
            "PUBLIC_BASE_URL cannot be 127.0.0.1"
        )


    if (
        "your-domain.com"
        in PUBLIC_BASE_URL.lower()
    ):

        errors.append(
            "Replace example PUBLIC_BASE_URL"
        )


    parsed = urlparse(
        PUBLIC_BASE_URL
    )


    if parsed.scheme not in (
        "http",
        "https",
    ):

        errors.append(
            "PUBLIC_BASE_URL must start with http:// or https://"
        )


    if (
        PAYMENT_LINK_EXPIRY_MINUTES
        < 5
    ):

        errors.append(
            "PAYMENT_LINK_EXPIRY_MINUTES must be >= 5"
        )


    if errors:

        raise RuntimeError(

            "Configuration errors:\n"

            + "\n".join(
                "- " + x
                for x in errors
            )
        )


# ============================================================
# MAIN
# ============================================================

async def main():

    global bot
    global payment_watcher_task


    validate_config()


    init_db()


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


    runner = (
        await start_web_server()
    )


    # --------------------------------------------------------
    # PAYMENT WATCHER
    # --------------------------------------------------------

    payment_watcher_task = asyncio.create_task(
        payment_watcher()
    )


    try:

        print()
        print(
            "=============================================="
        )

        print(
            "DARK STORE BOT STARTED"
        )

        print(
            "=============================================="
        )

        print(
            "Payment Gateway: PayU Payment Links"
        )

        print(
            "OAuth URL:",
            PAYU_TOKEN_URL,
        )

        print(
            "Payment Link URL:",
            PAYU_PAYMENT_LINK_URL,
        )

        print(
            "Transaction URL:",
            PAYU_TRANSACTION_URL,
        )

        print(
            "Merchant ID:",
            PAYU_MERCHANT_ID,
        )

        print(
            "Payment Link Expiry:",
            PAYMENT_LINK_EXPIRY_MINUTES,
            "minutes",
        )

        print(
            "Auto Verification:",
            PAYMENT_CHECK_INTERVAL,
            "seconds",
        )

        print(
            "Public Base URL:",
            PUBLIC_BASE_URL,
        )

        print(
            "=============================================="
        )


        await dp.start_polling(
            bot
        )


    finally:

        if payment_watcher_task:

            payment_watcher_task.cancel()


            try:

                await payment_watcher_task

            except asyncio.CancelledError:

                pass


        await runner.cleanup()


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
