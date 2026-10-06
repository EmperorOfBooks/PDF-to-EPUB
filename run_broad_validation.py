from __future__ import annotations

import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from epubcheck_harvester import ensure_epubcheck as harvest_epubcheck

ROOT = Path(__file__).resolve().parent
TEST_DIR = ROOT / 'test_batch'
TEST_DIR.mkdir(exist_ok=True)

SAMPLES = [
    ('w3_dummy', 'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf'),
    ('orimi_test', 'https://www.orimi.com/pdf-test.pdf'),
    ('adobe_sample', 'https://www.adobe.com/support/products/enterprise/knowledgecenter/media/c4611_sample_explain.pdf'),
]


def download(url: str, target: Path) -> None:
    if target.exists():
        return
    print(f'Downloading {url} -> {target.name}')
    urllib.request.urlretrieve(url, str(target))


def ensure_epubcheck() -> list[str]:
    exe = shutil.which('epubcheck')
    if exe:
        return [exe]
    jar = harvest_epubcheck(ROOT / 'epubcheck')
    return ['java', '-jar', str(jar)]


def run_case(name: str, pdf_path: Path) -> None:
    epub_path = TEST_DIR / f'{name}.epub'
    result = subprocess.run([
        sys.executable,
        str(ROOT / 'main.py'),
        '--input', str(pdf_path),
        '--output', str(epub_path),
    ], cwd=str(ROOT), capture_output=True, text=True)
    print(f'[{name}] main.py rc={result.returncode}')
    if result.stdout:
        print(result.stdout.strip())
    if result.stderr:
        print(result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError(f'[{name}] main.py failed')

    check = subprocess.run(ensure_epubcheck() + [str(epub_path)], cwd=str(ROOT), capture_output=True, text=True)
    print(f'[{name}] epubcheck rc={check.returncode}')
    if check.stdout:
        print(check.stdout.strip())
    if check.stderr:
        print(check.stderr.strip())
    if check.returncode != 0:
        raise RuntimeError(f'[{name}] epubcheck failed')

    if not epub_path.exists():
        raise RuntimeError(f'[{name}] EPUB was not created')
    print(f'PASS [{name}]')


def main() -> int:
    for name, url in SAMPLES:
        pdf_path = TEST_DIR / f'{name}.pdf'
        download(url, pdf_path)
        run_case(name, pdf_path)
    print('ALL EXTRA SAMPLE CASES PASSED')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
