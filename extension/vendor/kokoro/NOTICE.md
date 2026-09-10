# Kokoro-82M

`vocab.json` is the phoneme alphabet extracted from `tokenizer.json` in
[onnx-community/Kokoro-82M-v1.0-ONNX](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX).

**Licence: Apache-2.0**, model weights and voice packs both. The repository
serves no `LICENSE` file over its API, so this note records the terms rather
than shipping an empty file that looks like diligence and is not.

The weights themselves are not vendored. They are data rather than code, so the
Manifest V3 remote-code rule does not reach them, and they are fetched on first
use and cached. Someone who never turns on neural voices never downloads them.

| File | Fetched at runtime | Size |
|---|---|---|
| `onnx/model_quantized.onnx` | yes | 88 MB |
| `voices/<id>.bin` | yes, per voice used | 510 KB each |

## What is deliberately absent

No phonemizer. Kokoro takes phonemes rather than text, and the usual converter
is eSpeak NG, which is GPL-3.0. Both packages named `phonemizer` carry it: the
JavaScript one declares Apache-2.0 while embedding eSpeak as WebAssembly, and
the Python one is GPL outright. `piper-phonemize` embeds the same engine.

Pronunciation here comes from CMUdict instead, which is BSD-style. See
`docs/extension-spec.md` section 8.1b for what that costs and why it was chosen.
