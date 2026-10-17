"""Compact model context and minimal application-managed collection questions."""

from __future__ import annotations

from typing import Any


def compact_lead_state(state: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "profile": {
            key: value
            for key, value in state.get("profile", {}).items()
            if value is not None
        }
    }
    result["declined_fields"] = list(state.get("declined_fields", {}))
    for name, fields in {
        "pending_question": {"text", "target_fields"},
        "active_course_scope": {"course_names", "ambiguous"},
        "pending_action": {"action_type", "status", "requested_time_text", "details"},
        "current_request": {"intent"},
    }.items():
        if value := state.get(name):
            result[name] = {
                key: item
                for key, item in value.items()
                if key in fields and item is not None
            }
    if conflicts := state.get("unresolved_conflicts"):
        result["unresolved_conflicts"] = [
            {
                key: value
                for key, value in conflict.items()
                if key in {"field", "accepted_value", "proposed_value", "reason"}
            }
            for conflict in conflicts[-3:]
        ]
    return result


def compact_agent_context(capture: dict[str, Any] | None) -> dict[str, Any]:
    if not capture:
        return {"status": "unavailable"}
    return {
        **({"status": capture["status"]} if "status" in capture else {}),
        **(
            {"state": compact_lead_state(capture["state"])}
            if capture.get("state")
            else {}
        ),
        "action_outcomes": capture.get("action_outcomes", []),
    }


def plan_lead_question(
    prefs: dict[str, Any], capture: dict[str, Any]
) -> tuple[str, list[str]] | None:
    """Ask only core identity or information required for an explicit pending action."""
    if not prefs.get("capture_leads") or not (state := capture.get("state")):
        return None
    if state.get("pending_question"):
        return None
    profile = state.get("profile", {})
    declined = state.get("declined_fields", {})
    if prefs.get("capture_name") and not profile.get("name") and "name" not in declined:
        return "May I have your name?", ["name"]
    pending = state.get("pending_action") or {}
    if pending.get("status") != "awaiting_details":
        return None
    if profile.get("consent_to_contact") is False or "consent_to_contact" in declined:
        return None
    if profile.get("consent_to_contact") is not True:
        return "May a counsellor contact you about this request?", [
            "consent_to_contact"
        ]
    field = "phone" if pending.get("action_type") == "schedule_callback" else "email"
    if pending.get("action_type") != "send_course_details" and (
        profile.get("phone") or profile.get("email")
    ):
        return None
    if (
        prefs.get(f"capture_{field}")
        and not profile.get(field)
        and field not in declined
    ):
        question = (
            "What phone number should the counsellor use?"
            if field == "phone"
            else "What email address should the counsellor use?"
        )
        return question, [field]
    return None
