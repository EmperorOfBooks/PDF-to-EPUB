"""Optional OCR adapters for pages that lack a usable text layer."""

from __future__ import annotations

from importlib.util import find_spec

from PIL import Image


class OcrEngineUnavailable(RuntimeError):
    pass


class OcrEngine:
    def __init__(self) -> None:
        self._engine = None
        if find_spec("rapidocr_onnxruntime") is not None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = ("rapidocr", RapidOCR())
        elif find_spec("pytesseract") is not None:
            import pytesseract

            self._engine = ("tesseract", pytesseract)

    @property
    def name(self) -> str | None:
        return self._engine[0] if self._engine else None

    def recognize(self, image: Image.Image) -> str:
        if self._engine is None:
            raise OcrEngineUnavailable("Install rapidocr-onnxruntime or pytesseract to OCR scanned pages.")
        name, engine = self._engine
        if name == "tesseract":
            return str(engine.image_to_string(image))
        import numpy as np

        results, _ = engine(np.asarray(image.convert("RGB")))
        return "\n".join(str(result[1]) for result in results or [])
