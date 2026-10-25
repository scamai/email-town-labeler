<!-- Verbatim copy of the model card (README.md) shipped inside the town-modernbert-int8 release package. -->

# town-labels-modernbert-large-onnx-int8

[`town-modernbert`](https://github.com/scamai/email-town-labeler/blob/main/docs/model-cards/town-modernbert.md)
(`town-labels-modernbert-large-onnx@1`) with its weights quantized to int8, for machines where the default's memory
use matters. Same seven classes, tokenizer, input text and recommended policy. Optional: `town-modernbert` stays the
default.

Made with `scripts/quantize_onnx.py`: onnxruntime dynamic quantization (`quantize_dynamic`, QInt8 weights, one scale
per tensor). `config.quantized` in model.json names the onnxruntime version and the sha256 of the float32 graph it
came from; the same command on the same graph reproduces this `model.onnx` byte for byte (checked once, onnxruntime
1.31.0).

## Training data — read this

Unchanged from the float32 model, whose card is the reference:

- **4,176 inbound emails from one person's Gmail inbox** (English, July–October 2026), labeled by a hosted
  auto-labeling tool's six content labels; mail it left unlabeled is `unknown`.
- The mailbox owner consented to publishing these weights. Fine-tuned models can memorise fragments of their training
  data (names, senders, subject lines), and quantizing does not remove them. Do not use this model to try to recover
  that data.
- Labels reflect one person's inbox and one tool's taxonomy. Expect it to fit your mail less well than it fits theirs.

## How it compares with the float32 model

Measured on one Apple M4 Pro (macOS), onnxruntime 1.31.0, run by hand in a terminal, classifying 350 emails in one
batch:

| | float32 (`town-modernbert`) | int8 (this package) |
|---|---|---|
| download | 824 MB | 263 MB |
| on disk | 1.6 GB | 0.4 GB |
| peak memory while loaded | 2.96 GB | 1.52 GB |
| time per email | 187 ms | 150 ms |

**Agreement with the float32 model**, model plus recommended policy end to end:

- **On 350 real inbound emails from one mailbox that neither model was trained on** (received July 1 – October 25,
  2026): 347 of the 350 final labels are the same (99.1%). The 3 that differ moved to `action_needed` (2 from
  `updates`, 1 from `orders`). Probabilities move a little (mean absolute difference 0.0036, largest 0.37), and that
  matters most next to the 0.07 `action_needed` threshold.
- **On 50 made-up emails** (`metrics.parity.int8_vs_float32` in model.json): the top class agrees on 45 of 50 (90%);
  mean absolute probability difference 0.017, largest 0.42. These records are templates padded with repeated filler
  text, harder on the quantized model than the real mail above.

This is agreement with the float32 model, not accuracy against labels: no labels were available for those 350 emails,
and one mailbox is a small sample. The float32 held-out numbers (87.3% 7-way agreement with the labels; with the
recommended policy, action_needed recall 0.88 and precision 0.41) were not re-measured for int8; model.json keeps them
under `metrics.float32_source`.

## Use

    email-town-labeler model pull town-modernbert-int8 --use

Needs the `[onnx]` extra. Restart the background service afterwards. Corrections and the personal model are kept per
model, so after switching, `learn` starts over from the int8 model's decisions.

## License

Apache-2.0, same as the base model (answerdotai/ModernBERT-large).
