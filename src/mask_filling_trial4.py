from __future__ import annotations

import calendar
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import re
from typing import Any, Sequence

import pandas as pd

import mask_filling_trial3 as trial3
import mask_trial3 as masking3


LOGGER = logging.getLogger("mask_filling_trial4")
MASK_TOKEN = trial3.MASK_TOKEN
SENSITIVE_TERMS = (
    masking3.NEGATION_TERMS
    | masking3.SEVERITY_TERMS
    | masking3.TEMPORALITY_TERMS
    | masking3.OUTCOME_TERMS
    | masking3.POLARITY_TERMS
    | masking3.MEDICAL_TERMS
)
SAFE_TOKEN_PATTERN = re.compile(r"^[A-Za-z][A-Za-z'-]*$")
GESTATIONAL_WEEK_DELTA = 2
DURATION_DELTA = 2
MONTH_DAY_PATTERN = re.compile(
    r"^(?P<month>[A-Za-z]{3,9})\s+(?P<day>\d{1,2})(?P<suffix>st|nd|rd|th)?$",
    re.IGNORECASE,
)
WEEK_RANGE_PATTERN = re.compile(
    r"^(?P<start>\d{1,2})\s*[-–]\s*(?P<end>\d{1,2})\s+weeks?$",
    re.IGNORECASE,
)
GESTATIONAL_PATTERN = re.compile(
    r"^(?P<prefix>(?:gestational\s+)?age\s+)(?P<weeks>\d{1,2})"
    r"(?P<suffix>\s+weeks?)$",
    re.IGNORECASE,
)
DURATION_PATTERN = re.compile(
    r"^(?P<number>\d{1,3})(?P<space>\s*)"
    r"(?P<unit>days?|weeks?|months?|years?)$",
    re.IGNORECASE,
)


def classify_date_value(value: str) -> str:
    """Classify a DATE value into a machine-actionable replacement policy."""
    if trial3.parse_date_value(value)[0] is not None:
        return "absolute_date"
    stripped = value.strip()
    if MONTH_DAY_PATTERN.fullmatch(stripped):
        return "month_day"
    if WEEK_RANGE_PATTERN.fullmatch(stripped):
        return "week_range"
    if GESTATIONAL_PATTERN.fullmatch(stripped):
        return "gestational_age"
    if DURATION_PATTERN.fullmatch(stripped):
        return "duration"
    return "unparsed"


def bounded_delta(seed_days: int, limit: int) -> int:
    """Create a reproducible, non-zero small delta from the row date offset."""
    magnitude = abs(seed_days) % max(1, limit) + 1
    return magnitude if seed_days >= 0 else -magnitude


def shift_month_day(value: str, date_shift_days: int) -> str:
    """Shift a year-free month/day value while preserving its display style."""
    match = MONTH_DAY_PATTERN.fullmatch(value.strip())
    if not match:
        return value
    parsed: datetime | None = None
    for date_format in ("%B %d %Y", "%b %d %Y"):
        try:
            parsed = datetime.strptime(
                f"{match.group('month')} {match.group('day')} 2000", date_format
            )
            break
        except ValueError:
            continue
    if parsed is None:
        LOGGER.warning("Could not parse month/day DATE value: %s", value)
        return value
    shifted = parsed + timedelta(days=date_shift_days)
    return f"{calendar.month_name[shifted.month]} {shifted.day}"


def shift_week_range(value: str, date_shift_days: int) -> str:
    """Move a week range by a small amount while preserving its width."""
    match = WEEK_RANGE_PATTERN.fullmatch(value.strip())
    if not match:
        return value
    start, end = int(match.group("start")), int(match.group("end"))
    delta = bounded_delta(date_shift_days, GESTATIONAL_WEEK_DELTA)
    new_start = max(1, start + delta)
    new_end = new_start + max(0, end - start)
    return f"{new_start}-{new_end} weeks"


def shift_gestational_age(value: str, date_shift_days: int) -> str:
    """Adjust gestational age within a small clinically plausible range."""
    match = GESTATIONAL_PATTERN.fullmatch(value.strip())
    if not match:
        return value
    weeks = int(match.group("weeks"))
    delta = bounded_delta(date_shift_days, GESTATIONAL_WEEK_DELTA)
    shifted_weeks = max(1, min(42, weeks + delta))
    return f"{match.group('prefix')}{shifted_weeks}{match.group('suffix')}"


def shift_duration(value: str, date_shift_days: int) -> str:
    """Adjust a relative duration without changing its unit."""
    match = DURATION_PATTERN.fullmatch(value.strip())
    if not match:
        return value
    number = int(match.group("number"))
    delta = bounded_delta(date_shift_days, DURATION_DELTA)
    return (
        f"{max(1, number + delta)}{match.group('space')}"
        f"{match.group('unit')}"
    )


