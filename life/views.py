from rest_framework import status, viewsets
from rest_framework.views import APIView
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from rest_framework.viewsets import ReadOnlyModelViewSet
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.tokens import RefreshToken

from life.services.ai.reports import generate_report, ALL_REPORT_TYPES
from life.services.ai.client import AIClientError, AITimeoutError, AIRateLimitError

from datetime import date


from django.contrib.auth import get_user_model

from .models import (
    Category,
    Domain,
    Metric,
    MetricEntry,
    UserGoal
)

from .serializers import (
    CategorySerializer,
    DomainListSerializer,
    DomainDetailSerializer,
    MetricSerializer,
    MetricEntrySerializer,
    UserRegisterSerializer,
    LoginSerializer,
    UserGoalSerializer
)
from life.services.domains import registry
from life.services.templates import get_template_by_slug

User = get_user_model()

class RegisterAPIView(GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = UserRegisterSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.save()
        return Response(data, status=status.HTTP_201_CREATED)


class LoginAPIView(GenericAPIView):
    permission_classes = [AllowAny]
    serializer_class = LoginSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(serializer.validated_data, status=status.HTTP_200_OK)

class LogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, *args, **kwargs):
        refresh_token = request.data.get("refresh_token")
        if not refresh_token:
            return Response({"detail": "Refresh token required"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except Exception:
            return Response({"detail": "Invalid token or already blacklisted"}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": "Logout successful"}, status=status.HTTP_200_OK)

class CategoryModelViewset(ReadOnlyModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]


class DomainModelViewSet(viewsets.ModelViewSet):
    serializer_class = DomainListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Domain.objects.filter(user=self.request.user)
    
    def get_serializer_class(self):
        if self.action in ["retrieve"]:
            return DomainDetailSerializer
        return DomainListSerializer
    
    def retrieve(self, request, *args, **kwargs):
        instance = self.get_object()
        handler_key = getattr(instance, "slug", None)
        period = request.query_params.get("period", "weekly")
        end_date_raw = request.query_params.get("end_date", None)
        end_date = date.fromisoformat(end_date_raw) if end_date_raw else None
        service = registry.get_service(handler_key)
        analytics = service(instance, request.user, end_date=end_date, period=period)
        serializer = self.get_serializer(instance, context={"analytics": analytics})
        return Response(serializer.data, status=status.HTTP_200_OK)
    
    def perform_create(self, serializer):
        domain = serializer.save(user=self.request.user)
        tpl = get_template_by_slug(getattr(domain, "slug", None))

        if tpl:
            for m in tpl:
                name = m.get("name")
                unit = m.get("unit")
                aggregation = m.get("aggregation") or "sum"

                Metric.objects.get_or_create(
                    domain=domain,
                    name=name,
                    defaults={
                        "user": domain.user,
                        "unit": unit,
                        "aggregation_type": aggregation
                    }
                )

class MetricModelViewSet(viewsets.ModelViewSet):
    serializer_class = MetricSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Metric.objects.filter(user=self.request.user)
    
    def perform_create(self, serializer):
        domain = serializer.validated_data.get("domain")
        if domain.user != self.request.user:
            raise PermissionError("You can't add a metric to this domain")
        serializer.save(user=self.request.user)

class DomainTemplateAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, slug, *args, **kwargs):
        tpl = get_template_by_slug(slug)
        if tpl is None:
            return Response({"detail": "Template not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response({
                "slug": slug,
                "metrics": tpl
            },
            status=status.HTTP_200_OK)


class MetricEntryViewSet(viewsets.ModelViewSet):
    serializer_class = MetricEntrySerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = MetricEntry.objects.filter(metric__user=self.request.user)
        metric_id = self.request.query_params.get("metric")
        if metric_id:
            qs = qs.filter(metric_id=metric_id)
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        metric = serializer.validated_data.get("metric")
        if metric.user != self.request.user:
            raise PermissionError("You can't add entries to this metric")
        serializer.save()

class UserGoalModelViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = UserGoalSerializer

    def get_queryset(self):
        return UserGoal.objects.filter(user=self.request.user)
    
    def perform_create(self, serializer):
        data = serializer.validated_data
        UserGoal.objects.update_or_create(
            user=self.request.user,
            metric=data["metric"],
            period=data["period"],
            defaults={
                "target_value":    data["target_value"],
                "comparison_type": data["comparison_type"],
                "is_active":       True,
            }
        )

class AIAssistantView(APIView):
    permission_classes = [IsAuthenticated]
 
    def post(self, request):
        report_type = request.data.get("type")
        payload     = request.data.get("payload", {})
 
        # ── validate type ─────────────────────────────────────────────────────
        if not report_type:
            return Response(
                {"error": f"'type' is required. Valid types: {ALL_REPORT_TYPES}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
 
        # ── generate ──────────────────────────────────────────────────────────
        try:
            result = generate_report(request.user, report_type, payload)
            return Response({"result": result})
 
        except ValueError as e:
            return Response(
                {"error": str(e)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except AIRateLimitError as e:
            return Response(
                {"error": "AI service is rate limited. Please try again in a moment."},
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        except AITimeoutError as e:
            return Response(
                {"error": "AI request timed out. Please try again."},
                status=status.HTTP_504_GATEWAY_TIMEOUT,
            )
        except AIClientError as e:
            return Response(
                {"error": f"AI service error: {str(e)}"},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as e:
            return Response(
                {"error": "Unexpected error. Please try again."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )