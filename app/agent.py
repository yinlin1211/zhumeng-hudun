"""法律咨询智能体编排（律师问诊式）。

按项目指令实现"感知-规划-行动"律师问诊流程：

1. 紧急兜底: 命中人身安全/重大伤亡关键词时立即返回 110/120 提示，不走常规咨询。
2. 感知层: 识别核心诉求（欠薪/工伤/合同/文书代写）并提取关键要素；
3. 追问层: 信息缺失时主动多轮追问（表单式提问，前端渲染为 UI 表单）；
4. 规划层: 内心规划维权链路（收集证据→找老板谈→12333 投诉→劳动仲裁→法院起诉）；
5. 行动层: 调用工具卡片（文本分析/劳动争议调解实务工具包/合同审查/法律文书助手），
   其中"劳动争议调解实务工具包"由本系统的法条检索+引用校验真实落地，其余工具以
   tool_card 字段告知前端匹配意图，由前端展示对应卡片/引导；
6. 输出层: 思考过程包裹在 [智能体思考过程开始/结束] 内，正式回复放在【律师解答】后；
7. 防幻觉: 保留"引用即检索"校验（机制层）+ 越界兜底（12348/12333）；
8. 安全边界: 不索要敏感信息，不承诺必胜，紧急情况优先 110/120。

响应结构:
{
  "question":  原问题
  "thinking":  思考过程（前端可折叠展示）
  "answer":    【律师解答】正文（Markdown，先结论→再步骤→最后提醒）
  "form":      表单式追问 {title, fields:[{key,type,label,required,options}]} 或 null
  "tool_card": 匹配到的工具卡片名称 或 null
  "citations": [ {document_name, article_no, snippet, enacted_date, revision_date, status, score} ]
  "in_scope":  true/false
  "degraded":  true 表示无 LLM Key 的降级输出
  "disclaimer":免责声明
  "opening":   true 表示这是开场白（不调 LLM）
}
"""

import re

from . import config, db, normalize, retriever
from .llm import ChatClient, LLMUnavailable

_REF_RE = re.compile(r"《([^》]+)》第([^条]+)条")


def _short_doc_name(name: str) -> str:
    """去“中华人民共和国”前缀，统一文书全名与口语简称（《劳动合同法》）。"""
    return re.sub(r"^中华人民共和国", "", (name or "").strip())

# ---- 结构化输出标签（与前端协议一致，勿改）----
_THINK_OPEN = "[智能体思考过程开始]"
_THINK_CLOSE = "[智能体思考过程结束]"
_ANSWER_OPEN = "【律师解答】"
_ANSWER_CLOSE = "[律师解答结束]"
_FORM_OPEN = "[表单开始]"
_FORM_CLOSE = "[表单结束]"

_THINK_RE = re.compile(r"\[智能体思考过程开始\]([\s\S]*?)\[智能体思考过程结束\]")
_ANSWER_RE = re.compile(r"【律师解答】([\s\S]*?)(?:\[律师解答结束\]|$)")
_FORM_RE = re.compile(r"\[表单开始\]([\s\S]*?)\[表单结束\]")
_TOOL_RE = re.compile(r"准备调用[：:]\s*([^\n\r，。]+)")

OPENING = (
    "师傅您好，我是筑梦护盾的维权律师。您遇到了什么难处？直接跟我说，"
    "我帮您用最通俗的话把法律问题分析清楚。您可以直接告诉我：老板欠了多少？还是受了工伤？"
)

# 紧急情况关键词（人身安全/重大伤亡），命中即中断常规咨询。
# 注意：不能用“扣我/被扣/关我/重伤”——它们与“扣我工资”“工资被扣”“关我什么事”
# “工伤重伤”重合并会把普通欠薪/工伤咨询误升级为 110/120 话术。
_EMERGENCY_KEYWORDS = [
    "被打", "被打伤", "被殴打", "打我", "揍我", "殴打我", "有生命危险", "生命安全",
    "人身威胁", "威胁人身", "威胁我", "要打我",
    "被拘禁", "被关起来", "被关押", "被扣押", "非法拘禁", "绑架", "限制人身",
    "把我关", "把人扣", "扣我人", "扣住我", "押着我", "不让我走", "不让走",
    "绑我", "拘禁我",
    "大出血", "昏迷", "断手", "断指", "断腿", "快死了", "活不下去",
    "跳楼", "跳桥", "不想活", "自残", "自杀",
]

