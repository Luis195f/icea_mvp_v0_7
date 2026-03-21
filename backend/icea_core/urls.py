from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from .icea_plus_views import (
    ICEAPlusAggregateView,
    ICEAPlusCalibrateView,
    ICEAPlusExplainView,
    ICEAPlusFollowupIngestView,
    ICEAPlusFollowupRescoreView,
    ICEAPlusFollowupStatusView,
    ICEAPlusScoreView,
    ICEAPlusWritebackPatientView,
    ICEAPlusWritebackSummaryView,
)
from .views import HealthView, ICEAComputeView, ModelListView, ModelTrainView

urlpatterns = [
    path("health/", HealthView.as_view(), name="health"),
    # Auth endpoints (do not break existing clients; optional usage)
    path("auth/token/", TokenObtainPairView.as_view(), name="token-obtain-pair"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("models/", ModelListView.as_view(), name="models-list"),
    path("models/train/", ModelTrainView.as_view(), name="models-train"),
    path("icea/compute/", ICEAComputeView.as_view(), name="icea-compute"),
    path("icea-plus/score/", ICEAPlusScoreView.as_view(), name="icea-plus-score"),
    path("icea-plus/explain/", ICEAPlusExplainView.as_view(), name="icea-plus-explain"),
    path("icea-plus/aggregate/", ICEAPlusAggregateView.as_view(), name="icea-plus-aggregate"),
    path("icea-plus/calibrate/", ICEAPlusCalibrateView.as_view(), name="icea-plus-calibrate"),
    path("icea-plus/followup/ingest/", ICEAPlusFollowupIngestView.as_view(), name="icea-plus-followup-ingest"),
    path("icea-plus/followup/rescore/", ICEAPlusFollowupRescoreView.as_view(), name="icea-plus-followup-rescore"),
    path("icea-plus/followup/status/", ICEAPlusFollowupStatusView.as_view(), name="icea-plus-followup-status"),
    path("icea-plus/writeback/summary/", ICEAPlusWritebackSummaryView.as_view(), name="icea-plus-writeback-summary"),
    path("icea-plus/writeback/patient/", ICEAPlusWritebackPatientView.as_view(), name="icea-plus-writeback-patient"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
