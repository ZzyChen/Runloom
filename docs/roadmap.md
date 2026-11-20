# Runloom 补齐规划：一个完整 Agent Infra 项目

> 目标：把 Runloom 从「一个能跑的单 Agent loop 骨架」补成一个**面试可讲、经得起追问**的 Agent 基础设施项目。
>
> 本文档基于当前代码（`app/agent/` + `app/storage/sqlite_store.py`）与 `design.md` 的差异，给出问题清单、能力地图与分阶段落地顺序。

---

## 0. 现状一句话总结

你已经有了「能跑通 ReAct loop + 写 SQLite」的最小闭环，这是**正确的起点**。但 `design.md` 承诺的能力（幂等、检查点、崩溃恢复、SSE、fake model、pytest）绝大部分还没在代码里落地。

> ⚠️ 面试最大的风险不是「功能少」，而是「文档吹的牛，代码没兑现」。面试官一定会拿文档对照代码追问。

---

## 1. 当前代码的问题清单

### 1.1 正确性 bug（必须优先修）

| # | 位置 | 问题 | 后果 |
|---|------|------|------|
| 1 | `loop.py` | `response` 是循环体内局部变量，`for...else` 超限抛错后、或异常路径下，最后 `return response.content` / `finish_run(final_output=response.content)` 会触发 `UnboundLocalError` | 崩溃后 final_output 丢失，且二次异常掩盖真实错误 |
| 2 | `sqlite_store.py::_append_event` | 用 `MAX(sequence_no)+1` 计算序号，靠 `UNIQUE` 兜底 | 并发下撞唯一约束直接抛错，而非安全递增；不是真正的原子递增 |
| 3 | `tools.py::AddTool` | `execute` 直接 `arguments["a"]`，无 schema 校验 | 缺参/类型错变成兜底 KeyError，而非有类型语义的参数校验错误 |
| 4 | `model_adapter.py` | 未显式处理 `finish_reason=="tool_calls"` 时 `content=None`、空 content assistant 消息、多 tool_calls 的顺序 | 对部分 provider 返回结构容错不足 |

### 1.2 设计与实现脱节（核心卖点缺失）

`design.md` 承诺、代码未实现：

- `threads` 表（代码只有 `runs` / `events`）
- `steps` 表、`checkpoints` 表（完全没有）
- 工具幂等契约：`idempotency` / `side_effect` / `retry_policy` / `timeout_seconds`（`ToolSpec` 只有 name/description/schema）
- `intent_recorded` 状态、崩溃恢复扫描、`needs_reconciliation` 状态
- FastAPI + SSE（只有裸脚本 `run_agent.py`）
- `append_note` 幂等演示工具
- FakeModel（测试用，不依赖真实 LLM）

### 1.3 工程化缺失

- 无 `pyproject.toml` / `requirements.txt` → clone 后无法复现
- 无 README → 没有 quick start / 架构图 / 卖点说明
- `tests/` 是 print 脚本不是 pytest（`test_loop.py` 甚至 import 后不跑、`test_sqlite.py` 硬编码不存在的 `run_001`）
- `design.md` 里项目路径写 `F:\C\Runloom`，与实际不符
- 无 `.env.example`

---

## 2. 完整 Agent Infra 项目能力地图

一个「完整」的 Agent Infra 项目，面试含金量从高到低大致如下。**可靠性层是你的差异化核心，务必做扎实；其余按时间补齐。**

