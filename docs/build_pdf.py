"""把 docs/使用教程.md 转成 PDF（需要 Google Chrome）。用法：uv run --with markdown python docs/build_pdf.py"""
import subprocess
import tempfile
from pathlib import Path

import markdown

DOCS = Path(__file__).resolve().parent
MD = DOCS / "使用教程.md"
PDF = DOCS / "使用教程.pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 18mm 15mm; }
body { font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif; font-size: 13px; line-height: 1.75; color: #222; }
h1 { font-size: 26px; border-bottom: 2px solid #ff2442; padding-bottom: 8px; }
h2 { font-size: 20px; margin-top: 28px; border-left: 5px solid #ff2442; padding-left: 10px; }
h3 { font-size: 16px; margin-top: 20px; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; }
th, td { border: 1px solid #ccc; padding: 6px 10px; text-align: left; vertical-align: top; }
th { background: #f5f5f5; }
code, pre { background: #f4f4f4; border-radius: 4px; font-size: 12px; }
code { padding: 1px 4px; }
pre { padding: 10px; white-space: pre-wrap; word-break: break-all; }
blockquote { margin: 10px 0; padding: 6px 14px; background: #fff7e6; border-left: 4px solid #faad14; }
img { max-width: 100%; border: 1px solid #ddd; margin: 8px 0; }
hr { border: 0; border-top: 1px solid #ddd; margin: 24px 0; }
h2, h3, img, table, pre { page-break-inside: avoid; }
"""


def main() -> None:
    body = markdown.markdown(MD.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    html = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><style>{CSS}</style></head><body>{body}</body></html>'
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "t.html"
        page.write_text(html.replace('src="images/', f'src="{DOCS}/images/'), encoding="utf-8")
        subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={PDF}", f"file://{page}"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"已生成 {PDF}")


if __name__ == "__main__":
    main()
