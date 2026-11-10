# -*- coding: utf-8 -*-
import sys, io, json
from docx import Document
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

d = Document(r"C:\Users\Lenovo\Desktop\LLM\结果产物\加密_20261102.docx")
body = d.element.body
from docx.table import Table
from docx.text.paragraph import Paragraph

out = []
for child in body.iterchildren():
    if child.tag.endswith('}p'):
        p = Paragraph(child, d)
        out.append({"type": "p", "style": p.style.name, "text": p.text})
    elif child.tag.endswith('}tbl'):
        t = Table(child, d)
        out.append({"type": "tbl", "rows": [[c.text for c in r.cells] for r in t.rows]})
print(json.dumps(out, ensure_ascii=False, indent=1))
