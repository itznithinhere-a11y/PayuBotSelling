import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import secrets
import time
from typing import Optional

import aiohttp
import qrcode
from aiohttp import web
from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from dotenv import load_dotenv
from supabase import Client, create_client

load_dotenv()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "").strip()
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "").strip()
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "").strip()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "").strip()

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
]

WEBHOOK_HOST = os.getenv("WEBHOOK_HOST", "0.0.0.0").strip()
WEBHOOK_PORT = int(os.getenv("PORT", os.getenv("WEBHOOK_PORT", "10000")))

SUPPORT_USERNAME = os.getenv(
    "SUPPORT_USERNAME",
    "@Help_desk3_bot",
).strip()

PLANS = {
    "gold": {
        "name": "⚡ Gold Dark (Channel 1)",
        "price": 1499,
        "description": "Gold Dark — 30 Days Access",
        "duration_days": 30,
        "channel_id": os.getenv("GOLD_CHANNEL_ID", "").strip(),
        "access_link": os.getenv("GOLD_ACCESS_LINK", "").strip(),
    },
    "silver": {
        "name": "⚡ Silver Dark (Channel 2)",
        "price": 1499,
        "description": "Silver Dark — 30 Days Access",
        "duration_days": 30,
        "channel_id": os.getenv("SILVER_CHANNEL_ID", "").strip(),
        "access_link": os.getenv("SILVER_ACCESS_LINK", "").strip(),
    },
    "bronze": {
        "name": "⚡ Bronze Dark (Channel 3)",
        "price": 1499,
        "description": "Bronze Dark — 30 Days Access",
        "duration_days": 30,
        "channel_id": os.getenv("BRONZE_CHANNEL_ID", "").strip(),
        "access_link": os.getenv("BRONZE_ACCESS_LINK", "").strip(),
    },
    "iron": {
        "name": "⚡ Iron Dark (Channel 4)",
        "price": 1499,
        "description": "Iron Dark — 30 Days Access",
        "duration_days": 30,
        "channel_id": os.getenv("IRON_CHANNEL_ID", "").strip(),
        "access_link": os.getenv("IRON_ACCESS_LINK", "").strip(),
    },
}

router = Router()
bot: Optional[Bot] = None
supabase: Optional[Client] = None


# ============================================================
# FSM
# ============================================================

class BroadcastState(StatesGroup):
    waiting_for_message = State()


# ============================================================
# SUPABASE HELPERS
# ============================================================

def db():
    if supabase is None:
        raise RuntimeError("Supabase is not initialized.")
    return supabase


def init_supabase():
    global supabase

    if not SUPABASE_URL or not SUPABASE_KEY:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_KEY environment variables."
        )

    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    logger.info("Supabase initialized")


def save_user(user_id: int, username: str, first_name: str):
    try:
        db().table("bot_users").upsert(
            {
                "user_id": user_id,
                "username": username,
                "first_name": first_name,
                "created_at": int(time.time()),
            },
            on_conflict="user_id",
        ).execute()
    except Exception:
        logger.exception("Failed to save user")


def save_order(
    reference_id: str,
    user_id: int,
    plan_key: str,
    amount_paise: int,
    payment_link_id: str,
    payment_link_url: str,
):
    try:
        db().table("orders").insert(
            {
                "reference_id": reference_id,
                "user_id": user_id,
                "plan_key": plan_key,
                "amount_paise": amount_paise,
                "payment_link_id": payment_link_id,
                "payment_link_url": payment_link_url,
                "status": "created",
                "created_at": int(time.time()),
                "access_sent": 0,
            }
        ).execute()
    except Exception:
        logger.exception("Failed to save order")
        raise


