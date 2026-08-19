import asyncio
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timezone, timedelta
from email.utils import format_datetime
from typing import Any, Optional
from urllib.parse import urlencode

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
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv(
    "BOT_TOKEN",
    "",
).strip()


# ============================================================
# EXISTING PAYU V2 CONFIG
# ============================================================

PAYU_KEY = os.getenv(
    "PAYU_KEY",
    "",
).strip()


PAYU_SECRET = os.getenv(
    "PAYU_SECRET",
    "",
).strip()


# IMPORTANT:
# Existing PayU V2 endpoint is kept unchanged.

PAYU_URL = os.getenv(
    "PAYU_URL",
    "https://api.payu.in/v2/payments",
).strip()


# ============================================================
# PUBLIC URL
# ============================================================

PUBLIC_BASE_URL = os.getenv(
    "PUBLIC_BASE_URL",
    "",
).strip().rstrip("/")


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
# SERVER
# ============================================================

HOST = os.getenv(
    "HOST",
    "0.0.0.0",
).strip()


PORT = int(
    os.getenv(
        "PORT",
        "8080",
    )
)


# ============================================================
# SUPABASE
# ============================================================

SUPABASE_URL = os.getenv(
    "SUPABASE_URL",
    "",
).strip().rstrip("/")


SUPABASE_KEY = os.getenv(
    "SUPABASE_KEY",
    "",
).strip()


# ============================================================
# ADMIN
# ============================================================

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv(
        "ADMIN_IDS",
        "",
    ).split(",")
    if x.strip().isdigit()
}


# ============================================================
# SUPPORT
# ============================================================

SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "",
).strip().lstrip("@")


# ============================================================
# PAYMENT
# ============================================================

PAYMENT_AMOUNT = os.getenv(
    "PAYMENT_AMOUNT",
    "1",
).strip()


# ============================================================
# EXPIRY
# ============================================================

# Reminder kitne hours pehle bhejna hai.

EXPIRY_REMINDER_HOURS = int(
    os.getenv(
        "EXPIRY_REMINDER_HOURS",
        "24",
    )
)


# ============================================================
# PLANS
# ============================================================
#
# duration_days:
#
# 0 = Lifetime
# 30 = 30 days
# 90 = 90 days
# 365 = 365 days
#
# ============================================================

