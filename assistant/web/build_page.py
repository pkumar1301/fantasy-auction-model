"""Assemble the browser version: assistant/web/dist/index.html (page + tools inlined) and dist/gm_data.json.

  python3 -m assistant.web.build_page        (after build_data)
"""
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
DIST = HERE / "dist"


def build():
    DIST.mkdir(exist_ok=True)
    page = (HERE / "page.html").read_text()
    core = (HERE / "gm_core.js").read_text()
    assert "</script" not in core
    (DIST / "index.html").write_text(page.replace("/*GM_CORE*/", core, 1))
    shutil.copy(HERE / "gm_data.json", DIST / "gm_data.json")
    print("wrote", DIST / "index.html", f"({(DIST / 'index.html').stat().st_size / 1024:.0f} KB) and gm_data.json")


if __name__ == "__main__":
    build()