def get_order(reference_id: str):
    try:
        res = (
            db()
            .table("orders")
            .select("*")
            .eq("reference_id", reference_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception:
        logger.exception("Failed to get order")
        return None


def get_order_by_payment_link(payment_link_id: str):
    try:
        res = (
            db()
            .table("orders")
            .select("*")
            .eq("payment_link_id", payment_link_id)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception:
        logger.exception("Failed to get order by payment link")
        return None


def get_latest_order(user_id: int):
    try:
        res = (
            db()
            .table("orders")
            .select("*")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        return res.data[0] if res.data else None
    except Exception:
        logger.exception("Failed to get latest order")
        return None


def mark_paid(reference_id: str, payment_id: str):
    try:
        db().table("orders").update(
            {
                "status": "paid",
                "payment_id": payment_id or None,
                "paid_at": int(time.time()),
            }
        ).eq("reference_id", reference_id).execute()
    except Exception:
        logger.exception("Failed to mark order paid")
        raise


def mark_access_sent(reference_id: str):
    try:
        db().table("orders").update(
            {"access_sent": 1}
        ).eq("reference_id", reference_id).execute()
    except Exception:
        logger.exception("Failed to mark access sent")


def set_subscription_expiry(reference_id: str, expires_at: int):
    try:
        db().table("orders").update(
            {
                "access_expires_at": expires_at,
                "subscription_days": 30,
            }
        ).eq("reference_id", reference_id).execute()
    except Exception:
        logger.exception("Failed to set subscription expiry")
        raise


def get_active_expiry(user_id: int, plan_key: str) -> int:
    try:
        now = int(time.time())

        res = (
            db()
            .table("orders")
            .select("access_expires_at")
            .eq("user_id", user_id)
            .eq("plan_key", plan_key)
            .eq("status", "paid")
            .gt("access_expires_at", now)
            .order("access_expires_at", desc=True)
            .limit(1)
            .execute()
        )

        if res.data:
            return int(res.data[0].get("access_expires_at") or 0)

    except Exception:
        logger.exception("Failed to get active subscription")

    return 0


def get_expired_orders():
    try:
        now = int(time.time())

        res = (
            db()
            .table("orders")
            .select("*")
            .eq("status", "paid")
            .not_.is_("access_expires_at", "null")
            .lte("access_expires_at", now)
            .is_("access_revoked_at", "null")
            .limit(100)
            .execute()
        )

        return res.data or []

    except Exception:
        logger.exception("Failed to fetch expired subscriptions")
        return []


def mark_subscription_revoked(reference_id: str):
    try:
        db().table("orders").update(
            {
                "access_revoked_at": int(time.time()),
                "status": "expired",
            }
        ).eq("reference_id", reference_id).execute()
    except Exception:
        logger.exception("Failed to mark subscription revoked")


def event_already_processed(event_id: str) -> bool:
    if not event_id:
        return False

    try:
        res = (
            db()
            .table("processed_events")
            .select("event_id")
            .eq("event_id", event_id)
            .limit(1)
            .execute()
        )
        return bool(res.data)
    except Exception:
        logger.exception("Failed checking processed event")
        return False


def save_event(event_id: str):
    if not event_id:
        return

    try:
        db().table("processed_events").insert(
            {
                "event_id": event_id,
                "created_at": int(time.time()),
            }
        ).execute()
    except Exception:
        logger.exception("Failed to save processed event")


def get_all_users():
    try:
        res = db().table("bot_users").select("user_id").execute()
        return [
            int(row["user_id"])
            for row in (res.data or [])
            if row.get("user_id") is not None
        ]
    except Exception:
        logger.exception("Failed to fetch users for broadcast")
        return []


def format_expiry(timestamp: int) -> str:
    if not timestamp:
        return "-"

    return time.strftime(
        "%d %b %Y, %I:%M %p",
        time.localtime(int(timestamp)),
    )


# ============================================================
# CONFIG VALIDATION
# ============================================================

def validate_config():
    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not RAZORPAY_KEY_ID:
        missing.append("RAZORPAY_KEY_ID")

    if not RAZORPAY_KEY_SECRET:
        missing.append("RAZORPAY_KEY_SECRET")

    if not RAZORPAY_WEBHOOK_SECRET:
        missing.append("RAZORPAY_WEBHOOK_SECRET")

    if not SUPABASE_URL:
        missing.append("SUPABASE_URL")

    if not SUPABASE_KEY:
        missing.append("SUPABASE_KEY")

    if missing:
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
        )

    for key, plan in PLANS.items():
        if not plan["channel_id"] and not plan["access_link"]:
            logger.warning(
                "Plan %s has neither CHANNEL_ID nor ACCESS_LINK configured.",
                key,
            )


# ============================================================
# RAZORPAY
# ============================================================

async def razorpay_request(
    method: str,
    endpoint: str,
    payload=None,
):
    url = f"https://api.razorpay.com/v1/{endpoint.lstrip('/')}"
    auth = aiohttp.BasicAuth(
        RAZORPAY_KEY_ID,
        RAZORPAY_KEY_SECRET,
    )

    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.request(
            method,
            url,
            auth=auth,
            json=payload,
        ) as resp:
            text = await resp.text()

            if resp.status >= 400:
                raise RuntimeError(
                    f"Razorpay HTTP {resp.status}: {text[:1000]}"
                )

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                raise RuntimeError(
                    f"Razorpay returned invalid JSON: {text[:500]}"
                )


async def create_payment_link(
    user_id: int,
    plan_key: str,
):
    if plan_key not in PLANS:
        raise ValueError("Invalid plan")

    plan = PLANS[plan_key]

    amount_paise = int(round(float(plan["price"]) * 100))
    reference_id = f"{user_id}_{secrets.token_hex(8)}"

    payload = {
        "amount": amount_paise,
        "currency": "INR",
        "accept_partial": False,
        "reference_id": reference_id,
        "description": plan["description"],
        "notify": {
            "sms": False,
            "email": False,
            "whatsapp": False,
        },
        "reminder_enable": False,
        "notes": {
            "telegram_user_id": str(user_id),
            "plan_key": plan_key,
        },
    }

    result = await razorpay_request(
        "POST",
        "payment_links",
        payload,
    )

    payment_link_id = result.get("id")
    short_url = result.get("short_url")

    if not payment_link_id or not short_url:
        raise RuntimeError(
            f"Invalid Razorpay payment-link response: {result}"
        )

    save_order(
        reference_id=reference_id,
        user_id=user_id,
        plan_key=plan_key,
        amount_paise=amount_paise,
        payment_link_id=payment_link_id,
        payment_link_url=short_url,
    )

    return result


def verify_webhook(
    raw_body: bytes,
    received_signature: str,
) -> bool:
    if not RAZORPAY_WEBHOOK_SECRET:
        return False

    if not received_signature:
        return False

    expected = hmac.new(
        RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(
        expected,
        received_signature,
    )


# ============================================================
# TELEGRAM UI
# ============================================================

def support_url():
    return (
        f"https://t.me/"
        f"{SUPPORT_USERNAME.lstrip('@')}"
    )


def main_keyboard(is_admin: bool = False):
    kb = [
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
                url=support_url(),
            )
        ],
    ]

    if is_admin:
        kb.append(
            [
                InlineKeyboardButton(
                    text="📢 Broadcast",
                    callback_data="admin_broadcast",
                )
            ]
        )

    return InlineKeyboardMarkup(
        inline_keyboard=kb
    )


def plan_keyboard(plan_key: str):
    plan = PLANS[plan_key]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        f"⭐ Buy ₹{plan['price']} "
                        f"({plan.get('duration_days', 30)} Days)"
                    ),
                    callback_data=f"buy:{plan_key}",
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


def payment_keyboard(plan_key: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Check Payment",
                    callback_data=f"check:{plan_key}",
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


def access_keyboard(plan_key: str):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Renew Subscription",
                    callback_data=f"renew:{plan_key}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🏠 Main Menu",
                    callback_data="home",
                )
            ],
        ]
    )


