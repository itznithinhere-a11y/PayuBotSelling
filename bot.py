# ============================================================
# DARK STORE — PRODUCTION TELEGRAM BOT
# ============================================================
# Features
# ------------------------------------------------------------
# ✅ PayU V2 payment creation
# ✅ PayU payment verification
# ✅ Secure PayU callback hash verification
# ✅ Supabase users / orders / subscriptions
# ✅ Automatic payment verification
# ✅ Automatic channel invite generation
# ✅ Static channel link fallback
# ✅ Multiple active plans
# ✅ /myplan + /myplans
# ✅ Single-screen inline UI
# ✅ Correct callback user identification
# ✅ Subscription expiry
# ✅ Expiry reminder
# ✅ Automatic expired status
# ✅ Duplicate payment protection
# ✅ Amount verification
# ✅ Admin broadcast
# ✅ Admin stats
# ✅ Admin payment notification
# ✅ Health endpoint
# ✅ Production logging
# ============================================================

import asyncio
import hashlib
import html
import json
import logging
import os
import secrets
import time

from datetime import datetime, timezone
from email.utils import format_datetime
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
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger("dark-store")


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

HOST = os.getenv("HOST", "0.0.0.0").strip()

try:
    PORT = int(os.getenv("PORT", "8080"))
except ValueError:
    PORT = 8080


# ============================================================
# PAYU
# ============================================================

PAYU_KEY = os.getenv("PAYU_KEY", "").strip()

PAYU_SECRET = os.getenv("PAYU_SECRET", "").strip()

PAYU_URL = os.getenv(
    "PAYU_URL",
    "https://api.payu.in/v2/payments",
).strip()

PAYU_VERIFY_URL = os.getenv(
    "PAYU_VERIFY_URL",
    "https://info.payu.in/v3/transaction",
).strip()


# ============================================================
# PUBLIC URL
# ============================================================

PUBLIC_BASE_URL = (
    os.getenv("PUBLIC_BASE_URL", "")
    .strip()
    .rstrip("/")
)

SUCCESS_URL = (
    f"{PUBLIC_BASE_URL}/payu/success"
    if PUBLIC_BASE_URL
    else ""
)

FAILURE_URL = (
    f"{PUBLIC_BASE_URL}/payu/failure"
    if PUBLIC_BASE_URL
    else ""
)


# ============================================================
# SUPABASE
# ============================================================

SUPABASE_URL = (
    os.getenv("SUPABASE_URL", "")
    .strip()
    .rstrip("/")
)

SUPABASE_SERVICE_ROLE_KEY = (
    os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
    .strip()
)


# ============================================================
# ADMIN
# ============================================================

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}


# ============================================================
# SUPPORT
# ============================================================

SUPPORT_USERNAME = (
    os.getenv("SUPPORT_USERNAME", "")
    .strip()
    .lstrip("@")
)


# ============================================================
# EXPIRY
# ============================================================

try:
    EXPIRY_REMINDER_HOURS = int(
        os.getenv(
            "EXPIRY_REMINDER_HOURS",
            "24",
        )
    )
except ValueError:
    EXPIRY_REMINDER_HOURS = 24


# ============================================================
# PLANS
# ============================================================

PLANS = {
    "gold": {
        "name": "🥇 Gold Premium",
        "price": int(
            os.getenv("GOLD_PRICE", "499")
        ),
        "duration_days": int(
            os.getenv("GOLD_DURATION_DAYS", "365")
        ),
        "description": "Gold Premium Access",
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
        "name": "🥈 Silver Premium",
        "price": int(
            os.getenv("SILVER_PRICE", "999")
        ),
        "duration_days": int(
            os.getenv("SILVER_DURATION_DAYS", "0")
        ),
        "description": "Silver Premium Access",
        "channel_id": os.getenv(
            "SILVER_CHANNEL_ID",
            "",
        ).strip(),
        "access_link": os.getenv(
            "SILVER_ACCESS_LINK",
            "",
        ).strip(),
    },
}


# ============================================================
# GLOBALS
# ============================================================

bot: Optional[Bot] = None

router = Router()

expiry_task: Optional[asyncio.Task] = None

UI_MESSAGES = {}

PAYMENT_LOCKS = {}


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


def format_ts(ts) -> str:

    if not ts:
        return "-"

    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromtimestamp(
            int(ts),
            ZoneInfo("Asia/Kolkata"),
        )

        return dt.strftime(
            "%d %b %Y, %I:%M %p"
        )

    except Exception:
        return str(ts)


def make_txnid(user_id: int) -> str:

    random_part = secrets.token_hex(8)

    return (
        f"TG{user_id}_"
        f"{int(time.time())}_"
        f"{random_part}"
    )[:50]


def error_id() -> str:

    return (
        f"E-{now_ts()}-"
        f"{secrets.token_hex(3).upper()}"
    )


def safe_html(value) -> str:

    return html.escape(
        str(value or "")
    )


def get_payment_lock(txn_id: str):

    if txn_id not in PAYMENT_LOCKS:
        PAYMENT_LOCKS[txn_id] = asyncio.Lock()

    return PAYMENT_LOCKS[txn_id]


# ============================================================
# PAYU AUTH
# ============================================================

def make_auth(
    body: str,
    date: str,
) -> str:

    raw = (
        f"{body}|"
        f"{date}|"
        f"{PAYU_SECRET}"
    )

    signature = hashlib.sha512(
        raw.encode("utf-8")
    ).hexdigest()

    return (
        f'hmac username="{PAYU_KEY}",'
        f' algorithm="sha512",'
        f' headers="date",'
        f' signature="{signature}"'
    )


# ============================================================
# PAYU CREATE PAYMENT
# ============================================================

async def create_payment(
    user_id: int,
    amount: str,
    plan_key: str,
):

    if plan_key not in PLANS:
        raise RuntimeError(
            "Invalid plan."
        )

    txn_id = make_txnid(user_id)

    plan = PLANS[plan_key]

    try:
        amount_value = float(amount)
    except Exception:
        raise RuntimeError(
            "Invalid payment amount."
        )

    if amount_value <= 0:
        raise RuntimeError(
            "Payment amount must be greater than 0."
        )

    if not SUCCESS_URL or not FAILURE_URL:
        raise RuntimeError(
            "PUBLIC_BASE_URL missing."
        )

    payload = {
        "accountId": PAYU_KEY,
        "txnId": txn_id,
        "currency": "INR",

        "order": {
            "productInfo": plan["description"],

            "userDefinedFields": {
                "udf1": str(user_id),
                "udf2": plan_key,
                "udf3": txn_id,
            },

            "paymentChargeSpecification": {
                "price": amount_value,
            },
        },

        "billingDetails": {
            "firstName": "Telegram",
            "lastName": "Customer",

            "email": (
                f"telegram{user_id}"
                "@example.com"
            ),

            "phone": "9999999999",

            "address": {
                "address1": "India",
                "city": "Indore",
                "state": "Madhya Pradesh",
                "country": "India",
                "zipCode": "452001",
            },
        },

        "callBackActions": {
            "successAction": SUCCESS_URL,
            "failureAction": FAILURE_URL,
            "cancelAction": FAILURE_URL,
        },

        "additionalInfo": {
            "txnFlow": "nonseamless",
            "createOrder": True,
        },
    }

    body = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    date = format_datetime(
        datetime.now(timezone.utc),
        usegmt=True,
    )

    headers = {
        "date": date,
        "authorization": make_auth(
            body,
            date,
        ),
        "content-type": "application/json",
        "accept": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=30)

    logger.info(
        "Creating PayU payment txn=%s plan=%s amount=%s",
        txn_id,
        plan_key,
        amount_value,
    )

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                PAYU_URL,
                data=body.encode("utf-8"),
                headers=headers,
            ) as response:

                text = await response.text()

                logger.info(
                    "PayU create HTTP=%s txn=%s",
                    response.status,
                    txn_id,
                )

                try:
                    data = json.loads(text)
                except json.JSONDecodeError:
                    return (
                        None,
                        txn_id,
                        {
                            "http_status": response.status,
                            "raw_response": text[:3000],
                        },
                    )

                if response.status >= 400:

                    return (
                        None,
                        txn_id,
                        data,
                    )

                checkout_url = None

                if isinstance(data, dict):

                    result = data.get("result")

                    if isinstance(result, dict):

                        checkout_url = (
                            result.get("checkoutUrl")
                            or result.get("checkoutURL")
                            or result.get("checkout_url")
                            or result.get("paymentUrl")
                            or result.get("paymentURL")
                            or result.get("payment_url")
                        )

                    checkout_url = (
                        checkout_url
                        or data.get("checkoutUrl")
                        or data.get("checkoutURL")
                        or data.get("checkout_url")
                    )

                return (
                    checkout_url,
                    txn_id,
                    data,
                )

    except asyncio.TimeoutError:

        return (
            None,
            txn_id,
            {
                "error": "PayU request timeout"
            },
        )

    except aiohttp.ClientError as e:

        return (
            None,
            txn_id,
            {
                "error": f"HTTP error: {e}"
            },
        )


