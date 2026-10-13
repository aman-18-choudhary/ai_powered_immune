# Synthetic complaint dataset (`complaints_synthetic_v1.csv`)

**SYNTHETIC. Every row is generated from hand-authored templates by slot filling. No real
complaint, person, phone number, account or URL was used. Metrics measured on this data describe
the generator, not real-world performance, and must never be quoted as such.** Phone numbers are
random digit strings and may coincide with real numbers by chance; URLs use random suffixes.

Regenerate (byte-identical, seeded):

```
cd services/complaints && python -m complaints.ml.synth        # writes this CSV
```

Generator: `complaints/ml/synth.py` (logic) and `complaints/ml/synth_templates.py` (text).
Seed `20261007`, about 3,700 rows, about 1.1 MB.

## Contents

* 9 categories: DIGITAL_ARREST, UPI_FRAUD, PHISHING, OTP_SCAM, BANK_FRAUD, INVESTMENT_SCAM,
  JOB_SCAM, SOCIAL_MEDIA_FRAUD, OTHER. OTHER holds non-fraud and ambiguous complaints (lost
  parcels, refund delays, unclear short reports) and hard negatives: a genuine bank debit alert
  and a genuine delivery OTP (`hard_negative=true`), which share vocabulary with fraud classes.
* 10 scenario families per category, 4-5 templates each, in English, romanised Hindi and (for 5
  families per category) Devanagari. Roughly 56% English, 35% romanised Hindi, 9% Devanagari.
  Bank, app and agency names inside Devanagari text stay in Latin script.
* `text` is present on every row. `suspect_phone`, `suspicious_url`, `upi_id`, `amount`,
  `payment_method`, `city`/`state`, `social_platform`/`social_account`, `suspect_email` are
  independently missing with category-specific probabilities (see `FIELD_P`). Examples:
  phishing rows often carry a URL, OTP scams a phone, investment scams UPI id and amount,
  social-media fraud a platform and account, job scams an advance-fee amount.
* `synthetic` is `true` on every row (a column rather than a comment line, so CSV readers work).

## Columns

`row_id, text, language (en|hi|dv), label, true_category, label_flipped, template_id, group_id,
split, augmented, parent_id, hard_negative, suspect_phone, suspicious_url, upi_id, amount,
payment_method, city, state, social_platform, social_account, suspect_email, synthetic`

## Split (leakage guard)

* `group_id` is the scenario family (`<CATEGORY>.<family>`). `split` is assigned by family:
  per category 6 families train, 2 validation, 2 test (about 60/20/20 of rows). A family in the
  test split contributes no template and no row to training; tests assert zero overlap of
  `group_id` and `template_id` and a near-duplicate guard (Jaccard >= 0.8 on word sets).
* Slot pools (banks, apps, cities, amounts) are shared across splits; only the sentence
  templates are held out. This is a real limit: the held-out test is held-out *scenarios written
  by the same author*, not unseen real-world language.

## Augmentation (decision)

Augmentation (typos, casing, punctuation stripping, transliteration variants of common
romanised Hindi words) is applied at generation time to about 12% of rows. The augmented row has
`augmented=true`, `parent_id` and inherits `group_id`, `template_id` and `split` of its parent, so
an augmented variant is never on the other side of a split from its source. Evaluation also
applies fresh perturbations to the untouched test split (see `MODEL_CARD.md`).

## Label noise

About 3% of base rows have `label` flipped to a plausible confusable category (for example
UPI_FRAUD to BANK_FRAUD, PHISHING to OTP_SCAM), `label_flipped=true`; `true_category` keeps the
generator's category. Reported metrics use the observed `label` (the noisy one); the clean-label
accuracy is also stored in `category-v1.metrics.json`.
