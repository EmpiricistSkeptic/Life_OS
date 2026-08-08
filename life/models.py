from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class Category(models.Model):
    CATEGORIES_NAMES = (
        ("mind", "Mind"),
        ("body", "Body"),
        ("spirit", "Spirit"),
    )
    name = models.CharField(max_length=10, choices=CATEGORIES_NAMES)
    description = models.TextField(blank=True)

    def __str__(self):
        return self.name

class Domain(models.Model):
    SLUG_CHOICES = (
        ("default", "Default"),
        ("language", "Language"),
        ("habit", "Habit"),
        ("nutrition", "Nutrition"),
        ("programming", "Programming"),
        ("sleep", "Sleep"),
        ("stress", "Stress"),
        ("training", "Training"),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="domains")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="domains")
    slug = models.CharField(max_length=20, choices=SLUG_CHOICES, default="default")
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]


    def __str__(self):
        return self.name

class Metric(models.Model):
    AGGREGATION_CHOICES = (
        ("sum",  "Sum"),
        ("avg",  "Avg"),
        ("max",  "Max"),
        ("min",  "Min"),
        ("last", "Last"),
    )
    UNIT_CHOICES = (
        ("hours",   "Hours"),
        ("minutes", "Minutes"),
        ("count",   "Count"),
        ("points",  "Points"),
        ("kg",      "Kilograms"),
        ("kcal",    "Kilocalories"),
        ("g",       "Grams"),
        ("ml",      "Milliliters"),
        ("km",      "Kilometers"),
        ("custom",  "Custom"),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="metrics")
    domain = models.ForeignKey(Domain, on_delete=models.CASCADE, related_name="metrics")
    name = models.CharField(max_length=255)
    unit = models.CharField(max_length=20, choices=UNIT_CHOICES)
    aggregation_type = models.CharField(max_length=10, choices=AGGREGATION_CHOICES)

    def __str__(self):
        return f"{self.name} ({self.domain.name})"

class MetricEntry(models.Model):
    metric = models.ForeignKey(Metric, on_delete=models.CASCADE, related_name="entries")
    value = models.FloatField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.metric.name} = {self.value} at {self.created_at}"
    

class UserGoal(models.Model):
    PERIOD_CHOICES = (
        ("daily", "Daily"),
        ("weekly", "Weekly"),
        ("monthly", "Monthly"),
    )
    COMPARISON_CHICES = (
        ("at_least", "At_least"),
        ("at_most", "At_most"),
        ("exact", "Exact"),
    )
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="goals")
    metric = models.ForeignKey(Metric, on_delete=models.CASCADE, related_name="goals")
    target_value = models.FloatField()
    period = models.CharField(max_length=10, choices=PERIOD_CHOICES, default="weekly")
    comparison_type = models.CharField(max_length=10, choices=COMPARISON_CHICES, default="at_least")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "metric", "period")
        indexes = [
            models.Index(fields=["user", "metric"]),
        ]
    
    def __str__(self):
        return f"{self.user} -> {self.metric} ({self.period})"

class Report(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="reports")
    domain = models.ForeignKey(Domain, on_delete=models.SET_NULL, null=True, blank=True, related_name="reports")
    period_start = models.DateField()
    period_end = models.DateField()
    period_type = models.CharField(max_length=20, choices=[("weekly", "Weekly"), ("monthly", "Monthly"),])
    life_score = models.FloatField(null=True, blank=True)
    domain_score = models.FloatField(null=True, blank=True)
    data = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-period_end"]
        indexes = [
            models.Index(fields=["user", "period_start", "period_end"]),
        ]

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    telegram_chat_id = models.BigIntegerField(null=True, blank=True, unique=True)

    def __str__(self):
        return f"Profile({self.user.username})"