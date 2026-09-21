"""版本管理与效力状态机。

核心规则:
- 一个文书(legal_document)对应多个版本(document_version)，任一时刻仅一个版本处于 active。
- 发布新版本: 旧 active 版本 -> revised，新版本 -> active，同时更新文书修订日期。
- 废止文书: 当前 active 版本与文书状态 -> repealed，记录废止日期。
- 所有状态迁移必须经过 STATUS_TRANSITIONS 校验，非法迁移直接抛错。
"""

import uuid

from . import config, db


class VersionError(Exception):
    """版本/状态操作非法。"""


def _check_transition(cur: str, new: str, what: str) -> None:
    if (cur, new) not in config.STATUS_TRANSITIONS:
        raise VersionError(
            f"非法状态迁移: {what} {cur} -> {new} "
            f"（允许: {sorted(config.STATUS_TRANSITIONS)}）"
        )


def publish_new_version(conn, document_id: str, provisions: list[dict],
                        version_no: str, version_date: str,
                        change_note: str = "", doc_patch: dict | None = None) -> str:
    """发布文书新版本：旧版本标记 revised，新版本成为现行有效。

    provisions: 新版本全部条款 [{article_no, content, ...}]
    doc_patch:  需要同步更新的文书字段（如 revision_date、document_number）
    返回新版本 id。
    """
    doc = db.get_document(conn, document_id)
    if not doc:
        raise VersionError(f"文书不存在: {document_id}")
    if doc["status"] not in (config.STATUS_ACTIVE, config.STATUS_REVISED, config.STATUS_DRAFT):
        raise VersionError(f"文书已废止，不可发布新版本: {document_id}")

    ts = db.now()
    new_version_id = f"ver_{uuid.uuid4().hex[:12]}"

    # 1. 旧 active 版本退役
    conn.execute(
        "UPDATE document_version SET status = ? WHERE document_id = ? AND status = ?",
        (config.STATUS_REVISED, document_id, config.STATUS_ACTIVE),
    )

    # 2. 写入新版本
    conn.execute(
        """INSERT INTO document_version (id, document_id, version_no, version_date,
                                         status, change_note, created_at)
           VALUES (?,?,?,?,'active',?,?)""",
        (new_version_id, document_id, version_no, version_date, change_note, ts),
    )

    # 3. 新版本条款（替换旧条款，保证条款唯一性约束）
    conn.execute("DELETE FROM legal_provision WHERE document_id = ?", (document_id,))
    for i, p in enumerate(provisions, start=1):
        conn.execute(
            """INSERT INTO legal_provision (id, document_id, version_id, article_no,
                                            article_seq, content, status)
               VALUES (?,?,?,?,?,?,'active')""",
            (f"{document_id}::{p['article_no']}", document_id, new_version_id,
             p["article_no"], p.get("article_seq", i), p["content"]),
        )

    # 4. 文书状态与修订信息
    patch = dict(doc_patch or {})
    patch.setdefault("status", config.STATUS_ACTIVE)
    patch.setdefault("revision_date", version_date)
    patch.setdefault("updated_at", ts)
    sets, values = [], []
    for key in ("status", "revision_date", "updated_at", "document_number", "repeal_date"):
        if key in patch:
            sets.append(f"{key}=?")
            values.append(patch[key])
    values.append(document_id)
    conn.execute(f"UPDATE legal_document SET {', '.join(sets)} WHERE id=?", values)

    db._sync_fts(conn, document_id)
    return new_version_id


def repeal_document(conn, document_id: str, repeal_date: str, reason: str = "") -> None:
    """废止文书：文书与当前 active 版本均标记 repealed。"""
    doc = db.get_document(conn, document_id)
    if not doc:
        raise VersionError(f"文书不存在: {document_id}")
    _check_transition(doc["status"], config.STATUS_REPEALED, f"文书 {document_id}")
    ts = db.now()
    conn.execute(
        "UPDATE document_version SET status = ? WHERE document_id = ? AND status = ?",
        (config.STATUS_REPEALED, document_id, config.STATUS_ACTIVE),
    )
    conn.execute(
        """UPDATE legal_document SET status = ?, repeal_date = ?, updated_at = ?
           WHERE id = ?""",
        (config.STATUS_REPEALED, repeal_date, ts, document_id),
    )
    conn.execute(
        "UPDATE legal_provision SET status = ? WHERE document_id = ?",
        (config.STATUS_REPEALED, document_id),
    )


def set_verified(conn, document_id: str, verified: bool = True) -> None:
    """标记文书已通过人工核验（版本/条文与官方发布文本一致）。"""
    conn.execute(
        "UPDATE legal_document SET verified = ?, last_checked = ?, updated_at = ? WHERE id = ?",
        (1 if verified else 0, db.now(), db.now(), document_id),
    )
