#!/bin/sh

date
echo "migrate"
python manage.py migrate --noinput

date 
echo "Registering Telegram webhook..." 
python manage.py set_telegram_webhook


date
echo "starting gunicorn"

exec gunicorn \
  myproject.asgi:application \
  -k uvicorn.workers.UvicornWorker \
  --bind 0.0.0.0:${PORT:-8000} \
  --timeout 60