# 方言口语特征词（按方言分类），命中则检索前先翻译成普通话
# 广西话：桂柳话属西南官话（归到四川话特征），白话属粤语（归到粤语特征）
_DIALECT_SIGNS: dict[str, list[str]] = {
    "粤语": [
        "唔", "咗", "哋", "佢", "嘅", "咁", "嘢", "啲", "嗰", "俾", "冇", "嚟",
        "睇", "諗", "噉", "嘞", "囉", "噃", "邊度", "點解", "幾時", "咩",
        "返工", "人工", "出糧", "唔該", "多謝", "飲", "食", "瞓", "嘢",
    ],
    "四川话": [
        "啥子", "搞嘛", "娃儿", "巴适", "咋个", "冇得", "要得", "得行",
        "雄起", "硬是", "莫得", "搞锤子", "安逸", "撇脱", "搞快", "搞慢",
    ],
    "湖南话": [
        "冒得", "何解", "么子", "嬲", "呷", "策", "灵泛", "霸蛮", "拽",
        "娭毑", "堂客", "伢子",
    ],
}

# 扁平汇总（用于快速判断是否含方言）
_DIALECT_KEYWORDS = [w for kws in _DIALECT_SIGNS.values() for w in kws]

_TRANSLATE_PROMPT = (
    "你是一个方言翻译器。把用户说的话翻译成标准普通话书面语，"
    "只输出翻译结果，不要解释、不要加引号、不要换行。"
    "如果已经是普通话，原样输出。保留关键信息（金额、时间、人名等）。"
)

# 诉求意图关键词（用于 tool_card 匹配提示）
_INTENT_KEYWORDS = {
    "法律文书助手": ["代写", "起草", "申请书", "起诉状", "答辩状", "仲裁申请书", "投诉书", "举报书", "文书"],
    "合同审查": ["合同审查", "这合同", "这个合同", "能不能签", "签合同", "合同有坑", "合同条款"],
    "劳动争议调解实务工具包": ["能赔多少", "赔偿", "赔偿金", "补偿金", "双倍工资", "二倍工资", "加班费", "工伤待遇", "算一下", "能拿多少"],
}

# 范围判定强信号白名单：命中即视为“农民工维权领域内”。
# 关键原则是**顺序无关的核心词**——原版写死“辞退我/没签合同”这类整串，
# 用户说“把我辞退了”“没跟我签合同”就绕过去了，导致本领域问题被误判超纲。
# 这里刻意不收录“老板/工资/赔偿”等过泛的词（“老板打麻将输了钱”不该进范围）。
_INTENT_SCOPE_KEYWORDS = [
    # 欠薪 / 劳动报酬
    "欠薪", "欠工资", "欠工钱", "欠钱", "拖欠", "拖工资", "拖着不给", "跑路", "老板跑",
    "不发工资", "不给工资", "不给工钱", "不给钱", "没发工资", "少发工资", "扣发工资",
    "扣工资", "扣钱", "压工资", "克扣", "罚款", "工资条", "结工资", "发工资", "讨薪",
    "血汗钱", "工钱", "打工钱", "欠条", "双倍工资", "二倍工资", "加班费", "加班",
    "最低工资", "误工费",
    # 用工主体 / 场景
    "包工头", "工头", "工地", "施工现场", "用人单位", "厂里", "厂子", "中介", "介绍费",
    # 合同 / 解除 / 离职
    "劳动合同", "签合同", "没合同", "无合同", "书面合同", "合同到期", "试用期", "转正",
    "续签", "辞退", "开除", "解雇", "辞工", "辞职", "离职", "走人", "裁员", "被裁",
    "解除劳动", "违法解除", "经济补偿", "补偿金", "赔偿金", "违约金",
    # 押金 / 证件
    "押金", "保证金", "扣证件", "扣押身份证", "扣身份证", "压身份证", "离职证明",
    # 工伤 / 社保
    "工伤", "受伤", "摔伤", "砸伤", "伤残", "职业病", "停工留薪", "医药费", "医药",
    "社保", "社会保险", "五险", "养老保险", "医保", "公积金", "自愿放弃社保",
    "上下班", "下班路上",
    # 休假 / 特殊保护
    "年假", "年休假", "未休年假", "休假", "病假", "产假", "婚假", "请假",
    "怀孕", "女工", "童工", "未成年工", "高温补贴", "调岗",
    # 维权程序
    "劳动仲裁", "仲裁时效", "仲裁委员会", "劳动监察", "监察大队", "12333", "法律援助",
]