# ============================================================
# PAYU VERIFY
# ============================================================

async def verify_payu_payment(
    txn_id: str,
):

    txn_id = str(
        txn_id or ""
    ).strip()

    if not txn_id:
        return {
            "ok": False,
            "status": "invalid",
            "message": "Transaction ID missing.",
            "raw": {},
        }

    payload = {
        "txnId": [txn_id],
    }

    body = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    date = format_datetime(
        datetime.now(timezone.utc),
        usegmt=True,
    )

    headers = {
        "date": date,
        "authorization": make_auth(
            body,
            date,
        ),
        "content-type": "application/json",
        "accept": "application/json",
        "Info-Command": "verify_payment",
    }

    timeout = aiohttp.ClientTimeout(total=30)

    try:

        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:

            async with session.post(
                PAYU_VERIFY_URL,
                data=body.encode("utf-8"),
                headers=headers,
            ) as response:

                text = await response.text()

                try:
                    data = json.loads(text)
                except json.JSONDecodeError:

                    return {
                        "ok": False,
                        "status": "error",
                        "message":
                            "PayU returned invalid response.",
                        "raw": {
                            "http_status": response.status,
                            "text": text[:3000],
                        },
                    }

                if response.status >= 400:

                    return {
                        "ok": False,
                        "status": "error",
                        "message":
                            f"PayU HTTP {response.status}",
                        "raw": data,
                    }

                result = (
                    data.get("result")
                    if isinstance(data, dict)
                    else None
                )

                if isinstance(result, list):

                    item = (
                        result[0]
                        if result
                        else {}
                    )

                elif isinstance(result, dict):

                    item = result

                else:

                    item = {}

                if not isinstance(item, dict):
                    item = {}

                payu_status = str(
                    item.get("status")
                    or item.get("unmappedStatus")
                    or ""
                ).strip().lower()

                message = str(
                    item.get("message")
                    or (
                        data.get("message")
                        if isinstance(data, dict)
                        else ""
                    )
                    or item.get("errorMessage")
                    or ""
                )

                if payu_status == "success":

                    return {
                        "ok": True,
                        "status": "success",
                        "message":
                            message
                            or "Payment successful.",
                        "raw": data,
                        "result": item,

                        "txn_id": str(
                            item.get("txnId")
                            or txn_id
                        ),

                        "payment_id": str(
                            item.get("mihpayId")
                            or item.get("mihpayid")
                            or item.get("bankReferenceNumber")
                            or txn_id
                        ),

                        "amount": (
                            item.get("amount")
                            if item.get("amount") is not None
                            else item.get("originalAmount")
                        ),
                    }

                if payu_status in {
                    "pending",
                    "in progress",
                    "initiated",
                    "open",
                    "queued",
                }:
                    status = "pending"

                elif payu_status in {
                    "failed",
                    "failure",
                    "cancelled",
                    "canceled",
                    "dropped",
                    "bounced",
                }:
                    status = "failed"

                else:
                    status = "pending"

                return {
                    "ok": False,
                    "status": status,
                    "message":
                        message
                        or "Payment not successful yet.",
                    "raw": data,
                    "result": item,
                    "txn_id": txn_id,
                }

    except asyncio.TimeoutError:

        return {
            "ok": False,
            "status": "error",
            "message":
                "PayU verification timed out.",
            "raw": {},
        }

    except aiohttp.ClientError as e:

        return {
            "ok": False,
            "status": "error",
            "message":
                f"PayU HTTP error: {e}",
            "raw": {},
        }

    except Exception as e:

        return {
            "ok": False,
            "status": "error",
            "message":
                f"PayU verification error: {e}",
            "raw": {},
        }


# ============================================================
# SUPABASE
# ============================================================

def supabase_headers(
    prefer=None,
):

    headers = {
        "apikey":
            SUPABASE_SERVICE_ROLE_KEY,

        "Authorization":
            f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",

        "Content-Type":
            "application/json",
    }

    if prefer:
        headers["Prefer"] = prefer

    return headers


async def supabase_request(
    method: str,
    table: str,
    *,
    params=None,
    json_data=None,
):

    if not SUPABASE_URL:
        raise RuntimeError(
            "SUPABASE_URL missing."
        )

    if not SUPABASE_SERVICE_ROLE_KEY:
        raise RuntimeError(
            "SUPABASE_SERVICE_ROLE_KEY missing."
        )

    url = (
        f"{SUPABASE_URL}"
        f"/rest/v1/"
        f"{table}"
    )

    timeout = aiohttp.ClientTimeout(total=30)

    async with aiohttp.ClientSession(
        timeout=timeout
    ) as session:

        async with session.request(
            method,
            url,
            params=params,
            json=json_data,
            headers=supabase_headers(
                "return=representation"
            ),
        ) as response:

            text = await response.text()

            if not text:
                data = None

            else:
                try:
                    data = json.loads(text)
                except Exception:
                    data = {
                        "raw": text
                    }

            if response.status >= 400:

                raise RuntimeError(
                    f"Supabase HTTP "
                    f"{response.status}: "
                    f"{text[:1000]}"
                )

            return data


# ============================================================
# USER
# ============================================================

async def save_user(
    tg_user,
):

    if not tg_user:
        return

    data = {
        "user_id": tg_user.id,
        "username": tg_user.username,
        "first_name": tg_user.first_name,
        "last_name": tg_user.last_name,
        "updated_at": now_ts(),
    }

    await supabase_request(
        "POST",
        "bot_users",
        params={
            "on_conflict": "user_id",
        },
        json_data=data,
    )


async def get_user(
    user_id: int,
):

    result = await supabase_request(
        "GET",
        "bot_users",
        params={
            "user_id": f"eq.{user_id}",
            "limit": "1",
        },
    )

    if isinstance(result, list) and result:
        return result[0]

    return None


