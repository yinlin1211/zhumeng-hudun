#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""抓取/解析官方法规原文，生成 data/seed/*.json 种子文件（仅用标准库）。

用法：
    python scripts/fetch_laws.py                # 处理全部
    python scripts/fetch_laws.py --only gs_bx   # 只处理某个 key
    python scripts/fetch_laws.py --list         # 列出全部 key
    python scripts/fetch_laws.py --dry-run      # 只解析不写文件

自检：每部法规解析出的条款数必须与官方公布数一致，否则拒绝生成 JSON。
"""

import argparse
import html
import json
import os
import re
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_DIR = os.path.join(ROOT, "data", "seed")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

# 仅在最后一个条文的起始位置之后搜索，用于切掉网页页脚/推荐位
FOOTER_MARKERS = [
    "扫一扫在手机打开当前页",
    "相关稿件",
    "相关链接",
    "责任编辑",
    "【打印】",
    "【关闭】",
    "【我要纠错】",
    "分享到",
    "网站声明",
    "版权所有",
    "上一篇",
    "下一篇",
    "来源：",
    "国家规章库",
    "登录注册",
    "链接：",
    "解读",
]

# ---------------------------------------------------------------- 法规清单

LAWS = [
    {
        "key": "gs_bx",
        "file": "reg_work_injury_insurance.json",
        "name": "工伤保险条例",
        "doc_type": "administrative_regulation",
        "issuing_authority": "国务院",
        "document_number": "国务院令第375号（2010年12月20日修订）",
        "enacted_date": "2004-01-01",
        "revision_date": "2011-01-01",
        "source_url": "https://www.gov.cn/gongbao/content/2011/content_1778064.htm",
        "expected": 67,
        "kind": "url",
        "url": "https://www.gov.cn/gongbao/content/2011/content_1778064.htm",
    },
    {
        "key": "shbx",
        "file": "law_social_insurance.json",
        "name": "中华人民共和国社会保险法",
        "doc_type": "law",
        "issuing_authority": "全国人民代表大会常务委员会",
        "document_number": "主席令第三十五号（2010年10月28日通过，2018年12月29日修正）",
        "enacted_date": "2011-07-01",
        "revision_date": "2018-12-29",
        "source_url": "https://www.gov.cn/guoqing/2021-10/29/content_5647616.htm",
        "expected": 98,
        "kind": "url",
        "url": "https://www.gov.cn/guoqing/2021-10/29/content_5647616.htm",
    },
    {
        "key": "ldht_tl",
        "file": "reg_labor_contract_regulations.json",
        "name": "中华人民共和国劳动合同法实施条例",
        "doc_type": "administrative_regulation",
        "issuing_authority": "国务院",
        "document_number": "国务院令第535号",
        "enacted_date": "2008-09-18",
        "revision_date": "",
        "source_url": "https://www.gov.cn/zhengce/zhengceku/2008-09/19/content_6630.htm",
        "expected": 38,
        "kind": "url",
        "url": "https://www.gov.cn/zhengce/zhengceku/2008-09/19/content_6630.htm",
    },
    {
        "key": "ldbzjc",
        "file": "reg_labor_supervision.json",
        "name": "劳动保障监察条例",
        "doc_type": "administrative_regulation",
        "issuing_authority": "国务院",
        "document_number": "国务院令第423号",
        "enacted_date": "2004-12-01",
        "revision_date": "",
        "source_url": "https://www.gov.cn/gongbao/content/2004/content_63042.htm",
        "expected": 36,
        "kind": "url",
        "url": "https://www.gov.cn/gongbao/content/2004/content_63042.htm",
    },
    {
        "key": "gzzf",
        "file": "rules_wage_payment.json",
        "name": "工资支付暂行规定",
        "doc_type": "rules",
        "issuing_authority": "劳动部",
        "document_number": "劳部发〔1994〕489号",
        "enacted_date": "1995-01-01",
        "revision_date": "",
        "source_url": "https://www.gov.cn/zhengce/2022-08/31/content_5711284.htm",
        "expected": 20,
        "kind": "url",
        "url": "https://www.gov.cn/zhengce/2022-08/31/content_5711284.htm",
    },
    {
        "key": "njx",
        "file": "reg_annual_leave.json",
        "name": "职工带薪年休假条例",
        "doc_type": "administrative_regulation",
        "issuing_authority": "国务院",
        "document_number": "国务院令第514号",
        "enacted_date": "2008-01-01",
        "revision_date": "",
        "source_url": "https://www.gov.cn/gongbao/content/2008/content_859865.htm",
        "expected": 10,
        "kind": "url",
        "url": "https://www.gov.cn/gongbao/content/2008/content_859865.htm",
    },
    {
        "key": "sfjs1",
        "file": "ji_labor_dispute_interpretation_1.json",
        "name": "最高人民法院关于审理劳动争议案件适用法律问题的解释（一）",
        "doc_type": "judicial_interpretation",
        "issuing_authority": "最高人民法院",
        "document_number": "法释〔2020〕26号",
        "enacted_date": "2021-01-01",
        "revision_date": "",
        "source_url": "https://www.court.gov.cn/zixun/xiangqing/282121.html",
        "expected": 54,
        "kind": "url",
        "url": "https://www.court.gov.cn/zixun/xiangqing/282121.html",
    },
    # 以下 4 部原为精选条文（3~7 条），改为全文入库：
    # 法条里根本不出现“押金/加班费标准/违法解除赔偿金”等关键词，只有全量条文才能检索到
    {
        "key": "ldht",
        "file": "law_labor_contract_law.json",
        "name": "中华人民共和国劳动合同法",
        "doc_type": "law",
        "issuing_authority": "全国人民代表大会常务委员会",
        "document_number": "主席令第六十五号（2007年6月29日通过，2012年12月28日修正）",
        "enacted_date": "2008-01-01",
        "revision_date": "2012-12-28",
        "source_url": "https://www.gov.cn/jrzg/2007-06/29/content_667720.htm",
        "expected": 98,
        "kind": "txt",
        "path": os.path.join(ROOT, "website", "laws", "labor-contract-law-full.txt"),
    },
    {
        "key": "ldf",
        "file": "law_labor_law.json",
        "name": "中华人民共和国劳动法",
        "doc_type": "law",
        "issuing_authority": "全国人民代表大会常务委员会",
        "document_number": "主席令第二十八号（1994年7月5日通过，2018年12月29日第二次修正）",
        "enacted_date": "1995-01-01",
        "revision_date": "2018-12-29",
        "source_url": "https://www.gov.cn/flfg/2005-08/05/content_20968.htm",
        "expected": 107,
        "kind": "txt",
        "path": os.path.join(ROOT, "website", "laws", "labor-law-full.txt"),
    },
    {
        "key": "zc",
        "file": "law_labor_dispute_arbitration.json",
        "name": "中华人民共和国劳动争议调解仲裁法",
        "doc_type": "law",
        "issuing_authority": "全国人民代表大会常务委员会",
        "document_number": "主席令第八十号（2007年12月29日通过）",
        "enacted_date": "2008-05-01",
        "revision_date": "",
        "source_url": "https://www.gov.cn/flfg/2007-12/29/content_847310.htm",
        "expected": 54,
        "kind": "txt",
        "path": os.path.join(ROOT, "website", "laws", "labor-dispute-arbitration-full.txt"),
    },
    {
        "key": "nmg",
        "file": "reg_migrant_workers_wage.json",
        "name": "保障农民工工资支付条例",
        "doc_type": "administrative_regulation",
        "issuing_authority": "国务院",
        "document_number": "国务院令第724号",
        "enacted_date": "2020-05-01",
        "revision_date": "",
        "source_url": "https://www.gov.cn/zhengce/content/2020-01/07/content_5467278.htm",
        "expected": 64,
        "kind": "txt",
        "path": os.path.join(ROOT, "website", "laws", "farmer-pay-protection-full.txt"),
    },
    {
        "key": "jzf",
        "file": "law_construction.json",
        "name": "中华人民共和国建筑法",
        "doc_type": "law",
        "issuing_authority": "全国人民代表大会常务委员会",
        "document_number": "主席令第91号（2011年、2019年两次修正）",
        "enacted_date": "1998-03-01",
        "revision_date": "2019-04-23",
        "source_url": "https://www.gov.cn/flfg/2005-06/21/content_8407.htm",
        "expected": 85,
        "kind": "txt",
        "path": os.path.join(ROOT, "website", "laws", "construction-law-full.txt"),
    },
    {
        "key": "gd_jc",
        "file": "normative_guangdong_labor_supervision.json",
        "name": "广东省劳动保障监察条例",
        "doc_type": "normative",
        "issuing_authority": "广东省人民代表大会常务委员会",
        "document_number": "广东省第十一届人大常委会第三十八次会议通过（2019年修正）",
        "enacted_date": "2013-05-01",
        "revision_date": "2019-05-21",
        "source_url": "http://www.gd.gov.cn/zwgk/wjk/qbwj/content/post_2527427.html",
        "expected": 57,
        "kind": "txt",
        "path": os.path.join(ROOT, "website", "laws", "guangdong-labor-supervision-full.txt"),
    },
]

DIGITS = "零一二三四五六七八九"
ART_RE = re.compile(r"第([一二三四五六七八九十百零〇]{1,5})条")
CHAP_RE = re.compile(r"^第[一二三四五六七八九十]+[章节]")


def cn_to_int(s):
    s = s.replace("〇", "零")
    if not s:
        return None
    total, section, number = 0, 0, 0
    for ch in s:
        if ch == "零":
            continue
        if ch in DIGITS:
            number = DIGITS.index(ch)
        elif ch == "十":
            section += (number or 1) * 10
            number = 0
        elif ch == "百":
            section += (number or 1) * 100
            number = 0
        else:
            return None
    total = section + number
    return total or None


def int_to_cn(n):
    if n < 10:
        return DIGITS[n]
    if n == 10:
        return "十"
    if n < 20:
        return "十" + DIGITS[n % 10]
    if n < 100:
        s = DIGITS[n // 10] + "十"
        return s + DIGITS[n % 10] if n % 10 else s
    if n < 1000:
        s = DIGITS[n // 100] + "百"
        rem = n % 100
        if rem == 0:
            return s
        if rem < 10:
            return s + "零" + DIGITS[rem]
        return s + int_to_cn(rem)
    raise ValueError(n)


def fetch(url, timeout=40):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def html_to_text(markup):
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", markup)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|tr|h[1-6]|td|section|article)>", "\n", s)
    s = re.sub(r"(?s)<[^>]+>", "", s)
    s = html.unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = s.replace("　", " ").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in s.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def cut_footer(text, last_pos):
    cut = len(text)
    for mk in FOOTER_MARKERS:
        i = text.find(mk, last_pos)
        if i != -1:
            cut = min(cut, i)
    return text[:cut]


def extract_articles(text, expected):
    """从正文文本中切出条文，返回 [(文章序号 int, 正文 str)]，失败返回 (None, 原因)。"""
    lines = [ln for ln in text.split("\n") if not CHAP_RE.match(ln)]
    body = "\n".join(lines)

    heads = []
    for m in ART_RE.finditer(body):
        if body[body.rfind("\n", 0, m.start()) + 1 : m.start()].strip():
            continue  # 行首必须是条文编号，排除正文内的援引
        n = cn_to_int(m.group(1))
        if n:
            heads.append((m.start(), m.end(), n))

    best, best_i = [], None
    for i, (_, _, n) in enumerate(heads):
        if n != 1:
            continue
        run = [i]
        for j in range(i + 1, len(heads)):
            if heads[j][2] == heads[run[-1]][2] + 1:
                run.append(j)
        if len(run) > len(best):
            best, best_i = run, i
    if not best:
        return None, "未定位到条文正文"

    nums = [heads[i][2] for i in best]
    if len(nums) != expected or nums[0] != 1 or nums[-1] != expected:
        return None, "条款数不符：解析到 %d 条，应为 %d 条" % (len(nums), expected)

    text = cut_footer(body, heads[best[-1]][0])
    out = []
    for k, idx in enumerate(best):
        start = heads[idx][1]
        end = heads[best[k + 1]][0] if k + 1 < len(best) else len(text)
        content = re.sub(r"\s+", "", text[start:end])
        content = content.strip("　 ")
        if not content:
            return None, "第%d条正文为空" % nums[k]
        out.append((nums[k], content))
    return out, ""


def load_text(law):
    if law["kind"] == "txt":
        with open(law["path"], "rb") as f:
            raw = f.read()
        for enc in ("utf-8", "gb18030"):
            try:
                return raw.decode(enc)
            except UnicodeDecodeError:
                continue
        return raw.decode("utf-8", "replace")
    return html_to_text(fetch(law["url"]))


def build(law):
    text = load_text(law)
    articles, err = extract_articles(text, law["expected"])
    if articles is None:
        return None, err
    doc = {
        "id": os.path.splitext(law["file"])[0],
        "name": law["name"],
        "doc_type": law["doc_type"],
        "issuing_authority": law["issuing_authority"],
        "document_number": law["document_number"],
        "status": "active",
        "enacted_date": law["enacted_date"],
        "revision_date": law["revision_date"],
        "source_url": law["source_url"],
        "verified": False,
        "last_checked": "",
        "version_no": "v1",
        "version_date": law["revision_date"] or law["enacted_date"],
        "change_note": "官方来源自动解析，条款数已自检，种子数据待人工核验",
        "provisions": [
            {"article_no": "第%s条" % int_to_cn(n), "content": c} for n, c in articles
        ],
    }
    last = doc["provisions"][-1]["content"]
    if len(last) > 250:
        return None, "末条正文异常长（%d 字），疑吞入页脚" % len(last)
    if "施行" not in last and "执行" not in last:
        return None, "末条不合施行条款体例，疑混入无关内容：%s" % last[:60]
    return doc, ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", action="append", default=[])
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.list:
        for law in LAWS:
            print("%-10s %-28s 应 %d 条" % (law["key"], law["name"], law["expected"]))
        return 0

    todo = [l for l in LAWS if not args.only or l["key"] in args.only]
    failed = []
    for law in todo:
        print("=" * 70)
        print("[%s] %s（应 %d 条）" % (law["key"], law["name"], law["expected"]))
        try:
            doc, err = build(law)
        except Exception as exc:  # 网络/解析异常都不该中断整批
            doc, err = None, "%s: %s" % (type(exc).__name__, exc)
        if doc is None:
            print("  !! 失败：%s" % err)
            failed.append(law["key"])
            continue
        ps = doc["provisions"]
        print("  OK 解析 %d 条" % len(ps))
        print("  首条 %s %s" % (ps[0]["article_no"], ps[0]["content"][:70]))
        print("  末条 %s %s" % (ps[-1]["article_no"], ps[-1]["content"][:70]))
        if args.dry_run:
            continue
        path = os.path.join(SEED_DIR, law["file"])
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"documents": [doc]}, f, ensure_ascii=False, indent=2)
            f.write("\n")
        print("  -> %s" % os.path.relpath(path, ROOT))

    print("=" * 70)
    if failed:
        print("失败 %d 部：%s" % (len(failed), ", ".join(failed)))
        return 1
    print("全部完成：%d 部" % len(todo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
