# ScamShield Immune: slide deck

`ScamShield-Immune.pptx` is a 14-slide, 16:9, dark-theme deck (PDF export: `ScamShield-Immune.pdf`,
contact sheet: `preview.png`). Everything in it is simulator data and says so in each footer.

## Slide order

| # | Slide | Message |
|---|---|---|
| 1 | Title | One bank's detection protects every bank |
| 2 | The problem | 1.14 million complaints (2023) and Rs 1,776 crore (first nine months of 2024), both from the challenge statement |
| 3 | The insight | An immune system for payments: detect once, protect everywhere |
| 4 | What we built | Reflex, memory, brain, citizen shield, evidence ledger (drawn natively) |
| 5 | The hero run | Eight steps, 71 s at 1x, with player screenshots |
| 6 | Under the hood | Real services and models, simulated world |
| 7 | Results | Precision, recall, FPR with CIs, per-rail FPR (NEFT callout), ablation, lead time; native charts |
| 8 | Hard-case honesty | Hero 39/40 allow vs 40/40 held; delay sensitivity chart |
| 9 | Auditability | Hash chain, signed checkpoints, offline verifier (PASS_PINNED), what it does not prove |
| 10 | Privacy and safety | Tokens and hashes, PII guard, audit off the hot path, fail-safe, adversarial review |
| 11 | Three audiences | Command centre, analyst, citizen (4 languages), accessibility |
| 12 | Scalability and ops | Partition keys, latency, compose profiles, what was not run |
| 13 | Limits and next steps | Honest limits, pilot path |
| 14 | Close | `make demo`, repository placeholder |

## Speaker notes policy

Every slide has speaker notes with (1) the talk track and (2) the source file for each number or
claim. Slide bodies are kept to about 40 words or fewer. The two challenge-statement figures on
slide 2 are quoted from the brief and are not in the repository's files; the notes say so. Replace the
repository URL placeholder on slide 14 before sharing.

## How it was generated

`build_deck.py` (python-pptx, Pillow; no network) writes the .pptx. Chart values on slides 7 and 8
are read from `services/demo_server/demo_server/data/benchmark_summary.json` at build time;
screenshots come from `docs/screenshots/`. Fonts are Arial and Courier New (installed with Office).

```bash
python3 docs/deck/build_deck.py          # also prints a per-slide body word count
cd docs/deck && soffice --headless --convert-to pdf ScamShield-Immune.pptx
pdftoppm -png -r 100 ScamShield-Immune.pdf /tmp/slide      # inspect each slide
```

The preview and PDF were produced with LibreOffice and poppler; every slide was inspected as an
image. PowerPoint may lay text out slightly differently.
