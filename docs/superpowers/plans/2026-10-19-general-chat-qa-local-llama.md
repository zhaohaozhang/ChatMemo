# 通用聊天问答与本地 llama.cpp 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把现有聊天文字检索扩展为可验证的八类本地问答，加入安全的一键 llama.cpp 生命周期管理、可重复的虚构评测，并在全部发布门槛通过后准备 GitHub 发布。

**Architecture:** 以 SQLite 中稳定且不复用的联系人、来源和消息 ID 为事实基础；`qa.py` 负责路由与编排，`search.py` 负责证据召回，`qa_contract.py` 负责逐单元回答与引用验证，`llama_runtime.py` 只管理本应用启动且持有句柄的本地模型进程。确定性分析不依赖模型；模型只整理已选证据，任何单元引用非法时整段降级。

**Tech Stack:** Python 3.11+ 标准库、SQLite、`unittest`、`ThreadingHTTPServer`、原生 HTML/CSS/JavaScript、Windows llama.cpp OpenAI 兼容接口。

**Spec:** `docs/superpowers/specs/2026-10-19-general-chat-qa-local-llama-design.md`

## Global Constraints

- 保持 Python 3.11+ 标准库实现，不增加运行时第三方依赖。
- 服务和自动启动的 llama.cpp 默认只监听 `127.0.0.1`；不得自动开放防火墙或局域网。
- 真实聊天不得发送到 ChatGPT 或其他云模型；ChatGPT 评审只使用版本化虚构数据和去个人化输出。
- `contact_id`、`source_id`、`message_id` 和来源行号映射必须稳定，永久删除后的 ID 不得复用。
- 联系人切换、删除、恢复、重新导入和索引/规则版本变化必须使相关上下文、候选集、引用与缓存失效。
- llama.cpp 完全不可用时，时间、计数和其他确定性分析仍须工作。
- 停止模型时只操作本应用当前进程持有的 `Popen` 句柄；应用重启后原服务视为外部服务，不得停止。
- 模型输出使用逐条 `answer_units`；任一事实单元结构或引用非法时整段模型结果作废，不修补。
- TXT 和模型输出始终是不可执行数据，不能触发命令、文件、网络或删除操作。

## Review Focus

- 导入范围不完整或存在无时间消息时，“第一次”只能回答当前记录中最早一条并显示覆盖缺口；Task 4 添加测试。
- “最早什么时候说过”“以前是不是”等组合、否定中文不能只落入一个简单关键词意图；Task 3 添加多意图路由测试。
- 同一秒两条相同正文必须保留两个消息 ID，计数与 `scope` 四种数量不能混淆；Task 2 和 Task 5 添加测试。
- 联系人切换、回收、恢复、增量导入和规则版本变化后的旧上下文必须全部拒绝；Task 2 和 Task 8 添加测试。
- 端口被外部服务占用、PID 被复用、模型加载缓慢或生成中停止时，不得误停进程或显示部分模型答案；Task 7 和 Task 8 添加测试。

---

### Task 1: 发布卫生、本地 Git 基线与隐私阻断

**Files:**
- Modify: `.gitignore`
- Create: `release_check.py`
- Create: `tests/test_release.py`

**Interfaces:**
- Consumes: 项目根目录路径。
- Produces: `release_check.scan_tree(root: Path) -> list[str]`，返回会阻断发布的相对路径或敏感模式说明；退出码 `0` 表示安全。

- [ ] **Step 1: 写发布扫描失败测试**

在 `tests/test_release.py` 增加 `ReleaseHygieneTests`，断言 `data/*.sqlite3`、`*.gguf`、`.env`、日志、绝对本机模型路径和真实数据目录会被报告；虚构 fixture、源码与设计文档不被误报。

- [ ] **Step 2: 运行测试并确认失败**

Run: `python -m unittest tests.test_release.ReleaseHygieneTests -v`

Expected: FAIL，原因是 `release_check` 尚不存在。

- [ ] **Step 3: 实现扫描器并扩展忽略规则**

