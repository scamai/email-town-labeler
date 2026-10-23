<!-- Verbatim copy of the model card (README.md) shipped inside both town-modernbert release packages. -->

# town-labels-modernbert-large

A 7-way email classifier for [email-town-labeler](https://github.com/scamai/email-town-labeler): ModernBERT-large fine-tuned to pick one of
`action_needed, newsletters, promotional, orders, travel, updates, unknown` for an email, from its sender, subject,
mailing-list and calendar flags and the first 2,000 body characters (`email_town_labeler.features.email_text`, max 512 tokens).

## Training data — read this

- **4,176 inbound emails from one person's Gmail inbox** (English, July–October 2026), labeled by a hosted auto-labeling
  tool's six content labels; mail it left unlabeled is `unknown`.
- The mailbox owner consented to publishing these weights. Fine-tuned models can memorise fragments of their training
  data (names, senders, subject lines). Do not use this model to try to recover that data.
- Labels reflect one person's inbox and one tool's taxonomy. Expect it to fit your mail less well than it fits theirs;
  `email-town-labeler` records your corrections so a personal model can be trained later.

## Evaluation

A copy trained on half the emails (2,075, split by thread) was scored on the other half (2,101). The published weights
were then retrained on all 4,176, so these numbers describe the method, not a held-out test of the exact weights.

| metric | value |
|---|---|
| 7-way accuracy (agreement with the labels) | 87.3% (95% CI 85.9–88.8) |
| macro-F1, 5 classes with ≥20 test emails | 0.807 |
| always-"updates" baseline / Gmail-category rules | 40.5% / 60.8% |
| best zero-shot closed-source decision API on a 400-email sample | 67.8% |

Per-class recall / precision (test emails): updates 0.91 / 0.92 (850) · unknown 0.86 / 0.93 (673) ·
promotional 0.90 / 0.77 (384) · action_needed 0.76 / 0.72 (100) · newsletters 0.58 / 0.77 (74) ·
orders 0.87 / 0.93 (15) · travel 0.20 / 0.50 (5).

With the recommended policy below (action_needed at ≥0.07, promotional only at ≥0.8, thresholds chosen on the training
half): action_needed recall 0.88 (precision 0.41), promotional precision 0.82, accuracy 84.3%.

## Use

    email-town-labeler model pull town-modernbert --use

CPU, 6 threads: about 0.2–0.3 s per email (ONNX variant slightly faster, no PyTorch needed).

## License

Apache-2.0, same as the base model (answerdotai/ModernBERT-large).
