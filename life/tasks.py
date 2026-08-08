import logging
from celery import shared_task
from django.contrib.auth import get_user_model

from life.services.ai.reports import generate_weekly_auto_report, generate_metric_alert
from life.services.ai.context import get_weekly_comparison_context

from life.models import Domain, Metric
from life.services.ai.context import get_weekly_context

from django.conf import settings
from asgiref.sync import async_to_sync
from telegram import Bot

logger = logging.getLogger(__name__)
User = get_user_model()


# ── anomaly detection config ──────────────────────────────────────────────────

# If a metric changes by more than this % week-over-week → trigger alert
ALERT_THRESHOLD_PCT = 30.0

# Metrics where "going up" is bad (stress, awakenings, sleep latency etc.)
INVERTED_ALERT_METRICS = {
    "Stress Level", "Awakenings", "Sleep Latency",
}


# ── telegram sender (stub — replace with your bot logic) ─────────────────────

def _send_telegram(user, message: str) -> None:
    try:
        profile = user.profile
        chat_id = profile.telegram_chat_id
        if not chat_id:
            logger.warning(f"[Telegram] user={user.id} has no telegram_chat_id")
            return
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        async_to_sync(bot.send_message)(
            chat_id=chat_id,
            text=message,
            parse_mode="Markdown"
        )
        logger.info(f"[Telegram] sent to user={user.id}")
    except Exception as e:
        logger.error(f"[Telegram] failed for user={user.id}: {e}")


# ── task 1: weekly report ─────────────────────────────────────────────────────

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def send_weekly_report(self, user_id: int | None = None):
    """
    Generate and send a weekly AI report via Telegram.

    If user_id is provided — run for that user only.
    If not — run for all active users.

    Schedule: every Monday at 9am (see CELERY_BEAT_SCHEDULE).
    """
    if user_id:
        users = User.objects.filter(id=user_id, is_active=True)
    else:
        users = User.objects.filter(is_active=True)

    for user in users:
        try:
            logger.info(f"[Celery] Generating weekly report for user {user.id}")
            report_text = generate_weekly_auto_report(user)
            _send_telegram(user, report_text)

        except Exception as exc:
            logger.error(f"[Celery] Weekly report failed for user {user.id}: {exc}")
            try:
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                logger.error(f"[Celery] Max retries exceeded for user {user.id}")


# ── task 2: anomaly detection ─────────────────────────────────────────────────

@shared_task(bind=True, max_retries=1, default_retry_delay=30)
def check_anomalies(self, user_id: int | None = None):
    """
    Compare this week vs last week for each metric.
    If any metric changed by more than ALERT_THRESHOLD_PCT → generate AI alert
    and send via Telegram.

    Schedule: every day at 8am (see CELERY_BEAT_SCHEDULE).
    """
    if user_id:
        users = User.objects.filter(id=user_id, is_active=True)
    else:
        users = User.objects.filter(is_active=True)

    for user in users:
        try:
            _check_user_anomalies(user)
        except Exception as exc:
            logger.error(f"[Celery] Anomaly check failed for user {user.id}: {exc}")


def _check_user_anomalies(user) -> None:
    """Check all metrics for a single user and send alerts if needed."""

    current_ctx,  _ = get_weekly_context(user, offset=0)
    previous_ctx, _ = get_weekly_context(user, offset=-1)

    if not current_ctx or not previous_ctx:
        return

    for domain_name, current_data in current_ctx.items():
        previous_data = previous_ctx.get(domain_name, {})
        if not previous_data:
            continue

        current_per_metric  = current_data.get("report", {}).get("per_metric", {})
        previous_per_metric = previous_data.get("report", {}).get("per_metric", {})

        for metric_name, current_metric in current_per_metric.items():
            previous_metric = previous_per_metric.get(metric_name, {})
            if not previous_metric:
                continue

            # use monthly_avg as the comparable value
            current_val  = current_metric.get("monthly_avg")
            previous_val = previous_metric.get("monthly_avg")

            if current_val is None or previous_val is None or previous_val == 0:
                continue

            change_pct = ((current_val - previous_val) / abs(previous_val)) * 100

            # for inverted metrics (stress etc.) going UP is bad
            is_inverted = metric_name in INVERTED_ALERT_METRICS
            is_bad_change = (
                (not is_inverted and change_pct < -ALERT_THRESHOLD_PCT) or
                (is_inverted     and change_pct > ALERT_THRESHOLD_PCT)
            )

            if abs(change_pct) >= ALERT_THRESHOLD_PCT and is_bad_change:
                logger.info(
                    f"[Celery] Anomaly detected: {domain_name}/{metric_name} "
                    f"changed {change_pct:+.1f}%"
                )
                try:
                    # get unit from DB
                    metric_obj = Metric.objects.filter(
                        user=user, name=metric_name, domain__name=domain_name
                    ).first()
                    unit = metric_obj.unit if metric_obj else ""

                    alert_text = generate_metric_alert(
                        user           = user,
                        domain_name    = domain_name,
                        metric_name    = metric_name,
                        current_value  = current_val,
                        previous_value = previous_val,
                        unit           = unit,
                    )
                    _send_telegram(user, f"⚠️ *Alert*\n{alert_text}")

                except Exception as e:
                    logger.error(
                        f"[Celery] Failed to generate alert for "
                        f"{domain_name}/{metric_name}: {e}"
                    )


# ── task 3: manual trigger (for testing) ─────────────────────────────────────

@shared_task
def send_weekly_report_to_user(user_id: int):
    """
    Manually trigger a weekly report for a specific user.
    Useful for testing from Django shell:

        from life.tasks import send_weekly_report_to_user
        send_weekly_report_to_user.delay(user_id=1)
    """
    send_weekly_report.apply(kwargs={"user_id": user_id})