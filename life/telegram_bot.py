"""
life/telegram_bot.py

Telegram bot for Life OS.
Commands:
  /start   — link Telegram account to Life OS user
  /report  — get weekly AI report
  /week    — same as /report
  /status  — show current domain scores
  /help    — list commands
"""

import logging
import os
 
from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
 
from life.models import UserProfile
from life.services.ai.reports import generate_weekly_auto_report, generate_monthly_auto_report
 
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
User = get_user_model()
 
 
# ── helpers (wrapped in sync_to_async) ───────────────────────────────────────
 
@sync_to_async
def get_user_by_username(username: str):
    try:
        return User.objects.get(username=username)
    except User.DoesNotExist:
        return None
 
 
@sync_to_async
def link_telegram(user, chat_id: int):
    profile, _ = UserProfile.objects.get_or_create(user=user)
    profile.telegram_chat_id = chat_id
    profile.save()
 
 
@sync_to_async
def get_user_by_chat_id(chat_id: int):
    try:
        return UserProfile.objects.get(telegram_chat_id=chat_id).user
    except UserProfile.DoesNotExist:
        return None
 
 
@sync_to_async
def run_weekly_report(user):
    return generate_weekly_auto_report(user)

@sync_to_async
def run_monthly_report(user):
    return generate_monthly_auto_report(user)
 
 
# ── handlers ──────────────────────────────────────────────────────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 Weekly Report"],
        ["📅 Monthly Report"],
        ["ℹ️ Help"],
    ],
    resize_keyboard=True
)
 
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    chat_id = update.effective_chat.id
 
    if not args:
        await update.message.reply_text(
            "👋 Welcome to Life OS bot!\n\n"
            "To link your account send:\n"
            "/start <your_username>\n\n"
            "Example: /start john"
        )
        return
 
    username = args[0].strip()
    user = await get_user_by_username(username)
 
    if not user:
        await update.message.reply_text(f"❌ User '{username}' not found in Life OS.")
        return
 
    await link_telegram(user, chat_id)
    await update.message.reply_text(
        f"✅ Account linked!\n"
        f"User: *{user.username}*\n\n"
        f"Use /report to get your weekly AI report.",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "📊 Weekly Report":
        await cmd_report(update, context)

    elif text == "📅 Monthly Report":
        await cmd_month(update, context)
    
    elif text == "ℹ️ Help":
        await cmd_help(update, context)
 
 
async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = await get_user_by_chat_id(chat_id)
 
    if not user:
        await update.message.reply_text("❌ Account not linked. Send /start <username>")
        return
 
    await update.message.reply_text("⏳ Generating your report, please wait...")
 
    try:
        report = await run_weekly_report(user)
        await update.message.reply_text(report, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Report error: {e}")
        await update.message.reply_text("❌ Failed to generate report. Please try again.")


async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = await get_user_by_chat_id(chat_id)
    if not user:
        await update.message.reply_text("❌ Account not linked. Send /start <username>")
        return
    
    await update.message.reply_text("⏳ Generating your report, please wait...")

    try:
        report = await run_monthly_report(user)
        await update.message.reply_text(report, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Report error: {e}")
        await update.message.reply_text("❌ Failed to generate report. Please try again.")

 
 
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "*Life OS Bot Commands*\n\n"
        "/start <username> — link your account\n"
        "/report — weekly AI report\n"
        "/week — same as /report\n"
        "/help — show this message",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD
    )
 
 
# ── main ──────────────────────────────────────────────────────────────────────
 
def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise ValueError("TELEGRAM_BOT_TOKEN not set in environment")
 
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("week",   cmd_report))
    app.add_handler(CommandHandler("month", cmd_month))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_buttons))
 
    logger.info("Bot started...")
    app.run_polling(drop_pending_updates=True)
 
 
if __name__ == "__main__":
    main()