SYSTEM_PROMPT = """你是“筑梦护盾”平台的维权律师，正在与一位建筑行业的农民工师傅“一对一”对话。你具有丰富的农民工维权经验，能把晦涩的法律条文翻译成老百姓听得懂的大白话。你不是冷冰冰的问答机器，要让对方感觉到“自己真的在跟一位耐心、靠谱的律师交流”。

==== 一、角色与人设 ====
1. 极度专业，但表达极度接地气；称呼对方“师傅”或“您”，语气温暖、坚定、有力量，多用“咱们”“第一步”“您可以”。
2. 术语翻译：说人话。“仲裁请求”翻译为“您想让仲裁委支持您什么”；“被申请人”翻译为“您要告的老板或公司”；“仲裁时效”翻译为“多久之内可以去告”；“经济补偿金”翻译为“单位辞退你时按规定要补给你的钱”。
3. 每次回答尽量包含：做什么、为什么、去哪里、带什么、时限（如工伤 1 年内必须申请，劳动仲裁时效 1 年）。
4. 对老年用户，主动建议让子女或工友协助，或使用语音播报、大字号功能。

==== 二、核心交互模式：律师问诊式（感知-规划-行动） ====
千万不要用户问一句就直接给长篇大论的答案。农民工表达往往零散，你必须模仿真实律师接待当事人的流程：先安抚、再感知、多轮追问、补齐信息、最后给方案。
1. 【感知层】用户输入后，首先识别核心诉求：是欠薪？工伤？还是要签合同？自动提取关键信息（工种、欠了多少、有没有合同、是否受伤、是否有聊天记录等）。
2. 【追问层】如果关键信息缺失，必须主动多轮追问（每次问 2-3 个点，不要一次问太多），一步步帮用户把案情拼图拼完整。不要等用户主动提供。追问用“表单式提问”输出（格式见下）。
3. 【规划层】案情收集清楚后，你需要在内心规划维权链路（先收集证据 → 找老板谈 → 打 12333 投诉 → 申请劳动仲裁 → 法院起诉）。
4. 【输出层】给出最终的法律建议、行动清单、或者生成文书。

==== 三、内置技能与工具调用（匹配 UI 卡片） ====
你拥有以下技能卡片，当用户诉求匹配时，要在思考过程中明确“准备调用：XXX”，前端会展示对应卡片：
1. 【文本分析】（大模型基础能力）：当用户发来零散口述时，自动从杂乱口述中提取：欠薪主体、欠薪金额、欠薪时间、是否有证据、是否有合同等关键要素。信息缺失则暂停法律解答，先多轮追问补齐（每次 2-3 个点）。
2. 【劳动争议调解实务工具包】：当用户想知道“能赔多少钱”、咨询工伤待遇、未签合同双倍工资、经济补偿金、加班费计算时调用。下方【检索到的法律条文】即该工具包的真实数据来源，计算时必须依据其中的法条，用大白话向用户解释：“师傅，按照法律规定，老板不仅要补发您的工资，还得额外赔偿您……块钱，因为……”。
3. 【合同审查】：当用户询问“这合同能不能签”、上传或粘贴了一份合同时调用。重点核查“工伤自理”“自愿放弃社保”“押金”“扣证件”等违法条款。把风险报告翻译成大白话逐条告诉用户：“师傅，这合同里有坑，第一条写了‘受伤自理’，这是违法的，您千万别签，建议让他删掉。”
4. 【法律文书助手】：当用户说“我要去仲裁，不知道怎么弄”，或需要生成“投诉举报书”“民事起诉状”“仲裁申请书”时调用。按多轮收集的信息生成符合国家规范格式的文书底稿（支持导出 Word）。生成后以律师口吻提醒：“这是我帮您起草的初稿，您核对一下上面写的名字、金额和日期对不对。没问题的话，直接导出打印带去仲裁委。”

==== 四、输出结构（必须严格遵守，前端依赖此格式渲染） ====
你的每次回复必须严格按以下三段输出，标签一字不差：

[智能体思考过程开始]
（写清：我识别到了什么意图、我还缺什么信息、我准备调用哪个工具。可分点写，每点一行）
[智能体思考过程结束]

【律师解答】
（这里是给用户看的正式回复，使用 Markdown 格式：先给结论，再给步骤，最后给提醒。
 可以用 # 标题、**加粗**、有序列表 1. 2. 3.、无序列表 -、代码块等。
 语言要通俗，称呼“师傅”“您”。需要分点说明时用“第一、第二”或“1. 2.”。
 引用法律条文时，先用人话说明意思，再在后面带上《文书名称》第X条作为依据，不要大段抄条文。
 不要在【律师解答】段里再出现 [智能体思考过程...] 标签。）
[律师解答结束]

当需要追问补齐信息时，在【律师解答】段内嵌入“表单式提问”，格式如下（前端会渲染为可填写表单）：

[表单开始]
标题：补齐欠薪关键信息
字段：
- 金额|text|老板一共欠您多少钱？|必填
- 时间|text|欠薪大概从什么时候开始？|必填
- 合同|select|有没有签过劳动合同？|必填|有,没有,记不清
- 证据|text|手上有什么证据？（工资条、聊天记录、考勤等）|选填
[表单结束]

字段行格式：- 字段key|类型|标签|是否必填|选项（仅 select 用，逗号分隔）
类型只支持：text（文本）、select（下拉选择）、radio（单选）、textarea（长文本）。
表单后面可以再加一两句引导，如“填完我再帮您算账、走流程”。

表单格式铁律（任何情况下不得违反，前端程序按此解析）:
1. 标签 [表单开始] 和 [表单结束] 必须用简体中文原文，一字不差——即使用方言回答、即使其他文字都是粤语/四川话，这两个标签也不能翻译、不能改成繁体、不能用【表格】等其他写法代替；
2. 字段行的竖线 | 分隔格式必须保留，类型必须是 text/select/radio/textarea 之一，“必填/选填”两个词保持简体原文；
3. 只有字段的“标签”文字（即第三段说明文字）可以用方言写，方便用户看懂；
4. 每次需要用户补齐信息时都必须输出表单，不能因为用方言回答就省略表单或改用纯文字列举。

==== 五、安全边界与合规兜底 ====
1. 必须合规。不索要身份证号、银行卡号、密码、验证码。
2. 严禁承诺“一定赢”“一定能拿回钱”。必须提醒：“我是 AI 法律助手，我的解答是基于法律规定和逻辑的分析。如果案情复杂，建议拨打 12348 法律援助热线，或去当地司法局找免费律师帮忙。”
3. 遇到紧急情况（人身安全威胁、被非法拘禁、重大工伤），立刻中断常规咨询，在【律师解答】里优先提示：立即拨打 110、120，并想办法保留录音、录像证据。
4. 若用户诉求超出线上 AI 的处理能力（如涉及多个责任主体、跨省复杂纠纷），主动给出人工介入出口，引导拨打 12348 或去当地劳动监察大队。
5. 检索结果中没有覆盖的问题，明确回答“该问题超出本知识库范围”，不猜测、不编造，引导拨打 12348 法律援助热线或 12333 人社服务热线。引用法条只能引用下方【检索到的法律条文】中出现的条款，禁止编造《文书》第X条。

==== 六、关于本次输入 ====
上方【检索到的法律条文】是系统已为你检索到的现行有效法条（“劳动争议调解实务工具包”的真实数据）。
如果【历史对话】存在，请结合历史继续多轮问诊，不要重复追问已收集的信息。

==== 七、方言处理 ====
用户可能用方言口语（粤语、四川话、湖南话、广西话等）通过语音输入提问，例如“老板欠我三个月人工唔俾”（粤语：老板欠我三个月工资不给）。
系统已在检索前把方言翻译成普通话，你会同时收到【用户原话】、【普通话意思】和【用户方言类型】。

回答规则（重要）:
1. 如果【用户方言类型】不是“普通话”，你的【律师解答】说明文字要用与用户一致的方言文字版回答——粤语问就用粤语文字答（如“师傅，你先唔好急，老板欠人工呢件事，法律系撑你嘅”），四川话问就用四川话文字答（如“师傅，你先莫急，老板欠工资这个事情，法律是站咱们这边的”），湖南话同理。
2. 但引用的法律条文原文保持普通话（法条不能翻译），只把你的说明、追问、提醒翻译成方言。
3. 称呼“师傅”、用大白话，跟其他场景一致；思考过程用普通话写（方便理清逻辑）。
4. 如果方言文字表达有困难，可适度混用普通话，但主体尽量用方言，让用户感觉亲切。
5. 如果【用户方言类型】是“普通话”，正常用普通话回答。
6. 无论用什么方言回答，输出结构标签（[智能体思考过程开始/结束]、【律师解答】、[表单开始/结束]、字段竖线格式）必须保持简体中文原文不变——这些是前端程序解析用的格式标记，翻译它们会导致页面无法显示表单。
"""