# ============================================================
# HOME
# ============================================================

async def show_home(callback: CallbackQuery):
    user_id = callback.from_user.id
    is_admin = user_id in ADMIN_IDS

    text = (
        "👋 <b>Welcome to DARK STORE!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<blockquote><b>Channels:</b>\n"
        "⚡ Gold Dark (Channel 1)\n"
        "⚡ Silver Dark (Channel 2)\n"
        "⚡ Bronze Dark (Channel 3)\n"
        "⚡ Iron Dark (Channel 4)</blockquote>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💬 Support: {SUPPORT_USERNAME}\n\n"
        "🤖 <i>Powered by Telegram Store Bot</i>"
    )

    try:
        await callback.message.edit_text(
            text,
            reply_markup=main_keyboard(is_admin),
        )
    except Exception:
        await callback.message.answer(
            text,
            reply_markup=main_keyboard(is_admin),
        )


@router.message(CommandStart())
async def start_handler(message: Message):
    save_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.first_name or "",
    )

    is_admin = message.from_user.id in ADMIN_IDS

    text = (
        "👋 <b>Welcome to DARK STORE!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        "<blockquote><b>Channels:</b>\n"
        "⚡ Gold Dark (Channel 1)\n"
        "⚡ Silver Dark (Channel 2)\n"
        "⚡ Bronze Dark (Channel 3)\n"
        "⚡ Iron Dark (Channel 4)</blockquote>\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"💬 Support: {SUPPORT_USERNAME}\n\n"
        "🤖 <i>Powered by Telegram Store Bot</i>"
    )

    await message.answer(
        text,
        reply_markup=main_keyboard(is_admin),
    )


@router.callback_query(F.data == "home")
async def home_callback(callback: CallbackQuery):
    await callback.answer()
    await show_home(callback)


# ============================================================
# PLAN
# ============================================================

@router.callback_query(F.data.startswith("plan:"))
async def plan_callback(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]

    if plan_key not in PLANS:
        await callback.answer(
            "❌ Invalid plan.",
            show_alert=True,
        )
        return

    await callback.answer()

    plan = PLANS[plan_key]

    text = (
        f"<b>{plan['name']}</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Price: <b>₹{plan['price']}</b>\n"
        f"⏳ Duration: <b>{plan.get('duration_days', 30)} Days</b>\n\n"
        "Select the plan below:"
    )

    await callback.message.edit_text(
        text,
        reply_markup=plan_keyboard(plan_key),
    )


# ============================================================
# QR
# ============================================================

def generate_qr_file(url: str, filename: str):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )

    qr.add_data(url)
    qr.make(fit=True)

    img = qr.make_image(
        fill_color="black",
        back_color="white",
    )

    buf = io.BytesIO()
    img.save(buf, format="PNG")

    return BufferedInputFile(
        buf.getvalue(),
        filename=filename,
    )


# ============================================================
# BUY
# ============================================================