# ============================================================
# ORDERS
# ============================================================

async def create_order(
    *,
    reference_id,
    user_id,
    plan_key,
    amount_paise,
    txn_id,
):

    data = {
        "reference_id": reference_id,
        "user_id": user_id,
        "plan_key": plan_key,
        "amount_paise": amount_paise,
        "txn_id": txn_id,
        "status": "created",
        "created_at": now_ts(),
        "access_sent": False,
    }

    result = await supabase_request(
        "POST",
        "orders",
        json_data=data,
    )

    if isinstance(result, list) and result:
        return result[0]

    return result


async def get_order_by_txn(
    txn_id,
):

    result = await supabase_request(
        "GET",
        "orders",
        params={
            "txn_id": f"eq.{txn_id}",
            "limit": "1",
        },
    )

    if isinstance(result, list) and result:
        return result[0]

    return None


async def update_order(
    reference_id,
    values,
):

    return await supabase_request(
        "PATCH",
        "orders",
        params={
            "reference_id":
                f"eq.{reference_id}",
        },
        json_data=values,
    )


# ============================================================
# SUBSCRIPTIONS
# ============================================================

async def get_existing_active_same_plan(
    user_id: int,
    plan_key: str,
):

    result = await supabase_request(
        "GET",
        "subscriptions",
        params={
            "user_id": f"eq.{user_id}",
            "plan_key": f"eq.{plan_key}",
            "status": "eq.active",
            "order": "expires_at.desc",
            "limit": "1",
        },
    )

    if isinstance(result, list) and result:
        return result[0]

    return None


async def create_subscription(
    order,
    payment_id,
):

    plan = PLANS[
        order["plan_key"]
    ]

    duration_days = int(
        plan.get(
            "duration_days",
            0,
        )
    )

    started_at = now_ts()

    if duration_days <= 0:

        expires_at = None

    else:

        expires_at = (
            started_at
            + duration_days * 86400
        )

    existing = await get_existing_active_same_plan(
        order["user_id"],
        order["plan_key"],
    )

    if (
        existing
        and duration_days > 0
    ):

        old_expiry = existing.get(
            "expires_at"
        )

        if old_expiry:

            try:

                old_expiry = int(
                    old_expiry
                )

                if old_expiry > started_at:

                    expires_at = (
                        old_expiry
                        + duration_days * 86400
                    )

                    # Extend existing subscription
                    result = await update_subscription(
                        existing["id"],
                        {
                            "expires_at": expires_at,
                            "payment_id": payment_id,
                            "order_reference":
                                order["reference_id"],
                            "reminder_sent": False,
                            "expired_alert_sent": False,
                            "status": "active",
                        },
                    )

                    if isinstance(result, list) and result:
                        return result[0]

                    return existing

            except Exception:
                pass

    data = {
        "user_id":
            order["user_id"],

        "order_reference":
            order["reference_id"],

        "plan_key":
            order["plan_key"],

        "status":
            "active",

        "started_at":
            started_at,

        "expires_at":
            expires_at,

        "payment_id":
            payment_id,

        "access_link":
            None,

        "reminder_sent":
            False,

        "expired_alert_sent":
            False,
    }

    result = await supabase_request(
        "POST",
        "subscriptions",
        json_data=data,
    )

    if isinstance(result, list) and result:
        return result[0]

    return result


async def update_subscription(
    subscription_id,
    values,
):

    return await supabase_request(
        "PATCH",
        "subscriptions",
        params={
            "id":
                f"eq.{subscription_id}",
        },
        json_data=values,
    )


async def get_user_active_subscriptions(
    user_id: int,
):

    result = await supabase_request(
        "GET",
        "subscriptions",
        params={
            "user_id": f"eq.{user_id}",
            "status": "eq.active",
            "order": "started_at.desc",
            "limit": "100",
        },
    )

    if not isinstance(result, list):
        return []

    current = now_ts()
    active = []

    for sub in result:

        expires_at = sub.get(
            "expires_at"
        )

        if expires_at:

            try:

                expires_int = int(
                    expires_at
                )

                if expires_int <= current:

                    await update_subscription(
                        sub["id"],
                        {
                            "status": "expired",
                            "expired_alert_sent": True,
                        },
                    )

                    continue

            except Exception:
                pass

        active.append(sub)

    return active


async def get_active_subscriptions():

    result = await supabase_request(
        "GET",
        "subscriptions",
        params={
            "status": "eq.active",
            "limit": "1000",
        },
    )

    if isinstance(result, list):
        return result

    return []


# ============================================================
# EVENT DEDUPE
# ============================================================

async def event_processed(
    event_id,
):

    result = await supabase_request(
        "GET",
        "processed_events",
        params={
            "event_id":
                f"eq.{event_id}",
            "limit":
                "1",
        },
    )

    return bool(
        isinstance(result, list)
        and result
    )


async def save_event(
    event_id,
):

    await supabase_request(
        "POST",
        "processed_events",
        json_data={
            "event_id":
                event_id,
            "created_at":
                now_ts(),
        },
    )


# ============================================================
# ACCESS LINK
# ============================================================

async def make_access_link(
    plan_key: str,
):

    if not bot:
        return None

    plan = PLANS.get(plan_key)

    if not plan:
        return None

    channel_id = plan.get(
        "channel_id"
    )

    if channel_id:

        try:

            invite = (
                await bot.create_chat_invite_link(
                    chat_id=channel_id,
                    member_limit=1,
                )
            )

            return invite.invite_link

        except Exception as e:

            logger.warning(
                "Invite creation failed "
                "plan=%s error=%r",
                plan_key,
                e,
            )

    static_link = plan.get(
        "access_link"
    )

    if static_link:
        return static_link

    return None


# ============================================================
# ADMIN PAYMENT NOTIFICATION
# ============================================================

async def notify_admin_payment(
    order,
    payment_id,
    subscription,
):

    if not ADMIN_IDS or not bot:
        return

    user_id = order.get(
        "user_id"
    )

    plan_key = order.get(
        "plan_key"
    )

    plan = PLANS.get(
        plan_key,
        {},
    )

    amount_paise = int(
        order.get(
            "amount_paise",
            0,
        )
        or 0
    )

    amount = amount_paise / 100

    user = None

    try:
        user = await get_user(
            int(user_id)
        )
    except Exception:
        pass

    username = (
        f"@{user.get('username')}"
        if user and user.get("username")
        else "No username"
    )

    first_name = (
        user.get("first_name")
        if user
        else "Unknown"
    )

    expires_at = (
        subscription.get("expires_at")
        if subscription
        else None
    )

    if expires_at:
        expiry = format_ts(expires_at)
    else:
        expiry = "Lifetime"

    text = (
        "💰 <b>NEW PAYMENT RECEIVED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"👤 User: <b>{safe_html(first_name)}</b>\n"
        f"🆔 User ID: <code>{user_id}</code>\n"
        f"🔗 Username: {safe_html(username)}\n\n"

        f"📦 Plan: <b>{safe_html(plan.get('name', plan_key))}</b>\n"
        f"💰 Amount: <b>₹{amount:.2f}</b>\n"
        f"📌 Status: <b>PAID</b>\n"
        f"⏳ Expiry: <b>{safe_html(expiry)}</b>\n\n"

        f"🧾 Order:\n"
        f"<code>{safe_html(order.get('reference_id'))}</code>\n\n"

        f"🔐 Transaction:\n"
        f"<code>{safe_html(order.get('txn_id'))}</code>\n\n"

        f"💳 Payment ID:\n"
        f"<code>{safe_html(payment_id)}</code>"
    )

    for admin_id in ADMIN_IDS:

        try:

            await bot.send_message(
                admin_id,
                text,
            )

        except Exception as e:

            logger.warning(
                "Admin payment notification "
                "failed admin=%s error=%r",
                admin_id,
                e,
            )


