# Render deployment

## 1. Upload to GitHub
Put `main.py`, `requirements.txt` and `render.yaml` in a GitHub repository.

## 2. Create the Render service
Create a Render Web Service from the repository. The included `render.yaml` uses:

- Build: `pip install -r requirements.txt`
- Start: `python main.py`
- Health: `/health`

Render provides the `PORT` variable automatically; the app binds to it.

## 3. Add environment variables
Set these in Render:

- `BOT_TOKEN`
- `RAZORPAY_KEY_ID`
- `RAZORPAY_KEY_SECRET`
- `RAZORPAY_WEBHOOK_SECRET`
- `SUPPORT_USERNAME`
- `GOLD_CHANNEL_ID` / `GOLD_ACCESS_LINK`
- `SILVER_CHANNEL_ID` / `SILVER_ACCESS_LINK`
- `BRONZE_CHANNEL_ID` / `BRONZE_ACCESS_LINK`
- `IRON_CHANNEL_ID` / `IRON_ACCESS_LINK`

For production, use Razorpay LIVE keys. Do not put secrets in GitHub.

## 4. Database
This version uses SQLite. `DB_PATH=/var/data/store.db` is intended for a Render persistent disk. Without a persistent disk, local SQLite data is ephemeral.

For a serious production deployment, migrate the order tables to PostgreSQL.

## 5. Razorpay webhook
After deployment, use:

`https://YOUR-RENDER-DOMAIN/razorpay/webhook`

Configure the same webhook secret in Razorpay and Render's `RAZORPAY_WEBHOOK_SECRET`.

Subscribe to `payment_link.paid`.

## 6. Telegram channel permissions
The bot must be an administrator in each private channel if you want the bot to create one-time invite links dynamically. If no channel ID is supplied, the corresponding `*_ACCESS_LINK` can be used as a fallback.

## 7. Important
Do not use the Telegram/Razorpay credentials that were pasted into chat. Rotate/revoke them and create fresh secrets before deploying.
