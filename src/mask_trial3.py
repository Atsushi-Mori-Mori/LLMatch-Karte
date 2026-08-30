from __future__ import annotations

import json
import logging
from pathlib import Path
import re
from typing import Any, Sequence

import pandas as pd
from spacy.language import Language
from spacy.tokens import Doc, Span, Token

import mask_trial2 as trial2


LOGGER = logging.getLogger("mask_trial3")
MaskCandidate = dict[str, Any]

# These terms carry clinical facts and must not be offered to the filling model.
MEDICAL_TERMS = {
    "antigen",
    "antibiotic",
    "antibiotics",
    "arthralgia",
    "assay",
    "chapare",
    "diagnosis",
    "diagnosed",
    "fever",
    "hanta",
    "hantaan",
    "hantavirus",
    "hantaviral",
    "hunta",
    "igg",
    "igm",
    "lassa",
    "lasv",
    "machupo",
    "myalgia",
    "orthohantavirus",
    "pcr",
    "pregnancy",
    "pregnant",
    "puumala",
    "puuv",
    "ribavirin",
    "rt-pcr",
    "treatment",
    "treated",
    "vero",
    "virus",
    "vomiting",
}
NEGATION_TERMS = {
    "absent",
    "denied",
    "denies",
    "negative",
    "neither",
    "never",
    "no",
    "none",
    "not",
    "without",
}
SEVERITY_TERMS = {
    "acute",
    "critical",
    "fatal",
    "mild",
    "moderate",
    "severe",
    "stable",
    "worsening",
}
TEMPORALITY_TERMS = {
    "after",
    "before",
    "during",
    "earlier",
    "initial",
    "initially",
    "later",
    "prior",
    "subsequent",
    "subsequently",
}
OUTCOME_TERMS = {
    "dead",
    "death",
    "died",
    "discharged",
    "fatal",
    "recovered",
    "recovery",
    "survived",
}
POLARITY_TERMS = {"positive", "negative", "normal", "abnormal"}
SAFE_LOW_RISK_WORDS = {
    "also",
    "apparently",
    "commonly",
    "generally",
    "mainly",
    "primarily",
    "reportedly",
    "typically",
}
MEDICAL_SUFFIXES = (
    "algia",
    "emia",
    "itis",
    "osis",
    "pathy",
    "pnea",
    "rrhea",
    "virus",
)
MEDICAL_PATTERN = re.compile(
    r"\b(?:RT-?PCR|PCR|Ig[AGM]|Vero\s+E6|[A-Za-z]+virus)\b",
    re.IGNORECASE,
)


def token_flags(token: Token) -> dict[str, bool]:
    """Return clinical-safety flags used by masking and filling."""
    lower = token.text.lower()
    return {
        "medical_concept": is_medical_token(token),
        "negation_sensitive": lower in NEGATION_TERMS,
        "severity_sensitive": lower in SEVERITY_TERMS,
        "temporality_sensitive": lower in TEMPORALITY_TERMS,
        "outcome_sensitive": lower in OUTCOME_TERMS,
        "polarity_sensitive": lower in POLARITY_TERMS,
    }


def is_medical_token(token: Token) -> bool:
    """Conservatively identify medical terms without adding a new model dependency."""
    lower = token.text.lower()
    lemma = token.lemma_.lower()
    if lower in MEDICAL_TERMS or lemma in MEDICAL_TERMS:
        return True
    if lower.endswith(MEDICAL_SUFFIXES):
        return True
    return bool(MEDICAL_PATTERN.fullmatch(token.text))


def is_medical_span(span: Span) -> bool:
    """Return whether any token or phrase pattern in a span is clinical."""
    return bool(MEDICAL_PATTERN.search(span.text)) or any(
        is_medical_token(token) for token in span
    )


def span_for_candidate(doc: Doc, candidate: MaskCandidate) -> Span | None:
    """Resolve candidate offsets to a spaCy span for metadata enrichment."""
    return doc.char_span(
        int(candidate["start_char"]),
        int(candidate["end_char"]),
        alignment_mode="expand",
    )


def enrich_candidate(
    candidate: MaskCandidate, doc: Doc, *, protected: bool = False
) -> MaskCandidate:
    """Attach linguistic and safety metadata consumed by trial4."""
    item = dict(candidate)
    span = span_for_candidate(doc, item)
    token = span[0] if span and len(span) == 1 else None
    flags = token_flags(token) if token is not None else {
        "medical_concept": bool(span and is_medical_span(span)),
        "negation_sensitive": False,
        "severity_sensitive": False,
        "temporality_sensitive": False,
        "outcome_sensitive": False,
        "polarity_sensitive": False,
    }
    item.update(
        {
            "pos": token.pos_ if token is not None else "",
            "lemma": token.lemma_ if token is not None else "",
            "entity_type": (
                token.ent_type_
                if token is not None
                else ",".join(sorted({part.ent_type_ for part in span if part.ent_type_}))
                if span
                else ""
            ),
            **flags,
            "protected": protected or bool(flags["medical_concept"]),
        }
    )
    return item


