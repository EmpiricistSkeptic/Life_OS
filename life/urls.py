from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CategoryModelViewset,
    DomainModelViewSet,
    MetricModelViewSet,
    RegisterAPIView,
    LoginAPIView,
    LogoutAPIView,
    DomainTemplateAPIView,
    MetricEntryViewSet,
    UserGoalModelViewSet,
    AIAssistantView
)
router = DefaultRouter()

router.register(r"category", CategoryModelViewset, basename="category")
router.register(r"domain", DomainModelViewSet, basename="domain")
router.register(r"metric", MetricModelViewSet, basename="metric")
router.register(r"entry", MetricEntryViewSet, basename="entry")
router.register(r"goal", UserGoalModelViewSet, basename="goal")

urlpatterns = [
    path("", include(router.urls)),
    path("register/", RegisterAPIView.as_view(), name="register"),
    path("login/", LoginAPIView.as_view(), name="login"),
    path("logout/", LogoutAPIView.as_view(), name="logout"),
    path("domain-templates/<slug:slug>/", DomainTemplateAPIView.as_view(), name="domain-template"),
    path("ai/report/", AIAssistantView.as_view(), name="ai-report"),
]