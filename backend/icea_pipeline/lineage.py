from __future__ import annotations

from typing import Any, Dict, Iterable, Tuple

from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from config.request_context import get_actor



def _model_label(instance) -> str:
    return f"{instance.__class__.__module__.split('.')[0]}.{instance.__class__.__name__}"



def _pk_str(instance) -> str:
    try:
        return str(getattr(instance, "pk"))
    except Exception:
        return ""



def _field_names(instance) -> Iterable[str]:
    # Track only regular fields (avoid relations) and avoid PHI-heavy models.
    for f in instance._meta.fields:
        name = getattr(f, "name", "")
        if not name or name in {"id", "pk", "created_at", "updated_at"}:
            continue
        yield name



def _jsonable(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None), list, dict)):
        return value
    pk = getattr(value, "pk", None)
    if pk is not None:
        return str(pk)
    return str(value)



def _snapshot(instance) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in _field_names(instance):
        try:
            out[name] = _jsonable(getattr(instance, name))
        except Exception:
            continue
    return out



def _diff(old: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    changes: Dict[str, Any] = {}
    keys = set(old.keys()) | set(new.keys())
    for k in sorted(keys):
        a = old.get(k)
        b = new.get(k)
        if a != b:
            changes[k] = {"from": _jsonable(a), "to": _jsonable(b)}
    return changes


# Models to track (base entities + algorithm/config).
TRACKED = {
    ("icea_core", "Hospital"),
    ("icea_core", "Unit"),
    ("icea_core", "ModelArtifact"),
    ("icea_pipeline", "CausalSpec"),
}



def _is_tracked(instance) -> bool:
    mod = instance.__class__.__module__.split(".")[0]
    name = instance.__class__.__name__
    return (mod, name) in TRACKED


# Cache old snapshots across pre_save -> post_save
_PRE_SAVE_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}


@receiver(pre_save)
def _capture_old(sender, instance, **kwargs):
    if not _is_tracked(instance):
        return
    pk = _pk_str(instance)
    if not pk:
        return
    try:
        old = sender.objects.filter(pk=instance.pk).first()
        if old is None:
            return
        _PRE_SAVE_CACHE[(sender.__name__, pk)] = _snapshot(old)
    except Exception:
        return


@receiver(post_save)
def _log_change(sender, instance, created: bool, **kwargs):
    if not _is_tracked(instance):
        return
    try:
        from icea_pipeline.models import EntityChangeLog

        label = _model_label(instance)
        pk = _pk_str(instance)
        if not pk:
            return
        actor = get_actor("")

        if created:
            changes = {"snapshot": _snapshot(instance)}
            action = "create"
        else:
            old = _PRE_SAVE_CACHE.pop((sender.__name__, pk), {})
            new = _snapshot(instance)
            changes = _diff(old, new)
            action = "update"

        # Avoid noise-only updates
        if action == "update" and not changes:
            return

        EntityChangeLog.objects.create(
            actor=actor,
            model_label=label,
            object_id=pk,
            action=action,
            changes=changes,
        )
    except Exception:
        # Never break core flows for lineage
        return


@receiver(post_delete)
def _log_delete(sender, instance, **kwargs):
    if not _is_tracked(instance):
        return
    try:
        from icea_pipeline.models import EntityChangeLog

        label = _model_label(instance)
        pk = _pk_str(instance)
        if not pk:
            return
        actor = get_actor("")
        EntityChangeLog.objects.create(
            actor=actor,
            model_label=label,
            object_id=pk,
            action="delete",
            changes={"snapshot": _snapshot(instance)},
        )
    except Exception:
        return
