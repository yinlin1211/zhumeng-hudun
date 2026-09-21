"""法律知识库 + 智能体系统 (LegalKB Agent)

模块说明:
    config      - 全局配置（DB 路径、LLM 环境变量）
    db          - SQLite + FTS5 数据模型与连接管理
    versioning  - 文书版本管理与效力状态机
    validate    - 入库校验与全库时效校验
    ingest      - 入库/更新/废止命令行工具
    retriever   - 检索模块（BM25 + 效力位阶加权 + 现行有效过滤）
    llm         - LLM 客户端抽象（无 Key 可降级）
    agent       - 智能体编排（意图识别 -> RAG -> 引用校验 -> 越界兜底）
    main        - FastAPI 接口层
"""

__version__ = "0.1.0"
