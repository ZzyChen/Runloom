# Runloom：可恢复 Agent Runtime 设计文档

状态：设计草案，待确认  
项目目录：`F:\C\Runloom`  
当前版本范围：MVP（单机、单节点、单 Agent）

## 1. 项目意图

Runloom 是一个用于学习 Agent 与后端基础设施的轻量级运行时。它让一次工具调用型 Agent 执行（run）具备明确的状态、持久事件、检查点和恢复策略。用户可以通过 API 发起 run、获取执行进度，并在进程崩溃后查看哪些步骤已完成、哪些可以安全重试、哪些需要人工核对。

项目关注的核心问题不是让 Agent 更会回答，而是处理以下执行窗口：外部工具已经产生副作用，但 Agent 尚未把工具结果写入持久存储。进程此时崩溃，重启后不能简单地假设工具没执行，也不能无条件重做。Runloom 必须保留这项不确定性，并依据工具的幂等能力决定恢复或核对。

学习目标包括：Agent loop、工具 schema 与注册、API、关系型数据建模、事务和事件日志、检查点、并发控制、幂等、故障恢复、SSE 进度流、可观测性与测试。

## 2. 目标与非目标

### 目标

- 支持按 `thread_id` 维护连续对话状态，并为每次执行分配唯一 `run_id`。
- 通过模型适配层运行一个简单的 ReAct 风格循环：模型答复、模型请求工具、执行工具、返回结果，直到结束或达到上限。
- 工具必须通过注册表显式声明参数 schema、超时、重试和幂等策略。
- 每个 run、模型交互、工具步骤、状态转换和恢复决定都写入持久存储。
- SQLite 中的事件、状态更新和 checkpoint 具备清晰的一致性边界。
- 重启后发现未完成步骤，并采取可解释的恢复策略：继续、幂等重试或 `needs_reconciliation`。
- 提供最小 REST API、运行状态查询、事件查询和 SSE 进度流。
- 通过 fake model 与确定性工具测试核心逻辑，不要求测试访问外部 LLM。

### 非目标

- MVP 不实现多 Agent、长期语义记忆、技能自进化或通用工作流引擎。
- MVP 不承诺任意外部副作用都能 exactly-once 执行。跨数据库和外部系统的原子事务不可由本地 runtime 单独保证。
- MVP 不托管任意用户代码，不实现 Docker、gVisor、Firecracker 或 Kubernetes 沙箱。
- MVP 不做多租户认证计费、分布式队列、集群调度和水平扩展。
- MVP 不依赖某个 Agent 框架来隐藏执行循环；后续可以增加 LangGraph adapter 作对照。
- MVP 不把模型输出视为确定性。回放优先重用已记录的模型响应和工具结果，不要求重新采样得到相同输出。

## 3. 方案选择

### 方案 A：Durable Agent Runtime（选定）

从单 Agent 执行闭环出发，先实现 SQLite 事件记录、checkpoint、工具幂等协议和崩溃恢复，再逐步加入 API、SSE、追踪和沙箱。它同时覆盖 Agent 核心与后端可靠性，且可以用本地故障注入验证。

### 方案 B：Agent 追踪与回放

先做 OpenTelemetry trace、事件时间线和 CLI 回放，容易快速展示，也能帮助调试；但对事务、重试与崩溃恢复的训练较少。将其作为 Runloom 的后续可观测性阶段。

### 方案 C：沙箱生命周期控制面

先做 workspace 申领、执行、回收与隔离，Infra 属性强，但需要较早处理 Docker、安全与资源管理，Agent 执行机制占比低。将其作为可选后续扩展。

选择 A 的原因：Poirot 已经展示了 ReAct、middleware、sandbox 和 journal 等模块的价值；其 `RunJournal` 与 `RunManager` 适合作为学习参照，而进程内 `InMemorySaver` 和本地文件 run record 则为本项目留下了清晰的练习空间。Runloom 将围绕可靠恢复深入一个窄问题，而不是复制 Poirot 的全部功能。

## 4. 技术决策

