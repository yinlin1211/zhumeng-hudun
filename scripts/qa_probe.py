"""真实问法回归探针：跑一批口语化/方言化问题，看范围判定与检索命中。

用法: python scripts/qa_probe.py [--db 路径]
默认用生产库 data/legalkb.db（只读检索，不写入）。
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import config, db, retriever  # noqa: E402
from app.agent import LegalAgent  # noqa: E402

# (问题, 期望是否在范围内, 期望命中的关键文书名片段)
CASES = [
    ("老板没跟我签合同，干了8个月，现在把我辞退了，我能要哪些钱？", True, "劳动合同法"),
    ("老板把我开除了，还不给结工资", True, "劳动合同法"),
    ("老板拖欠我三个月工资一直不给，怎么办？", True, "农民工工资"),
    ("我在工地上干活摔伤了，包工头不给看病钱，这算工伤吗？", True, "工伤保险"),
    ("老板天天让我加班到半夜，加班费怎么算？", True, "劳动法"),
    ("干了一年多，老板一直没给我交社保，能让他给我补上吗？", True, "社会保险"),
    ("老板收了我500块押金，说不干满一年不退，这合法吗？", True, "劳动合同法"),
    ("我干满一年了，老板不让休年假也不给钱，怎么办？", True, "年休假"),
    ("工资条上被扣了两百块，说是罚款，老板能随便扣吗？", True, "工资支付"),
    ("工地上的钱一直拖着，包工头说甲方没给他钱，我该找谁要？", True, "农民工工资"),
    ("老板娘说试用期三个月不给上社保，这样对吗？", True, "社会保险"),
    ("下班路上被车撞了，算不算工伤？", True, "工伤保险"),
    ("火星移民的工资纠纷怎么处理？", False, None),
    ("离婚的时候房子怎么分？", False, None),
    ("我想问一下买房贷款怎么申请", False, None),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(config.DB_PATH))
    args = ap.parse_args()
    os.environ["LEGALKB_DB_PATH"] = args.db

    agent = LegalAgent()
    conn = db.connect()
    print("库: %s" % args.db)
    print("文书 %d 部 / 条文 %d 条\n" % (
        conn.execute("SELECT COUNT(*) FROM legal_document").fetchone()[0],
        conn.execute("SELECT COUNT(*) FROM legal_provision").fetchone()[0],
    ))

    bad = 0
    for q, want, expect_doc in CASES:
        hits = retriever.search(conn, q, top_k=5)
        got, reason = agent._scope_check(q, hits)
        mark = "ok " if got == want else "!! "
        if got != want:
            bad += 1
        top = hits[0] if hits else None
        print("%s[%s] %s" % (mark, "在范围内" if got else "拒答", q))
        if got != want:
            print("      原因: %s" % (reason or "白名单/覆盖率通过"))
        if top:
            print("      top1: 《%s》%s cov=%.3f rel=%.1f" % (
                top["document_name"], top["article_no"],
                top.get("coverage", 0), top.get("relevance", 0)))
            print("      %s" % top["content"][:80])
        else:
            print("      top1: 无命中")
        if expect_doc and hits and expect_doc not in hits[0]["document_name"] \
                and not any(expect_doc in h["document_name"] for h in hits):
            print("      (提示：期望命中含「%s」的文书，未出现在 top5)" % expect_doc)
    print("\n判定错误 %d / %d 例" % (bad, len(CASES)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
