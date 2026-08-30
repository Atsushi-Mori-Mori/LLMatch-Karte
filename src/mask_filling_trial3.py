from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import random
import re
from typing import Any, Sequence

import pandas as pd
import torch
from transformers import AutoModelForMaskedLM, AutoTokenizer


LOGGER = logging.getLogger("mask_filling_trial3")
MASK_TOKEN = "[MASK]"
FIRST_NAMES = ["Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Avery", "Jamie"]
LAST_NAMES = ["Turner", "Hayes", "Morgan", "Reed", "Carter", "Brooks", "Parker", "Bennett"]
LOCATIONS = ["Northfield", "Riverton", "Lakeside", "Fairview", "Westbrook", "Hillcrest"]
ORGANIZATIONS = [
    "Central Medical Center",
    "Regional General Hospital",
    "Community Health Clinic",
]
AGE_DELTA_BY_CATEGORY = {"pediatric": 2, "adult": 5, "older_adult": 4}


@dataclass(frozen=True)
class FillingConfig:
    """Runtime policy for constraint-aware Mask-Filling."""

    model_name: str
    device: str
    top_k: int = 8
    max_masks_per_row: int | None = None
    augmentation_factor: int = 1
    change_sex: bool = False
    date_shift_min_days: int = 30
    date_shift_max_days: int = 365


class ClinicalBertFiller:
    """Load Bio_ClinicalBERT and predict tokens for one visible mask."""

    def __init__(self, model_name: str, requested_device: str) -> None:
        self.device = resolve_device(requested_device)
        self.model_name = model_name
        try:
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModelForMaskedLM.from_pretrained(model_name).to(
                self.device
            )
            self.model.eval()
        except Exception:
            LOGGER.exception("Failed to load model '%s'", model_name)
            raise
        if self.tokenizer.mask_token is None or self.tokenizer.mask_token_id is None:
            raise ValueError(f"Tokenizer for {model_name} does not define a mask token")
        LOGGER.info("Loaded %s on %s", model_name, self.device)

    def build_single_mask_context(
        self, text: str, mask_start: int, window_chars: int = 900
    ) -> str:
        """Build local context containing only the target mask token."""
        mask_token = str(self.tokenizer.mask_token)
        mask_end = mask_start + len(mask_token)
        start = max(0, mask_start - window_chars)
        end = min(len(text), mask_end + window_chars)
        left = text[start:mask_start].replace(mask_token, "unknown")
        right = text[mask_end:end].replace(mask_token, "unknown")
        return f"{left}{mask_token}{right}"

    def predict_token_for_first_mask(
        self, text: str, top_k: int, candidate_rank: int
    ) -> dict[str, object]:
        """Predict ranked token candidates for the first mask."""
        mask_token = str(self.tokenizer.mask_token)
        mask_start = text.find(mask_token)
        if mask_start < 0:
            raise ValueError(f"Input text must include {mask_token}")
        context = self.build_single_mask_context(text, mask_start)
        inputs = self.tokenizer(
            context,
            return_tensors="pt",
            truncation=True,
            max_length=min(int(self.tokenizer.model_max_length), 512),
        ).to(self.device)
        mask_positions = torch.where(
            inputs["input_ids"] == self.tokenizer.mask_token_id
        )[1]
        if len(mask_positions) != 1:
            raise ValueError(
                f"Expected exactly one {mask_token} in model context, "
                f"found {len(mask_positions)}"
            )
        with torch.no_grad():
            outputs = self.model(**inputs)
        mask_logits = outputs.logits[0, mask_positions[0], :]
        actual_top_k = min(top_k, int(mask_logits.shape[-1]))
        top_values, top_token_ids = torch.topk(mask_logits, actual_top_k)
        candidates = [
            {
                "token": self.tokenizer.decode([token_id]).strip(),
                "score": float(score),
            }
            for score, token_id in zip(
                top_values.tolist(), top_token_ids.tolist(), strict=True
            )
        ]
        selected_index = min(candidate_rank, len(candidates) - 1)
        return {
            "mask_start": mask_start,
            "candidate_rank": selected_index,
            "predicted_token": candidates[selected_index]["token"],
            "candidates": candidates,
        }


