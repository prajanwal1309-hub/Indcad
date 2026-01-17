# decision_engine.py
# IndCad Decision Engine v1
# This file contains ONLY business logic. No web, no PDF, no auth.

from typing import Dict, Any


def classify_data_state(context: Dict[str, Any]) -> str:
    status = context.get("status")
    time_remaining = context.get("time_remaining")

    if not status or status == "Other / Not sure":
        return "LIMITED"

    if status != "Outside Canada" and not time_remaining:
        return "PARTIAL"

    return "COMPLETE"


def express_entry_reality(crs_score: int, recent_cutoff: int) -> Dict[str, Any]:
    crs_gap = recent_cutoff - crs_score

    if crs_gap <= 5:
        return {"ee_status": "REALISTIC", "crs_gap": crs_gap}
    elif crs_gap <= 40:
        return {"ee_status": "IMPROVABLE", "crs_gap": crs_gap}
    else:
        return {"ee_status": "NOT_REALISTIC", "crs_gap": crs_gap}


def determine_stay_priority(context: Dict[str, Any]) -> str:
    status = context.get("status")
    time_remaining = context.get("time_remaining")

    if status in ["Student", "PGWP", "Work Permit"] and time_remaining in ["<6", "6-12"]:
        return "HIGH"

    return "NORMAL"


def language_blocker(clb: int) -> bool:
    return clb < 9


def consent_flags(context: Dict[str, Any]) -> Dict[str, bool]:
    return {
        "allow_study": context.get("open_to_study") is True,
        "allow_move": context.get("open_to_move") is True,
        "allow_noc_change": context.get("open_to_job_change") is True,
    }


def select_primary_path(
    data_state: str,
    ee_status: str,
    stay_priority: str,
    noc_sector: str,
    consent: Dict[str, bool]
) -> Dict[str, Any]:

    # Rule A — Limited data
    if data_state == "LIMITED":
        return {
            "primary_path": "GENERAL_IMPROVEMENT_ONLY",
            "risk_level": "HIGH"
        }

    # Rule B — Express Entry realistic
    if ee_status == "REALISTIC":
        return {
            "primary_path": "EXPRESS_ENTRY_FOCUS",
            "risk_level": "MEDIUM"
        }

    # Rule C — EE not realistic + stay at risk
    if ee_status == "NOT_REALISTIC" and stay_priority == "HIGH":
        if consent["allow_study"]:
            return {
                "primary_path": "STUDY_PLUS_ALIGNMENT",
                "risk_level": "MEDIUM"
            }
        else:
            return {
                "primary_path": "LANGUAGE_ONLY",
                "risk_level": "HIGH"
            }

    # Rule D — Healthcare alignment
    if consent["allow_noc_change"]:
        if noc_sector == "healthcare":
            return {
                "primary_path": "HEALTHCARE_EXPERIENCE",
                "risk_level": "LOW"
            }
        elif consent["allow_study"]:
            return {
                "primary_path": "HEALTHCARE_ALIGNMENT",
                "risk_level": "MEDIUM"
            }

    # Rule E — Province shift
    if consent["allow_move"]:
        return {
            "primary_path": "PROVINCE_SHIFT",
            "risk_level": "MEDIUM"
        }

    # Rule F — Fallback
    return {
        "primary_path": "LANGUAGE_AND_WAIT",
        "risk_level": "HIGH"
    }


def select_secondary_path(primary_path: str, ee_status: str) -> str | None:
    if primary_path != "EXPRESS_ENTRY_FOCUS" and ee_status == "IMPROVABLE":
        return "EXPRESS_ENTRY_AFTER_IMPROVEMENT"
    return None


def timeline_detail_level(data_state: str) -> str:
    if data_state == "COMPLETE":
        return "DETAILED"
    if data_state == "PARTIAL":
        return "RANGE_ONLY"
    return "NONE"


def build_do_not_list(
    ee_status: str,
    consent: Dict[str, bool]
) -> list[str]:

    do_not = []

    if ee_status == "NOT_REALISTIC":
        do_not.append("Apply for Express Entry now")

    if not consent["allow_study"]:
        do_not.append("Enroll in random diploma programs")

    if not consent["allow_move"]:
        do_not.append("Relocate without job or provincial support")

    return do_not


def run_decision_engine(payload: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = payload.get("snapshot", {})
    context = payload.get("context", {})

    # Step 1 — Data classification
    data_state = classify_data_state(context)

    # Step 2 — Express Entry reality
    ee_result = express_entry_reality(
        crs_score=snapshot.get("crs_score", 0),
        recent_cutoff=snapshot.get("recent_cutoff", 0)
    )

    # Step 3 — Stay priority
    stay_priority = determine_stay_priority(context)

    # Step 4 — Language blocker
    lang_blocker = language_blocker(snapshot.get("clb", 0))

    # Step 5 — Consent flags
    consent = consent_flags(context)

    # Step 6 — Primary path
    primary_result = select_primary_path(
        data_state=data_state,
        ee_status=ee_result["ee_status"],
        stay_priority=stay_priority,
        noc_sector=snapshot.get("noc_sector"),
        consent=consent
    )

    # Step 7 — Secondary path
    secondary_path = select_secondary_path(
        primary_result["primary_path"],
        ee_result["ee_status"]
    )

    # Step 8 — Timeline detail
    timeline_detail = timeline_detail_level(data_state)

    # Step 9 — What not to do
    do_not_list = build_do_not_list(
        ee_status=ee_result["ee_status"],
        consent=consent
    )

    # Final output
    return {
        "data_state": data_state,
        "ee_status": ee_result["ee_status"],
        "crs_gap": ee_result["crs_gap"],
        "stay_priority": stay_priority,
        "language_blocker": lang_blocker,
        "primary_path": primary_result["primary_path"],
        "secondary_path": secondary_path,
        "risk_level": primary_result["risk_level"],
        "timeline_detail": timeline_detail,
        "do_not_list": do_not_list
    }


# Optional manual test
if __name__ == "__main__":
    test_payload = {
        "snapshot": {
            "crs_score": 462,
            "recent_cutoff": 485,
            "clb": 8,
            "noc_sector": "non-healthcare",
            "canadian_exp_months": 4
        },
        "context": {
            "status": "PGWP",
            "time_remaining": "6-12",
            "open_to_study": True,
            "open_to_job_change": True,
            "open_to_move": False,
            "province": "Ontario"
        }
    }

    result = run_decision_engine(test_payload)
    from pprint import pprint
    pprint(result)
