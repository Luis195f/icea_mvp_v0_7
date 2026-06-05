from __future__ import annotations

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterable

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction
from django.urls import NoReverseMatch, reverse
from rest_framework.test import APIClient

from icea_core.audit_identity import safe_stored_audit_actor
from icea_core.evidence import INTENDED_USE_SHADOW_AGGREGATE, REQUIRED_MODEL_LIMITATIONS, summarize_model_evidence
from icea_core.icea_plus_views import ICEAPlusScoreView, ICEAPlusWritebackPatientView, ICEAPlusWritebackSummaryView
from icea_core.models import ModelArtifact
from icea_core.permissions import (
    ICEAAggregateViewerPermission,
    ICEAResearcherPermission,
    ICEATrainingPermission,
)
from icea_core.views import ICEAComputeView, ModelListView, ModelTrainView
from icea_pipeline.models import AuditEvent, EpisodeFeatureRow
from icea_pipeline.views import RiskAssessmentWritebackView, WritebackListView


PASS = "pass"
WARN = "warn"
FAIL = "fail"

_PSEUDONYMOUS_ACTOR_RE = re.compile(r"^(?:authenticated_user|anonymous_[a-z_]+|service_[a-z_]+|legacy_actor):[0-9a-f]{64}$")
_SETTING_LOOKUP_ERRORS: dict[str, str] = {}


def safe_get_setting(name: str, default: Any = None) -> Any:
    """Read a Django setting without letting readiness output become non-JSON."""

    try:
        return getattr(settings, name, default)
    except ImproperlyConfigured:
        _SETTING_LOOKUP_ERRORS[name] = "improperly_configured"
        return default


@dataclass(frozen=True)
class OperationalCheck:
    code: str
    status: str
    detail: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "status": self.status, "detail": self.detail}


def _setting_bool(name: str, default: bool = False) -> bool:
    return bool(safe_get_setting(name, default))


def _check(code: str, ok: bool, detail: str, *, warn: bool = False) -> OperationalCheck:
    if ok:
        return OperationalCheck(code=code, status=PASS, detail=detail)
    return OperationalCheck(code=code, status=WARN if warn else FAIL, detail=detail)


def _http_detail(path: str, expected: str, response: Any, *, extra: str = "") -> str:
    status_code = getattr(response, "status_code", "unavailable")
    suffix = f" {extra}" if extra else ""
    return f"Expected {expected} for {path}; got status_code={status_code}.{suffix}"


def canonical_api_path(name: str, fallback: str) -> str:
    try:
        path = reverse(name)
    except NoReverseMatch:
        path = fallback
    if not path.endswith("/"):
        path = f"{path}/"
    return path


def _summarize(checks: list[OperationalCheck]) -> dict[str, Any]:
    failures = [check.to_dict() for check in checks if check.status == FAIL]
    warnings = [check.to_dict() for check in checks if check.status == WARN]
    status = FAIL if failures else WARN if warnings else PASS
    return {
        "status": status,
        "checks": [check.to_dict() for check in checks],
        "warnings": warnings,
        "failures": failures,
    }


def _has_url_name(name: str) -> bool:
    try:
        reverse(name)
    except NoReverseMatch:
        return False
    return True


def _has_dedicated_jwt_source() -> bool:
    return bool(
        (os.environ.get("JWT_SIGNING_KEY") or "").strip()
        or (os.environ.get("JWT_VERIFYING_KEY") or "").strip()
        or (os.environ.get("OIDC_JWKS_URL") or "").strip()
    )


def _permission_classes_are_restricted(classes: Iterable[type]) -> bool:
    names = {getattr(cls, "__name__", str(cls)) for cls in classes}
    return bool(names) and "AllowAny" not in names


def _raw_actor_is_pseudonymized() -> bool:
    try:
        return bool(_PSEUDONYMOUS_ACTOR_RE.fullmatch(safe_stored_audit_actor("clinician@example.test")))
    except ImproperlyConfigured:
        return False


def _governed_demo_artifacts() -> list[tuple[ModelArtifact, Any]]:
    governed = []
    for artifact in ModelArtifact.objects.order_by("-created_at"):
        evidence = summarize_model_evidence(artifact)
        pack = evidence.evidence_pack
        if (
            evidence.defensible
            and evidence.intended_use == INTENDED_USE_SHADOW_AGGREGATE
            and pack.get("shadow_mode") is True
            and pack.get("non_individual_use") is True
        ):
            governed.append((artifact, evidence))
    return governed


