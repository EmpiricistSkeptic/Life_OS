from django.contrib import admin

from .models import (
    Category,
    Domain,
    Metric,
    MetricEntry,
    UserGoal,
    UserProfile,
)

admin.site.register(Category)
admin.site.register(Domain)
admin.site.register(Metric)
admin.site.register(MetricEntry)
admin.site.register(UserGoal)
admin.site.register(UserProfile)