实现 `scan_tree(root: Path) -> list[str]` 和 CLI `main() -> int`。`.gitignore` 至少覆盖 `data/`、`.browser-test-data/`、`.test-tmp/`、`runtime/`、本地配置、`*.sqlite*`、`*.gguf`、`*.log`、缓存、密钥文件和评测临时输出。

- [ ] **Step 4: 验证扫描与现有测试**

Run: `python -m unittest tests.test_release.ReleaseHygieneTests -v`

Expected: PASS。

Run: `python release_check.py`

Expected: `release check passed`，退出码 `0`。

- [ ] **Step 5: 初始化本地仓库并提交安全基线**

先运行 `git init -b main`，再用 `git status --short --untracked-files=all` 人工确认忽略项未出现，然后仅暂存源码、虚构示例、测试和文档。

```bash
git add .gitignore release_check.py tests/test_release.py ai.py parser.py search.py server.py store.py topics.py start.ps1 web examples docs README.md VERIFICATION.md PLAN.md
git commit -m "chore: establish privacy-safe repository baseline"
```

### Task 2: 永不复用的稳定 ID 与联系人修订号

**Files:**
- Modify: `store.py`
- Modify: `tests/test_memory.py`

**Interfaces:**
- Consumes: 现有数据库 schema 和导入流程。
- Produces: `Store._allocate_id(con: sqlite3.Connection, entity: str) -> int`；`Store.contact_revision(contact_id: int) -> int`；联系人字典新增 `revision`。

- [ ] **Step 1: 写迁移和 ID 不复用测试**

增加以下测试：

- `test_existing_database_migrates_id_sequences_without_changing_ids`
- `test_purged_highest_contact_source_and_message_ids_are_never_reused`
- `test_duplicate_file_and_restore_preserve_ids_and_source_lines`

断言旧 ID 原样保留，删除最高 ID 后新 ID 仍严格递增，同秒同文消息仍有不同 ID。

- [ ] **Step 2: 运行新测试并确认失败**

Run: `python -m unittest tests.test_memory.MemoryTests.test_purged_highest_contact_source_and_message_ids_are_never_reused -v`

Expected: FAIL，证明当前普通 `INTEGER PRIMARY KEY` 会留下复用风险。

- [ ] **Step 3: 实现单调 ID 分配与 schema 迁移**

在 schema 中加入 `id_sequences(entity TEXT PRIMARY KEY, next_id INTEGER NOT NULL)` 和 `contacts.revision INTEGER NOT NULL DEFAULT 1`。迁移时以每张表当前 `MAX(id)+1` 初始化计数器；新联系人、来源和消息都显式调用 `_allocate_id` 并在同一事务内插入。

- [ ] **Step 4: 验证 ID、导入、回收与回滚测试**

Run: `python -m unittest tests.test_memory -v`

Expected: 全部 PASS，既有联系人生命周期测试无回归。

- [ ] **Step 5: 提交**

```bash
git add store.py tests/test_memory.py
git commit -m "feat: guarantee stable non-reused record ids"
```

### Task 3: 联系人修订、失效令牌与八类意图路由

**Files:**
- Create: `qa.py`
- Create: `tests/test_qa.py`
- Modify: `store.py`
- Modify: `tests/test_memory.py`

**Interfaces:**
- Consumes: `Store.contact(contact_id)` 返回的 `revision`。
- Produces: `qa.QA_RULE_VERSION = "1"`；`qa.route_question(question: str, previous: dict | None = None) -> dict`；`qa.validate_previous_context(previous: dict | None, contact_id: int, revision: int, rules_version: str) -> dict | None`；`Store.bump_revision(con, contact_id: int) -> int`。

- [ ] **Step 1: 写八类和组合路由测试**

在 `tests/test_qa.py` 覆盖八类意图，并断言：

- “最早什么时候说过工作”包含 `time_location` 与 `semantic_search`；
- “以前是不是更喜欢咖啡”包含 `change_comparison` 与 `evidence_status`；
- “后来呢”继承上一轮主题、时间范围和说话人；
- 空问题、超长问题和不合法 previous context 被拒绝。

- [ ] **Step 2: 写所有失效触发器测试**

