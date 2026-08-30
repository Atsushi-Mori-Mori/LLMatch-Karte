from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import random
import re
from typing import Any, Sequence

import pandas as pd
import spacy
from spacy.language import Language
from spacy.tokens import Doc, Token


LOGGER = logging.getLogger("mask_trial2")
MASK_TOKEN = "[MASK]"
ALLOWED_POS = {"ADJ", "ADV", "VERB"}
SAFE_VERBS = {
    "present",
    "complain",
    "develop",
    "report",
    "show",
    "include",
    "remain",
    "appear",
    "continue",
    "experience",
    "notice",
    "observe",
    "improve",
}
PROTECTED_TERMS = {
    "death",
    "dead",
    "died",
    "recovered",
    "recovery",
    "discharged",
    "diagnosis",
    "diagnosed",
    "treated",
    "treatment",
    "ribavirin",
    "antibiotics",
    "fever",
    "lassa",
    "chapare",
    "virus",
    "positive",
    "negative",
    "pregnant",
    "pregnancy",
}
SEX_TERMS = {
    "male",
    "female",
    "man",
    "woman",
    "boy",
    "girl",
    "gentleman",
    "lady",
    "he",
    "she",
    "his",
    "her",
    "him",
    "hers",
}
ID_PATTERN = re.compile(
    r"\b(?:patient\s*)?(?:id|mrn|record|case)\s*"
    r"(?:no\.?|number|#|:)?\s*[A-Z0-9-]{3,}\b",
    re.IGNORECASE,
)
AGE_PATTERN = re.compile(
    r"\b\d{1,3}\s*(?:-\s*)?(?:year|yr)s?\s*(?:-\s*)?old\b"
    r"|\b\d{1,3}\s*(?:y/o|yo)\b",
    re.IGNORECASE,
)
DATE_PATTERN = re.compile(
    r"\b(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4})\b"
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)"
    r"[a-z]*\.?\s+\d{1,2}(?:\s*(?:st|nd|rd|th))?,?\s+\d{4}\b"
    r"|\b\d{1,2}\s*(?:st|nd|rd|th)?,?\s+\d{4}\b",
    re.IGNORECASE,
)

MaskCandidate = dict[str, Any]


@dataclass(frozen=True)
class MaskingConfig:
    """Runtime settings for mask candidate collection."""

    mask_ratio: float = 0.15
    random_seed: int = 42
    mask_age: bool = True
    mask_sex: bool = True
    mask_dates: bool = True
    mask_names: bool = True
    mask_locations: bool = True
    mask_organizations: bool = True
    mask_ids: bool = True
    mask_low_risk_wording: bool = True


def positive_int(value: str) -> int:
    """Parse a positive integer command-line value."""
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be an integer greater than or equal to 1")
    return parsed


def ratio(value: str) -> float:
    """Parse a mask ratio between zero and one."""
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0.0 and 1.0")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Mask clinical case text with spaCy and rule-based constraints."
    )
    parser.add_argument(
        "--input-csv",
        type=Path,
        default=Path("example/carte_example.csv"),
        help="Input case CSV (default: example/carte_example.csv).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("mask_trial_outputs"),
        help="Directory for timestamped CSV and TXT outputs.",
    )
    parser.add_argument(
        "--target-column",
        default="Case presentation EN",
        help="CSV column containing English clinical case text.",
    )
    parser.add_argument(
        "--max-rows",
        type=positive_int,
        default=None,
        help="Process only the first N non-empty rows.",
    )
    parser.add_argument(
        "--mask-ratio",
        type=ratio,
        default=0.15,
        help="Fraction of eligible low-risk wording tokens to mask.",
    )
    parser.add_argument("--random-seed", type=int, default=42)
    parser.add_argument("--spacy-model", default="en_core_web_sm")
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default="INFO",
    )
    return parser.parse_args(argv)


