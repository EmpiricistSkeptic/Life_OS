import os, logging

logger = logging.getLogger(__name__)

API_KEY = os.getenv("API_KEY")
API_ENDPOINT = os.getenv("API_ENDPOINT")

if not API_ENDPOINT:
    logging.critical("Переменная окружения API_ENDPOINT не установлена!")

if not API_KEY:
    logging.critical("Переменная окружения AI_API_KEY не установлена!")



