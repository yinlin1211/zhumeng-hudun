"""冒烟测试：验证知识库入库、版本管理、校验、检索、智能体问答全链路。

运行: python scripts/smoke_test.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
os.environ["LEGALKB_DB_PATH"] = str(Path(tempfile.gettempdir()) / "legalkb_smoke.db")
if Path(os.environ["LEGALKB_DB_PATH"]).exists():
    os.remove(os.environ["LEGALKB_DB_PATH"])

from app import config, db, validate, versioning  # noqa: E402
from app.agent import LegalAgent  # noqa: E402
from app.retriever import search  # noqa: E402

PASS, FAIL = 0, 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  [PASS] {name}")
    else:
        FAIL += 1
        print(f"  [FAIL] {name} {detail}")


def main():
    seed_dir = Path(__file__).resolve().parent.parent / "data" / "seed"
    conn = db.connect()

    # 1. 入库
    print("\n== 1. 入库种子文书 ==")
    seeds = sorted(seed_dir.glob("*.json"))
    for f in seeds:
        data = json.loads(f.read_text(encoding="utf-8"))
        for doc in data["documents"]:
            probs = validate.validate_seed_document(doc)
            check(f"校验通过 {doc['id']}", not probs, str(probs))
            db.insert_document(conn, doc, f"ver_{doc['id']}_v1")
    check(f"入库 {len(seeds)} 部文书",
          conn.execute("SELECT COUNT(*) FROM legal_document").fetchone()[0] == len(seeds))

    # 2. 检索：现行有效过滤 + 相关性（口语化/同义表述容忍）
    print("\n== 2. 检索用例 ==")
    q1 = "公司拖欠工资怎么办"
    hits1 = search(conn, q1, top_k=8)
    check("口语化提问命中《劳动合同法》第三十条", any(h["article_no"] == "第三十条" for h in hits1), str(hits1)[:300])
    q1c = "老板拖欠我三个月工资一直不给，怎么办？"
    hits1c = search(conn, q1c, top_k=3)
    check("口语长句命中《劳动法》第五十条",
          any(h["article_no"] == "第五十条" and h["document_name"].endswith("劳动法") for h in hits1c),
          str(hits1c)[:300])

    q2 = "双倍工资"
    hits2 = search(conn, q2, top_k=5)
    check("同义表述命中《劳动合同法》第八十二条(二倍工资)", any(h["article_no"] == "第八十二条" for h in hits2), str(hits2)[:300])

    q3 = "仲裁时效是多长时间"
    hits3 = search(conn, q3, top_k=3)
    check("命中《劳动争议调解仲裁法》第二十七条", any(h["article_no"] == "第二十七条" for h in hits3))

    q1b = "及时足额支付劳动报酬"
    hits1b = search(conn, q1b, top_k=3)
    check("精确表述命中《劳动合同法》第三十条并排第一", hits1b and hits1b[0]["article_no"] == "第三十条", str(hits1b)[:200])

    # 口语 → 法言法语归一化回归（本轮实测样本）
    q4 = "下班路上被车撞了，算不算工伤？"
    hits4 = search(conn, q4, top_k=3)
    check("口语提问命中《工伤保险条例》第十四条",
          hits4 and hits4[0]["article_no"] == "第十四条" and "工伤" in hits4[0]["document_name"],
          str(hits4)[:200])

    q5 = "老板收了我500块押金，说不干满一年不退，这合法吗？"
    hits5 = search(conn, q5, top_k=3)
    check("“押金”召回《劳动合同法》收取财物条款",
          any(h["document_name"].endswith("劳动合同法") and h["article_no"] in ("第九条", "第八十四条") for h in hits5),
          str(hits5)[:200])

    q6 = "我干满一年了，老板不让休年假也不给钱，怎么办？"
    hits6 = search(conn, q6, top_k=3)
    check("口语“年假”命中《职工带薪年休假条例》",
          any("年休假条例" in h["document_name"] for h in hits6), str(hits6)[:200])

    # 3. 版本管理：发布新版本 -> 旧版本 revised
    print("\n== 3. 版本管理与状态机 ==")
    prov = [{"article_no": "第三十条", "content": "（示例修订）分包单位对所招用农民工工资支付负直接责任。"}]
    versioning.publish_new_version(conn, "reg_migrant_workers_wage", prov,
                                   version_no="v2", version_date="2026-06-01",
                                   change_note="演示：发布新版本")
    doc = db.get_document(conn, "reg_migrant_workers_wage")
    check("文书仍为 active", doc["status"] == "active")
    check("文书 revision_date 已更新", doc["revision_date"] == "2026-06-01")
    versions = db.get_versions(conn, "reg_migrant_workers_wage")
    check("存在 v1(revised) + v2(active)",
          sorted(v["status"] for v in versions) == ["active", "revised"])
    hits_v = search(conn, "分包单位", top_k=10)
    live = [h for h in hits_v if h["document_id"] == "reg_migrant_workers_wage"]
    check("检索只返回新版本条文", bool(live) and all(h["content"].startswith("（示例修订）") for h in live),
          str(hits_v)[:200])

    # 4. 废止
    print("\n== 4. 废止 ==")
    versioning.repeal_document(conn, "reg_migrant_workers_wage", "2026-06-01")
    doc = db.get_document(conn, "reg_migrant_workers_wage")
    check("文书状态 repealed", doc["status"] == "repealed")
    check("废止后不再被检索到",
          all(h["document_id"] != "reg_migrant_workers_wage"
              for h in search(conn, "分包单位拖欠工资", top_k=10)))

    # 5. 非法迁移防护
    print("\n== 5. 非法迁移防护 ==")
    try:
        versioning.repeal_document(conn, "reg_migrant_workers_wage", "2026-07-01")
        check("已废止文书不可重复废止", False)
    except versioning.VersionError:
        check("已废止文书不可重复废止", True)

    # 6. 全库校验报告
    print("\n== 6. 全库校验 ==")
    report = validate.validate_database(conn)
    check("校验报告生成", "statistics" in report and "problems" in report)
    check("待核验文书被标记", len(report["pending_verify"]) >= 2)

    # 7. 智能体问答（降级模式：未配置 LLM Key）
    print("\n== 7. 智能体问答 ==")
    conn.commit()   # 裸连接需显式提交，否则智能体新连接将看到空库
    # 强制走降级路径：本地 keys.env 有真密钥时该分支也要能测到（且不产生真实调用）
    config.LLM_API_KEY = ""
    agent = LegalAgent()
    check("降级模式生效", agent.degraded)
    r1 = agent.answer("公司拖欠我工资，可以解除劳动合同并要求经济补偿吗？")
    check("范围内回答含引用", r1["in_scope"] and len(r1["citations"]) > 0, str(r1)[:200])
    check("引用含出处信息", r1["citations"][0]["document_name"] and r1["citations"][0]["enacted_date"])
    check("附免责声明", "免责" in r1["disclaimer"] or "不构成正式法律意见" in r1["disclaimer"])
    # 本轮实测中被误判为“超出知识库”的口语长句，必须判在范围内
    for q in ("老板没跟我签合同，干了8个月，现在把我辞退了，我能要哪些钱？",
              "老板把我开除了，还不给结工资",
              "我在工地上干活摔伤了，包工头不给看病钱，这算工伤吗？"):
        r = agent.answer(q)
        check(f"口语长句判在范围内：{q[:14]}…", r["in_scope"] and len(r["citations"]) > 0, str(r)[:200])

    # 引用校验：模型惯写简称《劳动合同法》，库内是《中华人民共和国劳动合同法》，
    # 不折算简称会把真引用误报成可疑引用（线上实测出现过）
    fake_hits = [{"document_name": "中华人民共和国劳动合同法", "article_no": "第四十条"}]
    _, w_ok = agent._verify_citations("依《劳动合同法》第四十条。", fake_hits)
    check("文书简称引用不误报", w_ok is None, str(w_ok))
    _, w_bad = agent._verify_citations("依《劳动合同法》第九十九条。", fake_hits)
    check("引用未检索到的条文仍被提示", w_bad is not None)

    r2 = agent.answer("火星移民的工资纠纷怎么处理？")
    check("范围外问题明确告知", (not r2["in_scope"]) and "12348" in r2["answer"])

    conn.close()
    print(f"\n结果: {PASS} 通过, {FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
