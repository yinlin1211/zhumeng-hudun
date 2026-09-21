"""入库校验与全库时效校验。

两类校验:
1. 入库校验（validate_seed_document）: 单条文书数据进入系统前的格式/必填/状态一致性检查。
2. 全库校验（validate_database）: 巡检整个知识库的效力状态一致性，
   输出校验报告（违规项、待核验清单、统计信息）。
"""

from . import config, db


class ValidationError(Exception):
    """数据未通过校验。"""


DOC_TYPES = set(config.DOC_TYPE_RANK.keys())
STATUSES = {config.STATUS_ACTIVE, config.STATUS_REVISED, config.STATUS_REPEALED, config.STATUS_DRAFT}

REQUIRED_FIELDS = ("id", "name", "doc_type")
PROVISION_REQUIRED = ("article_no", "content")


def validate_seed_document(doc: dict) -> list[str]:
    """校验待入库文书，返回问题列表（空列表 = 通过）。"""
    problems = []
    for f in REQUIRED_FIELDS:
        if not doc.get(f):
            problems.append(f"缺少必填字段: {f}")
    if doc.get("id") and not isinstance(doc["id"], str):
        problems.append("id 必须是字符串")
    if doc.get("doc_type") and doc["doc_type"] not in DOC_TYPES:
        problems.append(f"doc_type 非法: {doc.get('doc_type')}，允许: {sorted(DOC_TYPES)}")
    if doc.get("status") and doc["status"] not in STATUSES:
        problems.append(f"status 非法: {doc.get('status')}")

    provisions = doc.get("provisions")
    if not provisions:
        problems.append("provisions 不能为空（至少一条条款）")
    else:
        seen = set()
        for p in provisions:
            for f in PROVISION_REQUIRED:
                if not p.get(f):
                    problems.append(f"条款缺少必填字段: {f}")
            if p.get("article_no") in seen:
                problems.append(f"条款编号重复: {p.get('article_no')}")
            seen.add(p.get("article_no"))
            if p.get("status") and p["status"] not in STATUSES:
                problems.append(f"条款状态非法: {p.get('status')}")
    return problems


def validate_database(conn) -> dict:
    """全库校验，返回校验报告。"""
    report = {
        "checked_at": db.now(),
        "statistics": {},
        "problems": [],
        "pending_verify": [],
    }

    docs = [dict(r) for r in conn.execute("SELECT * FROM legal_document")]
    report["statistics"]["documents"] = len(docs)
    report["statistics"]["active_documents"] = sum(1 for d in docs if d["status"] == config.STATUS_ACTIVE)
    report["statistics"]["provisions"] = conn.execute("SELECT COUNT(*) FROM legal_provision").fetchone()[0]

    for d in docs:
        # 1. active 文书必须存在且仅存在一个 active 版本
        active_versions = conn.execute(
            "SELECT COUNT(*) FROM document_version WHERE document_id=? AND status='active'",
            (d["id"],),
        ).fetchone()[0]
        if d["status"] == config.STATUS_ACTIVE and active_versions != 1:
            report["problems"].append(
                f"[{d['id']}] 状态为 active 但 active 版本数 = {active_versions}（应为 1）"
            )
        if d["status"] == config.STATUS_REVISED and active_versions > 0:
            report["problems"].append(f"[{d['id']}] 状态为 revised 但仍存在 active 版本")
        if d["status"] == config.STATUS_REPEALED:
            if not d.get("repeal_date"):
                report["problems"].append(f"[{d['id']}] 已废止但未记录 repeal_date")
            if active_versions > 0:
                report["problems"].append(f"[{d['id']}] 已废止但仍存在 active 版本")

        # 2. 生效日期完整性
        if d["status"] in (config.STATUS_ACTIVE, config.STATUS_REVISED) and not d.get("enacted_date"):
            report["problems"].append(f"[{d['id']}] 缺少 enacted_date")

        # 3. 核验标记
        if d["status"] == config.STATUS_ACTIVE and not d["verified"]:
            report["pending_verify"].append(
                {"id": d["id"], "name": d["name"],
                 "tip": "尚未经人工核验，正式上线前必须与官方发布文本核对"}
            )

    return report


def summarize_documents(conn) -> list[dict]:
    """文书清单（含效力状态标签），供管理接口展示。"""
    rows = conn.execute(
        """SELECT d.id, d.name, d.doc_type, d.status, d.enacted_date, d.revision_date,
                  d.repeal_date, d.verified, d.last_checked,
                  (SELECT version_no FROM document_version v
                   WHERE v.document_id = d.id AND v.status='active' LIMIT 1) AS current_version
           FROM legal_document d ORDER BY d.doc_type, d.name"""
    )
    return [dict(r) for r in rows]