def collect_spacy_entity_candidates(
    doc: Doc, text: str, config: trial2.MaskingConfig
) -> list[MaskCandidate]:
    """Collect privacy entities but never mask spans recognized as medical."""
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
        if not mask_type:
            continue
        if is_medical_span(entity):
            LOGGER.info(
                "Protected medical span from spaCy %s classification: %s",
                entity.label_,
                entity.text,
            )
            continue
        candidate = trial2.make_candidate(
            entity.start_char,
            entity.end_char,
            text,
            mask_type,
            f"spacy:{entity.label_}",
        )
        candidates.append(enrich_candidate(candidate, doc))
    if config.mask_sex:
        for token in doc:
            if token.text.lower() not in trial2.SEX_TERMS:
                continue
            candidate = trial2.make_candidate(
                token.idx,
                token.idx + len(token.text),
                text,
                "SEX",
                "lexicon",
            )
            candidates.append(enrich_candidate(candidate, doc))
    return candidates


def is_safe_low_risk_candidate(token: Token) -> bool:
    """Allow only neutral wording with no clinical or semantic safety flag."""
    flags = token_flags(token)
    if any(flags.values()):
        return False
    if (
        not token.text.strip()
        or token.is_space
        or token.is_punct
        or token.ent_type_
        or token.like_num
        or re.search(r"\d", token.text)
    ):
        return False
    lower = token.text.lower()
    if lower in trial2.PROTECTED_TERMS or lower in trial2.SEX_TERMS:
        return False
    # Trial3 deliberately narrows augmentation to a small neutral adverb set.
    return token.pos_ == "ADV" and lower in SAFE_LOW_RISK_WORDS


def collect_low_risk_wording_candidates(
    doc: Doc,
    text: str,
    selected: list[MaskCandidate],
    config: trial2.MaskingConfig,
    seed: int,
) -> list[MaskCandidate]:
    """Sample safe wording candidates and retain their classification."""
    if not config.mask_low_risk_wording:
        return []
    candidates: list[MaskCandidate] = []
    for token in doc:
        if not is_safe_low_risk_candidate(token):
            continue
        item = trial2.make_candidate(
            token.idx,
            token.idx + len(token.text),
            text,
            "LOW_RISK_WORDING",
            "spacy:pos",
        )
        item = enrich_candidate(item, doc)
        if not trial2.overlaps(item, selected):
            candidates.append(item)
    if not candidates or config.mask_ratio == 0:
        return []
    import random

    rng = random.Random(seed)
    count = max(1, round(len(candidates) * config.mask_ratio))
    return rng.sample(candidates, k=min(count, len(candidates)))


def mask_text_extended(
    text: str, nlp: Language, config: trial2.MaskingConfig, seed: int
) -> tuple[str, list[MaskCandidate]]:
    """Mask privacy fields and strictly neutral wording with rich metadata."""
    doc = nlp(text)
    priority = [
        enrich_candidate(item, doc)
        for item in trial2.collect_regex_candidates(text, config)
    ]
    priority.extend(collect_spacy_entity_candidates(doc, text, config))
    selected_priority = trial2.add_non_overlapping(priority)
    low_risk = collect_low_risk_wording_candidates(
        doc, text, selected_priority, config, seed
    )
    return trial2.apply_masks(text, selected_priority + low_risk)


def build_masked_dataset(
    cases: pd.DataFrame,
    target_column: str,
    nlp: Language,
    config: trial2.MaskingConfig,
) -> pd.DataFrame:
    """Create a trial4-compatible dataset while preserving trial2 columns."""
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
                "masked_tokens": trial2.summarize_metadata(metadata, "original_value"),
                "mask_types": trial2.summarize_metadata(metadata, "mask_type"),
                "original_mask_values": trial2.summarize_metadata(
                    metadata, "original_value"
                ),
                "replacement_constraints": trial2.summarize_metadata(
                    metadata, "constraint_rule"
                ),
                "mask_metadata": json.dumps(metadata, ensure_ascii=False),
                "mask_ratio": config.mask_ratio,
                "masking_version": "mask-trial3",
                "protection_policy": (
                    "medical terms, negation, severity, temporality, polarity, "
                    "and outcomes are excluded from LOW_RISK_WORDING"
                ),
            }
        )
    return pd.DataFrame(rows)


def save_outputs(masked_cases: pd.DataFrame, output_dir: Path) -> tuple[Path, Path]:
    """Save trial3 outputs without changing trial2 artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = trial2.datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_output = output_dir / f"clinical_case_mask_trial3_{timestamp}.csv"
    txt_output = output_dir / f"clinical_case_mask_trial3_{timestamp}.txt"
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
                    f"Mask metadata: {row['mask_metadata']}",
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
    """Run safety-aware clinical text masking."""
    args = trial2.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = trial2.MaskingConfig(
            mask_ratio=args.mask_ratio, random_seed=args.random_seed
        )
        cases = trial2.load_cases(args.input_csv, args.target_column, args.max_rows)
        nlp = trial2.load_spacy_model(args.spacy_model)
        masked = build_masked_dataset(cases, args.target_column, nlp, config)
        csv_output, txt_output = save_outputs(masked, args.output_dir)
    except Exception as exc:
        LOGGER.exception("Masking failed: %s", exc)
        return 1
    print(f"Rows processed: {len(masked)}")
    print(f"CSV saved: {csv_output}")
    print(f"TXT saved: {txt_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
