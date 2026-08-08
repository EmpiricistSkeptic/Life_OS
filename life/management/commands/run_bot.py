from django.core.management.base import BaseCommand
from life.telegram_bot import main

class Command(BaseCommand):
    help = "Run Telegram bot"

    def handle(self, *args, **options):
        main()