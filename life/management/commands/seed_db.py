import random
from datetime import timedelta
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.utils import timezone

from life.models import Category, Domain, Metric, MetricEntry, UserGoal

User = get_user_model()


SEED_DATA = [
    {
        "slug": "training",
        "name": "Fitness Training",
        "description": "Workouts, runs, gym sessions",
        "category": "body",
        "metrics": [
            {"name": "Workout Sessions",    "unit": "count", "agg": "sum", "goal": {"target": 5,    "period": "weekly", "comparison": "at_least"}},
            {"name": "Training Hours",      "unit": "hours", "agg": "sum", "goal": {"target": 5.0,  "period": "weekly", "comparison": "at_least"}},
            {"name": "Cardio Distance (km)","unit": "count", "agg": "sum", "goal": {"target": 20.0, "period": "weekly", "comparison": "at_least"}},
        ],
    },
    {
        "slug": "sleep",
        "name": "Sleep",
        "description": "Sleep tracking and analysis",
        "category": "spirit",
        "metrics": [
            {"name": "Sleep Duration", "unit": "hours",   "agg": "avg", "goal": {"target": 8.0, "period": "daily", "comparison": "at_least"}},
            {"name": "Sleep Quality",  "unit": "score",   "agg": "avg", "goal": {"target": 4.0, "period": "daily", "comparison": "at_least"}},
            {"name": "Bedtime",        "unit": "minutes", "agg": "avg"},
            {"name": "Wake Time",      "unit": "minutes", "agg": "avg"},
            {"name": "Awakenings",     "unit": "count",   "agg": "avg", "goal": {"target": 1.0, "period": "daily", "comparison": "at_most"}},
            {"name": "Sleep Latency",  "unit": "minutes", "agg": "avg"},
            {"name": "Nap Duration",   "unit": "hours",   "agg": "sum"},
        ],
    },
    {
        "slug": "nutrition",
        "name": "Nutrition",
        "description": "Calories, macros and hydration tracking",
        "category": "body",
        "metrics": [
            {"name": "Calories", "unit": "kcal", "agg": "avg", "goal": {"target": 2000, "period": "daily", "comparison": "at_most"}},
            {"name": "Protein",  "unit": "g",    "agg": "avg", "goal": {"target": 150,  "period": "daily", "comparison": "at_least"}},
            {"name": "Fat",      "unit": "g",    "agg": "avg", "goal": {"target": 65,   "period": "daily", "comparison": "at_most"}},
            {"name": "Carbs",    "unit": "g",    "agg": "avg", "goal": {"target": 220,  "period": "daily", "comparison": "at_most"}},
            {"name": "Water",    "unit": "ml",   "agg": "avg", "goal": {"target": 2500, "period": "daily", "comparison": "at_least"}},
        ],
    },
    {
        "slug": "programming",
        "name": "Programming",
        "description": "Coding hours and tasks completed",
        "category": "mind",
        "metrics": [
            {"name": "Coding Hours",     "unit": "hours", "agg": "sum", "goal": {"target": 20.0, "period": "weekly", "comparison": "at_least"}},
            {"name": "Tasks Completed",  "unit": "count", "agg": "sum", "goal": {"target": 25,   "period": "weekly", "comparison": "at_least"}},
            {"name": "Bugs Fixed",       "unit": "count", "agg": "sum", "goal": {"target": 10,   "period": "weekly", "comparison": "at_least"}},
        ],
    },
    {
        "slug": "habit",
        "name": "Daily Habits",
        "description": "Meditation, journaling, reading",
        "category": "spirit",
        "metrics": [
            {"name": "Meditation",   "unit": "count", "agg": "sum", "goal": {"target": 6, "period": "weekly", "comparison": "at_least"}},
            {"name": "No Sugar",     "unit": "count", "agg": "sum", "goal": {"target": 7, "period": "weekly", "comparison": "at_least"}},
            {"name": "Reading",      "unit": "count", "agg": "sum", "goal": {"target": 5, "period": "weekly", "comparison": "at_least"}},
            {"name": "Morning Walk", "unit": "count", "agg": "sum", "goal": {"target": 5, "period": "weekly", "comparison": "at_least"}},
            {"name": "Journaling",   "unit": "count", "agg": "sum", "goal": {"target": 5, "period": "weekly", "comparison": "at_least"}},
        ],
    },
    {
        "slug": "language",
        "name": "French",
        "description": "Vocabulary and speaking practice",
        "category": "mind",
        "metrics": [
            {"name": "Listening Minutes",  "unit": "minutes", "agg": "sum", "goal": {"target": 200, "period": "weekly", "comparison": "at_least"}},
            {"name": "Speaking Minutes",   "unit": "minutes", "agg": "sum", "goal": {"target": 100, "period": "weekly", "comparison": "at_least"}},
            {"name": "Reading Minutes",    "unit": "minutes", "agg": "sum", "goal": {"target": 150, "period": "weekly", "comparison": "at_least"}},
            {"name": "Writing Minutes",    "unit": "minutes", "agg": "sum", "goal": {"target": 60,  "period": "weekly", "comparison": "at_least"}},
            {"name": "Vocabulary New",     "unit": "count",   "agg": "sum", "goal": {"target": 100, "period": "weekly", "comparison": "at_least"}},
            {"name": "Vocabulary Review",  "unit": "count",   "agg": "sum", "goal": {"target": 200, "period": "weekly", "comparison": "at_least"}},
            {"name": "Grammar Exercises",  "unit": "count",   "agg": "sum", "goal": {"target": 30,  "period": "weekly", "comparison": "at_least"}},
        ],
    },
    {
        "slug": "language",
        "name": "Spanish",
        "description": "Vocabulary and speaking practice",
        "category": "mind",
        "metrics": [
            {"name": "Listening Minutes",  "unit": "minutes", "agg": "sum", "goal": {"target": 200, "period": "weekly", "comparison": "at_least"}},
            {"name": "Speaking Minutes",   "unit": "minutes", "agg": "sum", "goal": {"target": 100, "period": "weekly", "comparison": "at_least"}},
            {"name": "Reading Minutes",    "unit": "minutes", "agg": "sum", "goal": {"target": 150, "period": "weekly", "comparison": "at_least"}},
            {"name": "Writing Minutes",    "unit": "minutes", "agg": "sum", "goal": {"target": 60,  "period": "weekly", "comparison": "at_least"}},
            {"name": "Vocabulary New",     "unit": "count",   "agg": "sum", "goal": {"target": 100, "period": "weekly", "comparison": "at_least"}},
            {"name": "Vocabulary Review",  "unit": "count",   "agg": "sum", "goal": {"target": 200, "period": "weekly", "comparison": "at_least"}},
            {"name": "Grammar Exercises",  "unit": "count",   "agg": "sum", "goal": {"target": 30,  "period": "weekly", "comparison": "at_least"}},
        ],
    },
    {
        "slug": "language",
        "name": "English",
        "description": "Vocabulary and speaking practice",
        "category": "mind",
        "metrics": [
            {"name": "Listening Minutes",  "unit": "minutes", "agg": "sum", "goal": {"target": 200, "period": "weekly", "comparison": "at_least"}},
            {"name": "Speaking Minutes",   "unit": "minutes", "agg": "sum", "goal": {"target": 100, "period": "weekly", "comparison": "at_least"}},
            {"name": "Reading Minutes",    "unit": "minutes", "agg": "sum", "goal": {"target": 150, "period": "weekly", "comparison": "at_least"}},
            {"name": "Writing Minutes",    "unit": "minutes", "agg": "sum", "goal": {"target": 60,  "period": "weekly", "comparison": "at_least"}},
            {"name": "Vocabulary New",     "unit": "count",   "agg": "sum", "goal": {"target": 100, "period": "weekly", "comparison": "at_least"}},
            {"name": "Vocabulary Review",  "unit": "count",   "agg": "sum", "goal": {"target": 200, "period": "weekly", "comparison": "at_least"}},
            {"name": "Grammar Exercises",  "unit": "count",   "agg": "sum", "goal": {"target": 30,  "period": "weekly", "comparison": "at_least"}},
        ],
    },
    {
        "slug": "stress",
        "name": "Stress Management",
        "description": "Stress levels and recovery",
        "category": "spirit",
        "metrics": [
            {"name": "Stress Level",        "unit": "score",   "agg": "avg", "goal": {"target": 3.0, "period": "daily",  "comparison": "at_most"}},
            {"name": "Meditation Minutes",  "unit": "minutes", "agg": "sum", "goal": {"target": 70,  "period": "weekly", "comparison": "at_least"}},
            {"name": "Exercise Minutes",    "unit": "minutes", "agg": "sum", "goal": {"target": 120, "period": "weekly", "comparison": "at_least"}},
            {"name": "Relaxation Minutes",  "unit": "minutes", "agg": "sum", "goal": {"target": 150, "period": "weekly", "comparison": "at_least"}},
        ],
    },
]