def positive_int(value: str) -> int:
    """Parse a positive integer argument."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be an integer greater than or equal to 1")
    return parsed


def nonnegative_int(value: str) -> int:
    """Parse a non-negative integer argument."""
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be an integer greater than or equal to 0")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Fill mask-trial2 CSV masks with constraints and Bio_ClinicalBERT."
    )
    parser.add_argument("--input-csv", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("mask_filling_outputs")
    )
    parser.add_argument(
        "--model-name", default="emilyalsentzer/Bio_ClinicalBERT"
    )
    parser.add_argument(
        "--device",
        choices=("cuda", "cpu", "auto"),
        default="auto",
        help="'cuda' fails explicitly if CUDA is unavailable.",
    )
    parser.add_argument("--top-k", type=positive_int, default=8)
    parser.add_argument("--max-rows", type=positive_int, default=None)
    parser.add_argument("--max-masks-per-row", type=positive_int, default=None)
    parser.add_argument("--augmentation-factor", type=positive_int, default=1)
    parser.add_argument(
        "--change-sex",
        action="store_true",
        help="Swap recognized sex terms; disabled by default for clinical consistency.",
    )
    parser.add_argument("--date-shift-min-days", type=nonnegative_int, default=30)
    parser.add_argument("--date-shift-max-days", type=nonnegative_int, default=365)
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser.parse_args(argv)


def resolve_device(requested_device: str) -> torch.device:
    """Resolve a device and fail rather than silently falling back from CUDA."""
    if requested_device == "cuda":
        if not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA was requested but torch.cuda.is_available() is False. "
                "Check the NVIDIA driver and CUDA-enabled PyTorch installation."
            )
        return torch.device("cuda")
    if requested_device == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device("cpu")


def read_csv_with_encoding_fallback(csv_path: Path) -> pd.DataFrame:
    """Read a CSV using the encodings produced by notebook and Windows runs."""
    for encoding in ("utf-8-sig", "utf-8", "cp932", "shift_jis"):
        try:
            frame = pd.read_csv(csv_path, encoding=encoding)
            LOGGER.info("Loaded CSV with encoding=%s", encoding)
            return frame
        except UnicodeDecodeError as exc:
            LOGGER.warning("Failed to decode with encoding=%s: %s", encoding, exc)
        except Exception:
            LOGGER.exception("Failed to load %s with encoding=%s", csv_path, encoding)
            raise
    raise ValueError(f"Failed to decode input CSV: {csv_path}")


def load_mask_trial2_csv(input_csv_path: Path) -> pd.DataFrame:
    """Load and validate a mask-trial2-compatible CSV."""
    if not input_csv_path.is_file():
        raise FileNotFoundError(f"Input CSV was not found: {input_csv_path}")
    frame = read_csv_with_encoding_fallback(input_csv_path)
    if "masked_text" not in frame.columns:
        raise ValueError("Missing required column: masked_text")
    frame = frame.copy()
    frame["masked_text"] = frame["masked_text"].fillna("").astype(str)
    if "mask_metadata" not in frame.columns:
        frame["mask_metadata"] = "[]"
    frame["mask_metadata"] = frame["mask_metadata"].fillna("[]").astype(str)
    return frame


def stable_rng(row_number: int, variant_id: int, mask_index: int) -> random.Random:
    """Create deterministic randomness for reproducible output."""
    return random.Random(1000003 + row_number * 1009 + variant_id * 97 + mask_index)


def parse_metadata(raw_metadata: str) -> list[dict[str, Any]]:
    """Parse and validate mask metadata saved by mask-trial2."""
    try:
        data = json.loads(raw_metadata) if raw_metadata else []
    except (TypeError, json.JSONDecodeError) as exc:
        LOGGER.warning("Failed to parse mask_metadata: %s", exc)
        return []
    if not isinstance(data, list):
        LOGGER.warning("mask_metadata is not a JSON list")
        return []
    return [item for item in data if isinstance(item, dict)]


def age_category(age: int) -> str:
    """Return a broad clinical age category."""
    return "pediatric" if age < 18 else "adult" if age < 65 else "older_adult"


def replace_age(original_value: str, rng: random.Random) -> str:
    """Replace age while preserving its broad clinical category."""
    match = re.search(r"\d{1,3}", original_value)
    if not match:
        return original_value
    original_age = int(match.group())
    category = age_category(original_age)
    delta_limit = AGE_DELTA_BY_CATEGORY[category]
    lower = max(0, original_age - delta_limit)
    upper = min(110, original_age + delta_limit)
    if category == "pediatric":
        upper = min(17, upper)
    elif category == "adult":
        lower, upper = max(18, lower), min(64, upper)
    else:
        lower = max(65, lower)
    choices = [age for age in range(lower, upper + 1) if age != original_age]
    new_age = rng.choice(choices) if choices else original_age
    return original_value[: match.start()] + str(new_age) + original_value[match.end() :]


def parse_date_value(value: str) -> tuple[datetime | None, str]:
    """Parse common date formats and return their broad output style."""
    cleaned = re.sub(
        r"\s+(st|nd|rd|th)\b", "", value.strip(), flags=re.IGNORECASE
    )
    formats = (
        ("%Y-%m-%d", "ymd_dash"),
        ("%Y/%m/%d", "ymd_slash"),
        ("%m/%d/%Y", "mdy_slash4"),
        ("%m/%d/%y", "mdy_slash2"),
        ("%B %d, %Y", "month_name"),
        ("%b %d, %Y", "month_abbr"),
        ("%B %d %Y", "month_name_no_comma"),
        ("%b %d %Y", "month_abbr_no_comma"),
    )
    for date_format, style in formats:
        try:
            return datetime.strptime(cleaned, date_format), style
        except ValueError:
            continue
    return None, "unparsed"


def format_shifted_date(value: datetime, style: str) -> str:
    """Format a shifted date in the original broad style."""
    formats = {
        "ymd_dash": "%Y-%m-%d",
        "ymd_slash": "%Y/%m/%d",
        "mdy_slash4": "%m/%d/%Y",
        "mdy_slash2": "%m/%d/%y",
        "month_abbr": "%b %d, %Y",
        "month_name_no_comma": "%B %d %Y",
        "month_abbr_no_comma": "%b %d %Y",
        "month_name": "%B %d, %Y",
    }
    return value.strftime(formats.get(style, "%B %d, %Y"))


def replace_date(original_value: str, date_shift_days: int) -> str:
    """Shift a date by a row-level offset and preserve chronology."""
    parsed, style = parse_date_value(original_value)
    if parsed is None:
        return original_value
    return format_shifted_date(parsed + timedelta(days=date_shift_days), style)


def replace_id(original_value: str, rng: random.Random) -> str:
    """Create a fictitious identifier with a similar prefix."""
    match = re.match(
        r"^(.*?)([A-Z0-9-]{3,})$", original_value.strip(), flags=re.IGNORECASE
    )
    prefix = match.group(1) if match else "ID "
    return f"{prefix}X{rng.randint(100000, 999999)}"


def replace_sex(original_value: str, change_sex: bool) -> str:
    """Preserve sex unless an explicit swap was requested."""
    if not change_sex:
        return original_value
    pairs = {
        "male": "female",
        "female": "male",
        "man": "woman",
        "woman": "man",
        "boy": "girl",
        "girl": "boy",
        "he": "she",
        "she": "he",
        "his": "her",
        "her": "his",
        "him": "her",
        "hers": "his",
    }
    replacement = pairs.get(original_value.lower(), original_value)
    return replacement.capitalize() if original_value[:1].isupper() else replacement


def fill_mask_by_type(
    current_text: str,
    metadata: dict[str, Any],
    row_number: int,
    variant_id: int,
    date_shift_days: int,
    candidate_rank: int,
    config: FillingConfig,
    filler: ClinicalBertFiller,
) -> dict[str, object]:
    """Fill one mask with a constraint or Bio_ClinicalBERT."""
    mask_index = int(metadata.get("mask_index", 0) or 0)
    mask_type = str(metadata.get("mask_type", "LOW_RISK_WORDING"))
    original_value = str(metadata.get("original_value", ""))
    rng = stable_rng(row_number, variant_id, mask_index)
    candidates: list[dict[str, object]] = []
    if mask_type == "AGE":
        replacement, method = replace_age(original_value, rng), "constraint_age_category"
    elif mask_type == "DATE":
        replacement = replace_date(original_value, date_shift_days)
        method = "constraint_row_level_date_shift"
    elif mask_type == "SEX":
        replacement = replace_sex(original_value, config.change_sex)
        method = (
            "constraint_change_sex_pair"
            if config.change_sex
            else "constraint_preserve_sex"
        )
    elif mask_type == "NAME":
        replacement = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
        method = "constraint_fictitious_name"
    elif mask_type == "LOCATION":
        replacement, method = rng.choice(LOCATIONS), "constraint_fictitious_location"
    elif mask_type == "ORGANIZATION":
        replacement = rng.choice(ORGANIZATIONS)
        method = "constraint_fictitious_organization"
    elif mask_type == "ID":
        replacement, method = replace_id(original_value, rng), "constraint_fictitious_id"
    else:
        prediction = filler.predict_token_for_first_mask(
            current_text, config.top_k, candidate_rank
        )
        replacement = str(prediction["predicted_token"])
        candidates = list(prediction["candidates"])  # type: ignore[arg-type]
        method = "bio_clinicalbert_low_risk_wording"
    return {
        "mask_index": mask_index,
        "mask_type": mask_type,
        "original_value": original_value,
        "replacement": replacement,
        "method": method,
        "constraint_rule": metadata.get("constraint_rule", ""),
        "candidates": candidates,
    }


def fill_with_constraints(
    masked_text: str,
    raw_metadata: str,
    row_number: int,
    variant_id: int,
    candidate_rank: int,
    config: FillingConfig,
    filler: ClinicalBertFiller,
) -> dict[str, object]:
    """Fill all masks in metadata order."""
    if not masked_text.strip():
        return {
            "filled_text": masked_text,
            "predicted_tokens": [],
            "details": [],
            "status": "empty",
        }
    metadata = parse_metadata(raw_metadata)
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
    row_rng = stable_rng(row_number, variant_id, 0)
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
        filled_text = filled_text.replace(MASK_TOKEN, str(detail["replacement"]), 1)
        detail["date_shift_days"] = (
            date_shift_days if detail["mask_type"] == "DATE" else ""
        )
        details.append(detail)
    return {
        "filled_text": filled_text,
        "predicted_tokens": [str(item["replacement"]) for item in details],
        "details": details,
        "status": "filled" if MASK_TOKEN not in filled_text else "partially_filled",
    }


def run_mask_filling(
    frame: pd.DataFrame,
    max_rows: int | None,
    config: FillingConfig,
    filler: ClinicalBertFiller,
) -> pd.DataFrame:
    """Apply constraint-aware filling and preserve source columns."""
    target = frame.head(max_rows).copy() if max_rows is not None else frame.copy()
    records: list[dict[str, object]] = []
    for row_number, row in target.iterrows():
        masked_text = str(row["masked_text"])
        raw_metadata = str(row.get("mask_metadata", "[]"))
        LOGGER.info(
            "Processing row %s with %d masks", row_number, masked_text.count(MASK_TOKEN)
        )
        for variant_id in range(1, config.augmentation_factor + 1):
            output_row = row.to_dict()
            output_row.update(
                {
                    "variant_id": variant_id,
                    "augmentation_factor": config.augmentation_factor,
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
                        "fill_error": str(exc),
                    }
                )
            output_row["model_name"] = config.model_name
            output_row["filling_version"] = "mask-filling-trial3"
            output_row["constraint_policy"] = json.dumps(
                {
                    "CHANGE_SEX": config.change_sex,
                    "DATE_SHIFT_MIN_DAYS": config.date_shift_min_days,
                    "DATE_SHIFT_MAX_DAYS": config.date_shift_max_days,
                    "AGE_DELTA_BY_CATEGORY": AGE_DELTA_BY_CATEGORY,
                },
                ensure_ascii=False,
            )
            records.append(output_row)
    return pd.DataFrame(records)


def save_output(frame: pd.DataFrame, output_dir: Path) -> Path:
    """Save a timestamped mask-filling CSV."""
    output_dir.mkdir(parents=True, exist_ok=True)
    output = (
        output_dir
        / f"clinical_case_mask_filling3_{datetime.now():%Y%m%d_%H%M%S}.csv"
    )
    try:
        frame.to_csv(output, index=False, encoding="utf-8-sig")
    except Exception:
        LOGGER.exception("Failed to save output CSV: %s", output)
        raise
    LOGGER.info("Saved CSV: %s", output)
    return output


def main(argv: Sequence[str] | None = None) -> int:
    """Run constraint-aware Mask-Filling from the command line."""
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.date_shift_min_days > args.date_shift_max_days:
        LOGGER.error("--date-shift-min-days must not exceed --date-shift-max-days")
        return 2
    try:
        config = FillingConfig(
            model_name=args.model_name,
            device=args.device,
            top_k=args.top_k,
            max_masks_per_row=args.max_masks_per_row,
            augmentation_factor=args.augmentation_factor,
            change_sex=args.change_sex,
            date_shift_min_days=args.date_shift_min_days,
            date_shift_max_days=args.date_shift_max_days,
        )
        input_frame = load_mask_trial2_csv(args.input_csv)
        filler = ClinicalBertFiller(config.model_name, config.device)
        filled = run_mask_filling(input_frame, args.max_rows, config, filler)
        output = save_output(filled, args.output_dir)
    except Exception as exc:
        LOGGER.error("Mask-Filling failed: %s", exc)
        return 1
    print(f"Rows written: {len(filled)}")
    print(f"Device: {filler.device}")
    print(f"CSV saved: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
