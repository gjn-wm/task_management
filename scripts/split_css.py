import re
from pathlib import Path

css = Path("static/css/_extracted.css").read_text(encoding="utf-8")
out = Path("static/css")

tokens_end = css.find("        * {")
tokens = css[:tokens_end].strip()
rest = css[tokens_end:]
resp_start = rest.find("        /* Responsive Styles */")
base = rest[:resp_start].strip() if resp_start >= 0 else rest

sections = re.split(r"\n        /\* ([^*]+) \*/\n", base)
chunks = {"reset": sections[0] if sections else ""}

i = 1
while i < len(sections) - 1:
    name = sections[i].strip()
    content = sections[i + 1]
    if any(k in name for k in ["Kanban", "Task Card", "column-header", "cards-list"]):
        chunks.setdefault("kanban_old", "")
        chunks["kanban_old"] += f"\n/* {name} */\n{content}"
    elif any(
        k in name
        for k in [
            "Analytics",
            "Calendar",
            "Clean Panel",
            "All Tasks",
            "activity",
            "Label Badges",
            "viewer",
        ]
    ):
        chunks.setdefault("views", "")
        chunks["views"] += f"\n/* {name} */\n{content}"
    else:
        chunks.setdefault("components", "")
        chunks["components"] += f"\n/* {name} */\n{content}"
    i += 2

(out / "tokens.css").write_text(tokens, encoding="utf-8")
(out / "components.css").write_text(
    chunks.get("reset", "") + chunks.get("components", ""), encoding="utf-8"
)
(out / "views.css").write_text(chunks.get("views", ""), encoding="utf-8")
print("done")