- **语言：** Python 3.12+。便于学习 Agent 接口、异步 API 和测试，也与 Poirot 的语言一致。
- **HTTP：** FastAPI + Pydantic。API 与核心执行逻辑分层，核心模块不得依赖 FastAPI。
- **存储：** SQLite，启用 WAL；使用 SQLAlchemy 2.x 或轻量 SQL 层封装。第一阶段以 SQLite 事务和 SQL 为学习重点，不引入 Redis。
- **Agent loop：** 自己实现小型循环，控制 LLM 请求、工具调用、限制和状态写入；模型 provider 通过协议隔离。先接一个 OpenAI-compatible provider，测试使用 fake provider。
- **迁移：** Alembic 管理 schema 变化。
- **测试：** pytest，核心逻辑使用 fake model / fake tool；API 使用 FastAPI TestClient 或 httpx。
- **配置：** Pydantic Settings 或等价的环境配置；密钥只从环境变量读取，不写入事件或日志。
- **观测：** MVP 先用结构化日志和持久事件；后续增加 OpenTelemetry trace。

## 5. 系统架构

```text
HTTP Client / CLI
       │
       ▼
API Layer ───── SSE event stream
       │
       ▼
Run Service / Run Manager
       │ create, claim, cancel, recover
       ▼
Agent Runtime ───── Model Provider Protocol
       │
       ├── Tool Registry ── Tool Executor
       │
       ├── Checkpoint Manager
       │
       └── Event Writer
                 │
                 ▼
          SQLite Repository
       runs / messages / steps / events / checkpoints
```

### 关键职责

- **API Layer：** 校验请求、鉴权占位、返回响应与 SSE；不直接调用模型或工具。
- **Run Service：** 创建 run、确保同一 thread 不被意外并发写入、查询和取消 run，并协调恢复。
- **Agent Runtime：** 执行有限轮数的 Agent loop；不直接依赖 HTTP 或数据库实现。
- **Model Provider：** 将内部 `ModelRequest` 转成特定供应商调用，返回标准化 `ModelResponse`；记录 provider、model、请求摘要和响应内容策略。
- **Tool Registry：** 工具名到工具定义的映射，执行前验证 schema、权限、超时和幂等策略。
- **Tool Executor：** 在执行边界写入 intent，调用工具并标准化结果/错误，执行超时和重试规则。
- **Checkpoint Manager：** 保存能恢复下一步所需的最小完整状态。
- **Repository：** 提供事务边界、状态更新、事件追加和数据访问。

## 6. 数据模型

MVP 使用以下核心表。所有表使用 UTC 时间戳和明确的 schema 版本。

### `threads`

- `thread_id`：主键
- `created_at`、`updated_at`
- `metadata_json`
- 可选 `title`

### `runs`

- `run_id`：主键
- `thread_id`：外键
- `status`：`queued | running | succeeded | failed | cancelled | needs_reconciliation`
- `created_at`、`started_at`、`finished_at`、`updated_at`
- `model_name`、`prompt_version`
- `input_json`、`final_output`
- `error_code`、`error_message`
- `attempt`、`lease_owner`、`lease_expires_at`（为后续 worker 恢复预留；单进程 MVP 可以简化）

### `steps`

- `step_id`：主键
- `run_id`、`sequence_no`：唯一组合
- `kind`：`model | tool`
- `name`：工具名或模型阶段名
- `status`：`started | intent_recorded | succeeded | failed | unknown | needs_reconciliation`
- `input_json`、`input_hash`
- `output_json`、`error_json`
- `idempotency_key`
- `started_at`、`finished_at`、`duration_ms`

### `events`

- `event_id`：单调递增整数或 UUID 主键
- `run_id`、`thread_id`
- `sequence_no`：每个 run 内单调递增
- `event_type`、`payload_json`、`created_at`
- `UNIQUE(run_id, sequence_no)`

事件示例：`run.created`、`run.started`、`model.requested`、`model.responded`、`tool.intent_recorded`、`tool.started`、`tool.succeeded`、`tool.failed`、`checkpoint.saved`、`run.needs_reconciliation`、`run.succeeded`。

### `checkpoints`

- `checkpoint_id`：主键
- `run_id`、`sequence_no`
- `state_json`：可继续执行所需的 Agent 状态快照
- `created_at`
- `UNIQUE(run_id, sequence_no)`

checkpoint 至少保存消息序列、当前回合、已完成工具调用及其结果、下一步游标、Agent/prompt 版本和关联 event 序号。MVP 的 checkpoint 在每次成功工具结果写入后以及 run 结束时创建。

## 7. 状态机与一致性规则

### Run 状态

```text
queued → running → succeeded
                 ↘ failed
                 ↘ cancelled
                 ↘ needs_reconciliation
```

### Tool Step 状态

