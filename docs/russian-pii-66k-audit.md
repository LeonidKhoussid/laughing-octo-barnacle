# russian-pii-66k local data-quality audit

Audited `wolframko/russian-pii-66k` at revision `d458b5a2e299d2e16adbd3b3921487f652b06a96`. The pinned Parquet was downloaded locally and fully scanned. SHA-256: `aeb04b15604264291a1b2c22baed7584ff5bcb679203f1150eb49953287534c9`. No dataset rows were sent to external services.

## What was checked

The scan read all 65,652 rows and 210,647 annotations from the Parquet file. It checked schema and row count, label/language/locale distributions, empty text and masks, malformed fields, bounds, overlapping spans, annotation-value equality under code-point, UTF-16, and UTF-8 offset interpretations, exact duplicates, and canonical templates. Canonicalization replaced only verified, non-empty, non-overlapping code-point-aligned values with label placeholders. It never repaired or normalized an annotation.

## Results

- Schema columns: `source_text, privacy_mask, language, locale`; expected schema-column names match: `True`.
- Parquet metadata rows: 65,652; rows scanned: 65,652.
- Empty source texts: 0; rows with empty/no PII mask: 0; null masks: 0.
- Annotations: malformed 0; empty 0; out-of-bounds/reversed 0; overlap rows/pairs 0/0.
- Valid-bound span values exactly matched code-point offsets: 210,647/210,647 (100.00%). UTF-16 exact: 210,647; UTF-8 exact: 45. Non-BMP code points: 0; valid spans after one: 0. These alternative figures are diagnostic only; where no non-BMP code point precedes an offset, code-point and UTF-16 indexing produce the same slice.
- Exact source-text duplicates beyond first occurrence: 0 in 0 groups. Canonical-template duplicates beyond first: 470 among 65,652 eligible rows, in 307 groups; 470 duplicate instances are beyond exact text repetition.
- Deterministic safe repair candidates: 0. A candidate means annotated non-empty value occurred exactly once in its source text. No changes were made.

## Label inventory

| Label | Annotations |
|---|---:|
| ACCOUNTNUM | 13,177 |
| BUILDINGNUM | 9,356 |
| CITY | 14,760 |
| CREDITCARDNUMBER | 7,224 |
| DATEOFBIRTH | 9,700 |
| DRIVERLICENSENUM | 5,353 |
| EMAIL | 17,480 |
| GIVENNAME | 26,550 |
| IDCARDNUM | 8,558 |
| PASSWORD | 8,060 |
| SOCIALNUM | 8,184 |
| STREET | 10,464 |
| SURNAME | 22,070 |
| TAXNUM | 11,430 |
| TELEPHONENUM | 18,169 |
| USERNAME | 13,509 |
| ZIPCODE | 6,603 |

## Structural validity versus semantic gold correctness

The structural result is strong: every stored annotation has a non-empty label/value, in-bounds non-overlapping offsets, and its value exactly equals the code-point source slice. This establishes internal serialization and offset consistency only. It does **not** establish that every label is semantically correct, that all PII in each text is labeled, that categories follow AlfaGen policy, or that the records are lawful/approved training material. No external factual or human gold review was performed.

## Eligibility assessment

The file is structurally suitable for controlled evaluation of span parsing and label mapping. This structural audit alone does not establish suitability for training or permission to redistribute trained artifacts. Every row contains at least one annotation, so this is an all-positive corpus with no negative/no-PII examples. The reviewed card/metadata also contain no declared license, provenance, consent basis, or public/private PII classification. Label taxonomy needs an explicit mapping to AlfaGen categories before any training decision. Any future offset mismatches, malformed spans, overlaps, or empty annotations should be quarantined or separately reviewed; do not silently apply candidate offsets.

[`../artifacts/russian-pii-66k-audit.json`](../artifacts/russian-pii-66k-audit.json) contains complete aggregate counts, per-label results, hashes, sampled row indices, digests of full issue-index lists, and sanitized mismatch examples. It contains no source text or clear PII values. The exact audit script is preserved at `../artifacts/audit_russian_pii.py`; it reads the pinned Parquet from `/tmp/alfagen-russian-pii-review/` and writes temporary audit outputs there. The executed interpreter was `/tmp/alfagen-russian-pii-review/pyarrow-venv/bin/python`, with PyArrow 19.0.1. Runtime application dependencies were not changed.