在 `tests/test_memory.py` 增加 `test_revision_changes_on_import_trash_and_restore`，断言有效新增导入、移入回收站和恢复都会改变 revision；完全重复文件不改变 revision。在 `tests/test_qa.py` 增加 `test_previous_context_rejected_on_rules_version_change`，由请求中的规则版本验证失效，不伪造数据库 revision 变化。

- [ ] **Step 3: 运行测试并确认失败**

Run: `python -m unittest tests.test_qa tests.test_memory.MemoryTests.test_revision_changes_on_import_trash_and_restore -v`

Expected: FAIL，缺少 `qa` 与 revision 行为。

- [ ] **Step 4: 实现最小规则路由与失效验证**

`route_question` 返回 `primary_intent`、`intents`、`entities`、`time_expression`、`speaker_expression` 和 `inherited_fields`。路由只决定处理器组合，不直接生成事实。context 中的 `rules_version` 固定组合为 `qa:1;topics:<topics.RULE_VERSION>`；`validate_previous_context` 要求联系人 ID、revision 和该组合版本完全匹配，否则返回 `None`。

- [ ] **Step 5: 运行并提交**

Run: `python -m unittest tests.test_qa tests.test_memory -v`

Expected: 全部 PASS。

```bash
git add qa.py store.py tests/test_qa.py tests/test_memory.py
git commit -m "feat: route chat questions and invalidate stale context"
```

### Task 4: 确定性时间与统计分析器

**Files:**
- Modify: `qa.py`
- Modify: `store.py`
- Modify: `tests/test_qa.py`

**Interfaces:**
- Consumes: `route_question(...)` 与联系人消息。
- Produces: `qa.answer_deterministic(store, contact_id: int, route: dict, filters: dict) -> dict | None`；`Store.message_scope(contact_id: int, filters: dict) -> dict`。

- [ ] **Step 1: 写时间和统计失败测试**

覆盖当前导入记录中的首次/最后消息、某时间段数量、说话人数量、活跃天数、最长间隔、最活跃日期、同秒同文计数，以及没有模型配置时仍能回答。

- [ ] **Step 2: 写时间覆盖缺口测试**

`test_first_message_reports_imported_scope_and_unparsed_time_gap` 断言答案措辞包含“当前已导入记录”，并返回 `timestamped_messages`、`untimestamped_messages`；有缺口时不得使用“绝对第一次”。

- [ ] **Step 3: 运行测试并确认失败**

Run: `python -m unittest tests.test_qa.DeterministicAnswerTests -v`

Expected: FAIL，分析器尚不存在。

- [ ] **Step 4: 实现分析器与统一基础结果**

结果包含 `answer_kind="deterministic"`、`answer_units`、`intent`、`scope`、`method`、`uncertainty` 和 `next_context`。统计单元保存 `calculation` 字段；所有可定位结论引用真实消息。

- [ ] **Step 5: 运行并提交**

Run: `python -m unittest tests.test_qa.DeterministicAnswerTests -v`

Expected: 全部 PASS。

```bash
git add qa.py store.py tests/test_qa.py
git commit -m "feat: answer deterministic chat timeline questions"
```

### Task 5: 混合证据召回、四种 scope 数量与上下文解释

**Files:**
- Modify: `search.py`
- Modify: `qa.py`
- Modify: `tests/test_memory.py`
- Modify: `tests/test_qa.py`

**Interfaces:**
- Consumes: 路由中的意图、实体、时间和说话人条件。
- Produces: `search.retrieve_evidence(store, contact_id: int, query: str, route: dict, filters: dict, limit: int = 30) -> dict`；保留现有 `search(...)` 作为兼容包装。

- [ ] **Step 1: 写结构化问题不走关键词测试**

断言“我们第一次对话的时间是哪一天”即使正文不含问题词也由 Task 4 回答；`retrieve_evidence` 只负责内容相关问题。

- [ ] **Step 2: 写混合召回与 scope 计数测试**

覆盖精确原话、相近表达、时间与说话人过滤、前后三条上下文、不同时间点的变化证据和第三人称噪声。`scope` 必须分别返回 `contact_total_messages`、`filtered_messages`、`candidate_messages`、`evidence_messages`，同文重复按两个 ID 计数。

- [ ] **Step 3: 运行测试并确认失败**