# ============================================================
# DELIVERY
# ============================================================

async def deliver_access(
    order,
    payment_id,
):

    subscription = (
        await create_subscription(
            order,
            payment_id,
        )
    )

    if not subscription:
        raise RuntimeError(
            "Subscription creation failed."
        )

    access_link = (
        subscription.get(
            "access_link"
        )
    )

    if not access_link:

        access_link = await make_access_link(
            order["plan_key"]
        )

        if access_link:

            await update_subscription(
                subscription["id"],
                {
                    "access_link":
                        access_link,
                },
            )

            subscription[
                "access_link"
            ] = access_link

    plan = PLANS[
        order["plan_key"]
    ]

    duration_days = int(
        plan.get(
            "duration_days",
            0,
        )
    )

    if duration_days <= 0:

        expiry_text = (
            "♾️ <b>Lifetime Access</b>"
        )

    else:

        expiry_text = (
            "⏳ Expires: "
            f"<b>{format_ts(subscription.get('expires_at'))}</b>"
        )

    if access_link:

        access_text = (
            "🔗 <b>Channel Access</b>\n"
            f"{safe_html(access_link)}"
        )

    else:

        support = (
            f"@{SUPPORT_USERNAME}"
            if SUPPORT_USERNAME
            else "Admin"
        )

        access_text = (
            "⚠️ Channel link generate nahi ho paya.\n"
            f"Support: {safe_html(support)}"
        )

    # --------------------------------------------------------
    # Update order BEFORE sending notifications.
    # This protects against duplicate callback delivery.
    # --------------------------------------------------------

    await update_order(
        order["reference_id"],
        {
            "status": "paid",
            "payment_id": payment_id,
            "paid_at": now_ts(),
            "access_sent": True,
        },
    )

    user_text = (
        "🎉 <b>PAYMENT CONFIRMED!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{safe_html(plan['name'])}</b>\n"

        f"💰 Paid: "
        f"<b>₹{plan['price']}</b>\n"

        f"{expiry_text}\n\n"

        f"{access_text}\n\n"

        "📋 /myplans se apne active plans "
        "kabhi bhi check kar sakte ho.\n\n"

        "🧾 Transaction:\n"
        f"<code>{safe_html(order.get('txn_id'))}</code>"
    )

    try:

        await bot.send_message(
            order["user_id"],
            user_text,
        )

    except Exception as e:

        logger.warning(
            "User delivery message failed "
            "user=%s error=%r",
            order["user_id"],
            e,
        )

    try:

        await notify_admin_payment(
            order,
            payment_id,
            subscription,
        )

    except Exception:

        logger.exception(
            "Admin notification error"
        )

    return subscription


# ============================================================
# PAYMENT SUCCESS PROCESS
# ============================================================

async def process_successful_payment(
    data,
):

    txn_id = (
        data.get("txnid")
        or data.get("txnId")
        or data.get("transactionId")
    )

    if not txn_id:
        return False

    txn_id = str(
        txn_id
    ).strip()

    async with get_payment_lock(txn_id):

        order = await get_order_by_txn(
            txn_id
        )

        if not order:

            logger.error(
                "Order not found txn=%s",
                txn_id,
            )

            return False

        if str(order.get("status")).lower() == "paid":
            return True

        # ----------------------------------------------------
        # Amount verification
        # ----------------------------------------------------

        received_amount = (
            data.get("amount")
            or data.get("amt")
        )

        if received_amount is not None:

            try:

                expected = (
                    float(
                        order["amount_paise"]
                    ) / 100
                )

                received = float(
                    received_amount
                )

                if abs(
                    expected - received
                ) > 0.01:

                    logger.error(
                        "Amount mismatch "
                        "txn=%s expected=%s received=%s",
                        txn_id,
                        expected,
                        received,
                    )

                    return False

            except Exception:

                logger.error(
                    "Amount validation failed txn=%s",
                    txn_id,
                )

                return False

        payment_id = (
            data.get("mihpayid")
            or data.get("paymentId")
            or data.get("bank_ref_num")
            or txn_id
        )

        payment_id = str(
            payment_id
        )

        event_id = (
            f"paid:"
            f"{txn_id}:"
            f"{payment_id}"
        )

        if await event_processed(
            event_id
        ):
            return True

        subscription = await deliver_access(
            order,
            payment_id,
        )

        if not subscription:
            return False

        await save_event(
            event_id
        )

        return True


async def process_verified_payu_result(
    verification,
):

    if not verification.get("ok"):
        return False

    txn_id = str(
        verification.get("txn_id")
        or ""
    ).strip()

    payment_id = str(
        verification.get("payment_id")
        or txn_id
    ).strip()

    amount = verification.get(
        "amount"
    )

    return await process_successful_payment(
        {
            "txnid": txn_id,
            "mihpayid": payment_id,
            "amount": amount,
            "status": "success",
        }
    )


# ============================================================
# CREATE PLAN PAYMENT
# ============================================================

async def create_plan_payment(
    user_id,
    plan_key,
):

    if plan_key not in PLANS:
        raise RuntimeError(
            "Invalid plan."
        )

    plan = PLANS[
        plan_key
    ]

    checkout_url, txn_id, result = (
        await create_payment(
            user_id=user_id,
            amount=str(
                plan["price"]
            ),
            plan_key=plan_key,
        )
    )

    if not checkout_url:

        raise RuntimeError(
            json.dumps(
                result,
                ensure_ascii=False,
                default=str,
            )[:2000]
        )

    reference_id = (
        "ORD_"
        + secrets.token_hex(12)
    )

    await create_order(
        reference_id=reference_id,
        user_id=user_id,
        plan_key=plan_key,
        amount_paise=(
            plan["price"] * 100
        ),
        txn_id=txn_id,
    )

    return {
        "reference_id":
            reference_id,

        "txn_id":
            txn_id,

        "checkout_url":
            checkout_url,

        "amount":
            plan["price"],
    }


# ============================================================
# KEYBOARDS
# ============================================================

def home_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="💎 1 YEAR • ₹499",
                    callback_data="plan:gold",
                ),

                InlineKeyboardButton(
                    text="♾️ LIFETIME • ₹999",
                    callback_data="plan:silver",
                ),
            ],

            [
                InlineKeyboardButton(
                    text="📋 MY PLANS",
                    callback_data="myplans",
                ),
            ],
        ]
    )


def plan_keyboard(
    plan_key,
):

    plan = PLANS[
        plan_key
    ]

    duration = int(
        plan.get(
            "duration_days",
            0,
        )
    )

    if duration <= 0:
        duration_text = "LIFETIME"
    elif duration == 365:
        duration_text = "1 YEAR"
    else:
        duration_text = f"{duration} DAYS"

    pay_text = (
        f"💳 GET {duration_text} • ₹{plan['price']}"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text=pay_text,
                    callback_data=(
                        f"buy:{plan_key}"
                    ),
                ),
            ],

            [
                InlineKeyboardButton(
                    text="📋 MY PLANS",
                    callback_data="myplans",
                ),
            ],

            [
                InlineKeyboardButton(
                    text="↩️ BACK",
                    callback_data="home",
                ),
            ],
        ]
    )