```
┌─────────────────────────────────────────────────────────────┐
│  A. 可靠性层（★ 核心差异化）                                    │
│     steps / checkpoints / 幂等契约 / intent 状态机 /            │
│     崩溃恢复 / needs_reconciliation / 崩溃注入测试               │
├─────────────────────────────────────────────────────────────┤
│  B. 服务化层                                                   │
│     FastAPI / SSE / 取消 / 事件分页续读                         │
├─────────────────────────────────────────────────────────────┤
│  C. 模型与工具层                                                │
│     FakeModel / schema 校验 / 超时重试 / append_note 演示        │
├─────────────────────────────────────────────────────────────┤
│  D. 可观测性                                                   │
│     结构化日志 / OpenTelemetry trace / CLI 回放时间线            │
├─────────────────────────────────────────────────────────────┤
│  E. 工程化（面试观感，性价比极高）                                │
│     pyproject / pytest 全覆盖 / README / .env.example           │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 A. 可靠性层（最重要）

这是整个项目区别于「套 LangChain 也能写出来」的地方。核心思想：

> **执行窗口问题**：外部工具已产生副作用，但结果还没落库时进程崩溃。重启后既不能假设「没执行」（会重复副作用），也不能无条件「重做」。必须保留不确定性，依据工具幂等能力决定「重试」还是「人工核对」。

落地要点：

1. **数据模型补齐**：新增 `threads`、`steps`、`checkpoints` 三张表（schema 直接沿用 `design.md` §6）。
2. **工具副作用契约**，`ToolSpec` 扩展为：
   ```text
   name / description / input_schema / handler
   timeout_seconds
   retry_policy          # 最大重试次数 + 退避
   idempotency           # none | runtime_key | external_key
   side_effect           # read_only | idempotent_write | non_idempotent_write
   ```
3. **Tool Step 状态机**：
   ```
   started → intent_recorded → succeeded
                             ↘ failed
                             ↘ unknown → needs_reconciliation
   ```
4. **intent/result 顺序**（事务边界）：
   1. 模型返回工具调用 → 校验 schema/权限/预算
   2. 一个事务内：建 tool step + 写 `intent_recorded` 事件 + 幂等键 → commit
   3. 执行工具（带超时，透传同一幂等键）
   4. 成功：一个事务内写结果 + step 标 succeeded + `tool.succeeded` 事件 + checkpoint → commit
   5. 失败：按工具策略决定重试 / 标 failed / 标 unknown
5. **恢复扫描器**：启动时扫 `intent_recorded` 无终态的 step；幂等工具用同键重试，非幂等标 `needs_reconciliation`。
6. **3 个崩溃注入测试点**（这是面试最亮的演示）：
   - intent 落库后、工具执行前崩溃
   - 工具执行后、结果落库前崩溃
   - 结果落库后、模型继续前崩溃

### 2.2 B. 服务化层

- FastAPI + Pydantic，核心模块**不依赖** FastAPI（保持分层）
- 路由按 `design.md` §9：threads / runs / events / stream / cancel / reconcile
- SSE 用 `after_sequence` 续读，断线不丢事件
- SQLite 单写者 + 每 thread 运行互斥

### 2.3 C. 模型与工具层

- **FakeModel**：预置「普通答复 / 工具调用 / 连续调用 / 错误调用 / 循环超限」序列，让 loop 测试不碰真实 LLM
- 用 `jsonschema` 或 pydantic 做参数校验，产出**有类型语义**的校验错误
- `append_note` 工具：写本地受控文件/表，演示幂等键（`external_key`）
- 工具异常转成 `ToolResult(success=False, error=...)`，不抛到 loop

### 2.4 D. 可观测性

- 结构化日志（loguru / structlog），敏感字段过滤
- OpenTelemetry trace（可作 M4 阶段）
- CLI 回放：`python -m runloom replay <run_id>` 打印事件时间线

### 2.5 E. 工程化

- `pyproject.toml`（uv / pip 均可）+ 依赖锁定
- 真实 pytest 套件，覆盖 `design.md` §12 的全部验收项
- README：架构图（可放 mermaid）、quick start、核心卖点、一段可演示的恢复场景
- `.env.example`

---

## 3. 分阶段落地顺序（建议）

严格按依赖关系推进，避免一开始把 loop / 存储 / HTTP 耦合。

### M0 — 修 bug + 打地基（先做）
- [ ] 修 `loop.py` 的 `UnboundLocalError`（`response` 作用域 + finish 逻辑重构）
- [ ] 建 `pyproject.toml` / `requirements.txt` / `.env.example` / README 骨架
- [ ] 把 `tests/` 改成真 pytest（加 `__init__.py`、用 tmp_path 建临时 db、用断言）

### M1 — 持久化补齐
- [ ] 新增 `threads` / `steps` / `checkpoints` 表（含 Alembic 或手写迁移）
- [ ] 每次状态变化写事件（对齐 `design.md` §7 的事件名）
- [ ] 状态查询接口

### M2 — 可靠性核心（★ 面试重点）
- [ ] 扩展 `ToolSpec`：幂等 / 副作用 / 重试 / 超时
- [ ] intent/result 事务顺序 + 状态机
- [ ] 恢复扫描器 + `needs_reconciliation`
- [ ] `append_note` 幂等工具
- [ ] FakeModel + 3 个崩溃注入测试 + 验收演示

### M3 — 服务化
- [ ] FastAPI 路由 + SSE + 取消 + 事件分页

### M4 — 可观测（可选）
- [ ] 结构化日志 / OTel trace / CLI 回放

---

## 4. 面试答辩要点（提前想清楚）

这些是面试官大概率会追问的，写代码时就同步想好答案：

1. **为什么不用 LangGraph / LangChain？** → 为了看清执行循环、事务边界和恢复语义，不被框架隐藏；可加 adapter 作对照。
2. **为什么不承诺 exactly-once？** → 本地 runtime 无法对跨系统副作用做原子事务；可靠性契约是「记录 intent + 幂等安全重试 + 不确定时显式停下」。
3. **`intent` 为什么不能代表「确定执行了」？** → 它只表示「准备执行并已持久化」，进程可能在工具调用发出后、副作用发生前后任意时刻崩。
4. **SQLite 单写者的并发策略？** → busy timeout + 短事务 + 每 thread 互斥；何时要换 PostgreSQL。
5. **模型 replay 不确定性怎么处理？** → 复用已记录的响应/结果；新模型请求 = 新事件 + 保留旧历史，不覆盖。
6. **schema 版本不匹配怎么恢复旧 run？** → prompt/逻辑/tool schema 记版本，新代码不得无提示恢复旧 run。

---

## 5. 目录结构建议（终态）

```text
Runloom/
├── app/
│   ├── agent/           # loop / protocols / tools / model_adapter
│   │   └── models/      # FakeModel + OpenAI 适配
│   ├── runs/            # run service、状态机、恢复协调（M2）
│   ├── storage/         # repository / schema / migrations / checkpoint
│   ├── api/             # FastAPI 路由、schema、SSE（M3）
│   ├── config.py
│   └── main.py
├── tests/
│   ├── unit/            # fake model / fake tool 单测
│   ├── integration/     # 存储 + loop 集成
│   └── recovery/        # 崩溃注入场景
├── docs/
│   ├── design.md
│   └── roadmap.md       # 本文档
├── scripts/
├── pyproject.toml
├── .env.example
└── README.md
```

---

## 6. 一句话总结

你已经有了正确的骨架。接下来**按 M0→M1→M2→M3 的顺序补**，其中 **M2（可靠性/幂等/恢复）是让这个项目在面试中「可讲」的关键**——它是你区别于「套框架跑通 demo」的地方，也是面试官愿意深挖的深度。
