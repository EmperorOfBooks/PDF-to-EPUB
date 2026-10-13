"""Optional OCR adapters for pages that lack a usable text layer."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import uuid
from importlib.util import find_spec
from pathlib import Path

from PIL import Image


class OcrEngineUnavailable(RuntimeError):
    pass


def run_tesseract_subprocess(image: Image.Image, timeout: int = 60) -> str:
    """Execute tesseract binary safely with shell=False, UUID4 filename in temp dir, and strict timeout."""
    tesseract_bin = shutil.which("tesseract") or shutil.which("tesseract.exe")
    if not tesseract_bin:
        raise OcrEngineUnavailable("Tesseract executable not found on PATH.")

    with tempfile.TemporaryDirectory() as tmp_dir:
        safe_img_name = f"{uuid.uuid4().hex}.png"
        safe_img_path = Path(tmp_dir) / safe_img_name
        image.save(safe_img_path, format="PNG")
        output_base = Path(tmp_dir) / f"{uuid.uuid4().hex}_out"
        cmd = [tesseract_bin, str(safe_img_path), str(output_base)]
        result = subprocess.run(cmd, shell=False, capture_output=True, text=True, timeout=timeout)
        txt_file = output_base.with_suffix(".txt")
        if txt_file.exists():
            return txt_file.read_text(encoding="utf-8", errors="replace").strip()
        return result.stdout.strip()


class OcrEngine:
    def __init__(self) -> None:
        self._engine = None
        if find_spec("rapidocr_onnxruntime") is not None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = ("rapidocr", RapidOCR())
        elif find_spec("pytesseract") is not None:
            import pytesseract

            self._engine = ("tesseract", pytesseract)
        elif shutil.which("tesseract") or shutil.which("tesseract.exe"):
            self._engine = ("tesseract_cli", None)

    @property
    def name(self) -> str | None:
        return self._engine[0] if self._engine else None

    def recognize(self, image: Image.Image) -> str:
        if self._engine is None:
            raise OcrEngineUnavailable("Install rapidocr-onnxruntime or pytesseract to OCR scanned pages.")
        name, engine = self._engine
        if name == "tesseract":
            with tempfile.TemporaryDirectory() as tmp_dir:
                safe_img = Path(tmp_dir) / f"{uuid.uuid4().hex}.png"
                image.save(safe_img, format="PNG")
                return str(engine.image_to_string(safe_img, timeout=60))
        if name == "tesseract_cli":
            return run_tesseract_subprocess(image, timeout=60)
        import numpy as np

        results, _ = engine(np.asarray(image.convert("RGB")))
        return "\n".join(str(result[1]) for result in results or [])
