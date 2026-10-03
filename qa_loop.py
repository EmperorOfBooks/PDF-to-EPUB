from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent


def resolve_input_pdf() -> Path:
    env_value = os.environ.get("BOOK_PDF")
    if env_value:
        candidate = Path(env_value).expanduser().resolve()
        if candidate.exists():
            return candidate
    candidate = ROOT / "Book Done.pdf"
    if candidate.exists():
        return candidate
    raise FileNotFoundError("BOOK_PDF is not set and Book Done.pdf is not present in the project root.")


INPUT_PDF = resolve_input_pdf()
OUTPUT_EPUB = ROOT / "qa_generated.epub"
TARGET_GENERATOR = ROOT / "epub_builder.py"
MAX_ROUNDS = 6
XHTML_NS = {"xhtml": "http://www.w3.org/1999/xhtml"}


def run_cmd(args, cwd=None):
    return subprocess.run(args, cwd=str(cwd) if cwd else None, capture_output=True, text=True)


def ensure_epubcheck():
    exe = shutil.which("epubcheck")
    if exe:
        return [exe]
    zip_path = ROOT / "epubcheck.zip"
    report_dir = ROOT / "epubcheck"
    if not report_dir.exists():
        if not zip_path.exists():
            api = "https://api.github.com/repos/w3c/epubcheck/releases/latest"
            with urllib.request.urlopen(api, timeout=30) as response:
                payload = json.loads(response.read().decode())
            url = payload["assets"][0]["browser_download_url"]
            urllib.request.urlretrieve(url, str(zip_path))
        with zipfile.ZipFile(zip_path, "r") as archive:
            archive.extractall(report_dir)
    jar = next(report_dir.rglob("epubcheck.jar"), None)
    if jar is None:
        raise FileNotFoundError("epubcheck jar not found after unzip")
    return ["java", "-jar", str(jar)]


def ensure_playwright():
    try:
        import playwright
        return
    except Exception:
        run_cmd([sys.executable, "-m", "pip", "install", "playwright"])
    run_cmd([sys.executable, "-m", "playwright", "install", "chromium"])


def build_epub():
    return run_cmd([sys.executable, str(ROOT / "main.py"), "--input", str(INPUT_PDF), "--output", str(OUTPUT_EPUB)], cwd=ROOT)


def parse_epubcheck(log):
    text = (log.stdout or "") + "\n" + (log.stderr or "")
    combined = text.strip()
    if "no errors or warnings detected" in combined.lower():
        return []
    issues = []
    for line in text.splitlines():
        upper = line.upper()
        if "ERROR" in upper or "FATAL" in upper or "SEVERE" in upper or "WARNING" in upper:
            issues.append(line.strip())
    return issues


def analyze_epubcheck(epub_path):
    result = run_cmd(ensure_epubcheck() + [str(epub_path)], cwd=ROOT)
    return result, parse_epubcheck(result)


def extract_epub(epub_path, destination):
    with zipfile.ZipFile(epub_path, "r") as archive:
        archive.extractall(destination)
    return destination


def list_rendered_xhtml_files(extracted_dir):
    return [file for file in sorted(extracted_dir.rglob("*.xhtml")) if file.name != "nav.xhtml"]


def looks_like_junk(text):
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    if re.search(r"[A-Za-z]", compact):
        return False
    return len(compact) <= 6 or bool(re.fullmatch(r"[\W\d_]+", compact))


def inspect_rendered_content(extracted_dir):
    issues = []
    for file in list_rendered_xhtml_files(extracted_dir):
        try:
            root = ET.parse(file).getroot()
        except ET.ParseError as error:
            issues.append(f"parse error: {file.name} -> {error}")
            continue
        for tag in ("p", "h1", "h2", "h3"):
            for element in root.findall(f".//xhtml:{tag}", XHTML_NS):
                text = re.sub(r"\s+", " ", " ".join(element.itertext())).strip()
                if looks_like_junk(text):
                    issues.append(f"junk string: {file.name} -> {text}")
                    if len(issues) >= 8:
                        return issues
    return issues


