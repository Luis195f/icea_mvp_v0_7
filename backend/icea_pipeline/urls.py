from __future__ import annotations

from django.urls import path

from icea_pipeline.views import (
    CausalRunView,
    CausalReportView,
    CausalDiscoverView,
    CausalSimulateView,
    FederatedRoundStartView,
    FederatedSubmitUpdateView,
    FederatedAggregateView,
    DashboardSummaryView,
    PipelineBuildDatasetView,
    PipelineBuildWindowsView,
    PipelineIngestView,
    PipelineNormalizeView,
    PipelineTrainFromDBView,
    RiskAssessmentWritebackView,
    RosterSummaryView,
    RosterUploadView,
    WritebackListView,
    AuditEventsListView,
    GovernanceDecisionView,
    FHIROpisodeQualityView,
    ConformalPredictView,
    EntityChangeLogListView,
)

urlpatterns = [
    path("pipeline/ingest/", PipelineIngestView.as_view()),
    path("pipeline/normalize/", PipelineNormalizeView.as_view()),
    path("pipeline/build-dataset/", PipelineBuildDatasetView.as_view()),
    path("pipeline/build-windows/", PipelineBuildWindowsView.as_view()),
    path("pipeline/train/", PipelineTrainFromDBView.as_view()),
    path("dashboard/summary/", DashboardSummaryView.as_view()),

    path("governance/audit/events/", AuditEventsListView.as_view()),
    path("governance/decision/", GovernanceDecisionView.as_view()),
    path("governance/entity-changes/", EntityChangeLogListView.as_view()),

    path("roster/upload-csv/", RosterUploadView.as_view()),
    path("roster/summary/", RosterSummaryView.as_view()),

    path("causal/run/", CausalRunView.as_view()),
    path("causal/report/", CausalReportView.as_view()),
    path("causal/discover/", CausalDiscoverView.as_view()),
    path("causal/simulate/", CausalSimulateView.as_view()),

    path("federated/round/start/", FederatedRoundStartView.as_view()),
    path("federated/round/<uuid:round_id>/submit/", FederatedSubmitUpdateView.as_view()),
    path("federated/round/<uuid:round_id>/aggregate/", FederatedAggregateView.as_view()),

    path("fhir/writeback/riskassessment/", RiskAssessmentWritebackView.as_view()),
    path("fhir/writeback/list/", WritebackListView.as_view()),
    path("fhir/quality/episode/", FHIROpisodeQualityView.as_view()),

    path("predict/conformal/", ConformalPredictView.as_view()),
]