```text
started → intent_recorded → succeeded
                         ↘ failed
                         ↘ unknown → needs_reconciliation
```

工具调用顺序：

1. 模型返回工具名和参数；runtime 验证工具存在、schema、权限和预算。
2. 在一个 SQLite 事务内创建 tool step、写入 `tool.intent_recorded` 事件及幂等键，然后提交。
3. 执行工具。外部调用必须设置超时；如工具支持，向工具传递同一个幂等键。
4. 成功时，在一个事务内保存结果、将 step 标为 succeeded、追加 `tool.succeeded` 事件并保存 checkpoint。
5. 失败时，根据工具策略决定是否安全重试；否则记录失败或不确定状态。

若进程在第 3 步之后、第 4 步之前退出，重启扫描到 `intent_recorded` 但没有终态的工具 step。对声明为幂等且可透传幂等键的工具，可以用相同键重试；对非幂等或无法确认结果的工具标记为 `needs_reconciliation`，默认不自动重放。

`intent` 表示“系统准备执行并已持久记录”，不代表工具确定没有执行。Runloom 不宣称跨外部服务 exactly-once。可靠性契约是：记录执行意图、为幂等操作提供安全重试、对不确定的非幂等副作用显式停下。

## 8. Agent Loop 与工具契约

Agent loop 最大轮数、工具调用次数、单工具超时、总 run 超时、最大消息/输出大小都可配置，并有保守默认值。达到上限时以可查询的错误状态结束，不允许无界循环。

工具定义包含：

```text
name
description
input_schema
handler
timeout_seconds
retry_policy
idempotency: none | runtime_key | external_key
side_effect: read_only | idempotent_write | non_idempotent_write
```

工具参数必须在执行前校验；工具异常被转换为有类型的错误结果。密钥不传给模型，工具输出可配置截断与敏感字段过滤。MVP 内置工具建议包括 `add`（纯函数）和 `append_note`（写入本地受控文件或数据库，演示幂等键）。不开放任意 shell 执行。

## 9. API 草案

- `POST /v1/threads`：创建 thread。
- `POST /v1/threads/{thread_id}/runs`：创建 run，body 包含 `input`、可选 `idempotency_key`、可选运行参数；返回 `202` 与 `run_id`。
- `GET /v1/runs/{run_id}`：获取 run 状态、最终答复和错误摘要。
- `GET /v1/runs/{run_id}/events?after={sequence}`：分页获取事件。
- `GET /v1/runs/{run_id}/stream`：SSE 流式推送事件与状态变化。
- `POST /v1/runs/{run_id}/cancel`：请求取消；取消不能撤销已经发生的工具副作用。
- `POST /v1/runs/{run_id}/reconcile`：管理员或用户提交人工核对结果（后续阶段；MVP 可以先通过 CLI 命令完成）。

API 错误使用稳定的 `code`、简洁 `message` 和 `request_id`。SSE 客户端断线后可用最后接收的 event sequence 续读。

## 10. 恢复与回放

- 启动时扫描过期 lease 或未结束 run；MVP 单进程可以在启动阶段执行恢复审计。
- 从最近 checkpoint 加载状态，再根据 checkpoint 后事件重建可恢复状态。
- 已持久化的模型响应和工具结果用于 replay；回放不重新调用已经记录的副作用工具。
- 缺少结果的模型请求可根据策略重新请求模型，但这会形成新的非确定性分支，必须追加新事件并保留旧请求记录。
- 未知工具结果不得被伪装成成功或失败；必须保留 `unknown`/`needs_reconciliation`。
- prompt、Agent 逻辑和工具 schema 记录版本标识，避免用新代码无提示地恢复旧 run。

## 11. 安全与隐私边界

- 工具采用 allowlist；模型只能请求已注册工具。
- 对参数长度、输出大小、超时、循环次数和总预算设上限。
- 不记录 API key、Authorization header 或环境变量；日志对敏感字段做过滤。
- 持久化 prompt、模型响应和工具结果可能包含用户数据；配置是否记录完整内容，默认本地开发可记录，后续提供 redaction/脱敏模式。
- MVP 不执行模型生成的任意代码，不视为安全沙箱。
- API MVP 可仅监听 localhost；外部部署前需补认证、租户隔离和速率限制。

## 12. 测试与验收

测试不依赖真实模型服务。Fake model 预置输出：普通答复、工具调用、连续工具调用、错误工具调用、循环超限。

必须覆盖：

