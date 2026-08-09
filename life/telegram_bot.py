"""
life/telegram_bot.py

Telegram bot for Life OS.

Commands:
/start   — link Telegram account to Life OS user
/report  — get weekly AI report
/week    — same as /report
/month   — get monthly AI report
/help    — list commands

The bot uses Telegram Webhook instead of long polling.
"""

import logging
import os

from asgiref.sync import sync_to_async
from django.contrib.auth import get_user_model
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from life.models import UserProfile
from life.services.ai.reports import (
    generate_weekly_auto_report,
    generate_monthly_auto_report,
)


# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

User = get_user_model()


# -----------------------------------------------------------------------------
# Database helpers
# -----------------------------------------------------------------------------

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
    profile.save(update_fields=["telegram_chat_id"])


@sync_to_async
def get_user_by_chat_id(chat_id: int):
    try:
        return UserProfile.objects.get(
            telegram_chat_id=chat_id
        ).user
    except UserProfile.DoesNotExist:
        return None


@sync_to_async
def run_weekly_report(user):
    return generate_weekly_auto_report(user)


@sync_to_async
def run_monthly_report(user):
    return generate_monthly_auto_report(user)


# -----------------------------------------------------------------------------
# Keyboard
# -----------------------------------------------------------------------------

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        ["📊 Weekly Report"],
        ["📅 Monthly Report"],
        ["ℹ️ Help"],
    ],
    resize_keyboard=True,
)


# -----------------------------------------------------------------------------
# Commands
# -----------------------------------------------------------------------------

async def cmd_start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
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
        await update.message.reply_text(
            f"❌ User '{username}' not found in Life OS."
        )
        return

    await link_telegram(user, chat_id)

    await update.message.reply_text(
        f"✅ Account linked!\n"
        f"User: *{user.username}*\n\n"
        f"Use /report to get your weekly AI report.",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def cmd_report(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    user = await get_user_by_chat_id(chat_id)

    if not user:
        await update.message.reply_text(
            "❌ Account not linked. Send /start <username>"
        )
        return

    await update.message.reply_text(
        "⏳ Generating your report, please wait..."
    )

    try:
        report = await run_weekly_report(user)

        await update.message.reply_text(
            report,
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.exception("Weekly report error: %s", e)

        await update.message.reply_text(
            "❌ Failed to generate report. Please try again."
        )


async def cmd_month(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    chat_id = update.effective_chat.id

    user = await get_user_by_chat_id(chat_id)

    if not user:
        await update.message.reply_text(
            "❌ Account not linked. Send /start <username>"
        )
        return

    await update.message.reply_text(
        "⏳ Generating your report, please wait..."
    )

    try:
        report = await run_monthly_report(user)

        await update.message.reply_text(
            report,
            parse_mode="Markdown",
        )

    except Exception as e:
        logger.exception("Monthly report error: %s", e)

        await update.message.reply_text(
            "❌ Failed to generate report. Please try again."
        )


async def cmd_help(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    await update.message.reply_text(
        "*Life OS Bot Commands*\n\n"
        "/start — link your account\n"
        "/report — weekly AI report\n"
        "/week — same as /report\n"
        "/month — monthly AI report\n"
        "/help — show this message",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


# -----------------------------------------------------------------------------
# Keyboard handlers
# -----------------------------------------------------------------------------

async def handle_buttons(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    text = update.message.text

    if text == "📊 Weekly Report":
        await cmd_report(update, context)

    elif text == "📅 Monthly Report":
        await cmd_month(update, context)

    elif text == "ℹ️ Help":
        await cmd_help(update, context)


# -----------------------------------------------------------------------------
# Telegram application
# -----------------------------------------------------------------------------

def build_application() -> Application:
    """
    Create and configure the Telegram application.

    The application is NOT started here.

    Django/ASGI will receive Telegram webhook requests and pass
    Telegram Update objects to this application.
    """

    token = os.getenv("TELEGRAM_BOT_TOKEN")

    if not token:
        raise ValueError(
            "TELEGRAM_BOT_TOKEN not set in environment"
        )

    application = (
        Application.builder()
        .token(token)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", cmd_start)
    )

    application.add_handler(
        CommandHandler("report", cmd_report)
    )

    application.add_handler(
        CommandHandler("week", cmd_report)
    )

    application.add_handler(
        CommandHandler("month", cmd_month)
    )

    application.add_handler(
        CommandHandler("help", cmd_help)
    )

    # Keyboard buttons / ordinary text messages
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_buttons,
        )
    )

    logger.info("Telegram application configured.")

    return application
