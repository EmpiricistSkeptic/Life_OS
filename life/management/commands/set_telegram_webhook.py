import asyncio
import os

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from telegram import Bot


class Command(BaseCommand):
    help = "Register Telegram webhook"

    def handle(self, *args, **options):
        token = os.getenv("TELEGRAM_BOT_TOKEN")

        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN is not set")

        async def set_webhook():
            url = os.getenv("RENDER_EXTERNAL_URL")

            if not url:
                raise CommandError(
                    "RENDER_EXTERNAL_URL is not set"
                )

            url = url.rstrip("/")

            secret = settings.TELEGRAM_WEBHOOK_SECRET

            if not secret:
                raise CommandError(
                    "TELEGRAM_WEBHOOK_SECRET is not set"
                )

            webhook_url = (
                f"{url}/api/telegram/webhook/{secret}/"
            )

            async with Bot(token=token) as bot:
                await bot.set_webhook(
                    url=webhook_url,
                    secret_token=secret,
                    allowed_updates=["message"],
                )

                webhook_info = await bot.get_webhook_info()

                self.stdout.write(
                    self.style.SUCCESS(
                        f"Telegram webhook set: {webhook_info.url}"
                    )
                )

        asyncio.run(set_webhook())