@router.callback_query(F.data.startswith("buy:"))
async def buy_callback(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]

    if plan_key not in PLANS:
        await callback.answer(
            "❌ Invalid plan.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Generating dynamic QR code..."
    )

    try:
        result = await create_payment_link(
            callback.from_user.id,
            plan_key,
        )
    except Exception:
        logger.exception(
            "Payment link creation failed"
        )
        await callback.answer(
            "❌ Payment QR create nahi ho paya.",
            show_alert=True,
        )
        return

    plan = PLANS[plan_key]
    payment_url = result["short_url"]

    qr_file = generate_qr_file(
        payment_url,
        "payment_qr.png",
    )

    text = (
        "💳 <b>Scan & Pay</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Plan: <b>{plan['name']}</b>\n"
        f"💰 Amount: <b>₹{plan['price']}</b>\n"
        f"⏳ Duration: <b>{plan.get('duration_days', 30)} Days</b>\n\n"
        "📱 <b>GPay / PhonePe / Paytm / UPI app "
        "se scan karein.</b>\n\n"
        "⏱️ Payment hone ke baad "
        "<b>Check Payment</b> dabayein."
    )

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.message.answer_photo(
        photo=qr_file,
        caption=text,
        reply_markup=payment_keyboard(plan_key),
    )


# ============================================================
# RENEW
# ============================================================

@router.callback_query(F.data.startswith("renew:"))
async def renew_callback(callback: CallbackQuery):
    plan_key = callback.data.split(":", 1)[1]

    if plan_key not in PLANS:
        await callback.answer(
            "❌ Invalid plan.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Generating renewal payment..."
    )

    try:
        result = await create_payment_link(
            callback.from_user.id,
            plan_key,
        )
    except Exception:
        logger.exception(
            "Renewal payment link creation failed"
        )
        await callback.answer(
            "❌ Payment link create nahi ho paya.",
            show_alert=True,
        )
        return

    plan = PLANS[plan_key]

    qr_file = generate_qr_file(
        result["short_url"],
        "renewal_qr.png",
    )

    text = (
        "🔄 <b>Renew Subscription</b>\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"📦 Plan: <b>{plan['name']}</b>\n"
        f"💰 Renewal Price: <b>₹{plan['price']}</b>\n"
        f"⏳ Duration: <b>{plan.get('duration_days', 30)} Days</b>\n\n"
        "📱 GPay / PhonePe / Paytm / UPI app se scan karein.\n\n"
        "Payment ke baad <b>Check Payment</b> dabayein."
    )

    try:
        await callback.message.delete()
    except Exception:
        pass

    await callback.message.answer_photo(
        photo=qr_file,
        caption=text,
        reply_markup=payment_keyboard(plan_key),
    )


# ============================================================
# PAYMENT ACTIVATION
# ============================================================

async def activate_paid_order(
    order: dict,
    payment_id: str,
):
    reference_id = order["reference_id"]

    # Idempotency: don't add another 30 days if already paid.
    current = get_order(reference_id)

    if not current:
        return None

    if current["status"] == "paid":
        return current

    mark_paid(
        reference_id,
        payment_id,
    )

    updated = get_order(reference_id)

    if not updated:
        return None

    plan_key = updated["plan_key"]
    user_id = int(updated["user_id"])

    existing_expiry = get_active_expiry(
        user_id,
        plan_key,
    )

    now = int(time.time())

    duration_days = int(
        PLANS[plan_key].get(
            "duration_days",
            30,
        )
    )

    duration_seconds = (
        duration_days * 24 * 60 * 60
    )

    if existing_expiry > now:
        new_expiry = (
            existing_expiry +
            duration_seconds
        )
    else:
        new_expiry = (
            now +
            duration_seconds
        )

    set_subscription_expiry(
        reference_id,
        new_expiry,
    )

    return get_order(reference_id)


# ============================================================
# ACCESS
# ============================================================

async def make_access_link(
    plan_key: str,
) -> Optional[str]:
    plan = PLANS[plan_key]

    if plan["channel_id"]:
        try:
            invite = await bot.create_chat_invite_link(
                chat_id=plan["channel_id"],
                member_limit=1,
            )
            return invite.invite_link
        except Exception:
            logger.exception(
                "Invite link creation failed for %s",
                plan_key,
            )

    return plan["access_link"] or None


async def deliver_access(order: dict):
    if order.get("access_sent"):
        return True

    access_link = await make_access_link(
        order["plan_key"]
    )

    if not access_link:
        await bot.send_message(
            order["user_id"],
            (
                "⚠️ <b>Payment confirmed.</b>\n\n"
                "Lekin access link configure nahi hai.\n"
                f"📞 Support: {SUPPORT_USERNAME}"
            ),
        )
        return False

    plan = PLANS[order["plan_key"]]

    expires_at = order.get(
        "access_expires_at"
    )

    await bot.send_message(
        order["user_id"],
        (
            "🎉 <b>Payment Confirmed!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 Plan: <b>{plan['name']}</b>\n"
            f"💰 Paid: <b>₹{order['amount_paise'] / 100:.2f}</b>\n"
            f"⏳ Duration: <b>{plan.get('duration_days', 30)} Days</b>\n"
            f"📅 Valid Until: <b>{format_expiry(expires_at)}</b>\n"
            f"🧾 Payment ID: "
            f"<code>{order.get('payment_id') or '-'}</code>\n\n"
            "🔗 <b>Your Access Link:</b>\n"
            f"{access_link}\n\n"
            "⚠️ Access subscription expiry ke baad "
            "automatically remove ho jayega."
        ),
        reply_markup=access_keyboard(
            order["plan_key"]
        ),
    )

    mark_access_sent(
        order["reference_id"]
    )

    return True


