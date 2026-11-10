# -*- coding: utf-8 -*-
"""读取加密_20261102.docx 内容，生成汇报用 PPT"""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR

SRC = r"C:\Users\Lenovo\Desktop\LLM\结果产物\加密_20261102.docx"
DST = r"C:\Users\Lenovo\Desktop\LLM\结果产物\加密_20261102.pptx"

# ---------- 1. 读取 docx ----------
d = Document(SRC)
blocks = []
for child in d.element.body.iterchildren():
    if child.tag.endswith('}p'):
        p = Paragraph(child, d)
        if p.text.strip():
            blocks.append(("p", p.style.name, p.text.strip()))
    elif child.tag.endswith('}tbl'):
        t = Table(child, d)
        blocks.append(("tbl", None, [[c.text.strip() for c in r.cells] for r in t.rows]))

# ---------- 2. 组织为幻灯片 ----------
NAVY = RGBColor(0x1F, 0x3B, 0x63)
GOLD = RGBColor(0xC0, 0x8A, 0x2E)
GRAY = RGBColor(0x44, 0x44, 0x44)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width = Inches(13.333)
prs.slide_height = Inches(7.5)
BLANK = prs.slide_layouts[6]


def add_title(slide, text, size=30):
    box = slide.shapes.add_textbox(Inches(0.8), Inches(0.45), Inches(11.7), Inches(0.9))
    tf = box.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = PP_ALIGN.LEFT
    r = p.add_run()
    r.text = text
    r.font.size = Pt(size)
    r.font.bold = True
    r.font.color.rgb = NAVY
    r.font.name = "微软雅黑"
    # 标题下横线
    ln = slide.shapes.add_shape(1, Inches(0.8), Inches(1.38), Inches(11.7), Pt(3))
    ln.fill.solid()
    ln.fill.fore_color.rgb = GOLD
    ln.line.fill.background()
    ln.shadow.inherit = False
    return box


def add_bullets(slide, items, top=1.75, left=0.9, width=11.5, height=5.0, size=18):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    first = True
    for item in items:
        p = tf.paragraphs[0] if first else tf.add_paragraph()
        first = False
        p.space_after = Pt(14)
        r = p.add_run()
        r.text = item
        r.font.size = Pt(size)
        r.font.color.rgb = GRAY
        r.font.name = "微软雅黑"
    return box


def new_slide(title, items, size=18):
    s = prs.slides.add_slide(BLANK)
    add_title(s, title)
    add_bullets(s, items, size=size)
    return s


# 封面
s = prs.slides.add_slide(BLANK)
bar = s.shapes.add_shape(1, Inches(0), Inches(0), prs.slide_width, Inches(2.6))
bar.fill.solid()
bar.fill.fore_color.rgb = NAVY
bar.line.fill.background()
bar.shadow.inherit = False

tb = s.shapes.add_textbox(Inches(1.0), Inches(0.95), Inches(11.3), Inches(1.2))
tf = tb.text_frame
tf.word_wrap = True
p = tf.paragraphs[0]
p.alignment = PP_ALIGN.LEFT
r = p.add_run()
r.text = blocks[0][2]
r.font.size = Pt(36)
r.font.bold = True
r.font.color.rgb = WHITE
r.font.name = "微软雅黑"

tb2 = s.shapes.add_textbox(Inches(1.0), Inches(3.3), Inches(11.3), Inches(2.5))
tf2 = tb2.text_frame
tf2.word_wrap = True
p = tf2.paragraphs[0]
r = p.add_run()
r.text = "发文部门：财务部"
r.font.size = Pt(20)
r.font.color.rgb = GRAY
r.font.name = "微软雅黑"
for t in ["起草人：张三", "成文日期：二零二五年九月"]:
    pp = tf2.add_paragraph()
    rr = pp.add_run()
    rr.text = t
    rr.font.size = Pt(20)
    rr.font.color.rgb = GRAY
    rr.font.name = "微软雅黑"

# 正文区块：按 Heading 2 分组
sections = []  # (title, [items])
for kind, style, val in blocks[1:]:
    if kind == "p" and style.startswith("Heading"):
        sections.append([val, []])
    elif sections:
        sections[-1][1].append(("p", val))
    # 表格单独处理
for idx, (kind, style, val) in enumerate(blocks):
    pass

# 重新扫描，保留表格
sections = []
cur = None
for kind, style, val in blocks[1:]:
    if kind == "p" and style.startswith("Heading"):
        cur = [val, []]
        sections.append(cur)
    elif cur is not None:
        cur[1].append((kind, val))

for title, items in sections:
    if any(k == "tbl" for k, _ in items):
        # 含表格的页：先文字，再表格单独一页
        texts = [v for k, v in items if k == "p" and v]
        tbl = [v for k, v in items if k == "tbl"][0]
        if texts:
            new_slide(title, texts, size=18)
        ts = prs.slides.add_slide(BLANK)
        add_title(ts, title + "——值班人员安排表")
        rows, cols = len(tbl), len(tbl[0])
        gf = ts.shapes.add_table(rows, cols, Inches(1.4), Inches(2.0),
                                 Inches(10.5), Inches(0.55 * rows)).table
        for c in range(cols):
            gf.columns[c].width = Emu(int(Inches(10.5) / cols))
        for ri, row in enumerate(tbl):
            for ci, valu in enumerate(row):
                cell = gf.cell(ri, ci)
                cell.text = ""
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                pp = cell.text_frame.paragraphs[0]
                pp.alignment = PP_ALIGN.CENTER
                rr = pp.add_run()
                rr.text = valu
                rr.font.size = Pt(14 if ri else 15)
                rr.font.bold = (ri == 0)
                rr.font.name = "微软雅黑"
                rr.font.color.rgb = WHITE if ri == 0 else GRAY
                cell.fill.solid()
                cell.fill.fore_color.rgb = NAVY if ri == 0 else (
                    RGBColor(0xF2, 0xF5, 0xF9) if ri % 2 else WHITE)
    else:
        pts = [v for k, v in items if v]
        # 结尾部分：请遵照执行 / 特此通知
        new_slide(title, pts, size=17)

prs.save(DST)
print("saved:", DST)