def payment_keyboard(
    checkout_url,
    txn_id,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="💳 PAY NOW",
                    url=checkout_url,
                ),
            ],

            [
                InlineKeyboardButton(
                    text="🔄 VERIFY PAYMENT",
                    callback_data=(
                        f"verify:{txn_id}"
                    ),
                ),
            ],

            [
                InlineKeyboardButton(
                    text="📋 MY PLANS",
                    callback_data="myplans",
                ),
            ],

            [
                InlineKeyboardButton(
                    text="↩️ BACK",
                    callback_data="home",
                ),
            ],
        ]
    )


def myplans_keyboard():

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="🛒 BUY / RENEW PLAN",
                    callback_data="home",
                ),
            ],

            [
                InlineKeyboardButton(
                    text="🔄 REFRESH",
                    callback_data="myplans",
                ),
            ],
        ]
    )


# ============================================================
# UI EDIT
# ============================================================

async def edit_ui(
    message,
    text,
    reply_markup=None,
):

    try:

        await message.edit_text(
            text,
            reply_markup=reply_markup,
        )

    except Exception as e:

        if (
            "message is not modified"
            not in str(e).lower()
        ):
            raise


async def send_home(
    chat_id,
    message=None,
):

    text = (
        "💎 <b>PREMIUM STORE</b> ✨\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔐 <b>Private • Secure • Exclusive</b>\n"
        "⚡ Instant Access • 🚀 Auto Delivery\n"
        "🛡️ Secure Payment • ♾️ Lifetime Option\n\n"
        "🔥 <b>Choose Your Premium Plan</b>\n\n"
        "💎 <b>Gold</b> — 1 Year — ₹499\n"
        "♾️ <b>Silver</b> — Lifetime — ₹999\n\n"
        "👇 <i>Select your plan to continue</i>"
    )

    if message is not None:

        await edit_ui(
            message,
            text,
            home_keyboard(),
        )

        UI_MESSAGES[
            chat_id
        ] = message.message_id

        return

    old_message_id = UI_MESSAGES.get(
        chat_id
    )

    if old_message_id:

        try:

            await bot.delete_message(
                chat_id=chat_id,
                message_id=old_message_id,
            )

        except Exception:
            pass

    sent = await bot.send_message(
        chat_id,
        text,
        reply_markup=home_keyboard(),
    )

    UI_MESSAGES[
        chat_id
    ] = sent.message_id


# ============================================================
# START
# ============================================================

@router.message(
    CommandStart()
)
async def start_handler(
    message: Message,
):

    try:

        await save_user(
            message.from_user
        )

    except Exception as e:

        logger.error(
            "Save user error: %r",
            e,
        )

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
        callback.from_user.id,
        callback.message,
    )


# ============================================================
# PLAN DETAILS
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

        await edit_ui(
            callback.message,
            "❌ <b>Invalid plan.</b>",
            home_keyboard(),
        )

        return

    plan = PLANS[
        plan_key
    ]

    duration = int(
        plan["duration_days"]
    )

    if duration <= 0:

        duration_text = "♾️ Lifetime"

    elif duration == 365:

        duration_text = "📅 1 Year"

    else:

        duration_text = (
            f"📅 {duration} Days"
        )

    text = (
        f"{plan['name']}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 <b>₹{plan['price']}</b>\n"
        f"{duration_text}\n\n"

        "✨ Premium Access\n"
        "⚡ Instant Delivery\n"
        "🛡 Secure Payment\n"
        "🔐 Auto Verification\n"
        "📋 Multiple Plans Supported\n\n"

        "👇 Continue to payment"
    )

    await edit_ui(
        callback.message,
        text,
        plan_keyboard(plan_key),
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
        "PayU payment create ho raha hai..."
    )

    plan_key = callback.data.split(
        ":",
        1,
    )[1]

    if plan_key not in PLANS:

        await edit_ui(
            callback.message,
            "❌ Invalid plan.",
            home_keyboard(),
        )

        return

    try:

        result = (
            await create_plan_payment(
                user_id=
                    callback.from_user.id,

                plan_key=
                    plan_key,
            )
        )

    except Exception as e:

        eid = error_id()

        logger.exception(
            "[%s] PAYMENT ERROR",
            eid,
        )

        await edit_ui(
            callback.message,

            "❌ <b>Payment create nahi ho paya.</b>\n\n"

            "Reason:\n"
            f"<code>{safe_html(str(e)[:1500])}</code>\n\n"

            f"Error ID: <code>{eid}</code>",

            myplans_keyboard(),
        )

        return

    plan = PLANS[
        plan_key
    ]

    text = (
        "💳 <b>PAYU PAYMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{safe_html(plan['name'])}</b>\n"

        f"💰 Amount: "
        f"<b>₹{plan['price']}</b>\n\n"

        "👇 <b>Pay Now</b> dabao aur PayU checkout "
        "complete karo.\n\n"

        "Payment complete hone ke baad "
        "<b>Verify Payment</b> press karo.\n\n"

        "🛡️ Payment verify directly PayU se hoga.\n\n"

        "🧾 Transaction:\n"
        f"<code>{safe_html(result['txn_id'])}</code>"
    )

    await edit_ui(
        callback.message,
        text,
        payment_keyboard(
            result["checkout_url"],
            result["txn_id"],
        ),
    )


# ============================================================
# VERIFY
# ============================================================

@router.callback_query(
    F.data.startswith("verify:")
)
async def verify_callback(
    callback: CallbackQuery,
):

    await callback.answer(
        "Payment verify ho raha hai..."
    )

    txn_id = callback.data.split(
        ":",
        1,
    )[1].strip()

    order = await get_order_by_txn(
        txn_id
    )

    if not order:

        await edit_ui(
            callback.message,

            "❌ <b>Order nahi mila.</b>\n\n"
            "Transaction ID invalid ya purana ho sakta hai.",

            myplans_keyboard(),
        )

        return

    if int(
        order["user_id"]
    ) != callback.from_user.id:

        await edit_ui(
            callback.message,

            "❌ <b>Ye order aapka nahi hai.</b>",

            home_keyboard(),
        )

        return

    if str(
        order.get("status")
    ).lower() == "paid":

        await edit_ui(
            callback.message,

            "✅ <b>Payment already confirmed.</b>\n\n"
            "📋 Apne plans ke liye MY PLANS dabayein.",

            myplans_keyboard(),
        )

        return

    try:

        verification = (
            await verify_payu_payment(
                txn_id
            )
        )

    except Exception as e:

        eid = error_id()

        logger.exception(
            "[%s] VERIFY ERROR",
            eid,
        )

        await edit_ui(
            callback.message,

            "⚠️ <b>Verification temporarily failed.</b>\n\n"
            "Thodi der baad dobara try karein.\n\n"
            f"Error ID: <code>{eid}</code>",

            payment_keyboard(
                "",
                txn_id,
            ),
        )

        return

    if verification.get("ok"):

        try:

            processed = (
                await process_verified_payu_result(
                    verification
                )
            )

        except Exception as e:

            eid = error_id()

            logger.exception(
                "[%s] DELIVERY ERROR",
                eid,
            )

            await edit_ui(
                callback.message,

                "⚠️ PayU ne payment successful confirm kiya hai, "
                "lekin access delivery mein problem aayi.\n\n"
                "Please Verify Payment dobara press karein.\n\n"
                f"Error ID: <code>{eid}</code>",

                myplans_keyboard(),
            )

            return

        if processed:

            await edit_ui(
                callback.message,

                "🎉 <b>PAYMENT VERIFIED!</b>\n\n"
                "✅ Payment successfully confirmed.\n"
                "🔗 Access details Telegram par bhej di gayi hain.\n\n"
                "📋 Apne active plans check karein.",

                myplans_keyboard(),
            )

        else:

            await edit_ui(
                callback.message,

                "⚠️ <b>Payment verify hua, "
                "lekin order process nahi ho saka.</b>\n\n"
                "Please dobara Verify Payment karein.",

                myplans_keyboard(),
            )

        return

    status = verification.get(
        "status",
        "pending",
    )

    message_text = str(
        verification.get(
            "message"
        )
        or "Payment status unavailable."
    )[:500]

    if status == "failed":

        try:

            await update_order(
                order["reference_id"],
                {
                    "status": "failed"
                },
            )

        except Exception:
            pass

        await edit_ui(
            callback.message,

            "❌ <b>Payment successful nahi mila.</b>\n\n"
            f"PayU: <code>{safe_html(message_text)}</code>\n\n"
            "Agar amount deduct hua hai to thodi der baad "
            "Verify Payment dobara karein.",

            myplans_keyboard(),
        )

        return

    if status == "error":

        await edit_ui(
            callback.message,

            "⚠️ <b>Verification abhi complete nahi hui.</b>\n\n"
            f"Reason: <code>{safe_html(message_text)}</code>\n\n"
            "Thodi der baad dobara try karein.",

            myplans_keyboard(),
        )

        return

    await edit_ui(
        callback.message,

        "⏳ <b>Payment abhi confirm nahi hua.</b>\n\n"
        f"PayU: <code>{safe_html(message_text)}</code>\n\n"
        "10–30 seconds baad Verify Payment dobara press karein.",

        myplans_keyboard(),
    )