def read_csv_with_encoding_fallback(csv_path: Path) -> pd.DataFrame:
    """Read CSV with common UTF-8 and Japanese Windows encodings."""
    encodings = ("utf-8", "utf-8-sig", "cp932", "shift_jis")
    for encoding in encodings:
        try:
            frame = pd.read_csv(csv_path, encoding=encoding)
            LOGGER.info("Loaded CSV with encoding=%s", encoding)
            return frame
        except UnicodeDecodeError as exc:
            LOGGER.warning("Failed to decode with encoding=%s: %s", encoding, exc)
        except Exception:
            LOGGER.exception("Failed to load %s with encoding=%s", csv_path, encoding)
            raise
    raise ValueError(f"Failed to decode {csv_path} with encodings: {encodings}")


def load_cases(
    csv_path: Path, target_column: str, max_rows: int | None
) -> pd.DataFrame:
    """Load non-empty case text rows and retain their source row IDs."""
    if not csv_path.is_file():
        raise FileNotFoundError(f"Input CSV was not found: {csv_path}")
    frame = read_csv_with_encoding_fallback(csv_path)
    if target_column not in frame.columns:
        raise ValueError(
            f"Column not found: {target_column}. Columns: {frame.columns.tolist()}"
        )
    cases = frame[frame[target_column].notna()].copy()
    if max_rows is not None:
        cases = cases.head(max_rows)
    cases = cases.reset_index().rename(columns={"index": "source_row_id"})
    LOGGER.info("Loaded %d case rows from %s", len(cases), csv_path)
    return cases


def load_spacy_model(model_name: str) -> Language:
    """Load spaCy and report an actionable error when its model is missing."""
    try:
        return spacy.load(model_name)
    except OSError as exc:
        LOGGER.error(
            "spaCy model '%s' is unavailable. Run: python -m spacy download %s",
            model_name,
            model_name,
        )
        raise RuntimeError(f"Failed to load spaCy model: {model_name}") from exc


def get_age_constraint(value: str) -> str:
    """Return an age constraint that preserves the broad age category."""
    match = re.search(r"\d{1,3}", value)
    if not match:
        return "replace with a plausible age expression in the same clinical age category"
    age = int(match.group())
    category = "pediatric" if age < 18 else "adult" if age < 65 else "older_adult"
    return (
        f"replace only within the same age category ({category}) "
        "and keep the year-old wording"
    )


def constraint_for(mask_type: str, original_value: str) -> str:
    """Return the replacement constraint for a mask type."""
    if mask_type == "AGE":
        return get_age_constraint(original_value)
    constraints = {
        "NAME": "replace with a fictitious person name or neutral placeholder",
        "SEX": (
            "prefer preserving the original sex unless all related pronouns "
            "and sex-specific facts are updated consistently"
        ),
        "DATE": "shift all dates in the same case by the same offset and preserve chronology",
        "LOCATION": "replace with a fictitious or coarse location that does not identify a person",
        "ORGANIZATION": "replace with a fictitious institution name or neutral placeholder",
        "ID": "replace with a fictitious identifier using the same broad format",
        "LOW_RISK_WORDING": "replace only with a clinically neutral word of the same role",
    }
    return constraints.get(
        mask_type, "replace conservatively without changing clinical facts"
    )


def make_candidate(
    start: int, end: int, text: str, mask_type: str, source: str
) -> MaskCandidate:
    """Create one mask candidate with replacement metadata."""
    original_value = text[start:end]
    return {
        "start_char": int(start),
        "end_char": int(end),
        "original_value": original_value,
        "mask_type": mask_type,
        "source": source,
        "constraint_rule": constraint_for(mask_type, original_value),
    }


def overlaps(candidate: MaskCandidate, selected: list[MaskCandidate]) -> bool:
    """Return whether a candidate overlaps an already selected span."""
    start = int(candidate["start_char"])
    end = int(candidate["end_char"])
    return any(
        start < int(item["end_char"]) and end > int(item["start_char"])
        for item in selected
    )


def add_non_overlapping(candidates: list[MaskCandidate]) -> list[MaskCandidate]:
    """Keep candidates in priority order while removing overlaps."""
    selected: list[MaskCandidate] = []
    for candidate in candidates:
        if int(candidate["start_char"]) < int(candidate["end_char"]) and not overlaps(
            candidate, selected
        ):
            selected.append(candidate)
    return sorted(selected, key=lambda item: int(item["start_char"]))


