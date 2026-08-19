import asyncio
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


# ------------------------------------------------------------
# PAYU OAUTH
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


# ------------------------------------------------------------
# SERVER
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
).strip().rstrip("/")


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


# ------------------------------------------------------------
# PAYMENT CHECK SETTINGS
# ------------------------------------------------------------

PAYMENT_CHECK_INTERVAL = int(
    os.getenv(
        "PAYMENT_CHECK_INTERVAL",
        "15",
    )
)


PAYMENT_CHECK_MINUTES = int(
    os.getenv(
        "PAYMENT_CHECK_MINUTES",
        "35",
    )
)


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


# ============================================================
# GLOBALS
# ============================================================

router = Router()

bot: Optional[Bot] = None

token_lock = asyncio.Lock()

cached_access_token: Optional[str] = None

cached_token_expires_at: int = 0


# ============================================================
# ERROR ID
# ============================================================

def generate_error_id() -> str:

    return (
        f"E-{int(time.time())}-"
        f"{secrets.token_hex(2).upper()}"
    )


def print_error(
    error_id: str,
    title: str,
    details=None,
):

    print()
    print("=" * 72)
    print(f"ERROR ID: {error_id}")
    print(f"TITLE: {title}")

    if details is not None:

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

    print("=" * 72)
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

                invoice_number TEXT UNIQUE NOT NULL,

                payment_link TEXT,

                status TEXT NOT NULL DEFAULT 'created',

                payment_id TEXT,

                transaction_id TEXT,

                created_at INTEGER NOT NULL,

                paid_at INTEGER,

                access_link TEXT,

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


        # ----------------------------------------------------
        # DATABASE MIGRATION
        # ----------------------------------------------------

        columns = conn.execute(
            """
            PRAGMA table_info(orders)
            """
        ).fetchall()


        column_names = {
            row["name"]
            for row in columns
        }


        migrations = {

            "payment_link": (
                "ALTER TABLE orders "
                "ADD COLUMN payment_link TEXT"
            ),

            "payment_id": (
                "ALTER TABLE orders "
                "ADD COLUMN payment_id TEXT"
            ),

            "transaction_id": (
                "ALTER TABLE orders "
                "ADD COLUMN transaction_id TEXT"
            ),

            "paid_at": (
                "ALTER TABLE orders "
                "ADD COLUMN paid_at INTEGER"
            ),

            "access_link": (
                "ALTER TABLE orders "
                "ADD COLUMN access_link TEXT"
            ),

            "access_sent": (
                "ALTER TABLE orders "
                "ADD COLUMN access_sent INTEGER "
                "NOT NULL DEFAULT 0"
            ),
        }


        for column, sql in migrations.items():

            if column not in column_names:

                try:

                    conn.execute(
                        sql
                    )

                except Exception as e:

                    print(
                        f"Migration failed for {column}:",
                        repr(e),
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
    invoice_number: str,
    payment_link: str,
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
                invoice_number,
                payment_link,
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
                invoice_number,
                payment_link,
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

        return dict(row) if row else None


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

        return dict(row) if row else None


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

        return dict(row) if row else None


# ============================================================
# UPDATE ORDER
# ============================================================

def mark_paid(
    reference_id: str,
    payment_id: str,
    transaction_id: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET
                status = 'paid',
                payment_id = ?,
                transaction_id = ?,
                paid_at = ?

            WHERE reference_id = ?
            """,
            (
                payment_id,
                transaction_id,
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
            (
                reference_id,
            ),
        )

        conn.commit()


def save_access_link(
    reference_id: str,
    access_link: str,
):

    with closing(db()) as conn:

        conn.execute(
            """
            UPDATE orders

            SET access_link = ?

            WHERE reference_id = ?
            """,
            (
                access_link,
                reference_id,
            ),
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
            (
                reference_id,
            ),
        )

        conn.commit()


# ============================================================
# EVENT DEDUPE
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
# HTTP JSON HELPER
# ============================================================

async def read_json_response(
    response: aiohttp.ClientResponse,
):

    text = await response.text()

    try:

        data = json.loads(
            text
        )

    except Exception:

        data = {
            "raw": text
        }

    return data


# ============================================================
# PAYU OAUTH TOKEN
# ============================================================

async def get_payu_access_token(
    force_refresh: bool = False,
):

    global cached_access_token
    global cached_token_expires_at


    async with token_lock:

        now = int(
            time.time()
        )


        if (
            not force_refresh
            and cached_access_token
            and now < (
                cached_token_expires_at - 60
            )
        ):

            return cached_access_token


        error_id = generate_error_id()


        payload = {

            "client_id": PAYU_CLIENT_ID,

            "client_secret": PAYU_CLIENT_SECRET,

            "grant_type": "client_credentials",

            "scope": (
                "create_payment_links "
                "read_payment_links"
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


                    print(
                        "PAYU TOKEN HTTP:",
                        response.status,
                    )


                    if response.status >= 400:

                        print_error(
                            error_id,
                            "PayU OAuth token failed",
                            result,
                        )

                        raise RuntimeError(
                            f"{error_id}|"
                            f"PayU OAuth failed "
                            f"HTTP {response.status}"
                        )


        except asyncio.TimeoutError as e:

            print_error(
                error_id,
                "PayU OAuth timeout",
                repr(e),
            )

            raise RuntimeError(
                f"{error_id}|"
                "PayU OAuth request timed out."
            ) from e


        except aiohttp.ClientError as e:

            print_error(
                error_id,
                "PayU OAuth network error",
                repr(e),
            )

            raise RuntimeError(
                f"{error_id}|"
                "PayU OAuth network error."
            ) from e


        access_token = result.get(
            "access_token"
        )


        if not access_token:

            print_error(
                error_id,
                "PayU did not return access_token",
                result,
            )

            raise RuntimeError(
                f"{error_id}|"
                "PayU did not return access token."
            )


        expires_in = int(
            result.get(
                "expires_in",
                3600,
            )
        )


        cached_access_token = (
            access_token
        )


        cached_token_expires_at = (
            now + expires_in
        )


        return access_token


# ============================================================
# PAYU AUTH HEADERS
# ============================================================

def payu_headers(
    access_token: str,
):

    return {

        "merchantId":
            PAYU_MERCHANT_ID,

        "Authorization":
            f"Bearer {access_token}",

        "Content-Type":
            "application/json",

        "Accept":
            "application/json",
    }


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


    error_id = generate_error_id()


    # --------------------------------------------------------
    # UNIQUE INTERNAL REFERENCE
    # --------------------------------------------------------

    reference_id = (
        "ORD_"
        + secrets.token_hex(12)
    )


    # --------------------------------------------------------
    # PAYU INVOICE NUMBER
    #
    # Must be alphanumeric.
    # --------------------------------------------------------

    invoice_number = (
        "TG"
        + str(user_id)
        + str(int(time.time()))
        + secrets.token_hex(4)
    )


    # --------------------------------------------------------
    # AMOUNT
    #
    # PayU Payment Link docs use subAmount.
    # We use integer INR here.
    # --------------------------------------------------------

    amount = int(
        plan["price"]
    )


    # --------------------------------------------------------
    # CUSTOMER DATA
    # --------------------------------------------------------

    customer_name = (
        firstname
        or "Customer"
    )


    email = (
        f"telegram"
        f"{user_id}"
        f"@example.com"
    )


    # --------------------------------------------------------
    # EXPIRY
    #
    # 30 minutes
    # --------------------------------------------------------

    expiry = (
        datetime.now(
            timezone.utc
        )
        + timedelta(
            minutes=30
        )
    )


    expiry_date = expiry.strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    # --------------------------------------------------------
    # CALLBACK URLs
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
    # REQUEST BODY
    # --------------------------------------------------------

    payload = {

        "invoiceNumber":
            invoice_number,

        "isAmountFilledByCustomer":
            False,

        "subAmount":
            amount,

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

        "customer": {

            "name":
                customer_name,

            "email":
                email,

            "phone":
                "9999999999",
        },

        "udf": {

            "udf1":
                str(user_id),

            "udf2":
                plan_key,

            "udf3":
                reference_id,

            "udf4":
                invoice_number,

            "udf5":
                "telegram",
        },

        "viaEmail":
            False,

        "viaSms":
            False,

        "successURL":
            success_url,

        "failureURL":
            failure_url,

        "notes":
            f"Telegram User ID: {user_id}",
    }


    print()
    print("=" * 72)
    print("PAYU CREATE PAYMENT LINK")
    print("=" * 72)
    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
    )
    print("=" * 72)
    print()


    # --------------------------------------------------------
    # TOKEN
    # --------------------------------------------------------

    access_token = (
        await get_payu_access_token()
    )


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
                headers=payu_headers(
                    access_token
                ),
            ) as response:

                result = (
                    await read_json_response(
                        response
                    )
                )


                print(
                    "PAYU PAYMENT LINK HTTP:",
                    response.status,
                )


                print(
                    "PAYU PAYMENT LINK RESPONSE:"
                )


                print(
                    json.dumps(
                        result,
                        indent=2,
                        ensure_ascii=False,
                    )
                )


                # ------------------------------------------------
                # RETRY ON TOKEN EXPIRY
                # ------------------------------------------------

                if response.status == 401:

                    access_token = (
                        await get_payu_access_token(
                            force_refresh=True
                        )
                    )


                    async with session.post(
                        PAYU_PAYMENT_LINK_URL,
                        json=payload,
                        headers=payu_headers(
                            access_token
                        ),
                    ) as retry_response:

                        result = (
                            await read_json_response(
                                retry_response
                            )
                        )


                        print(
                            "PAYU RETRY HTTP:",
                            retry_response.status,
                        )


                        if (
                            retry_response.status
                            >= 400
                        ):

                            print_error(
                                error_id,
                                "PayU payment link retry failed",
                                result,
                            )

                            raise RuntimeError(
                                f"{error_id}|"
                                "PayU payment link "
                                "authentication failed."
                            )


                elif response.status >= 400:

                    print_error(
                        error_id,
                        "PayU payment link API failed",
                        result,
                    )


                    message = (
                        result.get(
                            "message"
                        )
                        or result.get(
                            "error"
                        )
                        or result.get(
                            "errorMessage"
                        )
                        or "Unknown PayU error."
                    )


                    raise RuntimeError(
                        f"{error_id}|{message}"
                    )


    except asyncio.TimeoutError as e:

        print_error(
            error_id,
            "Payment link timeout",
            repr(e),
        )

        raise RuntimeError(
            f"{error_id}|"
            "PayU payment link request timed out."
        ) from e


    except aiohttp.ClientError as e:

        print_error(
            error_id,
            "Payment link network error",
            repr(e),
        )

        raise RuntimeError(
            f"{error_id}|"
            "Network error while connecting to PayU."
        ) from e


    # --------------------------------------------------------
    # EXTRACT RESULT
    # --------------------------------------------------------

    result_obj = result.get(
        "result",
        {}
    )


    if not isinstance(
        result_obj,
        dict,
    ):

        result_obj = {}


    payment_link = (
        result_obj.get(
            "paymentLink"
        )
        or result_obj.get(
            "payment_link"
        )
    )


    returned_invoice = (
        result_obj.get(
            "invoiceNumber"
        )
        or invoice_number
    )


    if not payment_link:

        print_error(
            error_id,
            "PayU did not return paymentLink",
            result,
        )

        raise RuntimeError(
            f"{error_id}|"
            "PayU did not return a payment link."
        )


    # --------------------------------------------------------
    # SAVE ORDER
    # --------------------------------------------------------

    save_order(
        reference_id=reference_id,

        user_id=user_id,

        plan_key=plan_key,

        amount_paise=(
            amount * 100
        ),

        invoice_number=returned_invoice,

        payment_link=str(
            payment_link
        ),
    )


    return {

        "reference_id":
            reference_id,

        "invoice_number":
            returned_invoice,

        "payment_link":
            str(payment_link),

        "amount":
            amount,

        "expiry":
            expiry_date,
    }


# ============================================================
# PAYU TRANSACTION DETAILS
# ============================================================

async def get_payu_transactions(
    invoice_number: str,
):

    error_id = generate_error_id()


    access_token = (
        await get_payu_access_token()
    )


    today = datetime.now(
        timezone.utc
    ).date()


    date_from = (
        today - timedelta(
            days=2
        )
    ).strftime(
        "%Y-%m-%d"
    )


    date_to = (
        today + timedelta(
            days=1
        )
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
            date_from,

        "dateTo":
            date_to,
    }


    timeout = aiohttp.ClientTimeout(
        total=25
    )


    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.get(
                url,

                params=params,

                headers=payu_headers(
                    access_token
                ),
            ) as response:

                result = (
                    await read_json_response(
                        response
                    )
                )


                print(
                    "PAYU TXNS HTTP:",
                    response.status,
                )


                print(
                    "PAYU TXNS:",
                    json.dumps(
                        result,
                        indent=2,
                        ensure_ascii=False,
                    )[:12000],
                )


                if response.status == 401:

                    access_token = (
                        await get_payu_access_token(
                            force_refresh=True
                        )
                    )


                    async with session.get(
                        url,

                        params=params,

                        headers=payu_headers(
                            access_token
                        ),
                    ) as retry_response:

                        result = (
                            await read_json_response(
                                retry_response
                            )
                        )


                        if (
                            retry_response.status
                            >= 400
                        ):

                            raise RuntimeError(
                                f"{error_id}|"
                                "PayU transaction "
                                "authentication failed."
                            )


                elif response.status >= 400:

                    print_error(
                        error_id,
                        "PayU transaction API failed",
                        result,
                    )

                    raise RuntimeError(
                        f"{error_id}|"
                        f"PayU transaction API "
                        f"HTTP {response.status}"
                    )


                return result


    except asyncio.TimeoutError as e:

        raise RuntimeError(
            f"{error_id}|"
            "PayU transaction check timed out."
        ) from e


    except aiohttp.ClientError as e:

        raise RuntimeError(
            f"{error_id}|"
            "PayU transaction network error."
        ) from e


# ============================================================
# EXTRACT TRANSACTIONS
# ============================================================

def extract_transactions(
    result,
):

    if not isinstance(
        result,
        dict,
    ):

        return []


    result_obj = result.get(
        "result",
        {}
    )


    if not isinstance(
        result_obj,
        dict,
    ):

        return []


    data = result_obj.get(
        "data",
        []
    )


    if isinstance(
        data,
        list,
    ):

        return data


    # Fallbacks for alternate response formats

    transactions = result.get(
        "transactions",
        []
    )


    if isinstance(
        transactions,
        list,
    ):

        return transactions


    return []


# ============================================================
# FIND PAYMENT TRANSACTION
# ============================================================

def find_matching_transaction(
    transactions,
    invoice_number: str,
    expected_amount: float,
):

    for tx in transactions:

        if not isinstance(
            tx,
            dict,
        ):

            continue


        status = str(
            tx.get(
                "status",
                ""
            )
        ).lower().strip()


        # ----------------------------------------------------
        # Amount
        # ----------------------------------------------------

        amount_value = (
            tx.get(
                "settledAmount"
            )
            or tx.get(
                "amount"
            )
            or tx.get(
                "subAmount"
            )
        )


        received_amount = None


        try:

            if amount_value is not None:

                received_amount = float(
                    amount_value
                )

        except Exception:

            received_amount = None


        # ----------------------------------------------------
        # Transaction identifiers
        # ----------------------------------------------------

        transaction_id = (
            tx.get(
                "transactionId"
            )
            or tx.get(
                "txnId"
            )
            or tx.get(
                "transaction_id"
            )
        )


        payment_id = (
            tx.get(
                "paymentId"
            )
            or tx.get(
                "mihpayid"
            )
            or tx.get(
                "payuMoneyId"
            )
        )


        # ----------------------------------------------------
        # Amount validation
        # ----------------------------------------------------

        if (
            received_amount is not None
            and abs(
                received_amount
                - expected_amount
            ) > 0.01
        ):

            print(
                "Ignoring transaction due to amount mismatch:",
                received_amount,
                expected_amount,
            )

            continue


        # ----------------------------------------------------
        # Success
        # ----------------------------------------------------

        if status in (
            "success",
            "successful",
            "paid",
            "completed",
        ):

            return {

                "status":
                    "success",

                "transaction_id":
                    transaction_id
                    or "",

                "payment_id":
                    payment_id
                    or transaction_id
                    or "",

                "amount":
                    received_amount,
            }


        # ----------------------------------------------------
        # Failed
        # ----------------------------------------------------

        if status in (
            "failure",
            "failed",
            "cancelled",
            "canceled",
            "expired",
        ):

            return {

                "status":
                    "failed",

                "transaction_id":
                    transaction_id
                    or "",

                "payment_id":
                    payment_id
                    or "",

                "amount":
                    received_amount,
            }


    return None


# ============================================================
# VERIFY ORDER
# ============================================================

async def verify_order_payment(
    order: dict,
):

    if not order:

        return {
            "status":
                "not_found"
        }


    if order["status"] == "paid":

        return {
            "status":
                "success",

            "already_paid":
                True,
        }


    plan = PLANS.get(
        order["plan_key"]
    )


    if not plan:

        return {
            "status":
                "error",

            "message":
                "Plan not found.",
        }


    expected_amount = (
        order["amount_paise"]
        / 100
    )


    result = await get_payu_transactions(
        order["invoice_number"]
    )


    transactions = extract_transactions(
        result
    )


    matching = find_matching_transaction(
        transactions=transactions,

        invoice_number=(
            order["invoice_number"]
        ),

        expected_amount=(
            expected_amount
        ),
    )


    if not matching:

        return {
            "status":
                "pending"
        }


    if matching["status"] == "failed":

        mark_failed(
            order["reference_id"]
        )

        return {
            "status":
                "failed"
        }


    if matching["status"] == "success":

        mark_paid(
            reference_id=(
                order["reference_id"]
            ),

            payment_id=(
                matching["payment_id"]
            ),

            transaction_id=(
                matching["transaction_id"]
            ),
        )


        updated = get_order(
            order["reference_id"]
        )


        try:

            await deliver_access(
                updated
            )

        except Exception as e:

            print(
                "Automatic access delivery failed:",
                repr(e),
            )


        return {
            "status":
                "success",

            "transaction_id":
                matching["transaction_id"],
        }


    return {
        "status":
            "pending"
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

        "💳 Secure payment powered by PayU\n"

        "🔗 Payment Link — no QR required.\n\n"

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
# HELP
# ============================================================

@router.message(
    Command("help")
)
async def help_handler(
    message: Message,
):

    await message.answer(

        "ℹ️ <b>How to use</b>\n\n"

        "1️⃣ Select a plan.\n"

        "2️⃣ Click Pay.\n"

        "3️⃣ Open the PayU payment link.\n"

        "4️⃣ Complete payment.\n"

        "5️⃣ Bot automatically checks payment.\n"

        "6️⃣ Access link is sent automatically.\n\n"

        "📋 Use /myplan to check your order."
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


            print_error(
                error_id,
                "Unhandled payment link exception",
                repr(e),
            )


        await callback.message.answer(

            "❌ <b>Payment Link generate nahi ho paya.</b>\n\n"

            "PayU se payment link create karte waqt problem aayi.\n\n"

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


    keyboard = InlineKeyboardMarkup(

        inline_keyboard=[

            [

                InlineKeyboardButton(

                    text=(
                        f"💳 Open PayU Payment — "
                        f"₹{plan['price']}"
                    ),

                    url=(
                        result["payment_link"]
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

        "💳 <b>PAYU PAYMENT LINK</b>\n"

        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Amount: "
        f"<b>₹{plan['price']}</b>\n\n"

        "🔗 Click the button below to open "
        "the secure PayU checkout.\n\n"

        "⚡ Payment complete hone ke baad "
        "bot automatically payment check karega.\n\n"

        f"🧾 Invoice:\n"
        f"<code>{result['invoice_number']}</code>\n\n"

        "⏱️ Payment link validity: "
        "<b>30 minutes</b>",

        reply_markup=keyboard,
    )


# ============================================================
# MAKE ACCESS LINK
# ============================================================

async def make_access_link(
    order: dict,
) -> Optional[str]:

    # --------------------------------------------------------
    # Already generated
    # --------------------------------------------------------

    existing = (
        order.get(
            "access_link"
        )
    )


    if existing:

        return existing


    plan = PLANS.get(
        order["plan_key"]
    )


    if not plan:

        return None


    # --------------------------------------------------------
    # Create one-time Telegram invite
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


            access_link = (
                invite.invite_link
            )


            save_access_link(

                reference_id=(
                    order["reference_id"]
                ),

                access_link=(
                    access_link
                ),
            )


            return access_link


        except Exception as e:

            print(
                "Invite link creation failed:",
                repr(e),
            )


    # --------------------------------------------------------
    # Static fallback
    # --------------------------------------------------------

    if plan["access_link"]:

        save_access_link(

            reference_id=(
                order["reference_id"]
            ),

            access_link=(
                plan["access_link"]
            ),
        )


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

    if not order:

        return False


    if order.get(
        "status"
    ) != "paid":

        return False


    # --------------------------------------------------------
    # If already delivered, don't send duplicate
    # --------------------------------------------------------

    if order.get(
        "access_sent"
    ):

        return True


    access_link = (
        await make_access_link(
            order
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
        f"<b>₹{plan['price']}</b>\n\n"

        f"🧾 Invoice:\n"
        f"<code>{order['invoice_number']}</code>\n\n"

        f"🔗 <b>Your Access Link:</b>\n"
        f"{access_link}\n\n"

        "⚠️ Link kisi ke saath share mat karein."
    )


    mark_access_sent(
        order["reference_id"]
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
            "❌ Invalid order."
        )

        return


    if order["status"] == "paid":

        await callback.message.answer(

            "✅ <b>Payment already confirmed.</b>\n\n"

            "Use /myplan to get your access link."
        )

        return


    try:

        result = (
            await verify_order_payment(
                order
            )
        )


        status = result.get(
            "status"
        )


        if status == "success":

            updated = get_order(
                reference_id
            )


            await callback.message.answer(

                "🎉 <b>Payment Confirmed!</b>\n\n"

                "Your access is being delivered."
            )


            if updated:

                try:

                    await deliver_access(
                        updated
                    )

                except Exception as e:

                    print(
                        "Manual access delivery failed:",
                        repr(e),
                    )


        elif status == "failed":

            await callback.message.answer(

                "❌ <b>Payment failed.</b>\n\n"

                "Aap naya payment link create kar sakte hain."
            )


        elif status == "pending":

            await callback.message.answer(

                "⏳ <b>Payment not confirmed yet.</b>\n\n"

                "Agar payment abhi-abhi kiya hai "
                "to 10–30 seconds wait karke "
                "dobara Verify Payment dabayein."
            )


        else:

            await callback.message.answer(

                "❌ Payment verification mein problem aayi."
            )


    except Exception as e:

        error_id = (
            generate_error_id()
        )


        print_error(
            error_id,
            "Manual payment verification failed",
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


    status = str(
        order["status"]
    ).upper()


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

            f"🧾 Invoice: "
            f"<code>"
            f"{order['invoice_number']}"
            f"</code>\n\n"

            "Aap apna access link neeche "
            "dobara le sakte hain."
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
    # NOT PAID
    # --------------------------------------------------------

    keyboard_buttons = []


    if order.get(
        "payment_link"
    ):

        keyboard_buttons.append(

            [

                InlineKeyboardButton(

                    text="💳 Open Payment Link",

                    url=(
                        order[
                            "payment_link"
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

        inline_keyboard=(
            keyboard_buttons
        )
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
        f"<b>{status}</b>\n"

        f"🧾 Invoice:\n"
        f"<code>{order['invoice_number']}</code>\n\n"

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
                order
            )
        )


        if not access_link:

            support = (
                SUPPORT_USERNAME
                or "admin"
            )


            await callback.message.answer(

                "❌ Access link unavailable.\n\n"

                f"📞 Support: {support}"
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


        support = (
            SUPPORT_USERNAME
            or "admin"
        )


        await callback.message.answer(

            "❌ Access send nahi ho paya.\n\n"

            f"Contact {support}."
        )


# ============================================================
# AUTOMATIC PAYMENT MONITOR
# ============================================================

async def payment_monitor():

    print(
        "Automatic PayU payment monitor started."
    )


    while True:

        try:

            cutoff = (
                int(time.time())
                - (
                    PAYMENT_CHECK_MINUTES
                    * 60
                )
            )


            with closing(db()) as conn:

                rows = conn.execute(

                    """
                    SELECT *
                    FROM orders

                    WHERE status IN
                    (
                        'created'
                    )

                    AND created_at >= ?

                    ORDER BY created_at ASC

                    LIMIT 50
                    """,

                    (
                        cutoff,
                    ),
                ).fetchall()


            for row in rows:

                order = dict(
                    row
                )


                try:

                    result = (
                        await verify_order_payment(
                            order
                        )
                    )


                    if (
                        result.get(
                            "status"
                        )
                        == "success"
                    ):

                        print(
                            "Automatic payment confirmed:",
                            order[
                                "invoice_number"
                            ],
                        )


                except Exception as e:

                    print(
                        "Monitor order error:",
                        order[
                            "invoice_number"
                        ],
                        repr(e),
                    )


                await asyncio.sleep(
                    0.2
                )


        except asyncio.CancelledError:

            print(
                "Payment monitor stopped."
            )

            raise


        except Exception as e:

            print(
                "Payment monitor loop error:",
                repr(e),
            )


        await asyncio.sleep(
            PAYMENT_CHECK_INTERVAL
        )


# ============================================================
# HEALTH
# ============================================================

async def health(
    request: web.Request,
):

    return web.json_response(

        {

            "ok":
                True,

            "service":
                "telegram-store-bot-payu-payment-links",

            "time":
                int(time.time()),
        }
    )


# ============================================================
# PAYU SUCCESS URL
# ============================================================

async def payu_success(
    request: web.Request,
):

    try:

        data = {}

        if request.method == "POST":

            post_data = (
                await request.post()
            )

            data = dict(
                post_data
            )

        elif request.method == "GET":

            data = dict(
                request.query
            )


        print(
            "PAYU SUCCESS CALLBACK:",
            data,
        )


    except Exception as e:

        print(
            "PayU success callback error:",
            repr(e),
        )


    return web.Response(

        status=200,

        text=(
            "Payment received. "
            "You can return to Telegram."
        ),

        content_type="text/plain",
    )


# ============================================================
# PAYU FAILURE URL
# ============================================================

async def payu_failure(
    request: web.Request,
):

    try:

        data = {}

        if request.method == "POST":

            post_data = (
                await request.post()
            )

            data = dict(
                post_data
            )

        elif request.method == "GET":

            data = dict(
                request.query
            )


        print(
            "PAYU FAILURE CALLBACK:",
            data,
        )


    except Exception as e:

        print(
            "PayU failure callback error:",
            repr(e),
        )


    return web.Response(

        status=200,

        text=(
            "Payment failed or cancelled. "
            "You can return to Telegram."
        ),

        content_type="text/plain",
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


    app.router.add_get(
        "/payu/success",
        payu_success,
    )


    app.router.add_post(
        "/payu/failure",
        payu_failure,
    )


    app.router.add_get(
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
    print("=" * 72)
    print("WEB SERVER STARTED")
    print("=" * 72)

    print(
        "Health:",
        f"{PUBLIC_BASE_URL}/health",
    )

    print(
        "PayU Success:",
        f"{PUBLIC_BASE_URL}/payu/success",
    )

    print(
        "PayU Failure:",
        f"{PUBLIC_BASE_URL}/payu/failure",
    )

    print("=" * 72)
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


    if not PAYU_TOKEN_URL:

        errors.append(
            "PAYU_TOKEN_URL missing"
        )


    if not PAYU_PAYMENT_LINK_URL:

        errors.append(
            "PAYU_PAYMENT_LINK_URL missing"
        )


    if not PAYU_TRANSACTION_URL:

        errors.append(
            "PAYU_TRANSACTION_URL missing"
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


    monitor_task = asyncio.create_task(
        payment_monitor()
    )


    try:

        print()
        print("=" * 72)
        print("TELEGRAM STORE BOT STARTED")
        print("=" * 72)

        print(
            "Payment Gateway: PayU Payment Links"
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
            "PayU Transaction URL:",
            PAYU_TRANSACTION_URL,
        )

        print(
            "Merchant ID:",
            PAYU_MERCHANT_ID,
        )

        print(
            "Database:",
            DB_PATH,
        )

        print(
            "Auto check interval:",
            PAYMENT_CHECK_INTERVAL,
            "seconds",
        )

        print("=" * 72)
        print()


        await dp.start_polling(
            bot
        )


    finally:

        monitor_task.cancel()


        try:

            await monitor_task

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