# ============================================================
# MY PLANS
# ============================================================

async def build_my_plans_text(
    user_id: int,
):

    subscriptions = (
        await get_user_active_subscriptions(
            user_id
        )
    )

    if not subscriptions:

        return (
            "📋 <b>MY PLANS</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"

            "📭 <b>No active plan found.</b>\n\n"

            "Aapke account par abhi koi active "
            "subscription nahi hai.\n\n"

            "👇 Neeche se plan choose karke access le sakte ho."
        ), myplans_keyboard()

    lines = [
        "📋 <b>MY PLANS</b>",
        "━━━━━━━━━━━━━━━━━━",
        "",
    ]

    link_buttons = []

    for index, subscription in enumerate(
        subscriptions,
        start=1,
    ):

        plan_key = subscription.get(
            "plan_key",
            "",
        )

        plan = PLANS.get(
            plan_key,
            {},
        )

        plan_name = plan.get(
            "name",
            plan_key or "Unknown Plan",
        )

        expires_at = subscription.get(
            "expires_at"
        )

        if expires_at:

            try:

                expiry_text = (
                    f"⏳ Expires: "
                    f"<b>{format_ts(expires_at)}</b>"
                )

            except Exception:

                expiry_text = (
                    "⏳ Expiry: Unknown"
                )

        else:

            expiry_text = (
                "♾️ <b>Lifetime Access</b>"
            )

        access_link = subscription.get(
            "access_link"
        )

        if not access_link:

            try:

                access_link = (
                    await make_access_link(
                        plan_key
                    )
                )

                if access_link:

                    await update_subscription(
                        subscription["id"],
                        {
                            "access_link":
                                access_link,
                        },
                    )

            except Exception as e:

                logger.warning(
                    "Missing access link "
                    "subscription=%s error=%r",
                    subscription.get("id"),
                    e,
                )

        lines.extend(
            [
                f"🔹 <b>Plan {index}</b>",
                f"📦 {safe_html(plan_name)}",
                "📌 Status: <b>ACTIVE</b>",
                expiry_text,
            ]
        )

        if access_link:

            lines.append(
                "🔗 Channel access available below."
            )

            link_buttons.append(
                [
                    InlineKeyboardButton(
                        text=(
                            f"🔗 Open "
                            f"{plan_name}"
                        ),
                        url=access_link,
                    )
                ]
            )

        else:

            lines.append(
                "⚠️ Channel link currently unavailable."
            )

        lines.append("")

    lines.extend(
        [
            "━━━━━━━━━━━━━━━━━━",
            "🔐 Your active subscriptions are shown above.",
            "💡 Renew/buy another plan anytime.",
        ]
    )

    keyboard_rows = list(
        link_buttons
    )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🛒 BUY / RENEW PLAN",
                callback_data="home",
            )
        ]
    )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="🔄 REFRESH",
                callback_data="myplans",
            )
        ]
    )

    return (
        "\n".join(lines),
        InlineKeyboardMarkup(
            inline_keyboard=keyboard_rows
        ),
    )


async def show_my_plans(
    user_id: int,
    message=None,
):

    try:

        text, keyboard = (
            await build_my_plans_text(
                user_id
            )
        )

    except Exception as e:

        logger.exception(
            "My Plans DB error user=%s",
            user_id,
        )

        text = (
            "⚠️ <b>MY PLANS LOAD FAILED</b>\n\n"
            "Please thodi der baad dobara try karein."
        )

        keyboard = myplans_keyboard()

    if message is not None:

        await edit_ui(
            message,
            text,
            keyboard,
        )

        UI_MESSAGES[
            user_id
        ] = message.message_id

        return

    old_message_id = UI_MESSAGES.get(
        user_id
    )

    if old_message_id:

        try:

            await bot.delete_message(
                chat_id=user_id,
                message_id=old_message_id,
            )

        except Exception:
            pass

    sent = await bot.send_message(
        user_id,
        text,
        reply_markup=keyboard,
    )

    UI_MESSAGES[
        user_id
    ] = sent.message_id


# ============================================================
# /MYPLAN
# ============================================================

@router.message(
    Command("myplan")
)
async def myplan_command(
    message: Message,
):

    await show_my_plans(
        user_id=message.from_user.id,
        message=None,
    )


# ============================================================
# /MYPLANS
# ============================================================

@router.message(
    Command("myplans")
)
async def myplans_command(
    message: Message,
):

    await show_my_plans(
        user_id=message.from_user.id,
        message=None,
    )


# ============================================================
# MY PLANS CALLBACK
# ============================================================

@router.callback_query(
    F.data == "myplans"
)
async def myplans_callback(
    callback: CallbackQuery,
):

    await callback.answer(
        "Active plans load ho rahe hain..."
    )

    # IMPORTANT:
    # callback.from_user = REAL USER
    # callback.message.from_user = BOT
    #
    # Therefore ALWAYS use callback.from_user.id.

    await show_my_plans(
        user_id=callback.from_user.id,
        message=callback.message,
    )


# ============================================================
# ADMIN BROADCAST
# ============================================================

