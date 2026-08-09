"""
life/views_telegram.py

Telegram webhook endpoint for Life OS.
"""

import json
import logging

from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from telegram import Update

from life.telegram_runtime import (
    telegram_application,
    process_update_async,
)


logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
async def telegram_webhook(request, secret_path: str):
    """
    Receive Telegram updates through webhook.
    """

    # -------------------------------------------------------------------------
    # Security check
    # -------------------------------------------------------------------------

    if secret_path != settings.TELEGRAM_WEBHOOK_SECRET:
        return JsonResponse(
            {"detail": "Forbidden"},
            status=403,
        )

    header_secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if header_secret != settings.TELEGRAM_WEBHOOK_SECRET:
        logger.warning(
            "Telegram webhook request with invalid secret token."
        )

        return JsonResponse(
            {"detail": "Forbidden"},
            status=403,
        )

    # -------------------------------------------------------------------------
    # Parse Telegram update
    # -------------------------------------------------------------------------

    try:
        update_data = json.loads(request.body)

        update = Update.de_json(
            update_data,
            telegram_application.bot,
        )

    except (json.JSONDecodeError, TypeError, ValueError):
        logger.exception(
            "Invalid Telegram webhook payload."
        )

        return JsonResponse(
            {"detail": "Invalid payload"},
            status=400,
        )

    # -------------------------------------------------------------------------
    # Process update
    # -------------------------------------------------------------------------

    try:
        await process_update_async(update)

    except Exception:
        logger.exception(
            "Error while processing Telegram update."
        )

        # Return 200 so Telegram does not repeatedly retry
        # the update because of an internal application error.

    return JsonResponse({"ok": True})