def replace_date_extended(original_value: str, date_shift_days: int) -> str:
    """Apply the selected deterministic rule to a DATE-like value."""
    date_kind = classify_date_value(original_value)
    handlers = {
        "absolute_date": trial3.replace_date,
        "month_day": shift_month_day,
        "week_range": shift_week_range,
        "gestational_age": shift_gestational_age,
        "duration": shift_duration,
    }
    handler = handlers.get(date_kind)
    if handler is None:
        LOGGER.warning("Unsupported DATE value was restored: %s", original_value)
        return original_value
    return handler(original_value, date_shift_days)


def enrich_metadata(raw_metadata: str, change_sex: bool) -> str:
    """Add explicit machine-readable policies to source mask metadata."""
    enriched: list[dict[str, Any]] = []
    for source_item in trial3.parse_metadata(raw_metadata):
        item = dict(source_item)
        mask_type = str(item.get("mask_type", "LOW_RISK_WORDING"))
        original = str(item.get("original_value", ""))
        if mask_type == "DATE":
            date_kind = classify_date_value(original)
            item.update(
                {
                    "date_kind": date_kind,
                    "replacement_policy": (
                        f"shift_{date_kind}"
                        if date_kind != "unparsed"
                        else "preserve_unparsed"
                    ),
                    "preserve_chronology": date_kind
                    in {"absolute_date", "month_day"},
                }
            )
        elif mask_type == "SEX":
            item.update(
                {
                    "replacement_policy": (
                        "swap_consistently" if change_sex else "preserve"
                    ),
                    "consistency_scope": "row",
                }
            )
        elif mask_type == "AGE":
            item["replacement_policy"] = "same_age_category"
        elif mask_type in {"NAME", "LOCATION", "ORGANIZATION", "ID"}:
            item["replacement_policy"] = "fictitious_value"
        else:
            item["replacement_policy"] = "clinicalbert_filtered"
        enriched.append(item)
    return json.dumps(enriched, ensure_ascii=False)


def normalize_token(value: str) -> str:
    """Normalize a decoded WordPiece token for validation."""
    return value.strip().removeprefix("##").strip()


def candidate_rejection_reasons(
    candidate: str, metadata: dict[str, Any]
) -> list[str]:
    """Return explicit reasons why a model candidate is unsafe."""
    token = normalize_token(candidate)
    lower = token.lower()
    original = str(metadata.get("original_value", ""))
    original_lower = original.lower()
    reasons: list[str] = []
    if not token or not SAFE_TOKEN_PATTERN.fullmatch(token):
        reasons.append("not_a_complete_alphabetic_token")
    if lower in SENSITIVE_TERMS and lower != original_lower:
        reasons.append("candidate_is_medical_or_semantically_sensitive")
    if any(
        bool(metadata.get(flag))
        for flag in (
            "medical_concept",
            "negation_sensitive",
            "severity_sensitive",
            "temporality_sensitive",
            "outcome_sensitive",
            "polarity_sensitive",
            "protected",
        )
    ):
        reasons.append("source_mask_is_protected")
    pos = str(metadata.get("pos", ""))
    if pos == "ADV" and lower != original_lower and not lower.endswith("ly"):
        reasons.append("pos_mismatch_expected_adverb")
    if original[:1].isupper() != token[:1].isupper():
        reasons.append("capitalization_mismatch")
    return reasons


def select_safe_prediction(
    current_text: str,
    metadata: dict[str, Any],
    top_k: int,
    preferred_rank: int,
    filler: trial3.ClinicalBertFiller,
) -> tuple[str, list[dict[str, object]], str, list[str]]:
    """Select a constraint-compliant candidate or restore the original token."""
    prediction = filler.predict_token_for_first_mask(current_text, top_k, 0)
    raw_candidates = list(prediction["candidates"])  # type: ignore[arg-type]
    checked: list[dict[str, object]] = []
    accepted: list[str] = []
    for candidate in raw_candidates:
        token = normalize_token(str(candidate.get("token", "")))
        reasons = candidate_rejection_reasons(token, metadata)
        checked.append(
            {
                **candidate,
                "normalized_token": token,
                "accepted": not reasons,
                "rejection_reasons": reasons,
            }
        )
        if not reasons:
            accepted.append(token)
    if accepted:
        selected = accepted[min(preferred_rank, len(accepted) - 1)]
        return selected, checked, "bio_clinicalbert_filtered", []
    original = str(metadata.get("original_value", ""))
    return (
        original,
        checked,
        "fallback_restore_original",
        ["no_candidate_satisfied_constraints"],
    )