@router.message(
    Command("broadcast")
)
async def broadcast_command(
    message: Message,
):

    if message.from_user.id not in ADMIN_IDS:

        await message.answer(
            "❌ Admin only."
        )

        return

    parts = (
        message.text or ""
    ).split(
        maxsplit=1
    )

    if len(parts) < 2:

        await message.answer(
            "Usage:\n\n"
            "<code>/broadcast Your message</code>"
        )

        return

    broadcast_text = parts[1].strip()

    if not broadcast_text:

        await message.answer(
            "❌ Broadcast message empty hai."
        )

        return

    users = await supabase_request(
        "GET",
        "bot_users",
        params={
            "select":
                "user_id",
            "limit":
                "10000",
        },
    )

    if not isinstance(users, list):

        await message.answer(
            "❌ Users fetch failed."
        )

        return

    sent = 0
    failed = 0

    status_message = await message.answer(
        "📢 <b>Broadcast started...</b>"
    )

    for user in users:

        user_id = user.get(
            "user_id"
        )

        if not user_id:
            continue

        try:

            await bot.send_message(
                user_id,
                broadcast_text,
            )

            sent += 1

        except Exception as e:

            failed += 1

            logger.warning(
                "Broadcast failed "
                "user=%s error=%r",
                user_id,
                e,
            )

        await asyncio.sleep(
            0.05
        )

    await status_message.edit_text(
        "📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>"
    )


# ============================================================
# ADMIN STATS
# ============================================================

@router.message(
    Command("stats")
)
async def stats_command(
    message: Message,
):

    if message.from_user.id not in ADMIN_IDS:

        await message.answer(
            "❌ Admin only."
        )

        return

    try:

        users = await supabase_request(
            "GET",
            "bot_users",
            params={
                "select": "user_id",
                "limit": "10000",
            },
        )

        orders = await supabase_request(
            "GET",
            "orders",
            params={
                "select":
                    "amount_paise,status",
                "limit":
                    "10000",
            },
        )

    except Exception as e:

        eid = error_id()

        logger.exception(
            "[%s] Stats error",
            eid,
        )

        await message.answer(
            "❌ Stats load failed.\n\n"
            f"Error ID: <code>{eid}</code>"
        )

        return

    total_users = (
        len(users)
        if isinstance(users, list)
        else 0
    )

    total_orders = (
        len(orders)
        if isinstance(orders, list)
        else 0
    )

    paid_orders = 0
    revenue = 0.0

    if isinstance(orders, list):

        for order in orders:

            if order.get(
                "status"
            ) == "paid":

                paid_orders += 1

                try:

                    revenue += (
                        int(
                            order.get(
                                "amount_paise",
                                0,
                            )
                        ) / 100
                    )

                except Exception:
                    pass

    await message.answer(
        "📊 <b>BOT STATS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 Users: <b>{total_users}</b>\n"
        f"🧾 Orders: <b>{total_orders}</b>\n"
        f"✅ Paid: <b>{paid_orders}</b>\n"
        f"💰 Revenue: <b>₹{revenue:.2f}</b>"
    )


# ============================================================
# PAYU CALLBACK HASH
# ============================================================

def verify_payu_callback_hash(
    data,
):

    received_hash = str(
        data.get(
            "hash",
            "",
        )
    ).strip()

    if not received_hash:
        return False

    status = str(
        data.get(
            "status",
            "",
        )
    )

    email = str(
        data.get(
            "email",
            "",
        )
    )

    firstname = str(
        data.get(
            "firstname",
            "",
        )
    )

    productinfo = str(
        data.get(
            "productinfo",
            "",
        )
    )

    amount = str(
        data.get(
            "amount",
            "",
        )
    )

    txnid = str(
        data.get(
            "txnid",
            "",
        )
    )

    key = str(
        data.get(
            "key",
            PAYU_KEY,
        )
    )

    udf1 = str(
        data.get(
            "udf1",
            "",
        )
    )

    udf2 = str(
        data.get(
            "udf2",
            "",
        )
    )

    udf3 = str(
        data.get(
            "udf3",
            "",
        )
    )

    udf4 = str(
        data.get(
            "udf4",
            "",
        )
    )

    udf5 = str(
        data.get(
            "udf5",
            "",
        )
    )

    additional = str(
        data.get(
            "additionalCharges",
            "",
        )
    )

    if additional:

        raw = (
            f"{additional}|"
            f"{PAYU_SECRET}|"
            f"{status}||||||"
            f"{udf5}|{udf4}|{udf3}|"
            f"{udf2}|{udf1}|"
            f"{email}|{firstname}|"
            f"{productinfo}|{amount}|"
            f"{txnid}|{key}"
        )

    else:

        raw = (
            f"{PAYU_SECRET}|"
            f"{status}||||||"
            f"{udf5}|{udf4}|{udf3}|"
            f"{udf2}|{udf1}|"
            f"{email}|{firstname}|"
            f"{productinfo}|{amount}|"
            f"{txnid}|{key}"
        )

    calculated = hashlib.sha512(
        raw.encode("utf-8")
    ).hexdigest()

    return secrets.compare_digest(
        calculated.lower(),
        received_hash.lower(),
    )


# ============================================================
# PAYU SUCCESS
# ============================================================

