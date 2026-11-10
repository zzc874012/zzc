# -*- coding: utf-8 -*-
"""
命造三柱：丙戌年 辛丑月 庚戌日（日主庚金，生于丑月）
用神：庚生丑月寒土冻金 -> 先取丙丁火调候，次取甲乙木疏厚土为佐
     忌：戊己土(印重埋金)、辰(冲日支戌)、丑未(三刑)
吉日：日支 卯(六合) / 寅午(半合火局) + 天干 甲乙丙丁
"""

import datetime

STEMS  = "甲乙丙丁戊己庚辛壬癸"
BRANCH = "子丑寅卯辰巳午未申酉戌亥"

def jdn(y, m, d):
    a = (14 - m) // 12
    yy = y + 4800 - a
    mm = m + 12 * a - 3
    return (d + (153 * mm + 2) // 5 + 365 * yy + yy // 4
            - yy // 100 + yy // 400 - 32045)

def ganzhi(y, m, d):
    j = jdn(y, m, d)
    return STEMS[(j + 9) % 10], BRANCH[(j + 1) % 12]

# 年干 -> 寅月天干起点（五虎遁：甲己丙、乙庚戊、丙辛庚、丁壬壬、戊癸甲）
YEAR_START = {'甲':'丙','己':'丙','乙':'戊','庚':'戊','丙':'庚','辛':'庚',
              '丁':'壬','壬':'壬','戊':'甲','癸':'甲'}
def year_gan(y, m, d):
    # 立春（约 2/4）后为新年
    st, _ = ganzhi(y, m, d)
    if (m, d) < (2, 4):
        prev = STEMS.index(st)
        return STEMS[(prev - 1) % 10]
    return st

def month_pillar(dt):
    yg = year_gan(dt.year, dt.month, dt.day)
    # 2026/2027 节气月起点近似日
    starts = [(1,5,'丑'),(2,4,'寅'),(3,5,'卯'),(4,5,'辰'),(5,5,'巳'),(6,5,'午'),
              (7,7,'未'),(8,7,'申'),(9,7,'酉'),(10,8,'戌'),(11,7,'亥'),(12,7,'子')]
    mz = '丑'  # 默认年初为丑月
    best = None
    for (m, d, z) in starts:
        y = dt.year
        if m > dt.month:
            y = dt.year - 1
        cand = datetime.date(y, m, d)
        if cand <= dt and (best is None or cand > best[0]):
            best = (cand, z)
    if best:
        mz = best[1]
    ms = YEAR_START[yg]
    idx = BRANCH.index(mz) - BRANCH.index('寅')   # 从寅月顺数几位
    ms = STEMS[(STEMS.index(ms) + idx) % 10]
    return ms + mz, yg

# ---------------- 评分 ----------------
STEM_SCORE = {'甲':5,'乙':5,'丙':6,'丁':6,'壬':2,'癸':2,
              '庚':-1,'辛':-1,'戊':-6,'己':-6}
BRANCH_SCORE = {'卯':10,'寅':7,'午':7,'巳':3,'亥':0,'子':0,'申':0,
                '戌':-2,'酉':-3,'未':-6,'丑':-6,'辰':-8}
STEM_NOTE = {'甲':'偏财透出，利求财、开拓','乙':'正财透出，利稳定收益、务实推进',
             '丙':'七杀（调候用神）到位，有冲劲、宜攻坚',
             '丁':'正官到位，利考试、评比、守规矩','壬':'食神泄秀，利表达创作',
             '癸':'伤官泄秀，利才艺但防口舌','庚':'比肩，宜结伴，忌分钱',
             '辛':'劫财，防破财、勿合资','戊':'偏印重，易慵懒、被埋没',
             '己':'正印重，思虑多行动少'}
BRANCH_NOTE = {'卯':'六合日支＋桃花，主和合、人缘、感情',
               '寅':'与日支半合火局，主事业推动、得助力',
               '午':'与日支半合火局，主得认可、有压力但成事',
               '巳':'火旺助用神，精神状态好','子':'平','亥':'平',
               '申':'金比劫，宜社交不宜谈钱','戌':'伏吟日支，易反复纠结',
               '酉':'酉戌相害，防口舌小耗','未':'丑戌未三刑，防是非破财',
               '丑':'丑戌相刑，防纠结内耗','辰':'辰戌六冲，日支被冲，忌重大决定'}

start = datetime.date(2026, 11, 4)
end   = datetime.date(2027, 12, 31)
rows = []
d = start
while d <= end:
    st, br = ganzhi(d.year, d.month, d.day)
    mp, yg = month_pillar(d)
    rows.append((d, st + br, mp, yg, STEM_SCORE[st] + BRANCH_SCORE[br],
                 STEM_NOTE[st], BRANCH_NOTE[br]))
    d += datetime.timedelta(days=1)

WK = "一二三四五六日"
best = sorted(rows, key=lambda x: -x[4])[:25]
print("=" * 100)
print("【2026-11-04 ~ 2027-12-31 · 幸运日 TOP 25】")
print("=" * 100)
print(f"{'公历':<12}{'星期':<7}{'日柱':<7}{'月柱':<7}{'分':<5}说明")
print("-" * 100)
for r in best:
    print(f"{r[0].strftime('%Y-%m-%d'):<12}周{WK[r[0].weekday()]:<6}{r[1]:<7}{r[2]:<7}"
          f"{r[4]:<5}{r[5]}；{r[6]}")

print("\n" + "=" * 100)
print("【近期最佳 · 2026 年 11 月剩余】")
print("=" * 100)
for r in sorted([x for x in rows if x[0] <= datetime.date(2026,11,30)],
                key=lambda x: -x[4])[:6]:
    print(f"{r[0].strftime('%Y-%m-%d')}  周{WK[r[0].weekday()]}  日柱{r[1]}  "
          f"月柱{r[2]}  分{r[4]:>3}  {r[5]}；{r[6]}")

print("\n" + "=" * 100)
print("【2027 年（丁未年）每月首选吉日】")
print("=" * 100)
for m in range(1, 13):
    mr = [x for x in rows if x[0].year == 2027 and x[0].month == m]
    mr.sort(key=lambda x: -x[4])
    r = mr[0]
    print(f"2027-{m:02d}  首选 {r[0].strftime('%m-%d')} 周{WK[r[0].weekday()]} "
          f"日柱{r[1]} 月柱{r[2]} 分{r[4]:>3}   {r[5]}；{r[6]}")

print("\n" + "=" * 100)
print("【需要避开的日子（分 ≤ -7）】")
print("=" * 100)
bad = sorted([x for x in rows if x[4] <= -7], key=lambda x: x[0])
for r in bad:
    print(f"{r[0].strftime('%Y-%m-%d')} 周{WK[r[0].weekday()]} 日柱{r[1]} "
          f"分{r[4]:>3}  {r[5]}；{r[6]}")
print(f"\n合计 {len(bad)} 天，占 {len(bad)*100/len(rows):.0f}%")
