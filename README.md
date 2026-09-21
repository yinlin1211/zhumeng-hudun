# 农民工法律维权平台 · 法律知识库 + 智能体

面向"海之子杯"AI 智能体挑战计划的参赛系统。分两个阶段：

- **阶段一 · 法律文书知识库**：结构化存储法律法规/行政法规/司法解释等文书，支持版本管理、效力状态机（现行有效/已修订/已废止）、时效校验、更新机制，检索只返回现行有效条款。
- **阶段二 · 法律咨询智能体**：RAG 问答，回答引用法条原文并标注出处（文书名称、条款编号、生效/修订日期、效力状态），知识库范围外问题明确告知、绝不臆造。

## 快速开始

```bash
# 1. 安装依赖（Python 3.10+）
pip install -r requirements.txt

# 2. 初始化并入库种子文书
python -m app.ingest init
python -m app.ingest add data/seed/*.json

# 3. 全库校验 + 文书清单
python -m app.ingest validate
python -m app.ingest list

# 4. 启动服务（推荐：Windows 双击 start.bat）
start.bat
# 或命令行启动：
uvicorn app.main:app --host 0.0.0.0 --port 8000

# 5. 使用
#   - 浏览器打开 http://localhost:8000 （政务深绿风格聊天界面，投屏友好）
#   - API 调试台 http://localhost:8000/docs
#   - 命令行问答：
curl -X POST http://localhost:8000/api/chat -H "Content-Type: application/json" \
  -d '{"question": "公司拖欠我三个月工资，可以主张双倍工资吗？"}'
```

## 配置密钥

密钥统一放在项目根目录的 `keys.env`（已加入 `.gitignore`，不会提交到版本库），
由 `app/config.py` 启动时自动读取；环境变量优先于该文件。

```bash
cp keys.env.example keys.env   # 然后把里面的占位符换成自己的密钥
```

| 变量 | 用途 | 不配置的后果 |
|---|---|---|
| `LLM_API_KEY` | DeepSeek 等 OpenAI 兼容大模型 | 问答降级为「条文直出」：只列相关法条原文，不做生成 |
| `XFYUN_APPID` / `XFYUN_API_KEY` / `XFYUN_API_SECRET` | 讯飞方言语音识别 | 仅方言语音输入不可用，文字问答不受影响 |

非密钥配置（服务地址、模型名）可直接改 `start.bat`，或写进 `keys.env`：

```bash
LLM_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-flash
```

## 目录结构

```
app/
  config.py      全局配置（效力位阶、状态机、LLM 环境变量）
  db.py          SQLite + FTS5 数据模型与访问层
  versioning.py  版本管理与效力状态机
  validate.py    入库校验 + 全库时效校验
  ingest.py      入库/更新/废止/核验命令行工具
  retriever.py   检索（BM25 + 位阶加权 + 现行有效过滤，含向量扩展点）
  llm.py         LLM 客户端（无 Key 降级）
  agent.py       智能体编排（引用即检索 / 越界兜底 / 出处标注）
  main.py        FastAPI 接口
data/seed/       种子知识库（待人工核验标记）
docs/技术方案.md  完整技术方案
scripts/smoke_test.py  冒烟测试
```

## 重要安全提示

种子数据中所有条文均标记 `verified: false`（待核验）。**正式上线前必须**
通过 `python -m app.ingest verify <doc_id>` 逐部核对官方发布文本后方可启用，
本系统不对未核验条文的准确性负责。所有智能体回答均附带免责声明。
