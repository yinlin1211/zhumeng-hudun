"""FastAPI 接口层。

接口一览:
    POST   /api/chat                        智能体问答（自然语言 -> 引用标注回答）
    GET    /api/search?q=&top_k=            检索接口（返回条文与出处元数据）
    GET    /api/documents                   文书清单（含效力状态）
    GET    /api/documents/{doc_id}          文书详情（含版本历史与条款）
    POST   /api/documents                   文书入库
    POST   /api/documents/{doc_id}/version  发布新版本（修订）
    POST   /api/documents/{doc_id}/repeal   废止文书
    POST   /api/documents/{doc_id}/verify   标记人工核验通过
    GET    /api/validate                    全库时效/状态校验报告
    GET    /api/health                      健康检查

启动: uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, validate, versioning
from . import voice
from .agent import LegalAgent
from .retriever import search as kb_search

app = FastAPI(
    title="农民工法律维权平台 · 法律知识库与智能体",
    version="0.1.0",
    description="阶段一：法律文书知识库（版本管理/时效校验）；阶段二：RAG 法律咨询智能体（出处可追溯/越界兜底）",
)

_agent = LegalAgent()

# 网站静态文件目录（挂载在文件末尾，确保 /api/* 路由优先匹配）
_STATIC_DIR = Path(__file__).resolve().parent.parent / "website"


# ---------------- 请求模型 ----------------

class ChatTurn(BaseModel):
    """单轮历史消息（前端持有并回传，后端无状态）。"""
    role: str = Field(..., description="user 或 assistant")
    content: str = Field(..., min_length=1, max_length=4000)


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    top_k: Optional[int] = Field(None, ge=1, le=20)
    history: list[ChatTurn] = Field(default_factory=list, description="历史对话，前端回传，支持多轮问诊")


class DocumentIn(BaseModel):
    id: str
    name: str
    doc_type: str = "law"
    issuing_authority: str = ""
    document_number: str = ""
    status: str = "active"
    enacted_date: str = ""
    revision_date: str = ""
    repeal_date: str = ""
    source_url: str = ""
    verified: bool = False
    last_checked: str = ""
    version_no: str = "v1"
    version_date: str = ""
    provisions: list[dict] = Field(default_factory=list)


class NewVersionIn(BaseModel):
    provisions: list[dict]
    version_no: str
    version_date: str
    change_note: str = ""
    revision_date: Optional[str] = None


class RepealIn(BaseModel):
    date: str
    reason: str = ""


# ---------------- 智能体 ----------------

@app.get("/api/opening", summary="会话开场白（律师人设）")
def opening():
    """返回固定开场白，前端在会话开始时展示，不调 LLM、不检索。"""
    return _agent.opening()


@app.post("/api/voice/asr", summary="语音识别（讯飞方言版）")
async def voice_asr(
    audio: UploadFile = File(..., description="WAV 音频文件，16k/16bit/单声道"),
    dialect: str = Form("普通话", description="方言：普通话/四川话/成都话/湖南话/广西话/粤语/河南话/东北话/上海话/江西话"),
):
    """接收前端录音（WAV），调讯飞语音听写识别为文本，方言可选。"""
    wav_bytes = await audio.read()
    if not wav_bytes:
        raise HTTPException(400, "音频为空")
    result = voice.asr(wav_bytes, dialect)
    return result


@app.post("/api/chat", summary="智能体问答（律师问诊式，支持多轮）")
def chat(req: ChatRequest):
    history = [{"role": t.role, "content": t.content} for t in req.history]
    return _agent.answer(req.question, top_k=req.top_k, history=history)


@app.get("/api/search", summary="条文检索")
def search(q: str, top_k: int = 5, active_only: bool = True):
    with db.get_conn() as conn:
        return {"results": kb_search(conn, q, top_k=top_k, active_only=active_only)}


# ---------------- 知识库管理 ----------------

@app.get("/api/documents", summary="文书清单（含效力状态）")
def list_documents():
    with db.get_conn() as conn:
        return {"documents": validate.summarize_documents(conn)}


@app.get("/api/documents/{doc_id}", summary="文书详情（版本历史 + 条款）")
def get_document(doc_id: str):
    with db.get_conn() as conn:
        doc = db.get_document(conn, doc_id)
        if not doc:
            raise HTTPException(404, f"文书不存在: {doc_id}")
        versions = db.get_versions(conn, doc_id)
        provisions = db.get_provisions(conn, doc_id)
        return {"document": doc, "versions": versions, "provisions": provisions}


@app.post("/api/documents", summary="文书入库")
def create_document(doc: DocumentIn):
    problems = validate.validate_seed_document(doc.model_dump())
    if problems:
        raise HTTPException(422, {"problems": problems})
    with db.get_conn() as conn:
        if db.doc_exists(conn, doc.id):
            raise HTTPException(409, f"文书已存在: {doc.id}，如需更新请使用版本接口")
        version_id = f"ver_{doc.id}_v1"
        db.insert_document(conn, doc.model_dump(), version_id)
    return {"ok": True, "document_id": doc.id, "version_id": version_id}


@app.post("/api/documents/{doc_id}/version", summary="发布新版本（修订）")
def publish_version(doc_id: str, body: NewVersionIn):
    with db.get_conn() as conn:
        if not db.doc_exists(conn, doc_id):
            raise HTTPException(404, f"文书不存在: {doc_id}")
        patch = {"revision_date": body.revision_date} if body.revision_date else {}
        vid = versioning.publish_new_version(
            conn, doc_id, body.provisions, body.version_no, body.version_date,
            change_note=body.change_note, doc_patch=patch,
        )
    return {"ok": True, "document_id": doc_id, "version_id": vid}


@app.post("/api/documents/{doc_id}/repeal", summary="废止文书")
def repeal(doc_id: str, body: RepealIn):
    with db.get_conn() as conn:
        if not db.doc_exists(conn, doc_id):
            raise HTTPException(404, f"文书不存在: {doc_id}")
        versioning.repeal_document(conn, doc_id, body.date, body.reason)
    return {"ok": True, "document_id": doc_id, "repeal_date": body.date}


@app.post("/api/documents/{doc_id}/verify", summary="标记人工核验通过")
def verify(doc_id: str):
    with db.get_conn() as conn:
        if not db.doc_exists(conn, doc_id):
            raise HTTPException(404, f"文书不存在: {doc_id}")
        versioning.set_verified(conn, doc_id, True)
    return {"ok": True, "document_id": doc_id, "verified": True}


@app.get("/api/validate", summary="全库时效/状态校验报告")
def validate_all():
    with db.get_conn() as conn:
        return validate.validate_database(conn)


@app.get("/api/health", summary="健康检查")
def health():
    return {
        "ok": True,
        "llm_configured": _agent.client.available,
        "degraded_mode": _agent.degraded,
        "db": str(config.DB_PATH),
        "agent_mode": "lawyer-diagnostic",
    }


# 网站静态文件挂载（放在所有 API 路由之后，确保 /api/* 优先匹配）
app.mount("/", StaticFiles(directory=str(_STATIC_DIR), html=True), name="website")