CATEGORIES = {
    "mind":   "Intellectual growth and learning",
    "body":   "Physical health and nutrition",
    "spirit": "Mental wellbeing and daily habits",
}


class Command(BaseCommand):
    help = "Seed the database with domains, metrics and goals — NO random entries"

    def add_arguments(self, parser):
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Delete ALL existing data for user id=1 before seeding",
        )

    def handle(self, *args, **options):
        clear = options["clear"]

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== SEED DATABASE (clean) ==="))

        user = User.objects.get(id=1)
        self.stdout.write(f"[~] User: {user.username}")

        if clear:
            e, _ = MetricEntry.objects.filter(metric__user=user).delete()
            g, _ = UserGoal.objects.filter(user=user).delete()
            m, _ = Metric.objects.filter(user=user).delete()
            d, _ = Domain.objects.filter(user=user).delete()
            self.stdout.write(self.style.WARNING(
                f"[!] Cleared: {e} entries, {g} goals, {m} metrics, {d} domains"
            ))

        # categories
        categories = {}
        for name, desc in CATEGORIES.items():
            cat, created = Category.objects.get_or_create(name=name, defaults={"description": desc})
            categories[name] = cat
            status = "created" if created else "exists"
            self.stdout.write(f"[+] Category '{name}' — {status}")

        # domains + metrics + goals
        for cfg in SEED_DATA:
            domain, created = Domain.objects.get_or_create(
                user=user,
                name=cfg["name"],
                defaults={
                    "slug":        cfg["slug"],
                    "description": cfg["description"],
                    "category":    categories[cfg["category"]],
                },
            )
            status = "created" if created else "exists"
            self.stdout.write(f"\n  Domain: {self.style.SUCCESS(domain.name)} [{cfg['category'].upper()}] — {status}")

            for metric_cfg in cfg["metrics"]:
                metric, m_created = Metric.objects.get_or_create(
                    user=user,
                    domain=domain,
                    name=metric_cfg["name"],
                    defaults={
                        "unit":             metric_cfg["unit"],
                        "aggregation_type": metric_cfg["agg"],
                    },
                )

                goal_cfg = metric_cfg.get("goal")
                if goal_cfg:
                    _, g_created = UserGoal.objects.get_or_create(
                        user=user,
                        metric=metric,
                        period=goal_cfg["period"],
                        defaults={
                            "target_value":   goal_cfg["target"],
                            "comparison_type": goal_cfg["comparison"],
                            "is_active":       True,
                        },
                    )
                    goal_str = f"goal: {goal_cfg['comparison']} {goal_cfg['target']} / {goal_cfg['period']}"
                else:
                    goal_str = "no goal"

                m_status = "created" if m_created else "exists"
                self.stdout.write(f"    Metric: {metric.name:30s} [{metric_cfg['unit']:8s}] — {m_status} | {goal_str}")

        self.stdout.write(self.style.MIGRATE_HEADING("\n=== DONE ==="))
        self.stdout.write(f"  Domains: {Domain.objects.filter(user=user).count()}")
        self.stdout.write(f"  Metrics: {Metric.objects.filter(user=user).count()}")
        self.stdout.write(f"  Goals:   {UserGoal.objects.filter(user=user).count()}")
        self.stdout.write(f"  Entries: {MetricEntry.objects.filter(metric__user=user).count()} (should be 0)")
        self.stdout.write(self.style.SUCCESS("\nReady — start logging your own data!"))