def is_safe_low_risk_candidate(token: Token) -> bool:
    """Return whether a token is eligible for wording variation."""
    text = token.text.strip()
    lower = text.lower()
    if not text or token.is_space or token.is_punct or token.is_stop:
        return False
    if token.ent_type_ or token.like_num or re.search(r"\d", text):
        return False
    if lower in PROTECTED_TERMS or lower in SEX_TERMS:
        return False
    if token.pos_ not in ALLOWED_POS:
        return False
    if token.pos_ == "VERB" and token.lemma_.lower() not in SAFE_VERBS:
        return False
    return len(text) > 3


def collect_regex_candidates(
    text: str, config: MaskingConfig
) -> list[MaskCandidate]:
    """Collect regex-based anonymization candidates."""
    candidates: list[MaskCandidate] = []
    patterns = (
        (config.mask_ids, ID_PATTERN, "ID"),
        (config.mask_age, AGE_PATTERN, "AGE"),
        (config.mask_dates, DATE_PATTERN, "DATE"),
    )
    for enabled, pattern, mask_type in patterns:
        if enabled:
            candidates.extend(
                make_candidate(match.start(), match.end(), text, mask_type, "regex")
                for match in pattern.finditer(text)
            )
    return candidates


def collect_spacy_entity_candidates(
    doc: Doc, text: str, config: MaskingConfig
) -> list[MaskCandidate]:
    """Collect spaCy entity and lexicon candidates."""
    candidates: list[MaskCandidate] = []
    for entity in doc.ents:
        mask_type = ""
        if config.mask_names and entity.label_ == "PERSON":
            mask_type = "NAME"
        elif config.mask_dates and entity.label_ == "DATE":
            mask_type = "DATE"
        elif config.mask_locations and entity.label_ in {"GPE", "LOC", "FAC"}:
            mask_type = "LOCATION"
        elif config.mask_organizations and entity.label_ == "ORG":
            mask_type = "ORGANIZATION"
        if mask_type:
            candidates.append(
                make_candidate(
                    entity.start_char,
                    entity.end_char,
                    text,
                    mask_type,
                    f"spacy:{entity.label_}",
                )
            )
    if config.mask_sex:
        candidates.extend(
            make_candidate(
                token.idx,
                token.idx + len(token.text),
                text,
                "SEX",
                "lexicon",
            )
            for token in doc
            if token.text.lower() in SEX_TERMS
        )
    return candidates


def collect_low_risk_wording_candidates(
    doc: Doc,
    text: str,
    selected: list[MaskCandidate],
    config: MaskingConfig,
    seed: int,
) -> list[MaskCandidate]:
    """Sample low-risk wording candidates outside protected spans."""
    if not config.mask_low_risk_wording:
        return []
    candidates: list[MaskCandidate] = []
    for token in doc:
        item = make_candidate(
            token.idx,
            token.idx + len(token.text),
            text,
            "LOW_RISK_WORDING",
            "spacy:pos",
        )
        if is_safe_low_risk_candidate(token) and not overlaps(item, selected):
            candidates.append(item)
    if not candidates or config.mask_ratio == 0:
        return []
    rng = random.Random(seed)
    count = max(1, round(len(candidates) * config.mask_ratio))
    return rng.sample(candidates, k=min(count, len(candidates)))


def apply_masks(
    text: str, candidates: list[MaskCandidate]
) -> tuple[str, list[MaskCandidate]]:
    """Apply selected spans and assign one-based mask indexes."""
    selected = add_non_overlapping(candidates)
    parts: list[str] = []
    metadata: list[MaskCandidate] = []
    cursor = 0
    for mask_index, candidate in enumerate(selected, start=1):
        start = int(candidate["start_char"])
        end = int(candidate["end_char"])
        parts.extend((text[cursor:start], MASK_TOKEN))
        cursor = end
        item = dict(candidate)
        item.update({"mask_index": mask_index, "mask_token": MASK_TOKEN})
        metadata.append(item)
    parts.append(text[cursor:])
    return "".join(parts), metadata