def select_visual_sample_files(rendered_files):
    if not rendered_files:
        return []
    selected = [rendered_files[0]]
    middle = rendered_files[len(rendered_files) // 2]
    if middle not in selected:
        selected.append(middle)
    last = rendered_files[-1]
    if last not in selected:
        selected.append(last)
    image_rich = [file for file in rendered_files if file.name.startswith("chapter_") and file.name not in {item.name for item in selected}]
    for file in image_rich:
        if file not in selected:
            selected.append(file)
        if len(selected) >= 6:
            break
    return selected


def run_playwright_checks(extracted_dir):
    screenshot_dir = extracted_dir / "screens"
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    script = """
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(r'__ROOT__')
items = []
files = [file for file in sorted(root.rglob('*.xhtml')) if file.name != 'nav.xhtml']
sample_files = []
if files:
    sample_files.append(files[0])
    sample_files.append(files[len(files) // 2])
    sample_files.append(files[-1])
    for file in files:
        if file not in sample_files and 'chapter_' in file.name:
            sample_files.append(file)
        if len(sample_files) >= 6:
            break
sample_files = list(dict.fromkeys(sample_files))
for file in sample_files:
    url = 'file://' + str(file)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1200, 'height': 2000})
        page.goto(url)
        metrics = {
            'title': page.title(),
            'line_height': page.evaluate("() => getComputedStyle(document.body).lineHeight"),
            'font_size': page.evaluate("() => getComputedStyle(document.body).fontSize"),
            'h1_break': page.evaluate("() => { const node = document.querySelector('h1') || document.body; const s = getComputedStyle(node); return s.breakBefore || s.pageBreakBefore || ''; }"),
            'img_width': page.evaluate("() => Array.from(document.images).map(i => getComputedStyle(i).maxWidth).join('|')"),
            'text_count': page.locator('p').count(),
        }
        target = root / 'screens' / (file.stem + '.png')
        page.screenshot(path=str(target), full_page=True)
        items.append(metrics)
        browser.close()
print(json.dumps(items))
""".replace('__ROOT__', str(extracted_dir).replace('\\', '/'))
    result = run_cmd([sys.executable, "-c", script], cwd=ROOT)
    if result.returncode != 0:
        raise RuntimeError(f"Playwright render check failed: {result.stderr.strip() or result.stdout.strip()}")
    try:
        payload = json.loads(result.stdout or "[]")
        if not payload:
            raise RuntimeError("Playwright render check returned no page metrics.")
        return payload, screenshot_dir
    except Exception as exc:  # pragma: no cover - explicit fail loud for QA safety
        raise RuntimeError(f"Playwright metrics parse failed: {result.stdout.strip() or result.stderr.strip() or str(exc)}") from exc


def visual_heuristics(metrics):
    issues = []
    for item in metrics:
        try:
            line_height = float(str(item.get("line_height", "0")).replace("px", ""))
            font_size = float(str(item.get("font_size", "16")).replace("px", ""))
            line_ratio = line_height / font_size if font_size else line_height
            if line_ratio < 1.4 or line_ratio > 1.5:
                issues.append(f"line-height issue: {item.get('title')} -> {line_ratio:.2f}")
        except Exception:
            pass
        h1_break = str(item.get("h1_break", "")).lower()
        if item.get("text_count", 0) and h1_break == "avoid":
            issues.append(f"chapter break issue: {item.get('title')}")
        img_width = str(item.get("img_width", ""))
        if img_width and "100%" not in img_width and "none" not in img_width.lower():
            issues.append(f"image width issue: {item.get('title')}")
    return issues


def qa_round():
    build = build_epub()
    if build.returncode != 0:
        return False, f"build failed: {build.stderr.strip() or build.stdout.strip()}", build
    if not OUTPUT_EPUB.exists():
        return False, "epub not created", build
    check_result, check_issues = analyze_epubcheck(OUTPUT_EPUB)
    if check_issues:
        return False, "epubcheck issues: " + " | ".join(check_issues[:5]), check_result
    with tempfile.TemporaryDirectory() as tmp:
        extracted_dir = Path(tmp) / "epub"
        extract_epub(OUTPUT_EPUB, extracted_dir)
        integrity_issues = inspect_rendered_content(extracted_dir)
        if integrity_issues:
            return False, "content issues: " + " | ".join(integrity_issues[:5]), integrity_issues
        metrics, _ = run_playwright_checks(extracted_dir)
        issues = visual_heuristics(metrics)
        if issues:
            return False, "visual issues: " + " | ".join(issues[:5]), metrics
        return True, "pass", metrics


def main():
    ensure_playwright()
    for round_index in range(1, MAX_ROUNDS + 1):
        ok, message, data = qa_round()
        print(f"ROUND {round_index}: {message}")
        if ok:
            return 0
    print("QA loop did not converge within the configured limit.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
