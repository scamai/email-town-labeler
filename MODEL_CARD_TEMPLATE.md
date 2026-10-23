# Model card: `<name>@<version>`

<!--
Template for email-town-labeler model packages. Copy to MODEL_CARD.md inside the package, fill in every
section, and list the file (with its sha256) in model.json `files`. Write "none" or "not
measured" rather than deleting a section. Requirements: docs/models.md.
-->

## Summary

| | |
|---|---|
| Name / version | `<name>` / `<version>` |
| Backend | `embedding_probe` / `onnx_classifier` / `ollama_choice` / other |
| Base model | `<org/repo>` at revision `<commit hash>` (license: `<SPDX>`) |
| Package license | `<SPDX>` |
| Classes | `action_needed, newsletters, promotional, orders, travel, updates, unknown` |
| Input | `email_town_labeler.features.email_text`, `max_body_chars = <n>` |
| Size on disk / RAM at inference | `<MB>` / `<MB>` |
| Maintainer / contact | `<name, URL>` |

One paragraph: what this model is, and who it is for.

## Intended use

- Labeling incoming mail in a single user's inbox with email-town-labeler, on the user's own machine.
- Out of scope: <for example, spam/phishing detection, legal or HR decisions, languages not evaluated>.

## Training data

State the source of every example. Private mail only with the owner's explicit, informed
consent to publishing these weights: say so here, and say that the other people in that mail
were not individually asked and that the model may memorise fragments of it.

| Source | License | Examples | How labels were produced |
|---|---|---|---|
| `<public corpus / synthetic generator / consented donation>` | `<SPDX>` | `<n>` | `<annotation process>` |

- Class distribution of the training set: `<counts per class>`.
- Languages: `<list>`.
- Time range of the source mail, if known: `<range>`.
- If any mail was donated: how informed consent was obtained, how third parties in the mail
  were handled, and how the data is stored or was deleted.
- Preprocessing and deduplication: `<steps>`.
- Synthetic data: generator, prompts or templates, and how it was checked for realism.

## Training procedure

- Embedder: `<repo@revision>`, prompt `"<prompt>"`, normalization `<on/off>`.
- Head: `<for example multinomial logistic regression, C = ..., class weights ...>`.
- Hyperparameter selection: `<how; never on the evaluation set>`.
- Code: `<link to the training script, commit>`.

## Evaluation

Protocol: docs/models.md, "Evaluation methodology". Held-out data only.

- Evaluation set: `<name>`, `<n>` messages, split by `<sender domain / thread>`, labels by
  `<process>`, inter-annotator agreement `<value or not measured>`.

| Metric | Value |
|---|---|
| 7-way accuracy | TBD |
| Macro-F1 | TBD |
| Coverage at `min_confidence = 0.55` | TBD |
| Accuracy on covered messages | TBD |

Per class:

| Class | Precision | Recall | F1 | Support |
|---|---|---|---|---|
| action_needed | | | | |
| newsletters | | | | |
| promotional | | | | |
| orders | | | | |
| travel | | | | |
| updates | | | | |
| unknown | | | | |

Confusion matrix (rows = reference, columns = predicted, in the order above):

```
TBD
```

CPU throughput: `<messages/s>` at batch size `<n>` on `<hardware>`.

## Limitations and risks

- Known failure modes: <for example, short personal mail predicted as `updates`>.
- Languages, regions or mail styles under-represented in training data.
- Memorization: what was done to limit it (for example, only a linear head on a frozen public
  embedder) and any privacy evaluation performed.
- Misclassification impact in email-town-labeler is limited to a wrong label under `email-town-labeler/`; it
  never hides or deletes mail.

## Personalization

Whether `email-town-labeler personalize` supports this model (head-based backends do). Personalized
copies are derived from the user's mail and must stay private.

## Changelog

- `<version>` (`<date>`): <changes>.