PLANS = {
    "gold": {
        "name": "⚡ Gold Dark (Channel 1)",
        "price": int(os.getenv("GOLD_PRICE", "1499")),
        "duration_days": int(
            os.getenv("GOLD_DURATION_DAYS", "30")
        ),
        "description": "Gold Dark Access",
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
        "price": int(os.getenv("SILVER_PRICE", "1499")),
        "duration_days": int(
            os.getenv("SILVER_DURATION_DAYS", "30")
        ),
        "description": "Silver Dark Access",
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
        "price": int(os.getenv("BRONZE_PRICE", "1499")),
        "duration_days": int(
            os.getenv("BRONZE_DURATION_DAYS", "30")
        ),
        "description": "Bronze Dark Access",
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
        "price": int(os.getenv("IRON_PRICE", "1499")),
        "duration_days": int(
            os.getenv("IRON_DURATION_DAYS", "30")
        ),
        "description": "Iron Dark Access",
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

bot: Optional[Bot] = None

router = Router()

expiry_task: Optional[asyncio.Task] = None

http_session: Optional[aiohttp.ClientSession] = None


# ============================================================
# HELPERS
# ============================================================

def now_ts() -> int:
    return int(time.time())


def india_now() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(
            ZoneInfo("Asia/Kolkata")
        )

    except Exception:
        return datetime.now(
            timezone.utc
        ) + timedelta(
            hours=5,
            minutes=30,
        )


def format_ts(ts: Optional[int]) -> str:
    if not ts:
        return "-"

    try:
        from zoneinfo import ZoneInfo

        dt = datetime.fromtimestamp(
            ts,
            ZoneInfo("Asia/Kolkata"),
        )

        return dt.strftime(
            "%d %b %Y, %I:%M %p"
        )

    except Exception:
        return str(ts)


def make_txnid(
    user_id: int,
) -> str:

    random_part = secrets.token_hex(8)

    txn_id = (
        f"TG{user_id}{random_part}"
    )

    return txn_id[:50]


def error_id() -> str:
    return (
        f"E-{now_ts()}-"
        f"{secrets.token_hex(3).upper()}"
    )


# ============================================================
# PAYU V2 AUTH
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

    authorization = (
        f'hmac username="{PAYU_KEY}",'
        f' algorithm="sha512",'
        f' headers="date",'
        f' signature="{signature}"'
    )

    return authorization


# ============================================================
# PAYU V2 PAYMENT CREATION
# ============================================================
#
# THIS IS THE EXISTING PAYU V2 FLOW.
#
# OAuth Payment Links API is NOT used here.
#
# ============================================================

async def create_payment(
    user_id: int,
    amount: str,
    plan_key: str,
):

    txn_id = make_txnid(
        user_id
    )

    plan = PLANS[
        plan_key
    ]

    payload = {
        "accountId": PAYU_KEY,

        "txnId": txn_id,

        "currency": "INR",

        "order": {
            "productInfo": plan[
                "description"
            ],

            "paymentChargeSpecification": {
                "price": amount
            }
        },

        "billingDetails": {
            "firstName": "Telegram",

            "email": (
                f"telegram"
                f"{user_id}"
                f"@example.com"
            ),

            "phone": "9999999999",

            "address1": "India",

            "city": "Indore",

            "state": "Madhya Pradesh",

            "country": "India",

            "zipCode": "452001",
        },

        "callBackActions": {
            "successAction": SUCCESS_URL,

            "failureAction": FAILURE_URL,

            "cancelAction": FAILURE_URL,
        },

        "additionalInfo": {
            "txnFlow": "nonseamless",

            "createOrder": True,

            "udf1": str(user_id),

            "udf2": plan_key,
        },
    }

    body = json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
    )

    date = format_datetime(
        datetime.now(
            timezone.utc
        ),
        usegmt=True,
    )

    authorization = make_auth(
        body,
        date,
    )

    headers = {
        "date": date,

        "authorization":
            authorization,

        "content-type":
            "application/json",

        "accept":
            "application/json",
    }

    print()
    print("=" * 70)
    print("PAYU LIVE V2 REQUEST")
    print("=" * 70)
    print(
        "Transaction:",
        txn_id,
    )
    print(
        "Plan:",
        plan_key,
    )
    print(
        "Amount:",
        amount,
    )
    print("=" * 70)

    timeout = aiohttp.ClientTimeout(
        total=30
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

                print(
                    "PAYU HTTP:",
                    response.status,
                )

                print(
                    "PAYU RESPONSE:",
                    text,
                )

                try:

                    data = json.loads(
                        text
                    )

                except json.JSONDecodeError:

                    return (
                        None,
                        txn_id,
                        {
                            "http_status":
                                response.status,

                            "raw_response":
                                text,
                        },
                    )

                if response.status >= 400:

                    return (
                        None,
                        txn_id,
                        data,
                    )

                checkout_url = None

                if isinstance(
                    data,
                    dict,
                ):

                    result = data.get(
                        "result"
                    )

                    if isinstance(
                        result,
                        dict,
                    ):

                        checkout_url = (
                            result.get(
                                "checkoutUrl"
                            )
                            or result.get(
                                "checkoutURL"
                            )
                            or result.get(
                                "checkout_url"
                            )
                            or result.get(
                                "paymentUrl"
                            )
                            or result.get(
                                "paymentURL"
                            )
                            or result.get(
                                "payment_url"
                            )
                        )

                    checkout_url = (
                        checkout_url
                        or data.get(
                            "checkoutUrl"
                        )
                        or data.get(
                            "checkoutURL"
                        )
                        or data.get(
                            "checkout_url"
                        )
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
                "error":
                    "PayU request timeout"
            },
        )

    except aiohttp.ClientError as e:

        return (
            None,
            txn_id,
            {
                "error":
                    f"HTTP error: {e}"
            },
        )

    except Exception as e:

        return (
            None,
            txn_id,
            {
                "error":
                    str(e)
            },
        )


# ============================================================
# SUPABASE REST
# ============================================================

def supabase_headers(
    prefer: Optional[str] = None,
):

    headers = {
        "apikey":
            SUPABASE_KEY,

        "Authorization":
            f"Bearer {SUPABASE_KEY}",

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

    if not SUPABASE_KEY:
        raise RuntimeError(
            "SUPABASE_KEY missing."
        )

    url = (
        f"{SUPABASE_URL}"
        f"/rest/v1/"
        f"{table}"
    )

    timeout = aiohttp.ClientTimeout(
        total=30
    )

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
                    data = json.loads(
                        text
                    )

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
# SUPABASE USER
# ============================================================

async def save_user(
    tg_user,
):

    data = {
        "user_id":
            tg_user.id,

        "username":
            tg_user.username,

        "first_name":
            tg_user.first_name,

        "last_name":
            tg_user.last_name,

        "updated_at":
            now_ts(),
    }

    # Upsert by user_id.

    await supabase_request(
        "POST",
        "bot_users",
        params={
            "on_conflict":
                "user_id",
        },
        json_data=data,
    )


# ============================================================
# CREATE ORDER
# ============================================================

async def create_order(
    *,
    reference_id: str,
    user_id: int,
    plan_key: str,
    amount_paise: int,
    txn_id: str,
):

    data = {
        "reference_id":
            reference_id,

        "user_id":
            user_id,

        "plan_key":
            plan_key,

        "amount_paise":
            amount_paise,

        "txn_id":
            txn_id,

        "status":
            "created",

        "created_at":
            now_ts(),

        "access_sent":
            False,
    }

    result = await supabase_request(
        "POST",
        "orders",
        json_data=data,
    )

    if isinstance(
        result,
        list,
    ) and result:

        return result[0]

    return result


# ============================================================
# GET ORDER
# ============================================================

async def get_order(
    reference_id: str,
):

    result = await supabase_request(
        "GET",
        "orders",
        params={
            "reference_id":
                f"eq.{reference_id}",

            "limit":
                "1",
        },
    )

    if isinstance(
        result,
        list,
    ) and result:

        return result[0]

    return None


# ============================================================
# GET ORDER BY TXN
# ============================================================

async def get_order_by_txn(
    txn_id: str,
):

    result = await supabase_request(
        "GET",
        "orders",
        params={
            "txn_id":
                f"eq.{txn_id}",

            "limit":
                "1",
        },
    )

    if isinstance(
        result,
        list,
    ) and result:

        return result[0]

    return None


# ============================================================
# GET LATEST USER ORDER
# ============================================================

async def get_latest_paid_subscription(
    user_id: int,
):

    result = await supabase_request(
        "GET",
        "subscriptions",
        params={
            "user_id":
                f"eq.{user_id}",

            "status":
                "eq.active",

            "order":
                "expires_at.desc",

            "limit":
                "1",
        },
    )

    if isinstance(
        result,
        list,
    ) and result:

        return result[0]

    return None


# ============================================================
# UPDATE ORDER
# ============================================================

async def update_order(
    reference_id: str,
    values: dict,
):

    result = await supabase_request(
        "PATCH",
        "orders",
        params={
            "reference_id":
                f"eq.{reference_id}",
        },
        json_data=values,
    )

    return result


# ============================================================
# SUBSCRIPTION
# ============================================================

async def create_subscription(
    order: dict,
    payment_id: str,
):

    plan = PLANS[
        order[
            "plan_key"
        ]
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

    # If same user/plan already has active
    # subscription, extend from existing expiry.

    existing = await supabase_request(
        "GET",
        "subscriptions",
        params={
            "user_id":
                f"eq.{order['user_id']}",

            "plan_key":
                f"eq.{order['plan_key']}",

            "status":
                "eq.active",

            "order":
                "expires_at.desc",

            "limit":
                "1",
        },
    )

    if (
        duration_days > 0
        and isinstance(existing, list)
        and existing
    ):

        old_expiry = (
            existing[0].get(
                "expires_at"
            )
        )

        if old_expiry:

            try:

                old_expiry_int = int(
                    old_expiry
                )

                if old_expiry_int > started_at:

                    expires_at = (
                        old_expiry_int
                        + duration_days * 86400
                    )

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

    if isinstance(
        result,
        list,
    ) and result:

        return result[0]

    return result


# ============================================================
# GET ALL ACTIVE SUBSCRIPTIONS
# ============================================================

async def get_active_subscriptions():

    result = await supabase_request(
        "GET",
        "subscriptions",
        params={
            "status":
                "eq.active",

            "limit":
                "1000",
        },
    )

    return (
        result
        if isinstance(
            result,
            list,
        )
        else []
    )


# ============================================================
# UPDATE SUBSCRIPTION
# ============================================================

async def update_subscription(
    subscription_id: int,
    values: dict,
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


# ============================================================
# EVENT DEDUPE
# ============================================================

async def event_processed(
    event_id: str,
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
        isinstance(
            result,
            list,
        )
        and result
    )


async def save_event(
    event_id: str,
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

    plan = PLANS[
        plan_key
    ]

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

            print(
                "Invite creation failed:",
                repr(e),
            )

    static_link = plan.get(
        "access_link"
    )

    if static_link:
        return static_link

    return None


# ============================================================
# DELIVER ACCESS
# ============================================================

async def deliver_access(
    order: dict,
    payment_id: str,
):

    subscription = (
        await create_subscription(
            order,
            payment_id,
        )
    )

    access_link = await make_access_link(
        order["plan_key"]
    )

    if access_link:

        await supabase_request(
            "PATCH",
            "subscriptions",
            params={
                "id":
                    f"eq.{subscription['id']}",
            },
            json_data={
                "access_link":
                    access_link,
            },
        )

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
            "♾️ Lifetime Access"
        )

    else:

        expiry = subscription.get(
            "expires_at"
        )

        expiry_text = (
            f"⏳ Expires: "
            f"<b>{format_ts(expiry)}</b>"
        )

    if access_link:

        access_text = (
            "🔗 <b>Your Channel Access:</b>\n"
            f"{access_link}"
        )

    else:

        access_text = (
            "⚠️ Access link configure nahi hai.\n"
            f"Support: @{SUPPORT_USERNAME}"
        )

    await bot.send_message(

        order["user_id"],

        "🎉 <b>PAYMENT CONFIRMED!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Paid: "
        f"<b>₹{plan['price']}</b>\n\n"

        f"{expiry_text}\n\n"

        f"🧾 Transaction:\n"
        f"<code>{order.get('txn_id')}</code>\n\n"

        f"{access_text}\n\n"

        "⚠️ Access link share mat karein.",
    )

    await update_order(
        order["reference_id"],
        {
            "status":
                "paid",

            "payment_id":
                payment_id,

            "paid_at":
                now_ts(),

            "access_sent":
                True,
        },
    )


# ============================================================
# PAYU CALLBACK HASH
# ============================================================
#
# PayU standard callback verification.
#
# If PayU callback contains hash, verify it.
#
# ============================================================

def verify_payu_callback_hash(
    data: dict,
) -> bool:

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

    salt = PAYU_SECRET

    if additional:

        raw = (
            f"{additional}|"
            f"{salt}|"
            f"{status}||||||"
            f"{udf5}|{udf4}|{udf3}|"
            f"{udf2}|{udf1}|"
            f"{email}|{firstname}|"
            f"{productinfo}|{amount}|"
            f"{txnid}|{key}"
        )

    else:

        raw = (
            f"{salt}|"
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
# COMPLETE PAYMENT
# ============================================================

async def process_successful_payment(
    data: dict,
):

    txn_id = (
        data.get("txnid")
        or data.get("txnId")
        or data.get("transactionId")
    )

    if not txn_id:
        return False

    order = await get_order_by_txn(
        str(txn_id)
    )

    if not order:
        print(
            "No order found for txn:",
            txn_id,
        )
        return False

    if order.get("status") == "paid":
        return True

    # Amount validation.

    received_amount = (
        data.get("amount")
        or data.get("amt")
    )

    if received_amount:

        try:

            expected = (
                float(
                    order[
                        "amount_paise"
                    ]
                ) / 100
            )

            received = float(
                received_amount
            )

            if abs(
                expected - received
            ) > 0.01:

                print(
                    "AMOUNT MISMATCH:",
                    expected,
                    received,
                )

                return False

        except Exception:

            return False

    payment_id = (
        data.get("mihpayid")
        or data.get("paymentId")
        or data.get("bank_ref_num")
        or str(txn_id)
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

    await deliver_access(
        order,
        str(payment_id),
    )

    await save_event(
        event_id
    )

    return True


# ============================================================
# CREATE PAYMENT FOR PLAN
# ============================================================

async def create_plan_payment(
    user_id: int,
    plan_key: str,
):

    plan = PLANS[
        plan_key
    ]

    amount = str(
        plan["price"]
    )

    checkout_url, txn_id, result = (
        await create_payment(
            user_id=user_id,
            amount=amount,
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
                    text="⚡ Gold Dark",
                    callback_data="plan:gold",
                ),

                InlineKeyboardButton(
                    text="⚡ Silver Dark",
                    callback_data="plan:silver",
                ),
            ],

            [
                InlineKeyboardButton(
                    text="⚡ Bronze Dark",
                    callback_data="plan:bronze",
                ),

                InlineKeyboardButton(
                    text="⚡ Iron Dark",
                    callback_data="plan:iron",
                ),
            ],

            [
                InlineKeyboardButton(
                    text="📋 My Plan",
                    callback_data="myplan",
                ),
            ],
        ]
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
                ),
            ],

            [
                InlineKeyboardButton(
                    text="↩️ Back",
                    callback_data="home",
                ),
            ],
        ]
    )


def payment_keyboard(
    checkout_url: str,
    txn_id: str,
):

    return InlineKeyboardMarkup(
        inline_keyboard=[

            [
                InlineKeyboardButton(
                    text="💳 Pay Now",
                    url=checkout_url,
                ),
            ],

            [
                InlineKeyboardButton(
                    text="🔄 Verify Payment",
                    callback_data=(
                        f"verify:{txn_id}"
                    ),
                ),
            ],

            [
                InlineKeyboardButton(
                    text="📋 My Plan",
                    callback_data="myplan",
                ),
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
        f"@{SUPPORT_USERNAME}"
        if SUPPORT_USERNAME
        else "Admin"
    )

    text = (
        "👋 <b>Welcome to DARK STORE!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        "🔥 <b>Choose your channel plan:</b>\n\n"

        "⚡ Gold Dark\n"
        "⚡ Silver Dark\n"
        "⚡ Bronze Dark\n"
        "⚡ Iron Dark\n\n"

        "💳 Secure PayU payment\n"
        "🔐 Automatic payment confirmation\n"
        "🔗 Automatic channel access\n\n"

        f"💬 Support: {support}"
    )

    await bot.send_message(
        chat_id,
        text,
        reply_markup=home_keyboard(),
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

    try:
        await save_user(
            message.from_user
        )
    except Exception as e:
        print(
            "Save user error:",
            repr(e),
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
        callback.from_user.id
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

    duration = int(
        plan[
            "duration_days"
        ]
    )

    if duration <= 0:

        duration_text = (
            "♾️ Lifetime"
        )

    else:

        duration_text = (
            f"⏳ {duration} days"
        )

    text = (
        f"<b>{plan['name']}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"💰 Price: "
        f"<b>₹{plan['price']}</b>\n"

        f"📅 Access: "
        f"<b>{duration_text}</b>\n\n"

        "🔐 Secure PayU checkout\n"
        "⚡ Automatic verification\n"
        "🔗 Channel access after payment\n\n"

        "👇 Continue karne ke liye Pay button dabao."
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
        "PayU payment create ho raha hai..."
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
            await create_plan_payment(
                user_id=
                    callback.from_user.id,

                plan_key=
                    plan_key,
            )
        )

    except Exception as e:

        eid = error_id()

        print(
            f"[{eid}] PAYMENT ERROR:",
            repr(e),
        )

        await callback.message.answer(
            "❌ <b>Payment create nahi ho paya.</b>\n\n"
            f"Reason:\n"
            f"<code>{str(e)[:1500]}</code>\n\n"
            f"Error ID: <code>{eid}</code>"
        )

        return

    plan = PLANS[
        plan_key
    ]

    text = (
        "💳 <b>PAYU PAYMENT</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan['name']}</b>\n"

        f"💰 Amount: "
        f"<b>₹{plan['price']}</b>\n\n"

        "👇 PayU checkout open karne ke liye "
        "button dabao.\n\n"

        "Payment complete hone ke baad "
        "bot automatically verify karega.\n\n"

        f"🧾 Transaction:\n"
        f"<code>{result['txn_id']}</code>"
    )

    await callback.message.answer(
        text,
        reply_markup=payment_keyboard(
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
        "Payment status check ho raha hai..."
    )

    txn_id = callback.data.split(
        ":",
        1,
    )[1]

    order = await get_order_by_txn(
        txn_id
    )

    if not order:

        await callback.message.answer(
            "❌ Order nahi mila."
        )

        return

    if (
        int(order["user_id"])
        != callback.from_user.id
    ):

        await callback.message.answer(
            "❌ Ye order aapka nahi hai."
        )

        return

    if order.get("status") == "paid":

        await callback.message.answer(
            "✅ <b>Payment already confirmed.</b>\n\n"
            "Use /myplan."
        )

        return

    await callback.message.answer(
        "⏳ Payment verification PayU callback "
        "ke through complete hogi.\n\n"
        "Agar payment complete ho chuka hai to "
        "thodi der wait karein."
    )


# ============================================================
# MY PLAN
# ============================================================

async def send_my_plan(
    message: Message,
):

    subscription = (
        await get_latest_paid_subscription(
            message.from_user.id
        )
    )

    if not subscription:

        await message.answer(
            "📋 <b>My Plan</b>\n\n"
            "❌ Aapka koi active plan nahi hai.",
            reply_markup=home_keyboard(),
        )

        return

    plan = PLANS.get(
        subscription[
            "plan_key"
        ],
        {},
    )

    expires_at = subscription.get(
        "expires_at"
    )

    if expires_at:

        try:

            expires_int = int(
                expires_at
            )

        except Exception:

            expires_int = 0

        if (
            expires_int
            and expires_int <= now_ts()
        ):

            await update_subscription(
                subscription["id"],
                {
                    "status":
                        "expired",
                },
            )

            await message.answer(
                "⌛ <b>Your plan has expired.</b>\n\n"
                "Naya plan purchase karne ke liye "
                "neeche button use karein.",
                reply_markup=home_keyboard(),
            )

            return

        expiry_text = (
            f"⏳ Expires: "
            f"<b>{format_ts(expires_int)}</b>"
        )

    else:

        expiry_text = (
            "♾️ <b>Lifetime Access</b>"
        )

    access_link = (
        subscription.get(
            "access_link"
        )
    )

    buttons = []

    if access_link:

        buttons.append(
            [
                InlineKeyboardButton(
                    text="🔗 My Channel Link",
                    url=access_link,
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                text="🛒 Buy Another Plan",
                callback_data="home",
            )
        ]
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=buttons
    )

    text = (
        "📋 <b>MY PLAN</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"📦 Plan: "
        f"<b>{plan.get('name', subscription['plan_key'])}</b>\n"

        "📌 Status: <b>ACTIVE</b>\n"

        f"{expiry_text}\n\n"

        "🔐 Your access is active."
    )

    await message.answer(
        text,
        reply_markup=keyboard,
    )


@router.message(
    Command("myplan")
)
async def myplan_command(
    message: Message,
):

    await send_my_plan(
        message
    )


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
# BROADCAST
# ============================================================

@router.message(
    Command("broadcast")
)
async def broadcast_command(
    message: Message,
):

    if (
        message.from_user.id
        not in ADMIN_IDS
    ):

        await message.answer(
            "❌ Admin only."
        )

        return

    text = (
        message.text or ""
    )

    parts = text.split(
        maxsplit=1
    )

    if len(parts) < 2:

        await message.answer(
            "Usage:\n\n"
            "<code>/broadcast Your message here</code>"
        )

        return

    broadcast_text = parts[1].strip()

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

    if not isinstance(
        users,
        list,
    ):

        await message.answer(
            "❌ Users fetch failed."
        )

        return

    sent = 0
    failed = 0

    status_msg = await message.answer(
        "📢 Broadcast started..."
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

            print(
                "Broadcast failed:",
                user_id,
                repr(e),
            )

        await asyncio.sleep(
            0.05
        )

    await status_msg.edit_text(
        "📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>"
    )


# ============================================================
# STATS
# ============================================================

@router.message(
    Command("stats")
)
async def stats_command(
    message: Message,
):

    if (
        message.from_user.id
        not in ADMIN_IDS
    ):

        await message.answer(
            "❌ Admin only."
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
    revenue = 0

    if isinstance(
        orders,
        list,
    ):

        for order in orders:

            if order.get(
                "status"
            ) == "paid":

                paid_orders += 1

                revenue += (
                    int(
                        order.get(
                            "amount_paise",
                            0,
                        )
                    ) / 100
                )

    await message.answer(
        "📊 <b>BOT STATS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"

        f"👥 Users: <b>{total_users}</b>\n"
        f"🧾 Orders: <b>{total_orders}</b>\n"
        f"✅ Paid: <b>{paid_orders}</b>\n"
        f"💰 Revenue: <b>₹{revenue:.2f}</b>"
    )


# ============================================================
# PAYU SUCCESS CALLBACK
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

        print(
            "PayU success parse error:",
            repr(e),
        )

    print()
    print("=" * 70)
    print("PAYU SUCCESS CALLBACK")
    print("=" * 70)
    print(
        json.dumps(
            data,
            indent=2,
            ensure_ascii=False,
            default=str,
        )
    )
    print("=" * 70)

    txn_id = (
        data.get("txnid")
        or data.get("txnId")
    )

    # If PayU gives a hash, verify it.

    if data.get("hash"):

        valid_hash = (
            verify_payu_callback_hash(
                data
            )
        )

        if not valid_hash:

            print(
                "INVALID PAYU CALLBACK HASH"
            )

            return web.Response(
                status=403,
                text="Invalid payment signature.",
            )

    status = str(
        data.get(
            "status",
            "",
        )
    ).lower().strip()

    if status == "success":

        try:

            await process_successful_payment(
                data
            )

        except Exception as e:

            print(
                "Payment processing error:",
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
content="width=device-width,initial-scale=1">
<title>Payment Successful</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>✅ Payment Successful</h2>
<p>Payment verification complete.</p>
<p>Please return to Telegram.</p>
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

    except Exception:
        pass

    print(
        "PAYU FAILURE:",
        json.dumps(
            data,
            ensure_ascii=False,
            default=str,
        ),
    )

    txn_id = (
        data.get("txnid")
        or data.get("txnId")
    )

    if txn_id:

        try:

            order = await get_order_by_txn(
                str(txn_id)
            )

            if order:

                await update_order(
                    order[
                        "reference_id"
                    ],
                    {
                        "status":
                            "failed",
                    },
                )

        except Exception as e:

            print(
                "Failure DB error:",
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
content="width=device-width,initial-scale=1">
<title>Payment Failed</title>
</head>
<body style="font-family:Arial;text-align:center;padding:40px">
<h2>❌ Payment Failed</h2>
<p>Please return to Telegram and try again.</p>
</body>
</html>
""",
    )


# ============================================================
# EXPIRY WATCHER
# ============================================================

async def expiry_watcher():

    print(
        "Subscription expiry watcher started."
    )

    while True:

        try:

            subscriptions = (
                await get_active_subscriptions()
            )

            current = now_ts()

            for sub in subscriptions:

                expires_at = (
                    sub.get(
                        "expires_at"
                    )
                )

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

                    if not sub.get(
                        "expired_alert_sent"
                    ):

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
                                sub[
                                    "plan_key"
                                ],
                                {},
                            )

                            await bot.send_message(

                                sub["user_id"],

                                "⌛ <b>PLAN EXPIRED</b>\n"
                                "━━━━━━━━━━━━━━━━━━\n\n"

                                f"📦 Plan: "
                                f"<b>{plan.get('name', sub['plan_key'])}</b>\n\n"

                                "Aapka access period expire ho gaya hai.\n\n"

                                "🔄 Renew karne ke liye "
                                "neeche /start use karein.",
                            )

                        except Exception as e:

                            print(
                                "Expiry alert failed:",
                                repr(e),
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
                    remaining
                    <= reminder_seconds
                    and not sub.get(
                        "reminder_sent"
                    )
                ):

                    plan = PLANS.get(
                        sub[
                            "plan_key"
                        ],
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
                            f"<b>{plan.get('name', sub['plan_key'])}</b>\n"

                            f"⏳ Expires: "
                            f"<b>{format_ts(expires_at)}</b>\n\n"

                            "Renew karna na bhoolna.\n\n"

                            "🔄 /start → plan select karein.",
                        )

                    except Exception as e:

                        print(
                            "Reminder failed:",
                            repr(e),
                        )

        except asyncio.CancelledError:

            raise

        except Exception as e:

            print(
                "Expiry watcher error:",
                repr(e),
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

    print(
        f"Web server running on {HOST}:{PORT}"
    )

    return runner


# ============================================================
# VALIDATION
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

    if not SUPABASE_KEY:
        errors.append(
            "SUPABASE_KEY missing"
        )

    if not PUBLIC_BASE_URL:
        errors.append(
            "PUBLIC_BASE_URL missing"
        )

    if "YOUR-DOMAIN" in PUBLIC_BASE_URL:
        errors.append(
            "Replace PUBLIC_BASE_URL"
        )

    if not ADMIN_IDS:
        errors.append(
            "ADMIN_IDS missing"
        )

    if "apitest.payu.in" in PAYU_URL.lower():
        errors.append(
            "Test PayU URL detected. "
            "Use production v2 endpoint."
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

    print()
    print("=" * 70)
    print("DARK STORE BOT STARTED")
    print("=" * 70)
    print(
        "PayU:",
        PAYU_URL,
    )
    print(
        "Supabase:",
        SUPABASE_URL,
    )
    print(
        "Public URL:",
        PUBLIC_BASE_URL,
    )
    print(
        "Admins:",
        ADMIN_IDS,
    )
    print("=" * 70)

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

        print(
            "Bot stopped."
        )