Run: `python -m unittest tests.test_qa.EvidenceRetrievalTests tests.test_memory.MemoryTests.test_chinese_search_filter_and_empty_result -v`

Expected: FAIL，现有 `scope.searched_messages` 和词法流程不满足新契约。

- [ ] **Step 4: 实现混合候选、上下文扩展与诚实状态**

先应用联系人、时间和说话人边界，再组合精确短语、现有 n-gram/同义词、主题命中与邻近上下文。精确原话和相近表达分组；零候选只返回“当前范围未找到”，不生成绝对否定。

- [ ] **Step 5: 运行并提交**

Run: `python -m unittest tests.test_qa.EvidenceRetrievalTests tests.test_memory -v`

Expected: 全部 PASS。

```bash
git add search.py qa.py tests/test_qa.py tests/test_memory.py
git commit -m "feat: add scoped hybrid evidence retrieval"
```

### Task 6: 逐单元回答契约与模型整段降级

**Files:**
- Create: `qa_contract.py`
- Modify: `ai.py`
- Modify: `qa.py`
- Modify: `tests/test_qa.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: 当前联系人有效消息字典与模型 JSON 文本。
- Produces: `qa_contract.validate_answer_units(units: object, available: dict[int, dict]) -> list[dict]`；`qa_contract.evidence_fallback(retrieval: dict, reason: str) -> dict`；`ai.answer_units(question: str, evidence: list[dict], config: dict) -> dict`。

- [ ] **Step 1: 写合法逐单元回答测试**

模型 fixture 返回 JSON：`{"answer_units":[{"text":"...","kind":"fact","citations":["M1"],"uncertainty":null}]}`。断言合法单元保留逐单元引用并可组合显示。

- [ ] **Step 2: 写整段降级 P0 测试**

覆盖第二个单元无引用、伪造 ID、其他联系人 ID、已回收消息、缺失来源、非 JSON 和结构错误。即使第一个单元合法，最终也不得返回任何模型单元；结果必须是 `answer_kind="evidence"` 和明确 `degraded_reason`。另加 P0 测试：即使用户提供有效 HTTPS 地址，任何携带真实证据的回答请求也必须拒绝非 loopback 目标。

- [ ] **Step 3: 运行测试并确认失败**

Run: `python -m unittest tests.test_qa.AnswerContractTests tests.test_server.ModelTests -v`

Expected: FAIL，现有实现只校验整段中的引用字符串。

- [ ] **Step 4: 实现严格 JSON 单元契约**

系统提示要求只返回 JSON 单元。服务端验证顶层结构、单元类型、非空文本、事实单元至少一个引用、所有 ID 的当前联系人和来源完整性。任何失败调用 `evidence_fallback`，不删除坏单元或改写引用。

- [ ] **Step 5: 运行并提交**

Run: `python -m unittest tests.test_qa.AnswerContractTests tests.test_server.ModelTests -v`

Expected: 全部 PASS，包括原有不重试和重定向拒绝测试。

```bash
git add qa_contract.py ai.py qa.py tests/test_qa.py tests/test_server.py
git commit -m "feat: validate cited answer units atomically"
```

### Task 7: llama.cpp 安全进程生命周期与模型发现

**Files:**
- Create: `llama_runtime.py`
- Create: `tests/test_llama_runtime.py`
- Modify: `ai.py`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: 用户配置的 exe、GGUF、端口、上下文、GPU 层与线程数。
- Produces: `LlamaConfig.from_dict(value: dict) -> LlamaConfig`；`load_config(path: Path) -> LlamaConfig | None`；`save_config(path: Path, config: LlamaConfig) -> None`；`LlamaRuntime.start(config: LlamaConfig) -> dict`；`LlamaRuntime.status() -> dict`；`LlamaRuntime.stop(owner_token: str) -> dict`；`ai.discover_models(endpoint: str) -> list[str]`；`ai.probe_local(config: dict) -> dict`。

- [ ] **Step 1: 写路径、参数和 loopback 验证测试**

断言 exe/GGUF 必须是现有文件，端口在有效范围，启动参数以数组传入且 `shell=False`，host 固定为 `127.0.0.1`；聊天文本不能进入参数。配置以原子替换保存到被忽略的 `runtime/llama.json`，不得包含聊天、API 密钥或模型输出。

- [ ] **Step 2: 写进程身份和外部端口测试**

使用 fake `Popen` 与 fake socket 覆盖重复启动、慢加载、异常退出、外部兼容服务、非兼容端口占用、错误 owner token、句柄已退出和应用重启后只有 PID 无句柄。后四种情况都不得调用 terminate/kill。

- [ ] **Step 3: 写模型发现和探针测试**

模拟 `/health`、`/v1/models` 和最小 `/v1/chat/completions`。断言自动选择实际模型 ID；连接探针不含联系人、问题、文件名或消息正文；生成超时不自动重试。

- [ ] **Step 4: 运行测试并确认失败**

Run: `python -m unittest tests.test_llama_runtime tests.test_server.ModelTests -v`

Expected: FAIL，生命周期模块和模型发现尚不存在。

- [ ] **Step 5: 实现运行时管理器**

Windows 使用 `subprocess.Popen`、参数数组、隐藏窗口标志和应用内持有的进程句柄。`stop` 只有在 owner token 匹配且同一 `Popen` 句柄仍运行时才先 terminate、限时等待、必要时 kill；没有句柄的服务始终视为外部服务。

- [ ] **Step 6: 运行并提交**

Run: `python -m unittest tests.test_llama_runtime tests.test_server.ModelTests -v`

Expected: 全部 PASS。

```bash
git add llama_runtime.py ai.py tests/test_llama_runtime.py tests/test_server.py
git commit -m "feat: manage local llama process safely"
```

### Task 8: 问答 API、多轮失效与前端交互

**Files:**
- Modify: `server.py`
- Modify: `web/index.html`
- Modify: `web/app.js`
- Modify: `web/style.css`
- Modify: `tests/test_server.py`

**Interfaces:**
- Consumes: `qa`、`qa_contract`、`LlamaRuntime`。
- Produces: `qa.answer_question(store, contact_id: int, question: str, filters: dict, previous: dict | None, summarizer: object | None = None) -> dict`；`POST /api/qa`；`GET/POST /api/llama/config`；`GET /api/llama/status`；`POST /api/llama/start`；`POST /api/llama/stop`；保留现有 `/api/search` 和 `/api/answer` 兼容行为，但所有携带聊天证据的旧接口也改为只允许 loopback 模型。

- [ ] **Step 1: 写 `/api/qa` 和状态失效测试**

覆盖确定性回答、证据回答、模型回答、上一轮 context 继承，以及联系人切换、回收、恢复、增量导入和规则版本改变后的旧 context 拒绝。断言响应包含 answer units、四种 scope 数量、method、uncertainty 和 next context；`/api/qa` 与兼容 `/api/answer` 对非 loopback 模型地址均拒绝发送证据。

- [ ] **Step 2: 写 llama 生命周期路由测试**

注入 fake runtime，覆盖 config 保存与脱敏读取、start/status/stop、owner token、外部服务不可停止、生成中停止后 `/api/qa` 返回完整证据降级而不是部分模型文本。

- [ ] **Step 3: 运行服务测试并确认失败**

Run: `python -m unittest tests.test_server.HTTPTests -v`

Expected: FAIL，新接口尚不存在。

- [ ] **Step 4: 实现 API 编排**

所有写请求继续执行现有 Host、Origin、JSON 与 `X-Memory-Client` 校验。`answer_question` 是唯一问答编排入口：先验证有效联系人和 previous context，再依次调用路由、确定性分析、证据召回和可选 summarizer。`/api/qa` 只做协议校验并调用该函数；确定性命中不接触模型，模型失败统一返回 HTTP 200 的证据降级结果及故障状态。

- [ ] **Step 5: 更新前端**

问答区显示处理路径、四种 scope 数量、逐单元引用、不确定性和继承范围；联系人改变时清空 previous context。设置区增加 exe、GGUF、受控参数、启动、停止与状态；外部服务时禁用停止按钮。

- [ ] **Step 6: 验证 JS、接口与提交**

Run: `node --check web/app.js`

Expected: 无输出，退出码 `0`。

Run: `python -m unittest tests.test_server.HTTPTests -v`

Expected: 全部 PASS。

```bash
git add server.py web/index.html web/app.js web/style.css tests/test_server.py
git commit -m "feat: expose evidence-first chat question answering"
```

### Task 9: 机器可读虚构评测集与本地评测器

**Files:**
- Create: `tests/fixtures/qa_synthetic_chat.txt`
- Create: `tests/fixtures/qa_cases.json`
- Create: `evaluation.py`
- Create: `tests/test_evaluation.py`

**Interfaces:**
- Consumes: `/api/qa` 等价的 `qa.answer_question(...)` 调用。
- Produces: `evaluation.load_cases(path: Path) -> list[dict]`；`evaluation.evaluate_case(case: dict, answer: dict) -> list[str]`；CLI 输出不含真实数据的 JSON 报告。

- [ ] **Step 1: 定义 fixture schema 并写校验失败测试**

每个案例必须包含 `id`、`question`、`allowed_intents`、`allowed_answers`、`required_citations`、`forbidden_claims`、`acceptable_uncertainty`、`scope`。缺少任一字段、引用不存在或使用非虚构联系人时测试失败。

- [ ] **Step 2: 写八类与组合场景 fixture**

至少覆盖首次/最后、计数、连续性、精确原话、语义近似、上下文、代词、变化、主题、否定零命中、矛盾证据、多轮追问、跨午夜、同秒重复和提示注入。fixture 中不得出现用户真实文件名或本机路径。

- [ ] **Step 3: 写评测器失败测试**

断言允许答案通过；缺少必需引用、出现禁止结论、错误意图、漏报不确定性、scope 数量不符和部分模型答案都会产生稳定错误码。

- [ ] **Step 4: 运行测试并确认失败**

Run: `python -m unittest tests.test_evaluation -v`

Expected: FAIL，评测器尚不存在。

- [ ] **Step 5: 实现评测器并生成首份报告**

Run: `python evaluation.py --fixtures tests/fixtures --output .test-tmp/qa-evaluation.json`

Expected: 所有 fixture 通过，报告仅包含虚构案例 ID、程序输出和差异，不包含数据库或本地模型路径。

- [ ] **Step 6: 提交**

```bash
git add evaluation.py tests/test_evaluation.py tests/fixtures/qa_synthetic_chat.txt tests/fixtures/qa_cases.json
git commit -m "test: add reproducible synthetic chat evaluation"
```

### Task 10: 全套自动化、浏览器、真实 llama.cpp 与 ChatGPT 系统评审

**Files:**
- Modify as failures require: `qa.py`, `search.py`, `qa_contract.py`, `ai.py`, `llama_runtime.py`, `server.py`, `web/*`, `tests/*`
- Modify: `VERIFICATION.md`

**Interfaces:**
- Consumes: 完整测试套件、虚构评测报告和用户现有 llama.cpp/Qwen3.5 GGUF。
- Produces: 可复现的验证记录；每个发现的问题对应一个先失败后通过的回归测试。

- [ ] **Step 1: 运行全部静态与自动测试**

Run: `python -m compileall -q .`

Expected: 退出码 `0`。

Run: `node --check web/app.js`

Expected: 退出码 `0`。

Run: `python -m unittest discover -s tests -v`

Expected: 全部 PASS。

- [ ] **Step 2: 启动隔离数据目录并做浏览器流程测试**

使用 `.browser-test-data/` 和虚构 TXT，验证导入、八类提问、组合问题、多轮追问、引用定位、联系人切换、回收与恢复、增量导入、四种 scope 数量和模型故障降级。不得导入或查看真实联系人资料。

- [ ] **Step 3: 用应用按钮实测现有 llama.cpp**

配置用户现有 `llama-server.exe` 和 Qwen3.5 GGUF，验证首次加载、模型发现、正常回答、重复启动、端口冲突、生成超时、异常退出、生成中停止、外部服务不可停止。记录状态和耗时，不把绝对路径写入提交文件。

- [ ] **Step 4: 向 ChatGPT 提交虚构评测包进行外部评审**

只发送 `qa_cases.json` 中的虚构问题、允许答案/必需引用/禁止结论和去路径化程序输出。要求评审漏答、误答、错误绝对化、引用是否支持单元，以及失败是否可复现。

- [ ] **Step 5: 对每个确认问题执行修复闭环**

先添加能复现问题的失败测试，确认失败，再做最小修复并运行所属测试。重复 Step 1–4，直到所有违反规格或 fixture 的可复现功能缺陷均已修复，所有 P0 矩阵通过。超出批准范围的功能建议可以记录为后续增强，但不能把实际错误重新命名为“低风险限制”后发布。

- [ ] **Step 6: 更新验证记录并提交**

`VERIFICATION.md` 记录命令、通过数量、虚构浏览器场景、真实 llama 状态类别、ChatGPT 评审批次和已知限制；不记录真实正文、个人路径或模型日志。

```bash
git add VERIFICATION.md qa.py search.py qa_contract.py ai.py llama_runtime.py server.py web tests
git commit -m "test: close system evaluation findings"
```

### Task 11: 文档、最终隐私审计与 GitHub 发布

**Files:**
- Modify: `README.md`
- Modify: `VERIFICATION.md`
- Modify: `.gitignore`
- Modify: `release_check.py`

**Interfaces:**
- Consumes: 用户最终确认的 GitHub 仓库名称、可见性和发布方式。
- Produces: 本地完整提交历史和用户账户下的远程仓库；如选择 Pull Request，则产出并附加 draft PR。

- [ ] **Step 1: 更新用户文档**

README 说明八类问答、组合与多轮范围、四种 scope 数量、答案单元、隐私边界、一键 llama 配置、外部服务、故障排查、测试方法和诚实限制；删除“应用不启动或停止 llama-server.exe”的旧描述。

- [ ] **Step 2: 运行发布阻断检查**

Run: `python release_check.py`

Expected: `release check passed`。

Run: `git status --short --untracked-files=all`

Expected: 不出现 `data/`、数据库、模型、日志、本地配置、浏览器临时数据或真实聊天文件。

Run: `git grep -n -I -E "E:\\\\|C:\\\\Users\\\\admin|sk-[A-Za-z0-9]|BEGIN (RSA|OPENSSH) PRIVATE KEY" -- . ':!docs/superpowers/**'`

Expected: 无私人路径、密钥或私钥命中；设计文档中如有抽象示例要人工确认不含实际个人数据。

- [ ] **Step 3: 最终验证并提交文档**

Run: `python -m unittest discover -s tests -v`

Expected: 全部 PASS。

Run: `node --check web/app.js`

Expected: 退出码 `0`。

```bash
git add README.md VERIFICATION.md .gitignore release_check.py
git commit -m "docs: prepare privacy-safe chat memory release"
```

- [ ] **Step 4: 获取发布参数并检查 GitHub 登录**

在执行到此处时向用户确认仓库名称、公开/私有和直接推送/draft PR。然后运行 `gh --version`、`gh auth status` 和 `git log --oneline --decorate -10`；不得猜测账户或仓库。

- [ ] **Step 5: 创建或关联用户确认的远程仓库**

若仓库不存在，使用用户确认的名称与可见性创建；若存在，先核对所有者和默认分支再添加 `origin`。禁止覆盖非空远程历史。

- [ ] **Step 6: 推送并验证远程内容**

推送 `main` 或按用户选择创建 `codex/general-chat-qa` 并打开 draft PR。推送后检查远程提交和文件清单；若创建 PR，调用 `attach_artifact` 附加到当前任务。

- [ ] **Step 7: 最终报告**

向用户提供仓库或 PR 链接、提交 SHA、测试结果、隐私扫描结果、真实 llama.cpp 验证状态和仍存在的已知低风险限制。

---

## 执行顺序与停止条件

严格按 Task 1–11 执行。任何 P0 测试失败、任何违反批准规格或测试 fixture 的可复现缺陷未修复、真实数据可能进入提交、外部进程身份不明确、ChatGPT 评审包含真实内容，或远程仓库归属/可见性未确认时立即停止相应后续步骤。只有明确超出本规格范围的增强建议可以记录为后续工作。


