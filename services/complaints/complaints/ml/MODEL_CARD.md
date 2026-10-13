# Model card: complaint category classifier `category-v1`

**All training and evaluation data is SYNTHETIC (template generated, see `data/README.md`).
Every number below is a synthetic-data number. It is not a statement about performance on real
complaints, which have not been tested.**

## Intended use

Suggest a fraud category (DIGITAL_ARREST, UPI_FRAUD, PHISHING, OTP_SCAM, BANK_FRAUD,
INVESTMENT_SCAM, JOB_SCAM, SOCIAL_MEDIA_FRAUD, OTHER) for the free text of a complaint, as one
input to a human triage workflow. It abstains (`UNDETERMINED`) when its top class probability is
below `CATEGORY_MIN_CONFIDENCE` (artifact default 0.25, chosen on the validation split). It is not
a decision system, does not assess whether a complaint is genuine, and never changes the risk
score, level or the other analysis stages.

## Model

TF-IDF (word 1-2 grams + character 3-5 grams, digits folded) and multinomial logistic regression
(`class_weight=balanced`, C=1.0), text only. Structured fields (phone, URL, UPI id, amount,
location) are NOT features, so a missing field cannot change the prediction. Artifact
`complaints/ml/artifacts/category-v1.joblib` (about 0.4 MB), SHA-256 pinned in `artifact_pin.py`,
verified at load together with the scikit-learn version and a smoke test; on any failure the
pipeline reports `category_classifier: unavailable` and `classification: null`.

## Data and split

3,680 synthetic rows, 9 categories, 90 scenario families (10 per category) written by hand in
English, romanised Hindi and (modestly) Devanagari. Split by family: 54 families train (2,212
rows), 18 validation (741), 18 test (727); a test family contributes no template to training.
About 3% of labels are deliberately flipped to a confusable class; 12% of rows are augmented copies
(typos, casing) that stay on the same side of the split as their source.

## Procedure

C and the abstention threshold were chosen on the validation split only; the artifact is fitted
on the train split only; the test split was used once for the numbers below.
`python -m complaints.ml.train` reproduces everything (seeded) and rewrites
`artifacts/category-v1.metrics.json`.

## Held-out test metrics (SYNTHETIC, family-grouped split, n=727)

| Metric | Value |
|---|---|
| macro-F1 (argmax, abstention ignored) | 0.715 |
| accuracy (argmax) | 0.714 (0.744 against the generator's clean labels) |
| abstention rate at threshold 0.25 | 0.391 |
| accuracy on non-abstained rows | 0.815 |
| macro-F1 counting an abstention as an error | 0.598 |
| macro-F1 on the SAME test texts with fresh typo/casing noise | 0.683 |
| accuracy by language: en / hi / dv | 0.716 / 0.761 / 0.571 |
| hard negatives (genuine alerts etc., n=44) answered as a fraud category | 0.159 (0.614 answered OTHER, 0.227 abstained) |

Per-class precision/recall/F1 and the confusion matrix are in `category-v1.metrics.json`.
Weakest class: SOCIAL_MEDIA_FRAUD (F1 0.50), confused with investment and job scams.

### Leakage contrast (NOT the headline)

A random row split with the same hyper-parameters gives macro-F1 0.985, because test rows share
template families with training. The gap to 0.715 is the amount by which a leaky evaluation would
have overstated generalisation to unseen scenarios (still within one author's synthetic style).

### Structured fields

The model ignores them, so predictions are identical with them dropped. Test accuracy on rows with
no optional field is 0.571 (n=21) and 0.744 on rows with three or more (n=468); this is
correlation between field presence and scenario in the generator, not model use of fields.

## Known limitations and failure modes

* Template bound: vocabulary comes from one author's templates and slot pools. Real complaints
  are longer, noisier, mixed-language and describe several events.
* Not validated on any real complaint. No claim about real-world accuracy, precision or recall.
* No calibration: `confidence` is the maximum class probability of a regularised linear model and
  must not be read as a likelihood of being right. The threshold is an operating point on
  synthetic validation data.
* Language coverage: Devanagari is the weakest slice and only about 9% of the data; other Indian
  languages and scripts, and Hinglish spelling beyond the listed variants, are not covered.
* Overlapping scams are forced to one label; multi-category complaints will be misrouted or
  abstained.
* High abstention (about 39% here) is the price of not forcing a label on unseen scenarios.
* A text that quotes scam language but is a question or awareness query can be pulled toward
  fraud categories (hard negatives: about 16% answered as fraud).
