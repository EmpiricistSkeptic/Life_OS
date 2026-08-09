"""
life/telegram_runtime.py

Runtime bridge between Django ASGI and python-telegram-bot.
"""

import logging

from telegram import Update

from life.telegram_bot import build_application


logger = logging.getLogger(__name__)


# Create Telegram application once.
telegram_application = build_application()


async def initialize_telegram():
    """
    Initialize the Telegram application once.
    """

    if not telegram_application._initialized:
        await telegram_application.initialize()

        logger.info("Telegram application initialized.")


async def process_update_async(update: Update):
    """
    Process an already parsed Telegram Update.
    """

    await initialize_telegram()

    await telegram_application.process_update(update)

