# XNCAgent 一期 / 二期落地清单

> **一期窗口**：2026.09.15 – 2026.10.12（主链路稳定、可回归）  
> **二期启动**：一期测试、修 bug、回归绿灯之后  
> **版本**：配合 [v3.1 架构文档](./XNCAgent_Go微服务架构设计文档-v3.1.md)  
> **原则**：先垂直打穿对话与扣款，再按事件边界拆服务。实现状态标：一期已实现 / 一期测试中 / 二期规划。

**仓库（双仓）**

| 仓库 | 职责 |
|------|------|
| `XNCAgent` | Python Agent：对话、RAG、MCP、Sandbox |
| `XNCAgent-go` | Go：Gateway / User / Commercial；`deploy/docker-compose.yaml`、`deploy/init.sql` |

- 本地 compose 在 **Go 仓**：Postgres 用户 `xnc`、库 `xncagent`；Kafka 宿主机端口 **9093**（容器内 9092）；Jaeger UI `16686`、OTLP gRPC `4317`
- 一期表结构以 Go 仓 `init.sql` 为准（v3.1 一期），不含旧学习计划的 gift/ab 表

---

## 一、分期

完整能力在 v3.1。按「能不能稳定跑」分期。

**一期（必须可回归）**

1. Python：对话循环、SSE、2–3 个 tool、MCP Server、最小 Sandbox、RAG 进对话、安全树洞  
2. Go：Gateway + User + Commercial（worker 可先同进程）  
3. Redis Lua 预扣结算 + 幂等键；Kafka 一条 `balance_events`  
4. 迎新 + 充值赠送挂在充值 preview；互斥 YAML + 单测  
5. 最小 OTel：一次对话一条 Gateway→Agent trace  
6. docker-compose：Postgres / Redis / Kafka + 业务进程  
7. 主链路测试与修 bug

**二期（一期绿灯后）**

| 批次 | 内容 | 对应 v3.1 |
|------|------|-----------|
| 二期-1 | Promotion 独立进程；满减；无限畅聊并集服务化 | 第八章 |
| 二期-2 | Recommend + Profile；动态预扣接画像；规则加权 | 第九、十一章 |
| 二期-3 | Risk、AB 独立服务；实验分流与埋点 | 第十、十二章 |
| 二期-4 | 三层对账自动化、Commercial-worker 净额落库、K8s HPA、LLM 报表 | 第七、十八、十九章 |

一期有效工时按约 10 小时/天 × 22 天估算。一期稳定前不拆二期进程。

---

## 二、一期节奏

```
Week A (9.15-9.21): Agent 内核 + Go 骨架
Week B (9.22-9.30): 扣款主线 + 两个活动（Commercial 内）
十一   (10.01-10.07): 测试、修 bug；不新开进程
Week C (10.08-10.12): OTel、compose、文档与代码对齐
二期   (一期回归绿灯后): 按上一节批次推进
```

### Week A（9.15–9.21）：Agent 先能跑

本周目标：HTTP 或 CLI 能完整聊一轮并检索到话术；MCP Inspector 能看到 tools；能注册登录。

| 模块 | 具体内容 | 验收标准 |
|------|---------|---------|
| Python 对话 | 系统提示词、流式输出、多轮上下文（先内存） | 输入问题后逐字输出小喜子回复 |
| 工具 | `get_joke`、方言切换、情绪/场景切换（2–3 个即可） | 对话中能触发至少一个 tool |
| 安全树洞 | 连续两次极端负面 → 终止玩笑、沉默陪伴 | 「烦死了」「想哭」连续输入后不再搞笑 |
| RAG | 知识库脚本已有，检索结果拼进 Prompt | 问起床/职场能命中对应话术块 |
| MCP | Python MCP Server 暴露上述 tools | MCP Inspector 能看到 2–3 个 Tools |
| Sandbox | tool 走超时子进程（timeout 5s，超限 kill） | 死循环类调用被终止 |
| Go 环境 | 在 `XNCAgent-go`：Go 1.23+、`cmd/gateway`、`cmd/user`、现成 compose | `go build ./...` 通过；`deploy/docker-compose.yaml` 起 Postgres/Redis（Kafka/Jaeger 已在文件中） |
| Gateway | Gin、`/health`、统一 JSON、RequestID | `curl /health` 返回标准 JSON |
| User | 注册/登录、bcrypt、JWT access+refresh | 注册→登录→带 token 访问受保护接口 |

**周验收（9.21）**：Agent 不稳则本周继续修，活动核销推到链路稳定之后（仍属一期）。

### Week B（9.22–9.30）：扣款主线打穿

本周目标：注册→充值→对话→余额正确；同一 `idempotency_key` 不双扣。

