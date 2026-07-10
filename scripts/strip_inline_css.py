import re
from pathlib import Path

p = Path("static/index.html")
html = p.read_text(encoding="utf-8")
pattern = (
    r'    <link rel="stylesheet" href="/static/css/upgrade\.css">\s*'
    r'<link rel="stylesheet" href="/static/css/phase2\.css">\s*'
    r"<style>.*?</style>"
)
replacement = '    <link rel="stylesheet" href="/static/css/app.css">'
html, n = re.subn(pattern, replacement, html, count=1, flags=re.DOTALL)
if n != 1:
    raise SystemExit(f"pattern match count: {n}")
p.write_text(html, encoding="utf-8")
print("OK", len(html))