# ============================================================
# PAYMENT LINK VALIDATION
# ============================================================

async def fetch_and_validate_payment_link(
    payment_link_id: str,
    expected_reference_id: str,
    expected_amount_paise: int,
):
    result = await razorpay_request(
        "GET",
        f"payment_links/{payment_link_id}",
    )

    if result.get("id") != payment_link_id:
        raise RuntimeError(
            "Payment link ID mismatch."
        )

    if (
        result.get("reference_id")
        != expected_reference_id
    ):
        raise RuntimeError(
            "Payment reference mismatch."
        )

    if int(result.get("amount") or 0) != int(
        expected_amount_paise
    ):
        raise RuntimeError(
            "Payment amount mismatch."
        )

    if result.get("status") != "paid":
        return result, ""

    payments = result.get("payments") or []

    payment_id = ""

    if payments:
        payment_id = (
            payments[0].get("payment_id")
            or payments[0].get("id")
            or ""
        )

    return result, payment_id


# ============================================================
# PAYMENT CHECK
# ============================================================

@router.callback_query(F.data.startswith("check:"))
async def check_payment_callback(
    callback: CallbackQuery,
):
    plan_key = callback.data.split(":", 1)[1]

    if plan_key not in PLANS:
        await callback.answer(
            "❌ Invalid plan.",
            show_alert=True,
        )
        return

    await callback.answer(
        "Checking payment..."
    )

    # IMPORTANT:
    # Don't blindly use latest order from another plan.
    order = get_latest_order(
        callback.from_user.id
    )

    if not order:
        await callback.answer(
            "❌ You have no plans!",
            show_alert=True,
        )
        return

    if order["plan_key"] != plan_key:
        # Find latest matching plan.
        try:
            res = (
                db()
                .table("orders")
                .select("*")
                .eq(
                    "user_id",
                    callback.from_user.id,
                )
                .eq(
                    "plan_key",
                    plan_key,
                )
                .order(
                    "created_at",
                    desc=True,
                )
                .limit(1)
                .execute()
            )
            order = (
                res.data[0]
                if res.data
                else None
            )
        except Exception:
            logger.exception(
                "Failed finding plan order"
            )
            order = None

    if not order:
        await callback.answer(
            "❌ Payment order not found.",
            show_alert=True,
        )
        return

    if order["status"] == "paid":
        await callback.answer(
            "✅ Payment already confirmed!",
            show_alert=True,
        )
        return

    try:
        result, payment_id = (
            await fetch_and_validate_payment_link(
                order["payment_link_id"],
                order["reference_id"],
                order["amount_paise"],
            )
        )

        if result.get("status") == "paid":
            updated = await activate_paid_order(
                order,
                payment_id,
            )

            if updated:
                if not updated.get("access_sent"):
                    await deliver_access(
                        updated
                    )

                await callback.answer(
                    "✅ Payment verified!",
                    show_alert=True,
                )

                try:
                    await send_my_plan(
                        callback
                    )
                except Exception:
                    logger.exception(
                        "Failed refreshing My Plan"
                    )
            else:
                await callback.answer(
                    "❌ Order activation failed.",
                    show_alert=True,
                )
        else:
            status_text = (
                result.get("status")
                or "unknown"
            )

            await callback.answer(
                f"⏳ Status: {status_text}. "
                "Payment pending.",
                show_alert=True,
            )

    except Exception:
        logger.exception(
            "Payment verification failed"
        )
        await callback.answer(
            "❌ Payment status check nahi ho paya.",
            show_alert=True,
        )


# ============================================================
# MY PLAN
# ============================================================