def mask_text_extended(
    text: str, nlp: Language, config: MaskingConfig, seed: int
) -> tuple[str, list[MaskCandidate]]:
    """Mask anonymization fields and low-risk wording with metadata."""
    doc = nlp(text)
    priority = collect_regex_candidates(text, config)
    priority.extend(collect_spacy_entity_candidates(doc, text, config))
    selected_priority = add_non_overlapping(priority)
    low_risk = collect_low_risk_wording_candidates(
        doc, text, selected_priority, config, seed
    )
    return apply_masks(text, selected_priority + low_risk)


def summarize_metadata(metadata: list[MaskCandidate], key: str) -> str:
    """Join metadata values for compact CSV columns."""
    return "; ".join(str(item.get(key, "")) for item in metadata)


def build_masked_dataset(
    cases: pd.DataFrame,
    target_column: str,
    nlp: Language,
    config: MaskingConfig,
) -> pd.DataFrame:
    """Create the mask-trial2-compatible row-level dataset."""
    rows: list[dict[str, object]] = []
    for row_number, row in cases.iterrows():
        original_text = str(row[target_column])
        masked_text, metadata = mask_text_extended(
            original_text, nlp, config, config.random_seed + int(row_number)
        )
        rows.append(
            {
                "source_row_id": row["source_row_id"],
                "PMCID": row.get("PMCID", ""),
                "PMID": row.get("PMID", ""),
                "Case #": row.get("Case #", ""),
                "disease": row.get("Disease", ""),
                "title": row.get("Title", ""),
                "source_column": target_column,
                "original_text": original_text,
                "masked_text": masked_text,
                "masked_token_count": len(metadata),
                "masked_tokens": summarize_metadata(metadata, "original_value"),
                "mask_types": summarize_metadata(metadata, "mask_type"),
                "original_mask_values": summarize_metadata(
                    metadata, "original_value"
                ),
                "replacement_constraints": summarize_metadata(
                    metadata, "constraint_rule"
                ),
                "mask_metadata": json.dumps(metadata, ensure_ascii=False),
                "mask_ratio": config.mask_ratio,
                "masking_version": "mask-trial2",
            }
        )
    return pd.DataFrame(rows)


def save_outputs(masked_cases: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    """Save timestamped CSV and readable TXT outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_output = output_dir / f"clinical_case_mask_trial2_{timestamp}.csv"
    txt_output = output_dir / f"clinical_case_mask_trial2_{timestamp}.txt"
    try:
        masked_cases.to_csv(csv_output, index=False, encoding="utf-8")
        lines: list[str] = []
        for _, row in masked_cases.iterrows():
            lines.extend(
                [
                    "=" * 80,
                    f"source_row_id: {row['source_row_id']}",
                    f"disease: {row['disease']}",
                    f"masked_token_count: {row['masked_token_count']}",
                    f"mask_types: {row['mask_types']}",
                    "",
                    "Original:",
                    str(row["original_text"]),
                    "",
                    "Masked:",
                    str(row["masked_text"]),
                    "",
                    f"Original mask values: {row['original_mask_values']}",
                    "",
                    f"Replacement constraints: {row['replacement_constraints']}",
                    "",
                ]
            )
        txt_output.write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        LOGGER.exception("Failed to save outputs under %s", output_dir)
        raise
    LOGGER.info("Saved CSV: %s", csv_output)
    LOGGER.info("Saved TXT: %s", txt_output)
    return csv_output, txt_output


def main(argv: Sequence[str] | None = None) -> int:
    """Run clinical text masking from the command line."""
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = MaskingConfig(
            mask_ratio=args.mask_ratio, random_seed=args.random_seed
        )
        cases = load_cases(args.input_csv, args.target_column, args.max_rows)
        nlp = load_spacy_model(args.spacy_model)
        masked = build_masked_dataset(cases, args.target_column, nlp, config)
        csv_output, txt_output = save_outputs(masked, args.output_dir)
    except Exception as exc:
        LOGGER.error("Masking failed: %s", exc)
        return 1
    print(f"Rows processed: {len(masked)}")
    print(f"CSV saved: {csv_output}")
    print(f"TXT saved: {txt_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
