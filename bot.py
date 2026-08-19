import asyncio
import io
import json
import os
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
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


# ============================================================
# PAYU OAUTH
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


# PayU Production OAuth
PAYU_TOKEN_URL = os.getenv(
    "PAYU_TOKEN_URL",
    "https://accounts.payu.in/oauth/token",
).strip()


# ============================================================
# PAYU PAYMENT LINK API
# ============================================================

PAYU_PAYMENT_LINK_URL = os.getenv(
    "PAYU_PAYMENT_LINK_URL",
    "https://oneapi.payu.in/payment-links",
).strip()


# ============================================================
# PAYU TRANSACTION API
# ============================================================

PAYU_TRANSACTION_URL = os.getenv(
    "PAYU_TRANSACTION_URL",
    "https://oneapi.payu.in/payment-links",
).strip()


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
).strip().rstrip("/")


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

_payu_token = None

_payu_token_expires_at = 0


# ============================================================
# ERROR ID
# ============================================================

def generate_error_id():

    return (
        f"E-{int(time.time())}-"
        f"{secrets.token_hex(2).upper()}"
    )


def log_error(
    error_id,
    title,
    details=None,
):

    print()
    print("=" * 70)
    print(f"PAYU ERROR [{error_id}]")
    print("=" * 70)
    print("TITLE:")
    print(title)

    if details is not None:

        print()
        print("DETAILS:")

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


def column_exists(
    conn,
    table,
    column,
):

    row = conn.execute(
        f"""
        PRAGMA table_info({table})
        """
    ).fetchall()

    return any(
        item["name"] == column
        for item in row
    )


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

                status TEXT NOT NULL
                    DEFAULT 'created',

                payment_id TEXT,

                mihpayid TEXT,

                invoice_number TEXT,

                payment_link_id TEXT,

                payment_link_url TEXT,

                created_at INTEGER NOT NULL,

                paid_at INTEGER,

                access_sent INTEGER NOT NULL
                    DEFAULT 0
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

        # ----------------------------------------------------
        # Migration for old database
        # ----------------------------------------------------

        migrations = [

            (
                "payment_link_id",
                "TEXT",
            ),

            (
                "payment_link_url",
                "TEXT",
            ),

            (
                "invoice_number",
                "TEXT",
            ),

            (
                "payment_id",
                "TEXT",
            ),

            (
                "mihpayid",
                "TEXT",
            ),

            (
                "paid_at",
                "INTEGER",
            ),

            (
                "access_sent",
                "INTEGER NOT NULL DEFAULT 0",
            ),
        ]

        for column, definition in migrations:

            if not column_exists(
                conn,
                "orders",
                column,
            ):

                try:

                    conn.execute(
                        f"""
                        ALTER TABLE orders
                        ADD COLUMN {column}
                        {definition}
                        """
                    )

                except Exception as e:

                    print(
                        "Migration error:",
                        column,
                        repr(e),
                    )

        conn.commit()


# ============================================================
# SAVE ORDER
# ============================================================