def build_readiness_report() -> dict[str, Any]:
    _SETTING_LOOKUP_ERRORS.clear()
    secure_mode = _setting_bool("ICEA_SECURE_MODE")
    debug = _setting_bool("DEBUG")
    auth_required = _setting_bool("ICEA_AUTH_REQUIRED", True)
    rbac_enforce = _setting_bool("ICEA_RBAC_ENFORCE", True)
    dev_insecure = _setting_bool("ICEA_DEV_ALLOW_INSECURE")
    throttling_enabled = _setting_bool("ICEA_ENABLE_THROTTLING", True)
    secret_key = str(safe_get_setting("SECRET_KEY", "") or "").strip()
    secret_key_lower = secret_key.lower()

    checks: list[OperationalCheck] = []
    checks.append(
        _check(
            "config.secret_key.present",
            bool(secret_key) and "SECRET_KEY" not in _SETTING_LOOKUP_ERRORS,
            "SECRET_KEY is configured without printing its value."
            if secret_key and "SECRET_KEY" not in _SETTING_LOOKUP_ERRORS
            else "SECRET_KEY is missing or empty.",
        )
    )
    checks.append(
        _check(
            "config.secret_key.strong_non_placeholder_in_secure_mode",
            not secure_mode
            or (
                secret_key
                and secret_key != "unsafe-secret-for-dev"
                and "change_me" not in secret_key_lower
                and "change-me" not in secret_key_lower
                and len(secret_key) >= 16
            ),
            "Secure mode is not using a development, placeholder, or short SECRET_KEY.",
        )
    )
    checks.append(
        _check(
            "config.debug.false_in_secure_mode",
            not secure_mode or not debug,
            "DEBUG is false when ICEA_SECURE_MODE is true.",
        )
    )
    checks.append(
        _check(
            "config.secure_mode.coherent",
            not secure_mode or (auth_required and rbac_enforce and not dev_insecure),
            "Secure mode requires auth, RBAC, and no dev-insecure bypass.",
        )
    )
    checks.append(
        _check(
            "config.jwt.dedicated_key_source",
            (not secure_mode and not auth_required) or _has_dedicated_jwt_source(),
            "JWT_SIGNING_KEY, JWT_VERIFYING_KEY, or OIDC_JWKS_URL is configured when secure/auth mode requires it.",
            warn=not secure_mode,
        )
    )
    audit_secret = str(safe_get_setting("AUDIT_LOG_SECRET", "") or os.environ.get("ICEA_AUDIT_SECRET", "") or "").strip()
    checks.append(
        _check(
            "config.audit_log_secret.present",
            bool(audit_secret) and "change_me" not in audit_secret.lower() and "change-me" not in audit_secret.lower(),
            "AUDIT_LOG_SECRET or ICEA_AUDIT_SECRET is configured without printing its value.",
            warn=not secure_mode,
        )
    )

    rest_framework = safe_get_setting("REST_FRAMEWORK", {}) or {}
    throttle_classes = list(rest_framework.get("DEFAULT_THROTTLE_CLASSES") or [])
    throttle_rates = dict(rest_framework.get("DEFAULT_THROTTLE_RATES") or {})
    expected_throttle_classes = {
        "icea_core.throttling.IceaAnonRateThrottle",
        "icea_core.throttling.IceaUserRateThrottle",
        "icea_core.throttling.IceaScopedRateThrottle",
    }
    expected_scopes = {"anon", "user", "icea_read", "icea_compute", "icea_train", "icea_export", "icea_writeback"}
    checks.append(
        _check(
            "config.throttling.global_and_scoped",
            not throttling_enabled
            or (expected_throttle_classes.issubset(set(throttle_classes)) and expected_scopes.issubset(set(throttle_rates))),
            "Global anonymous/user throttles and ICEA scoped throttle rates are configured when throttling is enabled.",
        )
    )
    checks.append(
        _check(
            "config.throttling.not_disabled_in_secure_mode",
            not secure_mode or throttling_enabled,
            "ICEA_ENABLE_THROTTLING remains enabled in secure mode.",
        )
    )
    allowed_hosts = list(safe_get_setting("ALLOWED_HOSTS", []) or [])
    checks.append(
        _check(
            "config.allowed_hosts.production_not_wildcard",
            not (secure_mode or not debug) or (allowed_hosts and "*" not in allowed_hosts),
            "ALLOWED_HOSTS is explicit and non-wildcard for non-debug/secure operation.",
        )
    )
    checks.append(
        _check(
            "config.cors.production_not_open",
            not (secure_mode or not debug) or not bool(safe_get_setting("CORS_ALLOW_ALL_ORIGINS", False)),
            "CORS_ALLOW_ALL_ORIGINS is not enabled for non-debug/secure operation.",
        )
    )

    governed_models = _governed_demo_artifacts()
    checks.append(
        _check(
            "models.artifact.exists",
            ModelArtifact.objects.exists(),
            "At least one ModelArtifact exists; run seed_demo before demo smoke if this fails.",
        )
    )
    checks.append(
        _check(
            "models.demo.shadow_aggregate_defensible",
            bool(governed_models),
            "At least one defensible shadow_aggregate_research model is available.",
        )
    )
    if governed_models:
        _, evidence = governed_models[0]
        pack = evidence.evidence_pack
        missing_required_limitations = REQUIRED_MODEL_LIMITATIONS - set(evidence.limitations)
        checks.append(
            _check(
                "models.demo.evidence_pack.complete",
                bool(pack)
                and evidence.intended_use == INTENDED_USE_SHADOW_AGGREGATE
                and pack.get("shadow_mode") is True
                and pack.get("non_individual_use") is True
                and not missing_required_limitations
                and pack.get("declared_features_missing_from_payload") == [],
                "Governed demo model evidence pack is shadow-only, non-individual, limitation-complete, and feature-supported.",
            )
        )

    for code, name in {
        "api.health.registered": "health",
        "api.models.registered": "models-list",
        "api.models_train.registered": "models-train",
        "api.legacy_compute.registered": "icea-compute",
        "api.icea_plus_score.registered": "icea-plus-score",
        "api.icea_plus_aggregate.registered": "icea-plus-aggregate",
        "api.icea_plus_writeback_summary.registered": "icea-plus-writeback-summary",
        "api.icea_plus_writeback_patient.registered": "icea-plus-writeback-patient",
    }.items():
        checks.append(_check(code, _has_url_name(name), f"URL name `{name}` is registered with a canonical trailing-slash route."))

    permission_expectations = {
        "api.models.permission": (ModelListView.permission_classes, {ICEAResearcherPermission}),
        "api.train.permission": (ModelTrainView.permission_classes, {ICEATrainingPermission}),
        "api.legacy_compute.permission": (ICEAComputeView.permission_classes, {ICEAResearcherPermission}),
        "api.score.permission": (ICEAPlusScoreView.permission_classes, {ICEAResearcherPermission}),
        "api.writeback_patient.permission": (ICEAPlusWritebackPatientView.permission_classes, set()),
        "api.writeback_summary.permission": (ICEAPlusWritebackSummaryView.permission_classes, set()),
        "api.fhir_writeback.permission": (RiskAssessmentWritebackView.permission_classes, set()),
        "api.fhir_writeback_list.permission": (WritebackListView.permission_classes, set()),
    }
    for code, (classes, expected_any) in permission_expectations.items():
        checks.append(
            _check(
                code,
                _permission_classes_are_restricted(classes) and (not expected_any or bool(set(classes) & expected_any)),
                "Protected API surface has explicit non-AllowAny permission classes.",
            )
        )
    checks.append(
        _check(
            "api.aggregate.permission",
            ICEAAggregateViewerPermission in getattr(__import__("icea_core.icea_plus_views", fromlist=["ICEAPlusAggregateView"]).ICEAPlusAggregateView, "permission_classes", []),
            "Aggregate endpoint requires aggregate-capable roles.",
        )
    )
    for view_class, scope in {
        ModelListView: "icea_read",
        ModelTrainView: "icea_train",
        ICEAComputeView: "icea_compute",
        ICEAPlusScoreView: "icea_compute",
        ICEAPlusWritebackSummaryView: "icea_export",
        ICEAPlusWritebackPatientView: "icea_writeback",
        RiskAssessmentWritebackView: "icea_writeback",
        WritebackListView: "icea_export",
    }.items():
        checks.append(
            _check(
                f"api.{view_class.__name__}.throttle_scope",
                getattr(view_class, "throttle_scope", None) == scope,
                f"{view_class.__name__} uses throttle scope `{scope}`.",
            )
        )

    checks.append(
        _check(
            "audit.model.available",
            AuditEvent._meta.label == "icea_pipeline.AuditEvent",
            "AuditEvent model is available for hash-chained audit records.",
        )
    )
    checks.append(
        _check(
            "audit.actor.pseudonymized",
            _raw_actor_is_pseudonymized(),
            "Raw actors are pseudonymized before persistence/presentation.",
        )
    )
    checks.append(
        _check(
            "audit.system_actor.safe",
            safe_stored_audit_actor("api") == "system:api",
            "Known non-human system actor is stored without raw user identity.",
        )
    )

    return _summarize(checks)


