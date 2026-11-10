# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from pptx import Presentation
p = Presentation(r"C:\Users\Lenovo\Desktop\LLM\结果产物\加密_20261102.pptx")
print("slides:", len(p.slides))
for i, s in enumerate(p.slides, 1):
    txts = [sh.text_frame.text.replace("\n", " / ")[:70] for sh in s.shapes if sh.has_text_frame and sh.text_frame.text.strip()]
    tbs = ["<表格 %d行>" % len(sh.table.rows) for sh in s.shapes if sh.has_table]
    print(i, "|", " || ".join(txts + tbs))