## Fit with the current service

The running pipeline already uses two local trained models: RuBERT-tiny news NER (INT8 ONNX) for additional person candidates, and mDeBERTa MNLI/XNLI (FP32 ONNX) for mention-specific context decisions. Structured detectors and deterministic private-record checks remain part of the pipeline. Source: `app/detectors/semantic.py`, `app/nlp/model_runtime.py`, and `configs/semantic-models.json`.

This dataset is a candidate for adapting entity recognition. It is not direct supervision for the separate public/private context decision. Every document has at least one PII annotation; unannotated tokens still supply token-level negatives, but there are no wholly PII-free documents or explicit public/private decision labels. Whether its unannotated text contains suitable public-name examples was not established by this structural audit.

The current semantic adapter accepts only NER label `PER`. Replacing weights with a new multi-class PII model would therefore require an explicit adapter and offset/label regression checks; a model-file swap is insufficient.

| Dataset labels | Candidate service mapping | Review needed |
| --- | --- | --- |
| GIVENNAME, SURNAME | FULL_NAME | Preserve each labeled span; merge only verified contiguous components belonging to one person. Patronymics are not a separate source label. |
| CITY, STREET, BUILDINGNUM, ZIPCODE | ADDRESS components | Preserve service words and distinguish personal addresses from public institutions. |
| EMAIL, TELEPHONENUM, CREDITCARDNUMBER, DATEOFBIRTH, DRIVERLICENSENUM | EMAIL, PHONE, CARD, BIRTH_DATE, DRIVER_LICENSE | Review semantic label quality and format/domain coverage before training. |
| TAXNUM, IDCARDNUM | Potential INN, PASSPORT | Country locale alone does not establish that every tax/identity number has the required Russian document semantics. |
| ACCOUNTNUM, PASSWORD, USERNAME, SOCIALNUM | No automatic mapping to existing categories | Do not relabel account numbers as cards, passwords as PINs, or social identifiers as INN. New categories require an explicit policy decision. |

The dataset has 17 labels, but these are **not** the service's 17 required categories. It has no distinct labels for BIRTH_PLACE, CITIZENSHIP, PASSPORT_ISSUER, DEPARTMENT_CODE, PASSPORT_ISSUE_DATE, CVV, PIN, or CARDHOLDER_NAME. Its presence cannot establish coverage of those categories.

## Recommended experiment, not an implemented model change

1. Clarify dataset licensing/provenance before incorporating it into a distributed trained artifact. Review a stratified sample for missed PII, incorrect labels, document formats, and generated-template artifacts; valid offsets alone do not establish good gold labels.
2. Freeze the label mapping and group splits by templates and near-duplicate text before training. The 307 exact canonical-template groups found here are a lower bound on related examples, not a complete paraphrase detector. Keep the final test set untouched.
3. Fine-tune a trainable compact NER model in an isolated Linux training environment. The active quantized ONNX weights are inference artifacts. Do not repeat the crashing macOS Torch export path.
4. Separately prepare reviewed, mention-level public/private/uncertain examples: public authors and institutional contacts, private people with the same names, mixed subjects, private records containing book titles, and truly PII-free text. These address the user's context requirement; assigning every source annotation a public/private label automatically would not.
5. Compare the candidate with the current model on untouched examples using missed sensitive characters, unnecessary masking, category precision/recall, Unicode boundaries, and exact mask/restore. Run export parity and the existing HTTP/security regression tests before changing the active manifest. Measure CPU latency/RPS afterward on a disclosed workload.

Training, export, activation, dataset-based model quality evaluation, and speed optimization were **NOT_RUN** in this audit. No live model, threshold, or detector was changed. The user requested testing first and performance work afterward.

Primary references: [pinned dataset files](https://huggingface.co/datasets/wolframko/russian-pii-66k/tree/d458b5a2e299d2e16adbd3b3921487f652b06a96), [current NER model card](https://huggingface.co/onnx-community/ner-rubert-tiny-news-ONNX), [Hugging Face token classification training guide](https://huggingface.co/docs/transformers/tasks/token_classification).
