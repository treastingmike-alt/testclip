# Detection models

Not committed (`backend/assets` is gitignored, same as the fonts), so a fresh
checkout has to fetch this once.

## YuNet face detector

Used by `app/pipeline/reframe.py` to work out where the people are in podcast
footage, so the Podcast template can crop to them instead of letterboxing the
whole landscape frame.

```bash
curl -L -o backend/assets/models/face_detection_yunet_2023mar.onnx \
  https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
```

Verify — the file is stored in that repo via Git LFS, and a plain
`raw.githubusercontent.com` URL silently returns the 131-byte LFS *pointer*
rather than the model. The size check below is what catches that:

```
size    232589 bytes
sha256  8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4
```

```bash
shasum -a 256 backend/assets/models/face_detection_yunet_2023mar.onnx
```

Without it, `reframe.plan()` returns `None` and the Podcast template renders
its fixed letterbox frame. Nothing errors; the clips are just not face-aware.
