"""Syntax-check every frontend script with `node --check`.

Includes the large inline <script> in static/index.html, which no other tool
looks at. Exits non-zero on the first error. Used by CI; run it locally with
`python scripts/check_frontend.py`.
"""
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"


def main() -> int:
    files = sorted((STATIC / "js").glob("*.js")) + [STATIC / "service-worker.js"]
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    inline = re.findall(r"<script>(.*?)</script>", html, flags=re.S)
    failed = False
    with tempfile.TemporaryDirectory() as tmp:
        for i, body in enumerate(inline):
            path = pathlib.Path(tmp) / f"index.html.inline{i}.js"
            path.write_text(body, encoding="utf-8")
            files.append(path)
        for path in files:
            result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
            label = path.name if path.parent == pathlib.Path(tmp) else path.relative_to(ROOT)
            if result.returncode != 0:
                failed = True
                print(f"FAIL {label}\n{result.stderr}")
            else:
                print(f"ok   {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