def _json_has_numeric_key(payload: Any, keys: set[str]) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, (int, float)) and not isinstance(value, bool):
                return True
            if _json_has_numeric_key(value, keys):
                return True
    elif isinstance(payload, list):
        return any(_json_has_numeric_key(item, keys) for item in payload)
    return False


def _redacted_score_payload(payload: Any) -> bool:
    return not _json_has_numeric_key(payload, {"score", "raw_score", "prediction", "predictions"})


def _smoke_http_host() -> str:
    for host in list(safe_get_setting("ALLOWED_HOSTS", []) or []):
        host = str(host or "").strip()
        if host and host != "*":
            return host
    return "testserver"


def _smoke_client() -> APIClient:
    client = APIClient(HTTP_HOST=_smoke_http_host())
    client.raise_request_exception = False
    return client


def _smoke_secure_requests() -> bool:
    return _setting_bool("ICEA_SECURE_MODE") or not _setting_bool("DEBUG")


def _canonical_smoke_path(path: str) -> str:
    return path if path.endswith("/") else f"{path}/"


def smoke_get(client: APIClient, path: str, data: dict[str, Any] | None = None, headers: dict[str, Any] | None = None):
    return client.get(
        _canonical_smoke_path(path),
        data or {},
        secure=_smoke_secure_requests(),
        **(headers or {}),
    )