def fill_mask_by_type(
    current_text: str,
    metadata: dict[str, Any],
    row_number: int,
    variant_id: int,
    date_shift_days: int,
    candidate_rank: int,
    config: trial3.FillingConfig,
    filler: trial3.ClinicalBertFiller,
) -> dict[str, object]:
    """Fill a mask, filtering model predictions using trial3 metadata."""
    mask_type = str(metadata.get("mask_type", "LOW_RISK_WORDING"))
    if mask_type == "DATE":
        original_value = str(metadata.get("original_value", ""))
        return {
            "mask_index": int(metadata.get("mask_index", 0) or 0),
            "mask_type": mask_type,
            "original_value": original_value,
            "replacement": replace_date_extended(original_value, date_shift_days),
            "method": f"constraint_{metadata.get('replacement_policy', 'date')}",
            "constraint_rule": metadata.get("constraint_rule", ""),
            "candidates": [],
            "validation_issues": [],
        }
    if mask_type != "LOW_RISK_WORDING":
        detail = trial3.fill_mask_by_type(
            current_text,
            metadata,
            row_number,
            variant_id,
            date_shift_days,
            candidate_rank,
            config,
            filler,
        )
        detail["validation_issues"] = []
        return detail
    replacement, candidates, method, issues = select_safe_prediction(
        current_text,
        metadata,
        config.top_k,
        candidate_rank,
        filler,
    )
    return {
        "mask_index": int(metadata.get("mask_index", 0) or 0),
        "mask_type": mask_type,
        "original_value": str(metadata.get("original_value", "")),
        "replacement": replacement,
        "method": method,
        "constraint_rule": metadata.get("constraint_rule", ""),
        "classification": {
            key: metadata.get(key, "")
            for key in (
                "pos",
                "lemma",
                "entity_type",
                "medical_concept",
                "negation_sensitive",
                "severity_sensitive",
                "temporality_sensitive",
                "outcome_sensitive",
                "polarity_sensitive",
                "protected",
            )
        },
        "candidates": candidates,
        "validation_issues": issues,
    }


def non_mask_segments(masked_text: str) -> list[str]:
    """Return immutable text segments surrounding all masks."""
    return masked_text.split(MASK_TOKEN)


def validate_filled_text(
    masked_text: str,
    filled_text: str,
    metadata: list[dict[str, Any]],
    details: list[dict[str, object]],
) -> list[str]:
    """Validate mask count, immutable segments, and per-mask safety decisions."""
    issues: list[str] = []
    expected_masks = masked_text.count(MASK_TOKEN)
    if expected_masks != len(metadata):
        issues.append(
            f"mask_metadata_count_mismatch:{expected_masks}!={len(metadata)}"
        )
    if MASK_TOKEN in filled_text:
        issues.append("unfilled_mask_remaining")
    cursor = 0
    for segment in non_mask_segments(masked_text):
        found = filled_text.find(segment, cursor)
        if found < 0:
            issues.append("non_mask_text_changed")
            break
        cursor = found + len(segment)
    for detail in details:
        for issue in detail.get("validation_issues", []):  # type: ignore[union-attr]
            issues.append(f"mask_{detail.get('mask_index')}:{issue}")
    return list(dict.fromkeys(issues))


def fill_with_constraints(
    masked_text: str,
    raw_metadata: str,
    row_number: int,
    variant_id: int,
    candidate_rank: int,
    config: trial3.FillingConfig,
    filler: trial3.ClinicalBertFiller,
) -> dict[str, object]:
    """Fill all masks and perform post-generation validation."""
    if not masked_text.strip():
        return {
            "filled_text": masked_text,
            "predicted_tokens": [],
            "details": [],
            "status": "empty",
            "validation_issues": [],
        }
    metadata = trial3.parse_metadata(raw_metadata)
    if not metadata:
        metadata = [
            {
                "mask_index": index + 1,
                "mask_type": "LOW_RISK_WORDING",
                "original_value": "",
            }
            for index in range(masked_text.count(MASK_TOKEN))
        ]
    metadata.sort(key=lambda item: int(item.get("mask_index", 0) or 0))
    if config.max_masks_per_row is not None:
        metadata = metadata[: config.max_masks_per_row]
    row_rng = trial3.stable_rng(row_number, variant_id, 0)
    direction = 1 if variant_id % 2 else -1
    date_shift_days = direction * row_rng.randint(
        config.date_shift_min_days, config.date_shift_max_days
    )
    filled_text = masked_text
    details: list[dict[str, object]] = []
    for item in metadata:
        if MASK_TOKEN not in filled_text:
            break
        detail = fill_mask_by_type(
            filled_text,
            item,
            row_number,
            variant_id,
            date_shift_days,
            candidate_rank,
            config,
            filler,
        )
        filled_text = filled_text.replace(
            MASK_TOKEN, str(detail["replacement"]), 1
        )
        detail["date_shift_days"] = (
            date_shift_days if detail["mask_type"] == "DATE" else ""
        )
        details.append(detail)
    issues = validate_filled_text(masked_text, filled_text, metadata, details)
    status = "filled"
    if MASK_TOKEN in filled_text:
        status = "partially_filled"
    if issues:
        status = "filled_with_fallback" if all(
            "no_candidate_satisfied_constraints" in issue for issue in issues
        ) else "validation_failed"
    return {
        "filled_text": filled_text,
        "predicted_tokens": [str(item["replacement"]) for item in details],
        "details": details,
        "status": status,
        "validation_issues": issues,
    }