| 模块 | 具体内容 | 验收标准 |
|------|---------|---------|
| 账户与套餐 | accounts / packages / transactions（含 idempotency_key） | 能查余额、套餐列表 |
| 充值 | 事务写入 + Idempotency-Key | 重复请求余额不增加 |
| 预扣结算 | Redis Lua：预扣冻结、结算多退少补；预扣 TTL 30min 防泄漏 | 预扣 10 结算 8 → 可用 92、冻结 0 |
| 对话扣费 | Gateway：对话前 Prehold → SSE → 结束后 Settle | 一轮对话后流水完整 |
| Kafka | `balance_events`（key=user_id）、`chat.completed` | 消费者能读到事件 |
| 动态预扣 | 一期用近 3 次结算均值；画像调整值是二期-2 | 新用户走保守默认额度 |
| 活动 | 迎新礼包 + 充值赠送，挂在充值 preview（二期再拆 Promotion） | preview 能勾选；核销改余额 |
| 互斥单测 | 充值赠送 vs 满减（满减一期可先用测试夹具，二期-1 上完整活动） | 互斥组合单测全绿 |
| 等级 | User/Commercial 内一张表，聊天次数加分；二期可拆 Member | 能查询当前等级 |

**周验收（9.30）**：扣费不对就回归修账，二期-2/3 不提前开工。

### 十一（10.01–10.07）：测试与稳定

- 全链路回归：注册、登录、对话、充值、预扣、结算、幂等、SSE 中断
- 修超扣、双扣、冻结泄漏、流式断开
- 架构文档状态：一期已实现 / 一期测试中 / 二期规划
- **不新开二期进程**

### Week C（10.08–10.12）：一期收口

| 模块 | 具体内容 | 验收标准 |
|------|---------|---------|
| OTel | Gateway→Agent 一次对话一条 trace | Jaeger 或等价 UI 能看到该请求 |
| Docker | Go 仓 compose 起 infra + Gateway + Commercial；Python 仓起 Agent | 两边 README 写清双仓启动，不是单仓一条命令 |
| 对账脚本 | 对比 Redis 余额 vs DB 流水汇总（二期-4 再自动化） | 故意制造差异能打印出来 |
| Feature Flag | Gateway 一个 Redis 开关（二期-3 再上完整 AB 服务） | 关闭时主链路无额外分支 |
| 文档 | README、架构图与仓库一致，写清一期/二期 | 已实现的必须能跑 |

**一期验收（10.12）**：主链路可回归、文档与代码一致。此后开二期。

---

## 三、Week A 日计划（9.15–9.21）

| 日期 | 目标 | 当日结束必须有 |
|------|------|----------------|
| 9.15 一 | `XNCAgent-go`：Gateway `/health`（Go/compose/git 已齐） | `curl /health` 返回标准 JSON |
| 9.16 二 | User 注册登录 JWT | curl：注册→登录→401/200 |
| 9.17 三 | Python 对话流式 + 系统提示词 + 话术注入 | CLI 能聊一轮 |
| 9.18 四 | RAG 检索进 Prompt；安全树洞状态机 | 场景命中；连续负面不再搞笑 |
| 9.19 五 | 2–3 个 tools + MCP Server | Inspector 可见 tools |
| 9.20 六 | Sandbox 超时杀进程；Gateway 透传 SSE | curl 看到流式；恶意循环被 kill |
| 9.21 日 | 联调 + 单测；周验收 | 聊天+检索+登录全通 |

每日提交 **可运行代码**。

---

## 四、Week B 日计划（9.22–9.30）

| 日期 | 目标 | 当日结束必须有 |
|------|------|----------------|
| 9.22 一 | 充值幂等（表已在 `init.sql`） | 重复充值余额不变 |
| 9.23 二 | Redis Lua 预扣 | 并发两个预扣不超扣 |
| 9.24 三 | Lua 结算 + 预扣 TTL 释放 | 预扣 10 结算 8 余额正确 |
| 9.25 四 | 对话链路接入 Prehold/Settle | 一轮对话后流水完整 |
| 9.26 五 | Kafka `balance_events` + `chat.completed` | 能消费到事件 |
| 9.27 六 | 迎新 + 充值赠送 preview/核销 | 充值页能勾选活动 |
| 9.28 日 | 互斥单测 + 等级表 | 测试绿 |
| 9.29 一 | 全链路补测、修超扣/双扣 | 幂等用例固定下来 |
| 9.30 二 | Week B 验收 | 注册→充值→对话→余额对 |

核心账务（Lua 预扣/结算、幂等、预扣超时）与 MCP/Sandbox、JWT 需要能讲清实现，金融相关每笔问：幂等吗？对账吗？超卖吗？过期了吗？

---

## 五、实现状态（与代码对齐）

| 能力 | 状态 |
|------|------|
| 对话 / SSE / RAG / MCP / Sandbox | 一期（Week A） |
| 注册登录 JWT | 一期（Week A） |
| Lua 预扣结算 + 幂等 + Kafka 事件 | 一期（Week B） |
| 迎新 + 充值赠送（Commercial 内） | 一期（Week B） |
| Promotion 独立 / 满减 / 无限畅聊服务化 | 二期-1 |
| Recommend / Profile / 动态预扣调整值 | 二期-2 |
| Risk / AB 独立服务 | 二期-3 |
| 三层对账自动化 / K8s HPA / LLM 报表 | 二期-4 |

[架构演进.md](../架构/架构演进.md) 中未实现的部分标为规划，不标已落地。

---

*修订：2026-09-14*