async def payu_success(
    request: web.Request,
):

    data = {}

    try:

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

        logger.warning(
            "PayU callback parse error: %r",
            e,
        )

    txn_id = str(
        data.get("txnid")
        or data.get("txnId")
        or data.get("transactionId")
        or ""
    ).strip()

    if not txn_id:

        return web.Response(
            status=400,
            content_type="text/html",
            text="""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Payment Verification</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>⚠️ Payment Verification Pending</h2>
<p>Transaction ID nahi mila.</p>
<p>Telegram par return karke <b>Verify Payment</b> press karein.</p>
</body>
</html>
""",
        )

    # --------------------------------------------------------
    # Validate callback hash if PayU supplied one.
    # Direct verification is STILL performed below.
    # --------------------------------------------------------

    if data.get("hash"):

        if not verify_payu_callback_hash(data):

            logger.error(
                "Invalid PayU callback hash txn=%s",
                txn_id,
            )

            return web.Response(
                status=403,
                text="Invalid payment signature.",
            )

    try:

        order = await get_order_by_txn(
            txn_id
        )

    except Exception:

        logger.exception(
            "Callback order lookup error"
        )

        order = None

    if not order:

        return web.Response(
            status=404,
            content_type="text/html",
            text="""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Order Not Found</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>⚠️ Order Not Found</h2>
<p>Telegram par return karke support se contact karein.</p>
</body>
</html>
""",
        )

    # --------------------------------------------------------
    # If already paid, do not deliver twice.
    # --------------------------------------------------------

    if str(
        order.get("status")
    ).lower() == "paid":

        return web.Response(
            status=200,
            content_type="text/html",
            text="""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Payment Already Confirmed</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>✅ Payment Already Confirmed</h2>
<p>Your payment was already processed.</p>
<p>Access details have been sent to Telegram.</p>
</body>
</html>
""",
        )

    # --------------------------------------------------------
    # NEVER trust browser callback status.
    # Verify directly with PayU.
    # --------------------------------------------------------

    verification = await verify_payu_payment(
        txn_id
    )

    if verification.get("ok"):

        try:

            processed = (
                await process_verified_payu_result(
                    verification
                )
            )

        except Exception:

            logger.exception(
                "Callback delivery error"
            )

            processed = False

        if processed:

            return web.Response(
                status=200,
                content_type="text/html",
                text="""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Payment Successful</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>✅ Payment Verified</h2>
<p>Your payment was verified successfully.</p>
<p>🎉 Access details have been sent to Telegram.</p>
<p>You can close this page.</p>
</body>
</html>
""",
            )

        return web.Response(
            status=500,
            content_type="text/html",
            text="""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Delivery Pending</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>⚠️ Delivery Pending</h2>
<p>Payment verified, but Telegram delivery failed.</p>
<p>Return to Telegram and press Verify Payment again.</p>
</body>
</html>
""",
        )

    status = verification.get(
        "status",
        "pending",
    )

    message_text = str(
        verification.get(
            "message"
        )
        or "Payment not confirmed."
    )[:500]

    if status == "failed":

        try:

            await update_order(
                order["reference_id"],
                {
                    "status": "failed"
                },
            )

        except Exception:
            pass

        return web.Response(
            status=200,
            content_type="text/html",
            text=f"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Payment Failed</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>❌ Payment Not Successful</h2>
<p>{safe_html(message_text)}</p>
<p>Return to Telegram and try again.</p>
</body>
</html>
""",
        )

    return web.Response(
        status=200,
        content_type="text/html",
        text=f"""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Payment Pending</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>⏳ Payment Verification Pending</h2>
<p>{safe_html(message_text)}</p>
<p>Return to Telegram and press Verify Payment.</p>
</body>
</html>
""",
    )


# ============================================================
# PAYU FAILURE
# ============================================================

async def payu_failure(
    request: web.Request,
):

    data = {}

    try:

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

        logger.warning(
            "PayU failure callback parse error: %r",
            e,
        )

    txn_id = (
        data.get("txnid")
        or data.get("txnId")
        or data.get("transactionId")
    )

    if txn_id:

        try:

            order = await get_order_by_txn(
                str(txn_id)
            )

            if order and str(
                order.get("status")
            ).lower() != "paid":

                await update_order(
                    order["reference_id"],
                    {
                        "status": "failed"
                    },
                )

        except Exception as e:

            logger.warning(
                "Failure DB error: %r",
                e,
            )

    return web.Response(
        status=200,
        content_type="text/html",
        text="""
<!DOCTYPE html>
<html>
<head>
<meta name="viewport"
content="width=device-width,initial-scale=1">
<title>Payment Failed</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>❌ Payment Failed</h2>
<p>Return to Telegram and try again.</p>
</body>
</html>
""",
    )


# ============================================================
# EXPIRY WATCHER
# ============================================================

async def expiry_watcher():

    logger.info(
        "Subscription expiry watcher started."
    )

    while True:

        try:

            subscriptions = (
                await get_active_subscriptions()
            )

            current = now_ts()

            for sub in subscriptions:

                expires_at = sub.get(
                    "expires_at"
                )

                # Lifetime
                if not expires_at:
                    continue

                try:

                    expires_at = int(
                        expires_at
                    )

                except Exception:

                    continue

                remaining = (
                    expires_at
                    - current
                )

                # ------------------------------------------------
                # EXPIRED
                # ------------------------------------------------

                if remaining <= 0:

                    await update_subscription(
                        sub["id"],
                        {
                            "status":
                                "expired",

                            "expired_alert_sent":
                                True,
                        },
                    )

                    try:

                        plan = PLANS.get(
                            sub["plan_key"],
                            {},
                        )

                        await bot.send_message(
                            sub["user_id"],

                            "⌛ <b>PLAN EXPIRED</b>\n"
                            "━━━━━━━━━━━━━━━━━━\n\n"

                            f"📦 Plan: "
                            f"<b>{safe_html(plan.get('name', sub['plan_key']))}</b>\n\n"

                            "Aapka plan expire ho gaya hai.\n\n"

                            "🔄 Renew karne ke liye "
                            "/myplans use karein.",
                        )

                    except Exception as e:

                        logger.warning(
                            "Expiry alert failed: %r",
                            e,
                        )

                    continue

                # ------------------------------------------------
                # REMINDER
                # ------------------------------------------------

                reminder_seconds = (
                    EXPIRY_REMINDER_HOURS
                    * 3600
                )

                if (
                    remaining <= reminder_seconds
                    and not sub.get(
                        "reminder_sent"
                    )
                ):

                    plan = PLANS.get(
                        sub["plan_key"],
                        {},
                    )

                    await update_subscription(
                        sub["id"],
                        {
                            "reminder_sent":
                                True,
                        },
                    )

                    try:

                        await bot.send_message(
                            sub["user_id"],

                            "⚠️ <b>PLAN EXPIRING SOON</b>\n"
                            "━━━━━━━━━━━━━━━━━━\n\n"

                            f"📦 Plan: "
                            f"<b>{safe_html(plan.get('name', sub['plan_key']))}</b>\n"

                            f"⏳ Expires: "
                            f"<b>{format_ts(expires_at)}</b>\n\n"

                            "Renew karna na bhoolna.\n\n"
                            "📋 /myplans → "
                            "🛒 Buy / Renew Plan",
                        )

                    except Exception as e:

                        logger.warning(
                            "Reminder failed: %r",
                            e,
                        )

        except asyncio.CancelledError:

            raise

        except Exception:

            logger.exception(
                "Expiry watcher error"
            )

        await asyncio.sleep(
            60
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
                "dark-store-payu-bot",
            "time":
                now_ts(),
        }
    )


# ============================================================
# WEB SERVER
# ============================================================

async def start_web_server():

    app = web.Application()

    app.router.add_get(
        "/",
        health,
    )

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
        HOST,
        PORT,
    )

    await site.start()

    logger.info(
        "Web server running on %s:%s",
        HOST,
        PORT,
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

    if not PAYU_SECRET:
        errors.append(
            "PAYU_SECRET missing"
        )

    if not SUPABASE_URL:
        errors.append(
            "SUPABASE_URL missing"
        )

    if not SUPABASE_SERVICE_ROLE_KEY:
        errors.append(
            "SUPABASE_SERVICE_ROLE_KEY missing"
        )

    if not PUBLIC_BASE_URL:
        errors.append(
            "PUBLIC_BASE_URL missing"
        )

    if not ADMIN_IDS:
        errors.append(
            "ADMIN_IDS missing"
        )

    if PUBLIC_BASE_URL and "YOUR-DOMAIN" in PUBLIC_BASE_URL:
        errors.append(
            "Replace PUBLIC_BASE_URL"
        )

    if "apitest.payu.in" in PAYU_URL.lower():
        errors.append(
            "Test PayU URL detected."
        )

    if "api.payu.in" not in PAYU_URL.lower():
        logger.warning(
            "PAYU_URL does not look like PayU production URL: %s",
            PAYU_URL,
        )

    for plan_key, plan in PLANS.items():

        if int(plan["price"]) <= 0:

            errors.append(
                f"{plan_key} price must be greater than 0"
            )

        if int(plan["duration_days"]) < 0:

            errors.append(
                f"{plan_key} duration_days cannot be negative"
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
    global expiry_task

    validate_config()

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

    expiry_task = asyncio.create_task(
        expiry_watcher()
    )

    logger.info(
        "=========================================="
    )

    logger.info(
        "DARK STORE BOT STARTED"
    )

    logger.info(
        "PayU: %s",
        PAYU_URL,
    )

    logger.info(
        "PayU Verify: %s",
        PAYU_VERIFY_URL,
    )

    logger.info(
        "Supabase: %s",
        SUPABASE_URL,
    )

    logger.info(
        "Public URL: %s",
        PUBLIC_BASE_URL,
    )

    logger.info(
        "Admins: %s",
        ADMIN_IDS,
    )

    logger.info(
        "=========================================="
    )

    try:

        await dp.start_polling(
            bot
        )

    finally:

        if expiry_task:

            expiry_task.cancel()

            try:

                await expiry_task

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

        logger.info(
            "Bot stopped."
        )