def run_mask_filling(
    frame: pd.DataFrame,
    max_rows: int | None,
    config: trial3.FillingConfig,
    filler: trial3.ClinicalBertFiller,
) -> pd.DataFrame:
    """Apply classification-aware filling and retain validation evidence."""
    target = frame.head(max_rows).copy() if max_rows is not None else frame.copy()
    records: list[dict[str, object]] = []
    for row_number, row in target.iterrows():
        masked_text = str(row["masked_text"])
        source_metadata = str(row.get("mask_metadata", "[]"))
        raw_metadata = enrich_metadata(source_metadata, config.change_sex)
        LOGGER.info(
            "Processing row %s with %d masks", row_number, masked_text.count(MASK_TOKEN)
        )
        for variant_id in range(1, config.augmentation_factor + 1):
            output_row = row.to_dict()
            output_row.update(
                {
                    "variant_id": variant_id,
                    "augmentation_factor": config.augmentation_factor,
                    "source_mask_metadata": source_metadata,
                    "enriched_mask_metadata": raw_metadata,
                }
            )
            try:
                result = fill_with_constraints(
                    masked_text,
                    raw_metadata,
                    int(row_number),
                    variant_id,
                    variant_id - 1,
                    config,
                    filler,
                )
                output_row.update(
                    {
                        "filled_text": result["filled_text"],
                        "predicted_tokens": "; ".join(result["predicted_tokens"]),
                        "mask_fill_details": json.dumps(
                            result["details"], ensure_ascii=False
                        ),
                        "fill_status": result["status"],
                        "validation_issues": json.dumps(
                            result["validation_issues"], ensure_ascii=False
                        ),
                        "fill_error": "",
                    }
                )
            except Exception as exc:
                LOGGER.exception(
                    "Mask-Filling failed for row %s variant %s",
                    row_number,
                    variant_id,
                )
                output_row.update(
                    {
                        "filled_text": masked_text,
                        "predicted_tokens": "",
                        "mask_fill_details": "[]",
                        "fill_status": "error",
                        "validation_issues": "[]",
                        "fill_error": str(exc),
                    }
                )
            output_row["model_name"] = config.model_name
            output_row["filling_version"] = "mask-filling-trial4"
            records.append(output_row)
    return pd.DataFrame(records)


def save_output(frame: pd.DataFrame, output_dir: Path) -> Path:
    """Save a timestamped trial4 CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = (
        output_dir
        / f"clinical_case_mask_filling4_{trial3.datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    try:
        frame.to_csv(output, index=False, encoding="utf-8-sig")
    except Exception:
        LOGGER.exception("Failed to save output CSV: %s", output)
        raise
    LOGGER.info("Saved CSV: %s", output)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    """Run classification-aware Bio_ClinicalBERT filling."""
    args = trial3.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.date_shift_min_days > args.date_shift_max_days:
        LOGGER.error("--date-shift-min-days must not exceed --date-shift-max-days")
        return 2
    try:
        config = trial3.FillingConfig(
            model_name=args.model_name,
            device=args.device,
            top_k=args.top_k,
            max_masks_per_row=args.max_masks_per_row,
            augmentation_factor=args.augmentation_factor,
            change_sex=args.change_sex,
            date_shift_min_days=args.date_shift_min_days,
            date_shift_max_days=args.date_shift_max_days,
        )
        input_frame = trial3.load_mask_trial2_csv(args.input_csv)
        filler = trial3.ClinicalBertFiller(config.model_name, config.device)
        filled = run_mask_filling(input_frame, args.max_rows, config, filler)
        output = save_output(filled, args.output_dir)
    except Exception as exc:
        LOGGER.exception("Mask-Filling failed: %s", exc)
        return 1
    print(f"Rows written: {len(filled)}")
    print(f"Device: {filler.device}")
    print(f"CSV saved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
