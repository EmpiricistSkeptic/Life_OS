from rest_framework import serializers
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import get_user_model, authenticate
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import Category, Domain, Metric, MetricEntry, UserGoal, Report

User = get_user_model()

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email"]

class MetricEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = MetricEntry
        fields = ["id", "metric", "value", "created_at"]
        read_only_fields = ["created_at"]

class MetricSerializer(serializers.ModelSerializer):
    latest_value = serializers.SerializerMethodField()

    class Meta:
        model = Metric
        fields = ["id", "name", "domain", "unit", "aggregation_type", "latest_value"]
    
    def get_latest_value(self, obj):
        if obj.aggregation_type == "sum":
            today = timezone.now().date()
            result = obj.entries.filter(
                created_at__date=today
            ).aggregate(total=Sum("value"))["total"]
            return result or 0.0
        else:
            last = obj.entries.order_by("-created_at").first()
            return last.value if last else None

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description"]

class DomainListSerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()
    metrics = MetricSerializer(many=True, read_only=True)
    category_name = serializers.SerializerMethodField()

    class Meta:
        model = Domain
        fields = ["id", "name", "description", "category", "category_name", "slug", "summary", "metrics", "created_at"]
        read_only_fields = ["created_at"]

    def get_summary(self, obj):
        return {
            "metrics_count": obj.metrics.count()
        }
    
    def get_category_name(self, obj):
        return obj.category.name if obj.category else None

class DomainDetailSerializer(DomainListSerializer):
    analytics = serializers.SerializerMethodField()

    class Meta(DomainListSerializer.Meta):
        fields = DomainListSerializer.Meta.fields + ["created_at", "analytics"]
    
    def get_analytics(self, obj):
        return self.context.get("analytics")


class UserRegisterSerializer(serializers.ModelSerializer):
    username = serializers.CharField(max_length=100)
    password = serializers.CharField(write_only=True)
    email = serializers.EmailField()

    class Meta:
        model = User
        fields = ["username", "email", "password"]
    
    def validate_email(self, value):
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("This email already exists")
        return value.lower()
    
    @transaction.atomic
    def create(self, validated_data):
        user = User.objects.create_user(
            username=validated_data["username"],
            password=validated_data["password"],
            email=validated_data.get("email", "").lower(),
            is_active=True
        )
        refresh = RefreshToken.for_user(user)
        user_data = UserSerializer(user).data

        return {
            "user": user_data,
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "message": "User registered successfully.",
        }

class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        user = authenticate(username=data.get("username"), password=data.get("password"))
        if not user:
            raise serializers.ValidationError("Username or password are invalid")
        if not user.is_active:
            raise serializers.ValidationError("Account has not been activated")
        
        refresh = RefreshToken.for_user(user)

        return {
            "user_id": user.pk,
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }

class UserGoalSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserGoal
        fields = ["id", "user", "metric", "target_value", "period", "comparison_type", "is_active", "created_at", "updated_at"]
        read_only_fields = ["id", "user", "created_at", "updated_at"]

class Report(serializers.ModelSerializer):
    class Meta:
        model = Report
        fields = ["id", "user", "domain", "period_start", "period_end", "period_type", "life_score", "domain_score", "data", "created_at"]
        read_only_fields = ["id", "created_at"]