def build_my_plan_view(order: dict):
    plan = PLANS.get(
        order["plan_key"],
        {},
    )

    status = order.get("status")

    if status == "paid":
        expires_at = order.get(
            "access_expires_at"
        )

        text = (
            "📋 <b>My Plan</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 Plan: <b>{plan.get('name', order['plan_key'])}</b>\n"
            f"💰 Amount: "
            f"<b>₹{order['amount_paise'] / 100:.2f}</b>\n"
            "📌 Status: <b>PAID</b>\n"
            f"⏳ Valid Until: "
            f"<b>{format_expiry(expires_at)}</b>\n"
            f"🧾 Payment ID: "
            f"<code>{order.get('payment_id') or '-'}</code>"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔗 Send Access Link",
                        callback_data=(
                            f"access:{order['reference_id']}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Renew",
                        callback_data=(
                            f"renew:{order['plan_key']}"
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

    else:
        text = (
            "📋 <b>My Plan</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"📦 Plan: <b>{plan.get('name', order['plan_key'])}</b>\n"
            f"💰 Amount: "
            f"<b>₹{order['amount_paise'] / 100:.2f}</b>\n"
            f"📌 Status: <b>{status.upper()}</b>\n\n"
            "Payment complete nahi hua hai."
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Check Payment",
                        callback_data=(
                            f"check:{order['plan_key']}"
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

    return text, kb


async def send_my_plan(
    callback: CallbackQuery,
):
    order = get_latest_order(
        callback.from_user.id
    )

    if not order:
        text = (
            "📋 <b>My Plan</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ <b>You have no plans!</b>"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="↩️ Back",
                        callback_data="home",
                    )
                ]
            ]
        )

        await callback.message.edit_text(
            text,
            reply_markup=kb,
        )
        return

    text, kb = build_my_plan_view(order)

    await callback.message.edit_text(
        text,
        reply_markup=kb,
    )


@router.callback_query(F.data == "myplan")
async def myplan_callback(
    callback: CallbackQuery,
):
    await callback.answer()
    await send_my_plan(callback)


@router.message(Command("myplan"))
async def myplan_message(message: Message):
    order = get_latest_order(
        message.from_user.id
    )

    if not order:
        text = (
            "📋 <b>My Plan</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "❌ <b>You have no plans!</b>"
        )

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="↩️ Back",
                        callback_data="home",
                    )
                ]
            ]
        )

        await message.answer(
            text,
            reply_markup=kb,
        )
        return

    text, kb = build_my_plan_view(order)

    await message.answer(
        text,
        reply_markup=kb,
    )


# ============================================================
# ACCESS CALLBACK
# ============================================================

@router.callback_query(F.data.startswith("access:"))
async def access_callback(
    callback: CallbackQuery,
):
    reference_id = callback.data.split(
        ":", 1
    )[1]

    await callback.answer(
        "Checking..."
    )

    order = get_order(reference_id)

    if not order:
        await callback.answer(
            "❌ Order not found.",
            show_alert=True,
        )
        return

    if int(order["user_id"]) != int(
        callback.from_user.id
    ):
        await callback.answer(
            "❌ Order not found.",
            show_alert=True,
        )
        return

    if order["status"] != "paid":
        await callback.answer(
            "❌ Payment abhi confirmed nahi hai.",
            show_alert=True,
        )
        return

    expires_at = int(
        order.get("access_expires_at") or 0
    )

    if expires_at <= int(time.time()):
        await callback.answer(
            "❌ Subscription expired.",
            show_alert=True,
        )
        return

    try:
        # For an already delivered order this sends a fresh
        # one-user invite link. This is useful if the old link
        # was lost.
        access_link = await make_access_link(
            order["plan_key"]
        )

        if not access_link:
            raise RuntimeError(
                "No access link configured."
            )

        await bot.send_message(
            callback.from_user.id,
            (
                "🔗 <b>Your Access Link</b>\n\n"
                f"{access_link}\n\n"
                f"⏳ Valid Until: "
                f"<b>{format_expiry(expires_at)}</b>"
            ),
        )

        await callback.answer(
            "✅ Access link sent!",
            show_alert=True,
        )

    except Exception:
        logger.exception(
            "Access link send failed"
        )
        await callback.answer(
            "❌ Access send nahi ho paya.",
            show_alert=True,
        )


# ============================================================
# RAZORPAY WEBHOOK
# ============================================================

def extract_payment_link_event(
    event: dict,
):
    payload = event.get("payload") or {}

    payment_link_entity = (
        payload.get("payment_link", {})
        .get("entity", {})
    )

    payment_entity = (
        payload.get("payment", {})
        .get("entity", {})
    )

    # Some Razorpay payment-link event payloads can expose
    # order/payment-link information differently. Use the
    # available entity and then resolve the DB order.
    if not payment_link_entity:
        payment_link_entity = (
            payload.get("order", {})
            .get("entity", {})
        )

    reference_id = (
        payment_link_entity.get("reference_id")
        or payment_entity.get("reference_id")
        or payment_entity.get("notes", {}).get(
            "reference_id"
        )
    )

    payment_link_id = (
        payment_link_entity.get("id")
        or payment_link_entity.get(
            "payment_link_id"
        )
    )

    payment_id = (
        payment_entity.get("id")
        or payment_entity.get("payment_id")
        or ""
    )

    return (
        reference_id,
        payment_link_id,
        payment_id,
    )


async def process_paid_event(
    event: dict,
):
    (
        reference_id,
        payment_link_id,
        payment_id,
    ) = extract_payment_link_event(event)

    if not reference_id and payment_link_id:
        order = get_order_by_payment_link(
            payment_link_id
        )
    else:
        order = (
            get_order(reference_id)
            if reference_id
            else None
        )

    if not order:
        logger.warning(
            "Webhook order not found. "
            "reference_id=%s payment_link_id=%s",
            reference_id,
            payment_link_id,
        )
        return

    # Never trust webhook payload amount/reference alone.
    # Fetch the payment link from Razorpay and validate it.
    actual_payment_link_id = (
        order["payment_link_id"]
    )

    result, fetched_payment_id = (
        await fetch_and_validate_payment_link(
            actual_payment_link_id,
            order["reference_id"],
            order["amount_paise"],
        )
    )

    if result.get("status") != "paid":
        logger.warning(
            "Webhook received but payment link is not paid: %s",
            result.get("status"),
        )
        return

    payment_id = (
        fetched_payment_id
        or payment_id
        or ""
    )

    updated = await activate_paid_order(
        order,
        payment_id,
    )

    if not updated:
        return

    if not updated.get("access_sent"):
        try:
            await deliver_access(updated)
        except Exception:
            logger.exception(
                "Access delivery failed"
            )


async def razorpay_webhook(
    request: web.Request,
):
    raw_body = await request.read()

    signature = request.headers.get(
        "X-Razorpay-Signature",
        "",
    )

    if not verify_webhook(
        raw_body,
        signature,
    ):
        return web.Response(
            status=400,
            text="invalid signature",
        )

    event_id = request.headers.get(
        "x-razorpay-event-id",
        "",
    )

    if event_id and event_already_processed(
        event_id
    ):
        return web.Response(
            status=200,
            text="already processed",
        )

    try:
        event = json.loads(
            raw_body.decode("utf-8")
        )
    except (json.JSONDecodeError, UnicodeDecodeError):
        return web.Response(
            status=400,
            text="invalid json",
        )

    if event.get("event") != "payment_link.paid":
        # Ignore unrelated Razorpay webhook events.
        if event_id:
            save_event(event_id)

        return web.Response(
            status=200,
            text="ignored",
        )

    try:
        await process_paid_event(event)

        # Save only after successful processing so a temporary
        # DB/Razorpay/Telegram failure can be retried by Razorpay.
        if event_id:
            save_event(event_id)

        return web.Response(
            status=200,
            text="ok",
        )

    except Exception:
        logger.exception(
            "Razorpay webhook processing failed"
        )

        # Return 500 so Razorpay can retry the webhook.
        return web.Response(
            status=500,
            text="processing failed",
        )


# ============================================================
# SUBSCRIPTION EXPIRY
# ============================================================

async def remove_expired_user(
    order: dict,
):
    user_id = int(order["user_id"])
    plan_key = order["plan_key"]

    plan = PLANS.get(plan_key)

    if not plan:
        logger.error(
            "Unknown plan during expiry: %s",
            plan_key,
        )
        return

    channel_id = plan.get("channel_id")

    if not channel_id:
        logger.warning(
            "No channel_id configured for expired plan %s. "
            "Cannot auto-remove user.",
            plan_key,
        )

        # Still mark the order expired so the worker does not
        # retry forever. Configure channel_id for real auto revoke.
        mark_subscription_revoked(
            order["reference_id"]
        )
        return

    active_expiry = get_active_expiry(
        user_id,
        plan_key,
    )

    now = int(time.time())

    # User has another active paid order for this same channel.
    if active_expiry > now:
        logger.info(
            "User %s renewed %s until %s; skipping removal.",
            user_id,
            plan_key,
            format_expiry(active_expiry),
        )

        # This old order is finished, but the user's access remains
        # active because another paid order covers it.
        mark_subscription_revoked(
            order["reference_id"]
        )
        return

    try:
        await bot.ban_chat_member(
            chat_id=channel_id,
            user_id=user_id,
        )

        logger.info(
            "Expired user %s removed from %s",
            user_id,
            plan_key,
        )

        # Unban immediately so a future paid invite can work.
        try:
            await bot.unban_chat_member(
                chat_id=channel_id,
                user_id=user_id,
                only_if_banned=True,
            )
        except Exception:
            logger.exception(
                "Failed to unban expired user %s",
                user_id,
            )

    except Exception:
        logger.exception(
            "Failed to remove user %s from channel %s",
            user_id,
            channel_id,
        )

    mark_subscription_revoked(
        order["reference_id"]
    )

    try:
        await bot.send_message(
            user_id,
            (
                "⚠️ <b>Premium Subscription Expired</b>\n"
                "━━━━━━━━━━━━━━━━━━\n\n"
                f"📦 Plan: <b>{plan['name']}</b>\n"
                "❌ Status: <b>EXPIRED</b>\n\n"
                "Aapka premium access 30 days complete hone ke "
                "baad expire ho gaya hai aur channel se "
                "access remove kar diya gaya hai.\n\n"
                "Premium continue karne ke liye neeche "
                "<b>Renew Subscription</b> par click karein."
            ),
            reply_markup=access_keyboard(
                plan_key
            ),
        )
    except Exception:
        logger.exception(
            "Failed to send expiry message to %s",
            user_id,
        )


async def subscription_expiry_worker():
    logger.info(
        "Subscription expiry worker started"
    )

    while True:
        try:
            expired_orders = get_expired_orders()

            if expired_orders:
                logger.info(
                    "Found %s expired subscriptions",
                    len(expired_orders),
                )

            for order in expired_orders:
                try:
                    await remove_expired_user(
                        order
                    )
                except Exception:
                    logger.exception(
                        "Expiry processing failed for %s",
                        order.get("reference_id"),
                    )

                await asyncio.sleep(0.2)

        except asyncio.CancelledError:
            logger.info(
                "Subscription expiry worker stopped"
            )
            raise

        except Exception:
            logger.exception(
                "Subscription expiry worker error"
            )

        await asyncio.sleep(60)