def _strip_fences(text: str) -> str:
    """仅清理代码块围栏，保留 Markdown 结构与思考/解答/表单标签。

    与旧版不同：本次按项目指令允许并要求 Markdown 输出，不再做全量纯文本化。
    """
    if not text:
        return text
    text = re.sub(r"```[^\n]*\n?", "", text)
    text = re.sub(r"```", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_tool_card(question: str, thinking: str) -> str | None:
    """工具卡片识别：用户问题关键词优先（反映真实意图），
    匹配不到再从 thinking 里抽取‘准备调用：XXX’。

    降级模式下 thinking 是固定文本，必须让问题关键词优先，否则用户问
    “代写仲裁申请书”会被降级 thinking 里的“劳动争议调解实务工具包”盖过。
    thinking 里若抽到非标准卡片名（如“先不急着给方案”），返回 None，
    避免前端显示“调用技能：先不急着给方案”这类误导性标签。
    """
    for card, kws in _INTENT_KEYWORDS.items():
        if any(k in question for k in kws):
            return card
    if thinking:
        m = _TOOL_RE.search(thinking)
        if m:
            name = m.group(1).strip().rstrip("。， ")
            for card in ("法律文书助手", "合同审查", "劳动争议调解实务工具包", "文本分析"):
                if card in name:
                    return card
            # 抽到非标准名：不显示，避免误导
            return None
    return None


def _parse_form(form_text: str) -> dict | None:
    """解析 [表单开始]...[表单结束] 内的文本为结构化表单。

    支持格式：
        标题：补齐欠薪关键信息
        字段：
        - 金额|text|老板一共欠您多少钱？|必填
        - 合同|select|有没有签过劳动合同？|必填|有,没有,记不清
    """
    if not form_text:
        return None
    title = ""
    fields: list[dict] = []
    for line in form_text.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("字段"):
            continue
        if line.startswith("标题"):
            # 标题：xxx  或  标题: xxx
            title = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            continue
        if line.startswith("-"):
            line = line.lstrip("- ").strip()
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            field = {
                "key": parts[0],
                "type": parts[1] if parts[1] in ("text", "select", "radio", "textarea") else "text",
                "label": parts[2],
                "required": len(parts) > 3 and parts[3] in ("必填", "required", "必填项"),
            }
            if len(parts) > 4 and parts[4]:
                field["options"] = [o.strip() for o in parts[4].split(",") if o.strip()]
            fields.append(field)
    if not fields:
        return None
    return {"title": title or "请补充以下信息", "fields": fields}


class LegalAgent:
    def __init__(self, client: ChatClient | None = None):
        self.client = client or ChatClient()
        self.degraded = not self.client.available

    # ---------------- 对外入口 ----------------

    def opening(self) -> dict:
        """会话开场白（不调 LLM、不检索）。"""
        return {
            "question": "",
            "thinking": "会话开始，播报开场白，等待用户描述诉求。",
            "answer": OPENING,
            "form": None,
            "tool_card": None,
            "citations": [],
            "in_scope": True,
            "degraded": self.degraded,
            "disclaimer": config.DISCLAIMER,
            "opening": True,
        }

    def answer(self, question: str, top_k: int | None = None,
               history: list[dict] | None = None) -> dict:
        """律师问诊式回答。

        history: [{role: "user"|"assistant", content: "..."}]，前端持有并回传，后端无状态。
        """
        # 1. 紧急兜底（人身安全优先）
        if self._emergency_check(question):
            return self._emergency_response(question)

        # 2. 方言归一化：检测到方言口语时，先用 LLM 翻译成普通话用于检索
        #    提高法条命中率（粤语"人工"→普通话"工资"等）
        #    同时识别方言类型，供 LLM 用同种方言文字回答
        dialect = self._detect_dialect(question)
        search_q = question
        translated = None
        if dialect != "普通话" and not self.degraded:
            try:
                translated = self._normalize_dialect(question)
                if translated and translated.strip() and translated.strip() != question:
                    search_q = translated.strip()
            except Exception:
                pass  # 翻译失败不影响主流程，用原话检索

        # 3. 检索现行有效法条（用普通话版本提高命中率）
        conn = db.connect()
        try:
            hits = retriever.search(conn, search_q, top_k=top_k, active_only=True)
        finally:
            conn.close()

        in_scope, reason = self._scope_check(search_q, hits)
        citations = [self._to_citation(h) for h in hits[:3]]
        ctx = self._format_context(hits[:5]) if hits else "（本次未检索到相关法条）"

        # 3. 越界兜底：仍有思考与引导，但不臆造法条
        if not in_scope:
            thinking = (
                "识别意图：初步判断为法律咨询。\n"
                f"范围判定：{reason}，本知识库未收录相关现行有效法条。\n"
                "准备调用：无（引导用户拨打 12348/12333 人工咨询）。"
            )
            answer = (
                "【律师解答】\n"
                "师傅，您说的这个事儿，我手头的法律知识库暂时没收录能对得上的条文。"
                "为了不误导您，我不会瞎猜。\n\n"
                "**咱们这么办：**\n"
                "1. 拨打 **12348 法律援助热线**（免费法律咨询）\n"
                "2. 拨打 **12333 人社服务热线**（劳动保障投诉咨询）\n"
                "3. 带上您手上的材料（合同、工资条、聊天记录等）去当地**法律援助中心**或**劳动监察大队**当面问\n\n"
                "我是 AI 法律助手，本回答基于法律规定和逻辑分析，不构成正式法律意见。"
            )
            return {
                "question": question,
                "thinking": thinking,
                "answer": answer,
                "form": None,
                "tool_card": None,
                "citations": [],
                "in_scope": False,
                "degraded": self.degraded,
                "disclaimer": config.DISCLAIMER,
                "opening": False,
            }

        # 4. 降级模式：无 LLM Key 时直出检索条文 + 律师口吻说明
        if self.degraded:
            thinking = (
                "识别意图：法律咨询。\n"
                "当前未配置大模型 Key，进入降级模式：直出检索到的高相关法条，"
                "并按律师问诊流程提示用户补齐信息。\n"
                "准备调用：劳动争议调解实务工具包（法条直出）。"
            )
            answer = self._fallback_answer(hits)
            return {
                "question": question,
                "thinking": thinking,
                "answer": answer,
                "form": None,
                "tool_card": _detect_tool_card(question, thinking),
                "citations": citations,
                "in_scope": True,
                "degraded": True,
                "disclaimer": config.DISCLAIMER,
                "opening": False,
            }

        # 5. 正常路径：组装历史 + 检索上下文 → LLM 生成
        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            for h in history[-10:]:  # 最近 10 轮，避免超长
                role = h.get("role")
                content = h.get("content", "")
                if role in ("user", "assistant") and content:
                    messages.append({"role": role, "content": str(content)[:1500]})
        messages.append({
            "role": "user",
            "content": (
                f"【检索到的法律条文】\n{ctx}\n\n"
                + (f"【用户原话（{dialect}）】\n{question}\n\n【普通话意思（系统翻译）】\n{translated}"
                   if translated and translated.strip() and translated.strip() != question
                   else f"【用户本次问题】\n{question}")
                + f"\n\n【用户方言类型】{dialect}"
            ),
        })

        try:
            raw = self.client.generate(messages, temperature=0.3, max_tokens=2200)
        except LLMUnavailable as e:
            # 单次安全降级：条文直出 + 提示，下次仍重试 LLM
            thinking = (
                "识别意图：法律咨询。\n"
                f"LLM 调用失败（{e}），本次安全降级为条文直出。\n"
                "准备调用：劳动争议调解实务工具包（降级）。"
            )
            return {
                "question": question,
                "thinking": thinking,
                "answer": self._fallback_answer(hits),
                "form": None,
                "tool_card": _detect_tool_card(question, thinking),
                "citations": citations,
                "in_scope": True,
                "degraded": True,
                "disclaimer": config.DISCLAIMER,
                "opening": False,
            }

        # 6. 解析结构化输出：thinking / answer / form / tool_card
        raw = _strip_fences(raw)
        thinking = self._extract(raw, _THINK_RE, default="")
        answer = self._extract(raw, _ANSWER_RE, default=raw)
        form_text = self._extract(raw, _FORM_RE, default="")
        form = _parse_form(form_text)

        # 若 LLM 未按格式输出 thinking/answer，做容错：整段当作 answer
        if not thinking and not answer:
            thinking = ""
            answer = raw
        elif not answer:
            answer = raw

        # 从 answer 正文中剔除表单块原文（表单已单独解析到 form 字段，
        # 避免前端同时显示表单标记文本和渲染后的表单，造成“看起来没回答”）
        answer = _FORM_RE.sub("", answer).strip()
        # 同时剔除可能残留的 [表单开始]/[表单结束] 单标签
        answer = answer.replace(_FORM_OPEN, "").replace(_FORM_CLOSE, "").strip()

        # 7. 引用即检索校验（只校验 answer 段，不校验 thinking）
        answer, warning = self._verify_citations(answer, hits)
        if warning:
            answer = answer + f"\n\n> {warning}"

        tool_card = _detect_tool_card(question, thinking)

        return {
            "question": question,
            "thinking": thinking.strip(),
            "answer": answer.strip(),
            "form": form,
            "tool_card": tool_card,
            "citations": citations,
            "in_scope": True,
            "degraded": False,
            "disclaimer": config.DISCLAIMER,
            "opening": False,
        }

    # ---------------- 内部逻辑 ----------------

    @staticmethod
    def _extract(text: str, pattern: re.Pattern, default: str = "") -> str:
        m = pattern.search(text)
        return m.group(1).strip() if m else default

    @staticmethod
    def _emergency_check(question: str) -> bool:
        return any(kw in question for kw in _EMERGENCY_KEYWORDS)

    @staticmethod
    def _is_dialect(question: str) -> bool:
        """检测是否含方言口语特征词（粤语为主）。"""
        return any(kw in question for kw in _DIALECT_KEYWORDS)

    @staticmethod
    def _detect_dialect(question: str) -> str:
        """识别用户用的方言类型（粤语/四川话/湖南话/普通话）。"""
        for name, kws in _DIALECT_SIGNS.items():
            if any(k in question for k in kws):
                return name
        return "普通话"

    def _normalize_dialect(self, question: str) -> str | None:
        """用 LLM 把方言口语翻译成标准普通话，用于提高法条检索命中率。
        失败返回 None，不影响主流程。
        """
        if not self.client.available:
            return None
        try:
            raw = self.client.generate(
                [
                    {"role": "system", "content": _TRANSLATE_PROMPT},
                    {"role": "user", "content": question},
                ],
                temperature=0.0,
                max_tokens=200,
            )
            return raw.strip() if raw else None
        except LLMUnavailable:
            return None

    def _emergency_response(self, question: str) -> dict:
        thinking = (
            "识别到紧急情况：用户可能面临人身安全威胁、被非法拘禁或重大伤亡。\n"
            "立即中断常规法律咨询，优先保障人身安全。\n"
            "准备调用：紧急救助（110/120）。"
        )
        answer = (
            "【律师解答】\n"
            "师傅，您说的情况很紧急，**人身安全第一**，咱们先保命再讲法律。\n\n"
            "**马上做三件事：**\n"
            "1. 立即拨打 **110** 报警\n"
            "2. 如果有伤情，立即拨打 **120** 急救\n"
            "3. 想办法保留证据：**录音、录像、拍照、找证人**\n\n"
            "不要等，先报警保安全。等您安全了，咱们再坐下来聊法律维权、要赔偿。\n\n"
            "如果您现在不方便说话，把手机给身边的工友或家人，让他们帮您报警。\n"
            "我是 AI 法律助手，本回答不构成正式法律意见，紧急情况以 110/120 指挥为准。"
        )
        return {
            "question": question,
            "thinking": thinking,
            "answer": answer,
            "form": None,
            "tool_card": "紧急救助",
            "citations": [],
            "in_scope": True,
            "degraded": False,
            "disclaimer": config.DISCLAIMER,
            "opening": False,
        }

    def _scope_check(self, question: str, hits: list[dict]) -> tuple[bool, str]:
        """范围判定（三层信号，避免臆造）:
        1. 零命中 -> 范围外（没有可引用条文就不能回答）；
        2. 强信号白名单命中（“欠工资/工伤/双倍工资/辞退/…”等农民工维权核心词）
           -> 范围内。白名单同时匹配原话与归一化文本，因此“把我辞退了”“没跟我签合同”
           这类带把字句/插入虚词的说法也能命中；
        3. 无强信号时，要求检索结果的实义字符覆盖率 >= 0.7 且有 >= 3 个连续双字命中
           —— 门槛刻意设高，用于识别“用户直接用法律术语提问”的情形，
           同时挡住“火星移民的工资纠纷”这类靠常见字蹭到的匹配。
        判定看 top-3 而非只看 top-1：top-1 可能被跨文书的无意义排序挤掉。
        """
        if not hits:
            return False, "知识库中未检索到与问题相关的现行有效法律条文"
        norm = normalize.to_legal(question)
        if any(kw in question or kw in norm for kw in _INTENT_SCOPE_KEYWORDS):
            return True, ""
        qchars = normalize.query_chars(norm)
        for h in hits[:3]:
            if h.get("coverage", 0) >= 0.7 and normalize.bigram_hits(qchars, h.get("content", "")) >= 3:
                return True, ""
        return False, "问题与知识库现有条文的匹配度过低，无法可靠回答"

    @staticmethod
    def _bigram_hits(qchars: list[str], content: str) -> int:
        """统计查询实义字符的相邻双字对在条文中连续出现的数量。"""
        return normalize.bigram_hits(qchars, content)

    def _to_citation(self, h: dict) -> dict:
        return {
            "document_name": h["document_name"],
            "article_no": h["article_no"],
            "snippet": h["content"][:120],
            "enacted_date": h["enacted_date"],
            "revision_date": h["revision_date"],
            "status": h["status"],
            "score": h["score"],
        }

    def _format_context(self, hits: list[dict]) -> str:
        lines = []
        for i, h in enumerate(hits, 1):
            lines.append(
                f"[{i}] 《{h['document_name']}》{h['article_no']} "
                f"（生效 {h['enacted_date']}；效力状态 {h['status']}）\n{h['content']}"
            )
        return "\n\n".join(lines)

    def _fallback_answer(self, hits: list[dict]) -> str:
        """无 LLM 时的降级回答：律师口吻 + 条文直出 + 追问引导。"""
        parts = [
            "【律师解答】",
            "师傅，我现在没法用大模型帮您细聊，但已经从知识库里翻出几条最相关的法条，"
            "您先看看；配置好大模型 Key后我会用大白话帮您分析。\n",
            "**相关法条（直出，请以官方文本为准）：**",
        ]
        for i, h in enumerate(hits[:3], 1):
            parts.append(f"{i}. {retriever.cite_of(h)}\n   {h['content']}")
        parts.append(
            "\n**接下来您可以：**\n"
            "1. 把欠薪金额、时间、有没有合同、手上有什么证据补给我，我帮您算账\n"
            "2. 拨打 **12348 法律援助热线**（免费）或 **12333 人社服务热线**\n"
            "3. 我是 AI 法律助手，本回答不构成正式法律意见。"
        )
        return "\n".join(parts)

    def _verify_citations(self, answer: str, hits: list[dict]) -> tuple[str, str | None]:
        """引用即检索：检查 LLM 引用的每条《文书》第X条都在检索结果中。

        article_no 字段存完整条款号（如“第五十条”），模型输出“《文书》第三十条”
        经正则提取后还原为“第三十条”再比对，保证引用可溯源。

        文书名按去“中华人民共和国”前缀的简称比对：模型习惯写《劳动合同法》，
        而库内全名是《中华人民共和国劳动合同法》，不折算会把真引用误报成可疑引用。
        """
        allowed = {(h["document_name"], h["article_no"]) for h in hits}
        by_short: dict[str, set[str]] = {}
        for name, art in allowed:
            by_short.setdefault(_short_doc_name(name), set()).add(art)
        bad = set()
        for m in _REF_RE.finditer(answer):
            name, art = m.group(1).strip(), "第" + m.group(2).strip() + "条"
            if (name, art) in allowed or art in by_short.get(_short_doc_name(name), set()):
                continue
            bad.add((name, art))
        if bad:
            sample = "；".join(f"《{n}》{a}" for n, a in sorted(bad))
            warning = (f"检测到引用未出现在本次检索结果中的条款（{sample}），"
                       "已按规则提示复核；请以上方检索条文为准，必要时人工核实。")
            return answer, warning
        return answer, None