def smoke_post(client: APIClient, path: str, data: dict[str, Any] | None = None, headers: dict[str, Any] | None = None):
    return client.post(
        _canonical_smoke_path(path),
        json.dumps(data or {}),
        content_type="application/json",
        secure=_smoke_secure_requests(),
        **(headers or {}),
    )


def _client_for_role(role: str) -> APIClient:
    group, _ = Group.objects.get_or_create(name=role)
    user, _ = get_user_model().objects.get_or_create(username=f"icea-smoke-{role}")
    user.groups.add(group)
    client = _smoke_client()
    client.force_authenticate(user=user)
    return client


def _response_json(response) -> Any:
    try:
        return response.json()
    except Exception:
        return {}


def _audit_actor_is_safe(actor: Any) -> bool:
    value = str(actor)
    return bool(_PSEUDONYMOUS_ACTOR_RE.fullmatch(value)) or value.startswith("system:")


def _audit_events_have_safe_actors(events: Iterable[dict[str, Any]]) -> bool:
    return all(_audit_actor_is_safe(event.get("actor")) for event in events)


@contextmanager
def _rolled_back_transaction():
    with transaction.atomic():
        try:
            yield
        finally:
            transaction.set_rollback(True)


def build_smoke_report() -> dict[str, Any]:
    checks: list[OperationalCheck] = []

    with _rolled_back_transaction():
        unauth = _smoke_client()
        researcher = _client_for_role("researcher")
        admin = _client_for_role("admin")
        viewer = _client_for_role("viewer_aggregate")
        initial_audit_count = AuditEvent.objects.count()
        initial_audit_ids = set(AuditEvent.objects.values_list("id", flat=True))

        health_path = canonical_api_path("health", "/api/v1/health/")
        models_path = canonical_api_path("models-list", "/api/v1/models/")
        train_path = canonical_api_path("models-train", "/api/v1/models/train/")
        score_path = canonical_api_path("icea-plus-score", "/api/v1/icea-plus/score/")
        aggregate_path = canonical_api_path("icea-plus-aggregate", "/api/v1/icea-plus/aggregate/")
        legacy_path = canonical_api_path("icea-compute", "/api/v1/icea/compute/")
        summary_path = canonical_api_path("icea-plus-writeback-summary", "/api/v1/icea-plus/writeback/summary/")
        patient_path = canonical_api_path("icea-plus-writeback-patient", "/api/v1/icea-plus/writeback/patient/")

        health = smoke_get(unauth, health_path)
        auth_health = None
        if health.status_code in {401, 403}:
            auth_health = smoke_get(researcher, health_path)
        health_body = _response_json(auth_health or health)
        health_ok = (
            health.status_code == 200
            and health_body.get("status") == "ok"
        ) or (
            health.status_code in {401, 403}
            and auth_health is not None
            and auth_health.status_code == 200
            and health_body.get("status") == "ok"
        )
        checks.append(
            _check(
                "smoke.health",
                health_ok,
                _http_detail(
                    health_path,
                    "200 ok or 401/403 anonymous plus 200 authenticated ok",
                    auth_health or health,
                    extra=f"anonymous_status_code={health.status_code}",
                ),
            )
        )

        readiness = build_readiness_report()
        demo_readiness_codes = {
            "models.artifact.exists",
            "models.demo.shadow_aggregate_defensible",
        }
        readiness_failures = list(readiness.get("failures") or [])
        blocking_readiness_failures = [
            failure for failure in readiness_failures if failure.get("code") not in demo_readiness_codes
        ]
        checks.append(
            _check(
                "smoke.readiness.basic",
                not blocking_readiness_failures,
                "Readiness report is parseable and has no blocking non-demo failure.",
            )
        )

        governed_models = _governed_demo_artifacts()
        model = governed_models[0][0] if governed_models else None
        checks.append(
            _check(
                "smoke.model.governed_available",
                model is not None,
                "Governed shadow aggregate model is available for smoke.",
                warn=True,
            )
        )

        anon_models = smoke_get(unauth, models_path)
        checks.append(
            _check(
                "smoke.models.unauth_blocked",
                anon_models.status_code in {401, 403},
                _http_detail(models_path, "401/403 unauthenticated block", anon_models),
            )
        )
        auth_models = smoke_get(researcher, models_path)
        checks.append(
            _check(
                "smoke.models.auth_responds",
                auth_models.status_code == 200,
                _http_detail(models_path, "200 for researcher role", auth_models),
            )
        )

        first_feature_row = EpisodeFeatureRow.objects.select_related("episode").order_by("episode_id").first()
        checks.append(
            _check(
                "smoke.dataset.feature_row_available",
                first_feature_row is not None,
                "At least one DB feature row is available.",
                warn=True,
            )
        )

        if model is not None:
            score = smoke_post(
                researcher,
                score_path,
                {"model_id": str(model.id), "grain": "episode", "from_db": True},
            )
            score_body = _response_json(score)
            checks.append(
                _check(
                    "smoke.score.responds",
                    score.status_code == 200,
                    _http_detail(score_path, "200 for researcher role", score),
                )
            )
            checks.append(
                _check(
                    "smoke.score.no_individual_numeric_score",
                    score.status_code == 200 and _redacted_score_payload(score_body),
                    _http_detail(score_path, "200 with score/raw_score/prediction suppressed", score),
                )
            )

            aggregate = smoke_get(
                viewer,
                aggregate_path,
                {"model_id": str(model.id), "grain": "episode", "group_by": "unit"},
            )
            aggregate_body = _response_json(aggregate)
            checks.append(
                _check(
                    "smoke.aggregate.responds",
                    aggregate.status_code == 200,
                    _http_detail(aggregate_path, "200 for viewer_aggregate role", aggregate),
                )
            )
            checks.append(
                _check(
                    "smoke.aggregate.aggregate_only",
                    aggregate.status_code == 200
                    and aggregate_body.get("non_individual_use") is True
                    and aggregate_body.get("shadow_mode") is True
                    and aggregate_body.get("results") is not None,
                    _http_detail(aggregate_path, "200 aggregate-only shadow metadata", aggregate),
                )
            )

            if first_feature_row is not None:
                legacy = smoke_post(
                    researcher,
                    legacy_path,
                    {"model_id": str(model.id), "data": [first_feature_row.features], "nurse_cols": ["nurse_hppd"]},
                )
                legacy_body = _response_json(legacy)
                checks.append(
                    _check(
                        "smoke.legacy_compute.censored",
                        legacy.status_code == 200
                        and legacy_body.get("status") == "shadow_only"
                        and legacy_body.get("results") == {}
                        and legacy_body.get("score_summary") is None
                        and legacy_body.get("score_summary_redacted") is True,
                        _http_detail(legacy_path, "200 censored legacy shadow_only response", legacy),
                    )
                )

            blocked = smoke_post(unauth, train_path, {})
            checks.append(
                _check(
                    "smoke.protected_without_auth.blocked",
                    blocked.status_code in {401, 403},
                    _http_detail(train_path, "401/403 unauthenticated block", blocked),
                )
            )

            summary_unauth = smoke_get(unauth, summary_path, {"model_id": str(model.id)})
            checks.append(
                _check(
                    "smoke.writeback_summary.unauth_blocked",
                    summary_unauth.status_code in {401, 403},
                    _http_detail(summary_path, "401/403 unauthenticated block", summary_unauth),
                )
            )
            summary = smoke_get(admin, summary_path, {"model_id": str(model.id)})
            checks.append(
                _check(
                    "smoke.writeback_summary.protected_redacted",
                    summary.status_code in {200, 400} and "episode_id" not in json.dumps(_response_json(summary), default=str).lower(),
                    _http_detail(summary_path, "200/400 governed response without episode_id", summary),
                )
            )

            if first_feature_row is not None:
                patient = smoke_get(
                    admin,
                    patient_path,
                    {"model_id": str(model.id), "episode_id": int(first_feature_row.episode_id)},
                )
                patient_body = _response_json(patient)
                checks.append(
                    _check(
                        "smoke.writeback_patient.no_numeric_score",
                        patient.status_code in {200, 400} and _redacted_score_payload(patient_body),
                        _http_detail(patient_path, "200/400 governed response with score/raw_score suppressed", patient),
                    )
                )

            bad_model = ModelArtifact.objects.create(
                name="icea-smoke-non-defensible",
                version="v0",
                target=model.target,
                features=list(model.features or []),
                model_type=model.model_type,
                model_path=model.model_path,
                metrics={},
            )
            blocked_model = smoke_post(
                researcher,
                score_path,
                {"model_id": str(bad_model.id), "grain": "episode", "from_db": True},
            )
            checks.append(
                _check(
                    "smoke.non_defensible_model.blocked",
                    blocked_model.status_code == 400 and _response_json(blocked_model).get("detail") == "model_not_defensible",
                    _http_detail(score_path, "400 model_not_defensible without row results", blocked_model),
                )
            )
            blocked_baseline = smoke_post(
                researcher,
                score_path,
                {"model_id": str(model.id), "baseline_model_id": str(bad_model.id), "grain": "episode", "from_db": True},
            )
            blocked_baseline_body = _response_json(blocked_baseline)
            checks.append(
                _check(
                    "smoke.non_defensible_baseline.blocked",
                    blocked_baseline.status_code == 400
                    and blocked_baseline_body.get("detail") == "baseline_model_not_defensible"
                    and "results" not in blocked_baseline_body,
                    _http_detail(score_path, "400 baseline_model_not_defensible without row results", blocked_baseline),
                )
            )

        final_audit_count = AuditEvent.objects.count()
        smoke_events = list(
            AuditEvent.objects.exclude(id__in=initial_audit_ids).order_by("-created_at").values("actor", "event_type")[:20]
        )
        checks.append(
            _check(
                "smoke.audit.event_generated",
                final_audit_count > initial_audit_count,
                "Smoke operations generated audit events.",
                warn=model is None,
            )
        )
        checks.append(
            _check(
                "smoke.audit.actor_pseudonymous",
                final_audit_count > initial_audit_count
                and _audit_events_have_safe_actors(smoke_events),
                "Audit actors are pseudonymous/system identifiers, not raw usernames, emails, or primary keys.",
                warn=model is None,
            )
        )

    return _summarize(checks)