# ============================================================
# ADMIN BROADCAST
# ============================================================

@router.callback_query(F.data == "admin_broadcast")
async def broadcast_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer(
            "❌ You are not authorized!",
            show_alert=True,
        )
        return

    await callback.answer()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Back",
                    callback_data="home",
                )
            ]
        ]
    )

    await callback.message.edit_text(
        (
            "📢 <b>Broadcast Setup</b>\n\n"
            "Apna message bhejein.\n"
            "Aap <b>Image + Caption</b> ya "
            "<b>Text</b> bhej sakte hain."
        ),
        reply_markup=kb,
    )

    await state.set_state(
        BroadcastState.waiting_for_message
    )


@router.message(
    BroadcastState.waiting_for_message
)
async def broadcast_send(
    message: Message,
    state: FSMContext,
):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return

    await state.clear()

    users = get_all_users()

    status_msg = await message.answer(
        f"🚀 Broadcast started to {len(users)} users..."
    )

    success = 0
    failed = 0

    for user_id in users:
        try:
            if message.photo:
                await bot.send_photo(
                    chat_id=user_id,
                    photo=message.photo[-1].file_id,
                    caption=message.caption or "",
                )
            elif message.text:
                await bot.send_message(
                    chat_id=user_id,
                    text=message.text,
                )
            else:
                failed += 1
                continue

            success += 1

            # Telegram rate-limit friendly delay.
            await asyncio.sleep(0.05)

        except Exception:
            failed += 1

    await status_msg.edit_text(
        (
            "✅ <b>Broadcast Completed!</b>\n\n"
            f"📤 Successful: <b>{success}</b>\n"
            f"❌ Failed: <b>{failed}</b>"
        )
    )


# ============================================================
# ADMIN STATS
# ============================================================

@router.message(Command("stats"))
async def admin_stats(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    try:
        users_res = (
            db()
            .table("bot_users")
            .select("user_id", count="exact")
            .execute()
        )

        users_count = (
            int(users_res.count)
            if users_res.count is not None
            else len(users_res.data or [])
        )

        all_orders = (
            db()
            .table("orders")
            .select(
                "plan_key, amount_paise, status"
            )
            .execute()
            .data
            or []
        )

        total_orders = len(all_orders)

        revenue_by_plan = {
            "gold": 0.0,
            "silver": 0.0,
            "bronze": 0.0,
            "iron": 0.0,
        }

        total_revenue = 0.0

        for order in all_orders:
            if order.get("status") != "paid":
                continue

            amount = (
                int(order.get("amount_paise") or 0)
                / 100
            )

            plan = order.get("plan_key")

            if plan in revenue_by_plan:
                revenue_by_plan[plan] += amount

            total_revenue += amount

        stats_text = (
            "📊 <b>Admin Dashboard - Statistics</b>\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            f"👥 Total Users: <b>{users_count}</b>\n"
            f"📦 Total Orders Created: "
            f"<b>{total_orders}</b>\n\n"
            f"💰 Total Revenue: "
            f"<b>₹{total_revenue:.2f}</b>\n\n"
            "<u>Revenue by Plan:</u>\n"
            f"⚡ Gold: ₹{revenue_by_plan['gold']:.2f}\n"
            f"⚡ Silver: ₹{revenue_by_plan['silver']:.2f}\n"
            f"⚡ Bronze: ₹{revenue_by_plan['bronze']:.2f}\n"
            f"⚡ Iron: ₹{revenue_by_plan['iron']:.2f}"
        )

        await message.answer(
            stats_text
        )

    except Exception:
        logger.exception(
            "Error fetching stats"
        )
        await message.answer(
            "❌ Stats fetch karne me error aaya."
        )


# ============================================================
# HEALTH / WEB SERVER
# ============================================================

async def health(request: web.Request):
    return web.json_response(
        {
            "ok": True,
            "service": "telegram-store-bot",
            "time": int(time.time()),
        }
    )


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

    app.router.add_post(
        "/razorpay/webhook",
        razorpay_webhook,
    )

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        WEBHOOK_HOST,
        WEBHOOK_PORT,
    )

    await site.start()

    logger.info(
        "HTTP server listening on %s:%s",
        WEBHOOK_HOST,
        WEBHOOK_PORT,
    )

    return runner


# ============================================================
# MAIN
# ============================================================

async def main():
    global bot

    validate_config()
    init_supabase()

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    dp = Dispatcher()
    dp.include_router(router)

    runner = await start_web_server()

    expiry_task = asyncio.create_task(
        subscription_expiry_worker()
    )

    try:
        logger.info(
            "Bot polling started"
        )

        await dp.start_polling(
            bot
        )

    finally:
        logger.info(
            "Shutting down..."
        )

        expiry_task.cancel()

        try:
            await expiry_task
        except asyncio.CancelledError:
            pass

        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