def save_order(
    reference_id,
    user_id,
    plan_key,
    amount_paise,
    txnid,
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
# UPDATE PAYMENT LINK
# ============================================================

def save_payment_link(
    reference_id,
    invoice_number,
    payment_link_id,
    payment_link_url,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET
                invoice_number = ?,
                payment_link_id = ?,
                payment_link_url = ?

            WHERE reference_id = ?
            """,

            (
                invoice_number,
                payment_link_id,
                payment_link_url,
                reference_id,
            ),
        )

        conn.commit()


# ============================================================
# GET ORDER
# ============================================================

def get_order(
    reference_id,
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


def get_order_by_txnid(
    txnid,
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


def get_latest_order(
    user_id,
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
    reference_id,
    payment_id,
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
    reference_id,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET status = 'failed'

            WHERE reference_id = ?

            AND status != 'paid'
            """,

            (
                reference_id,
            ),
        )

        conn.commit()


# ============================================================
# ACCESS SENT
# ============================================================

def mark_access_sent(
    reference_id,
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
# PAYU TOKEN
# ============================================================

async def get_payu_access_token():

    global _payu_token
    global _payu_token_expires_at

    # --------------------------------------------------------
    # Reuse token
    # --------------------------------------------------------

    if (
        _payu_token
        and time.time()
        < _payu_token_expires_at - 60
    ):

        return _payu_token

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
            "create_payment_links "
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

                text = await response.text()

                print()
                print(
                    "PAYU TOKEN HTTP:",
                    response.status,
                )

                # Don't print credentials/token
                print(
                    "PAYU TOKEN RESPONSE:",
                    text[:5000],
                )

                if response.status >= 400:

                    raise RuntimeError(
                        f"{error_id}|"
                        f"PayU token HTTP "
                        f"{response.status}: "
                        f"{text[:1500]}"
                    )

                try:

                    result = json.loads(
                        text
                    )

                except json.JSONDecodeError:

                    raise RuntimeError(
                        f"{error_id}|"
                        "PayU token response "
                        "was not JSON."
                    )

                access_token = result.get(
                    "access_token"
                )

                if not access_token:

                    error_message = (
                        result.get(
                            "error_description"
                        )
                        or result.get(
                            "error"
                        )
                        or "Access token missing."
                    )

                    raise RuntimeError(
                        f"{error_id}|"
                        f"{error_message}"
                    )

                expires_in = int(
                    result.get(
                        "expires_in",
                        3600,
                    )
                )

                _payu_token = (
                    access_token
                )

                _payu_token_expires_at = (
                    time.time()
                    + expires_in
                )

                return access_token

    except asyncio.TimeoutError as e:

        raise RuntimeError(
            f"{error_id}|"
            "PayU token request timed out."
        ) from e

    except aiohttp.ClientError as e:

        raise RuntimeError(
            f"{error_id}|"
            f"PayU token network error: {e}"
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

    # --------------------------------------------------------
    # Validation
    # --------------------------------------------------------

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
    # IDs
    # --------------------------------------------------------

    txnid = (
        f"TG"
        f"{user_id}"
        f"{int(time.time())}"
        f"{secrets.token_hex(5).upper()}"
    )

    reference_id = (
        f"ORD"
        f"{secrets.token_hex(12).upper()}"
    )

    # PayU invoiceNumber must be alphanumeric.
    invoice_number = (
        f"INV"
        f"{int(time.time())}"
        f"{secrets.token_hex(5).upper()}"
    )

    firstname = (
        firstname
        or "Customer"
    )

    # --------------------------------------------------------
    # Save order FIRST
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
    # Get OAuth token
    # --------------------------------------------------------

    try:

        access_token = (
            await get_payu_access_token()
        )

    except Exception:

        mark_failed(
            reference_id
        )

        raise

    # --------------------------------------------------------
    # Expiry
    # --------------------------------------------------------

    expiry_date = (
        datetime.now()
        + timedelta(minutes=30)
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    # --------------------------------------------------------
    # Customer
    # --------------------------------------------------------

    customer_email = (
        f"telegram"
        f"{user_id}"
        f"@example.com"
    )

    # --------------------------------------------------------
    # Payload
    # --------------------------------------------------------

    payload = {

        "isAmountFilledByCustomer":
            False,

        "isPartialPaymentAllowed":
            False,

        "subAmount":
            plan["price"],

        "description":
            plan["description"],

        "invoiceNumber":
            invoice_number,

        "expiryDate":
            expiry_date,

        # No additional tax.
        "tax":
            0,

        "shippingCharge":
            0,

        "source":
            "API",

        "currency":
            "INR",

        "maxPaymentsAllowed":
            1,

        "customer": {

            "name":
                firstname,

            "phone":
                "9999999999",

            "email":
                customer_email,
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

        "viaEmail":
            False,

        "viaSms":
            False,

    }

    # --------------------------------------------------------
    # Optional success/failure URLs
    # --------------------------------------------------------

    if PUBLIC_BASE_URL:

        payload[
            "successURL"
        ] = (
            f"{PUBLIC_BASE_URL}"
            "/payu/success"
        )

        payload[
            "failureURL"
        ] = (
            f"{PUBLIC_BASE_URL}"
            "/payu/failure"
        )

    # --------------------------------------------------------
    # DEBUG
    # --------------------------------------------------------

    safe_payload = dict(
        payload
    )

    print()
    print("=" * 70)
    print("PAYU PAYMENT LINK REQUEST")
    print("=" * 70)

    print(
        json.dumps(
            safe_payload,
            indent=2,
            ensure_ascii=False,
        )
    )

    print("=" * 70)

    # --------------------------------------------------------
    # HTTP
    # --------------------------------------------------------

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(

                PAYU_PAYMENT_LINK_URL,

                json=payload,

                headers={

                    "Authorization":
                        f"Bearer {access_token}",

                    "merchantId":
                        PAYU_MERCHANT_ID,

                    "Content-Type":
                        "application/json",

                    "Accept":
                        "application/json",
                },

            ) as response:

                http_status = (
                    response.status
                )

                response_text = (
                    await response.text()
                )

    except asyncio.TimeoutError as e:

        mark_failed(
            reference_id
        )

        log_error(
            error_id,
            "Payment link request timeout",
            repr(e),
        )

        raise RuntimeError(
            f"{error_id}|"
            "PayU payment link request timed out."
        ) from e

    except aiohttp.ClientError as e:

        mark_failed(
            reference_id
        )

        log_error(
            error_id,
            "Payment link network error",
            repr(e),
        )

        raise RuntimeError(
            f"{error_id}|"
            f"PayU network error: {e}"
        ) from e

    # --------------------------------------------------------
    # Response logging
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("PAYU PAYMENT LINK RESPONSE")
    print("=" * 70)

    print(
        "HTTP:",
        http_status,
    )

    print(
        response_text[:10000]
    )

    print("=" * 70)

    # --------------------------------------------------------
    # HTTP ERROR
    # --------------------------------------------------------

    if http_status >= 400:

        mark_failed(
            reference_id
        )

        log_error(
            error_id,
            f"PayU HTTP {http_status}",
            response_text[:5000],
        )

        raise RuntimeError(
            f"{error_id}|"
            f"PayU HTTP {http_status}: "
            f"{response_text[:2000]}"
        )

    # --------------------------------------------------------
    # JSON
    # --------------------------------------------------------

    try:

        result = json.loads(
            response_text
        )

    except json.JSONDecodeError as e:

        mark_failed(
            reference_id
        )

        raise RuntimeError(
            f"{error_id}|"
            "PayU returned invalid JSON: "
            f"{response_text[:1500]}"
        ) from e

    # --------------------------------------------------------
    # Parse result
    # --------------------------------------------------------

    result_data = result.get(
        "result"
    )

    if not isinstance(
        result_data,
        dict,
    ):

        result_data = {}

    payment_link = (
        result_data.get(
            "paymentLink"
        )
    )

    # --------------------------------------------------------
    # Check success
    # --------------------------------------------------------

    if (
        result.get("status") != 0
        or not payment_link
    ):

        mark_failed(
            reference_id
        )

        message = (
            result.get(
                "message"
            )
            or "PayU did not return payment link."
        )

        error_code = result.get(
            "errorCode"
        )

        reason = (
            f"{message}"
        )

        if error_code is not None:

            reason += (
                f" | Error Code: "
                f"{error_code}"
            )

        log_error(
            error_id,
            "Payment link generation failed",
            result,
        )

        raise RuntimeError(
            f"{error_id}|{reason}"
        )

    # --------------------------------------------------------
    # Payment link ID
    # --------------------------------------------------------

    payment_link_id = (
        result_data.get(
            "invoiceNumber"
        )
        or invoice_number
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_payment_link(
        reference_id=reference_id,

        invoice_number=invoice_number,

        payment_link_id=payment_link_id,

        payment_link_url=payment_link,
    )

    # --------------------------------------------------------
    # Return
    # --------------------------------------------------------

    return {

        "reference_id":
            reference_id,

        "txnid":
            txnid,

        "invoice_number":
            invoice_number,

        "payment_link_id":
            payment_link_id,

        "payment_link":
            payment_link,

        "amount":
            plan["price"],
    }


# ============================================================
# GET SINGLE PAYMENT LINK
# ============================================================

async def get_payu_payment_link(
    invoice_number,
):

    error_id = generate_error_id()

    token = (
        await get_payu_access_token()
    )

    url = (
        PAYU_PAYMENT_LINK_URL.rstrip("/")
        + "/"
        + invoice_number
    )

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(

                url,

                headers={

                    "Authorization":
                        f"Bearer {token}",

                    "merchantId":
                        PAYU_MERCHANT_ID,

                    "Accept":
                        "application/json",
                },

            ) as response:

                text = await response.text()

                print()
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
                        f"{text[:1500]}"
                    )

                try:

                    return json.loads(
                        text
                    )

                except json.JSONDecodeError:

                    raise RuntimeError(
                        f"{error_id}|"
                        "Invalid PayU status response."
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
# GET TRANSACTIONS FOR PAYMENT LINK
# ============================================================

async def get_payu_transactions(
    invoice_number,
):

    error_id = generate_error_id()

    token = (
        await get_payu_access_token()
    )

    # --------------------------------------------------------
    # Today's date
    # --------------------------------------------------------

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    tomorrow = (
        datetime.now()
        + timedelta(days=1)
    ).strftime(
        "%Y-%m-%d"
    )

    url = (
        PAYU_TRANSACTION_URL.rstrip("/")
        + "/"
        + invoice_number
        + "/txns"
    )

    params = {

        "pageSize":
            "50",

        "dateFrom":
            today,

        "dateTo":
            tomorrow,
    }

    timeout = aiohttp.ClientTimeout(
        total=30
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(

                url,

                params=params,

                headers={

                    "Authorization":
                        f"Bearer {token}",

                    "merchantId":
                        PAYU_MERCHANT_ID,

                    "Accept":
                        "application/json",
                },

            ) as response:

                text = await response.text()

                print()
                print(
                    "PAYU TRANSACTION HTTP:",
                    response.status,
                )

                print(
                    "PAYU TRANSACTION RESPONSE:",
                    text[:10000],
                )

                if response.status >= 400:

                    raise RuntimeError(
                        f"{error_id}|"
                        f"PayU transaction HTTP "
                        f"{response.status}: "
                        f"{text[:1500]}"
                    )

                try:

                    return json.loads(
                        text
                    )

                except json.JSONDecodeError:

                    raise RuntimeError(
                        f"{error_id}|"
                        "Invalid PayU transaction response."
                    )

    except asyncio.TimeoutError as e:

        raise RuntimeError(
            f"{error_id}|"
            "PayU transaction request timed out."
        ) from e

    except aiohttp.ClientError as e:

        raise RuntimeError(
            f"{error_id}|"
            f"PayU transaction network error: {e}"
        ) from e


# ============================================================
# FIND SUCCESSFUL TRANSACTION
# ============================================================

async def verify_payu_order(
    order,
):

    invoice_number = (
        order.get(
            "invoice_number"
        )
    )

    if not invoice_number:

        return {
            "success":
                False,

            "status":
                "missing_invoice",

            "message":
                "Payment link invoice number missing.",
        }

    result = await get_payu_transactions(
        invoice_number
    )

    if not isinstance(
        result,
        dict,
    ):

        return {
            "success":
                False,

            "status":
                "invalid_response",

            "message":
                "Invalid PayU response.",
        }

    transaction_result = result.get(
        "result"
    )

    if not isinstance(
        transaction_result,
        dict,
    ):

        transaction_result = {}

    rows = transaction_result.get(
        "data",
        []
    )

    if not isinstance(
        rows,
        list,
    ):

        rows = []

    expected_amount = (
        order["amount_paise"]
        / 100
    )

    # --------------------------------------------------------
    # Search transaction
    # --------------------------------------------------------

    for transaction in rows:

        if not isinstance(
            transaction,
            dict,
        ):

            continue

        status = str(
            transaction.get(
                "status",
                ""
            )
        ).lower()

        settled_amount = (
            transaction.get(
                "settledAmount"
            )
        )

        if settled_amount is None:

            settled_amount = (
                transaction.get(
                    "amount"
                )
            )

        # ----------------------------------------------------
        # Amount
        # ----------------------------------------------------

        try:

            received_amount = float(
                settled_amount
                if settled_amount is not None
                else 0
            )

        except Exception:

            received_amount = 0

        # ----------------------------------------------------
        # Successful
        # ----------------------------------------------------

        if status == "success":

            if abs(
                received_amount
                - expected_amount
            ) > 0.01:

                return {

                    "success":
                        False,

                    "status":
                        "amount_mismatch",

                    "message":
                        (
                            "Payment amount mismatch."
                        ),

                    "received":
                        received_amount,

                    "expected":
                        expected_amount,

                    "transaction":
                        transaction,
                }

            payment_id = (
                transaction.get(
                    "paymentId"
                )
                or transaction.get(
                    "transactionId"
                )
                or transaction.get(
                    "merchantReferenceId"
                )
                or order["txnid"]
            )

            return {

                "success":
                    True,

                "status":
                    "success",

                "payment_id":
                    str(payment_id),

                "amount":
                    received_amount,

                "transaction":
                    transaction,
            }

    # --------------------------------------------------------
    # Failure
    # --------------------------------------------------------

    for transaction in rows:

        if not isinstance(
            transaction,
            dict,
        ):

            continue

        status = str(
            transaction.get(
                "status",
                ""
            )
        ).lower()

        if status in (
            "failed",
            "failure",
        ):

            return {

                "success":
                    False,

                "status":
                    "failed",

                "message":
                    "PayU payment failed.",

                "transaction":
                    transaction,
            }

    # --------------------------------------------------------
    # Pending
    # --------------------------------------------------------

    return {

        "success":
            False,

        "status":
            "pending",

        "message":
            "Payment not confirmed yet.",
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


def plan_keyboard(
    plan_key,
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
    chat_id,
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
        "🔗 Payment Link payment\n\n"

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
        "💳 Pay securely using PayU\n"
        "🔗 Payment Link generated instantly\n\n"

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
        "Generating PayU payment link..."
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

        print()
        print(
            "FINAL PAYMENT LINK ERROR:",
            repr(e),
        )
        print()

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

    # --------------------------------------------------------
    # Payment Link
    # --------------------------------------------------------

    plan = PLANS[
        plan_key
    ]

    payment_link = result[
        "payment_link"
    ]

    reference_id = result[
        "reference_id"
    ]

    text = (

        "💳 <b>PAYU PAYMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Amount: "
        f"<b>₹{plan['price']}</b>\n\n"

        "🔐 <b>Lifetime Access</b>\n\n"

        "👇 Payment karne ke liye "
        "<b>Pay Now</b> button dabayein.\n\n"

        "Payment complete hone ke baad "
        "<b>Verify Payment</b> press karein.\n\n"

        f"🧾 Order ID:\n"
        f"<code>{reference_id}</code>\n\n"

        "⏱️ Payment Link validity: "
        "<b>30 minutes</b>"
    )

    keyboard = InlineKeyboardMarkup(

        inline_keyboard=[

            [

                InlineKeyboardButton(

                    text="💳 Pay Now",

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

    await callback.message.answer(
        text,
        reply_markup=keyboard,
    )


# ============================================================
# MAKE ACCESS LINK
# ============================================================

async def make_access_link(
    plan_key,
) -> Optional[str]:

    plan = PLANS[
        plan_key
    ]

    # --------------------------------------------------------
    # One-time Telegram invite
    # --------------------------------------------------------

    if plan["channel_id"]:

        try:

            invite = (
                await bot.create_chat_invite_link(

                    chat_id=plan[
                        "channel_id"
                    ],

                    member_limit=1,
                )
            )

            return invite.invite_link

        except Exception as e:

            print(
                "Invite creation failed:",
                repr(e),
            )

    # --------------------------------------------------------
    # Static fallback
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
    order,
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

    # --------------------------------------------------------
    # Security
    # --------------------------------------------------------

    if (
        order["user_id"]
        != callback.from_user.id
    ):

        await callback.message.answer(
            "❌ Invalid order."
        )

        return

    # --------------------------------------------------------
    # Already paid
    # --------------------------------------------------------

    if order["status"] == "paid":

        await callback.message.answer(
            "✅ Payment already confirmed.\n\n"
            "Use /myplan to get your access."
        )

        return

    # --------------------------------------------------------
    # Verify
    # --------------------------------------------------------

    try:

        verification = (
            await verify_payu_order(
                order
            )
        )

    except Exception as e:

        error_id = (
            generate_error_id()
        )

        log_error(
            error_id,
            "PayU payment verification failed",
            repr(e),
        )

        await callback.message.answer(

            "❌ <b>Payment verification failed.</b>\n\n"

            f"🧾 Error ID: "
            f"<code>{error_id}</code>\n\n"

            "Thodi der baad dobara try karein."
        )

        return

    # ========================================================
    # SUCCESS
    # ========================================================

    if verification[
        "success"
    ]:

        payment_id = (
            verification.get(
                "payment_id"
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

            delivered = (
                await deliver_access(
                    updated
                )
            )

            if not delivered:

                await callback.message.answer(

                    "✅ <b>Payment confirmed.</b>\n\n"

                    "❌ Access link generate nahi ho paya.\n"

                    f"📞 Support: "
                    f"{SUPPORT_USERNAME or 'Admin'}"
                )

        except Exception as e:

            print(
                "Access delivery failed:",
                repr(e),
            )

            await callback.message.answer(

                "✅ <b>Payment confirmed.</b>\n\n"

                "❌ Access delivery mein problem aayi.\n"

                f"📞 Contact: "
                f"{SUPPORT_USERNAME or 'Admin'}"
            )

        return

    # ========================================================
    # AMOUNT MISMATCH
    # ========================================================

    if verification[
        "status"
    ] == "amount_mismatch":

        await callback.message.answer(

            "🚨 <b>Amount mismatch detected.</b>\n\n"

            f"Expected: "
            f"<b>₹{verification['expected']:.2f}</b>\n"

            f"Received: "
            f"<b>₹{verification['received']:.2f}</b>\n\n"

            "Access automatically nahi diya gaya.\n"
            "Support se contact karein."
        )

        return

    # ========================================================
    # FAILED
    # ========================================================

    if verification[
        "status"
    ] == "failed":

        mark_failed(
            order["reference_id"]
        )

        await callback.message.answer(
            "❌ <b>Payment failed.</b>\n\n"
            "Aap naya payment link generate karke "
            "dobara payment kar sakte hain."
        )

        return

    # ========================================================
    # PENDING
    # ========================================================

    await callback.message.answer(

        "⏳ <b>Payment abhi confirm nahi hua.</b>\n\n"

        "Agar payment abhi-abhi kiya hai, "
        "30–60 seconds wait karke dobara "
        "<b>Verify Payment</b> dabayein."
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

    status = (
        order["status"]
        .upper()
    )

    # ========================================================
    # PAID
    # ========================================================

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

            f"🧾 Transaction:\n"
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

    # ========================================================
    # UNPAID
    # ========================================================

    keyboard_buttons = []

    if order.get(
        "payment_link_url"
    ):

        keyboard_buttons.append(

            [

                InlineKeyboardButton(

                    text="💳 Pay Now",

                    url=order[
                        "payment_link_url"
                    ],
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

        "Payment karne ke baad "
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

                f"Contact "
                f"{SUPPORT_USERNAME or 'admin'}."
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

            f"Contact "
            f"{SUPPORT_USERNAME or 'admin'}."
        )


# ============================================================
# PAYU SUCCESS PAGE
# ============================================================

async def payu_success(
    request: web.Request,
):

    return web.Response(

        status=200,

        content_type="text/html",

        text="""
<!DOCTYPE html>

<html>

<head>

<meta charset="utf-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Payment Successful</title>

<style>

body {
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    text-align: center;
    padding: 50px 20px;
}

.box {
    max-width: 500px;
    margin: auto;
    background: white;
    padding: 35px;
    border-radius: 16px;
    box-shadow: 0 5px 25px rgba(0,0,0,.08);
}

h1 {
    color: #16a34a;
}

</style>

</head>

<body>

<div class="box">

<h1>✅ Payment Successful</h1>

<p>
Your payment has been received by PayU.
</p>

<p>
Please return to Telegram and press
<b>Verify Payment</b>.
</p>

</div>

</body>

</html>
""",
    )


# ============================================================
# PAYU FAILURE PAGE
# ============================================================

async def payu_failure(
    request: web.Request,
):

    return web.Response(

        status=200,

        content_type="text/html",

        text="""
<!DOCTYPE html>

<html>

<head>

<meta charset="utf-8">

<meta name="viewport"
content="width=device-width, initial-scale=1">

<title>Payment Failed</title>

<style>

body {
    font-family: Arial, sans-serif;
    background: #f5f5f5;
    text-align: center;
    padding: 50px 20px;
}

.box {
    max-width: 500px;
    margin: auto;
    background: white;
    padding: 35px;
    border-radius: 16px;
    box-shadow: 0 5px 25px rgba(0,0,0,.08);
}

h1 {
    color: #dc2626;
}

</style>

</head>

<body>

<div class="box">

<h1>❌ Payment Failed</h1>

<p>
The payment was not completed.
</p>

<p>
Please return to Telegram and create
a new payment link.
</p>

</div>

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
                "telegram-store-bot-payu",

            "payment":
                "PayU Payment Link",

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
        "======================================"
    )

    print(
        "PayU callback/web server started."
    )

    print(
        f"Host: "
        f"{WEBHOOK_HOST}"
    )

    print(
        f"Port: "
        f"{WEBHOOK_PORT}"
    )

    print(
        f"Health: "
        f"{PUBLIC_BASE_URL}/health"
    )

    print(
        f"Success: "
        f"{PUBLIC_BASE_URL}/payu/success"
    )

    print(
        f"Failure: "
        f"{PUBLIC_BASE_URL}/payu/failure"
    )

    print(
        "======================================"
    )

    print()

    return runner


# ============================================================
# CONFIG VALIDATION
# ============================================================

def validate_config():

    errors = []

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    if not BOT_TOKEN:

        errors.append(
            "BOT_TOKEN missing"
        )

    # --------------------------------------------------------
    # PayU OAuth
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Public URL
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # URLs
    # --------------------------------------------------------

    if not PAYU_TOKEN_URL:

        errors.append(
            "PAYU_TOKEN_URL missing"
        )

    if not PAYU_PAYMENT_LINK_URL:

        errors.append(
            "PAYU_PAYMENT_LINK_URL missing"
        )

    # --------------------------------------------------------
    # Plan validation
    # --------------------------------------------------------

    for key, plan in PLANS.items():

        if not plan["price"]:

            errors.append(
                f"{key}: price missing"
            )

    if errors:

        raise RuntimeError(

            "Configuration errors:\n"

            + "\n".join(
                f"- {error}"
                for error in errors
            )
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

    # --------------------------------------------------------
    # Bot
    # --------------------------------------------------------

    bot = Bot(

        token=BOT_TOKEN,

        default=(
            DefaultBotProperties(
                parse_mode=ParseMode.HTML
            )
        ),
    )

    # --------------------------------------------------------
    # Dispatcher
    # --------------------------------------------------------

    dp = Dispatcher()

    dp.include_router(
        router
    )

    # --------------------------------------------------------
    # Web server
    # --------------------------------------------------------

    runner = (
        await start_web_server()
    )

    try:

        print(
            "======================================"
        )

        print(
            "Telegram Store Bot Started"
        )

        print(
            "Payment Gateway: PayU"
        )

        print(
            "Payment Method: Payment Link"
        )

        print(
            "Dynamic QR: DISABLED"
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