1. 无工具请求能够完成 run，状态和最终答复持久化。
2. 工具调用参数通过 schema 校验，工具结果回到模型消息中。
3. 每个状态转移同时有事件记录；事件 sequence 在并发写入下不重复。
4. 相同 idempotency key 的重复请求不产生第二个 run 或副作用。
5. 工具失败、超时、取消和循环预算超限有明确终态。
6. 事务中断不会留下无效半条 JSON/损坏状态。
7. 崩溃注入：intent 落库后、工具执行前崩溃；工具执行后、结果落库前崩溃；结果落库后、模型继续前崩溃。恢复结果分别符合工具幂等策略。
8. SSE 断线后可按 sequence 续传且不丢事件。
9. SQLite 文件重新打开后 run、events、steps 和 checkpoints 仍可读取。

核心验收演示：调用具有可观察副作用的幂等工具；在工具返回后、结果事务前通过测试故障钩子杀停执行；重启服务后，runtime 以原幂等键安全恢复，或将非幂等步骤明确标为 `needs_reconciliation`。验证副作用计数和 event timeline 与预期一致。

## 13. 目录结构提案

```text
Runloom/
├── app/                         # Python 应用包（核心实现从这里开始）
│   ├── agent/                   # Agent loop、模型协议、工具注册和执行
│   ├── runs/                    # run 服务、状态机和恢复协调
│   ├── storage/                 # SQLite repository、schema、checkpoint/event 存储
│   ├── api/                     # FastAPI 路由、请求/响应 schema、SSE
│   ├── config.py
│   └── main.py
├── da/                          # 暂按 data/artifacts 处理；确认用途后再定
│   ├── db/                      # 本地开发数据库（应加入 gitignore）
│   └── runs/                    # 运行产物或导出文件（应加入 gitignore）
├── tests/
│   ├── unit/
│   ├── integration/
│   └── recovery/
├── docs/
│   └── design.md
├── pyproject.toml
└── README.md
```

如果现存 `app` 或 `da` 已有内容，先确认用途再调整结构。推荐把 `da` 定义为本地数据目录而非 Python 源码目录；数据库与运行日志不能混入 `app/`。

## 14. 里程碑

### M0：可运行的最小 Agent

实现 model protocol、fake model、tool registry、两个工具和有轮数上限的 loop。用内存状态跑通“模型调用工具并总结结果”。

### M1：持久化执行

实现 SQLite schema、thread/run/step/event/checkpoint repository；每次状态变化写事件；实现状态查询。先不用 HTTP。

### M2：故障恢复与幂等

定义工具副作用契约、intent/result 顺序、恢复扫描与 `needs_reconciliation`；实现三个崩溃注入点和验收演示。

### M3：服务化

添加 FastAPI 路由、后台 run 执行、取消、分页事件和 SSE 续传。明确 SQLite 单写者限制及 thread 并发策略。

### M4：可观测与回放

添加结构化日志、OpenTelemetry trace、CLI 时间线、基于已记录响应的 replay 和 prompt/version diff。

### M5：可选 Infra 扩展

按学习兴趣选择 PostgreSQL/队列、多租户配额、受限 subprocess 或 Docker sandbox。每次只扩一个能力，并用负载或故障场景说明其必要性。

## 15. 风险与决策记录

- **SQLite 并发与锁：** MVP 定位为单机单节点；设置 busy timeout、短事务和每 thread 的运行互斥。达到并发需求后再评估 PostgreSQL。
- **副作用不可回滚：** 用明确的工具幂等契约与 reconciliation 处理；不声称通用 exactly-once。
- **模型 replay 不确定：** 保存模型请求/响应版本用于重放；新模型请求视为新事件，不覆盖原历史。
- **复杂度膨胀：** 不在 M0/M1 加多 Agent、向量数据库、任务队列、Kubernetes 或任意代码沙箱。
- **已有目录语义未知：** `app` 和 `da` 当前存在但内容需先盘点；目录设计以它们为空或可迁移为前提。

## 16. 设计批准后第一步

设计确认后，先在 `app/agent/` 实现 M0：`protocols.py`（模型与工具协议）、`tools.py`（注册表和内置工具）、`loop.py`（有限轮数的 Agent loop），并在 `tests/unit/agent/` 用 fake model 测试完整工具调用闭环。M0 验收后再进入 `app/storage/`，避免一开始把 agent loop、数据库和 HTTP 三类问题耦合在一起。
