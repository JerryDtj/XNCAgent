# XNCAgent Go 微服务模块架构设计文档

> **版本**：v3.1  
> **范围**：本文档仅覆盖 Go 微服务层架构设计。Go 实现仓库为 **`XNCAgent-go`**；Python Agent Core 在 **`XNCAgent`**，架构见该仓 `doc/架构`。  
> **设计原则**：生产可落地、可扩展、可观测、可回滚  
> **目标读者**：技术面试官、团队协作开发者  
> **v3.1 变更摘要**：  
> 1. 会员等级更名为古代官职体系（里正→县令→知府→丞相→皇帝），分数区间与规则权重不变  
> 2. 商业化扣款链路重写：Redis Lua 同步扣款（抗压）+ Kafka 事件溯源（权威账本）+ worker 批量净额落库（异步投影），单节点 Redis + 降级开关 + 三层对账  
> 3. 对账机制重写：DB 内部 / Kafka↔DB / Redis↔Kafka 三层口径，核心为 event_id 逐条 diff  
> 4. 营销活动入口合并进充值链路（preview + 下单核销），取消独立商城设计  
> 5. 推荐服务接入 LLM 话术生成（RecommendPlugin），规则引擎召回、LLM 不定价  
> 6. 新增 ADR-007 / ADR-008 / ADR-009

---

## 目录

1. [背景与目标](#一背景与目标)
2. [Go 微服务总体架构](#二go-微服务总体架构)
3. [服务拆分与职责边界](#三服务拆分与职责边界)
4. [网关层设计](#四网关层设计)
5. [用户服务](#五用户服务)
6. [会员等级服务（好友度模型）](#六会员等级服务好友度模型)
7. [商业化服务（Redis 扣款 + Kafka 账本 + 异步落库）](#七商业化服务redis-扣款--kafka-账本--异步落库)
8. [营销活动服务](#八营销活动服务)
9. [推荐服务（规则引擎 + LLM 话术）](#九推荐服务规则引擎--llm-话术)
10. [A/B 测试服务（Feature Flag）](#十ab-测试服务feature-flag)
11. [用户画像服务](#十一用户画像服务)
12. [风控服务](#十二风控服务)
13. [事件总线](#十三事件总线)
14. [Go 层与 Python Agent Core 的交互](#十四go-层与-python-agent-core-的交互)
15. [数据存储设计](#十五数据存储设计)
16. [一致性保证方案](#十六一致性保证方案)
17. [扩展性与性能设计](#十七扩展性与性能设计)
18. [可观测性设计](#十八可观测性设计)
19. [部署与运维](#十九部署与运维)
20. [风险清单与应对](#二十风险清单与应对)
21. [附录：关键设计决策记录（ADR）](#附录关键设计决策记录adr)

---

## 一、背景与目标

### 1.1 业务背景

本项目 AI Agent 情感陪伴系统的 Go 微服务层（仓库 **`XNCAgent-go`**），负责高并发网关、用户体系、商业化闭环（积分/套餐/活动）、用户成长体系（会员等级）、推荐系统、A/B 测试、风控等核心后端能力。Python Agent Core（仓库 **`XNCAgent`**）负责对话状态机、LLM 推理、MCP 工具调用，架构文档在该仓 `doc/架构`，本文档不再赘述。

### 1.2 技术目标

- **高并发会话管理**：Gateway 支持 SSE 流式透传，goroutine 级别并发控制
- **金融级账务一致性**：积分充值、消费、预扣、结算必须保证幂等性、原子性、可对账
- **扣款链路抗压**：对话扣费高峰期（活动大促、热门时段）数据库不能成为瓶颈，Redis 承担同步扣款压力，Kafka 承担账本角色，DB 异步批量投影
- **营销活动防冲突**：多个活动同时命中时，有明确的优先级和互斥策略，防止资损
- **A/B 测试可开关**：产品初期用户量不足时，A/B 测试能零成本关闭，不侵入主链路
- **预扣额度智能计算**：根据用户历史行为动态计算预扣额度，避免频繁预扣/释放
- **可降级**：Redis 故障时可一键切换 DB 直连模式，切换瞬间在途业务不受损

### 1.3 设计哲学

1. **先保证正确性，再追求性能**：涉及用户资产的操作，宁可慢一点，不可错一点
2. **账本唯一**：Kafka 是全系统唯一权威账本，Redis 是现场投影，DB 是异步投影；任何修复动作都是"让账本完整"或"按账本同步投影"
3. **开关文化**：所有实验性功能、营销活动、扣款链路模式都有 Feature Flag 控制，可随时关闭/切换
4. **事件驱动解耦**：服务间通过 Kafka 异步通信，避免同步调用链过长导致级联故障
5. **数据聚合前置**：画像报表等场景，数据先通过 SQL 聚合，再消费，控制成本

---

## 二、Go 微服务总体架构

### 2.1 架构图

```
+-----------------------------------------------------------------------------+
|                              接入层                                          |
|  +-------------+  +-------------+  +-------------+                          |
|  |   Web App   |  |   Mobile    |  |   Admin     |                          |
|  |   (React)   |  |   (Flutter) |  |   Dashboard |                          |
|  +------+------+  +------+------+  +------+------+                          |
+--------+---------------+---------------+------------------------------------+
         |               |               |
         +---------------+---------------+
                         |
+------------------------+----------------+----------------------------------+
|                           API Gateway (Gin)                                |
|  +-------------+  +-------------+  +-------------+  +-----------------+  |
|  |  JWT Auth   |  | RequestID   |  | Rate Limit  |  | Feature Flag    |  |
|  |  Middleware |  | Middleware  |  | Middleware  |  | Middleware      |  |
|  +-------------+  +-------------+  +-------------+  +-----------------+  |
|  +-------------+  +-------------+  +-------------+  +-----------------+  |
|  |   CORS      |  |  Recovery   |  |   Zap Log   |  |  A/B Split      |  |
|  |  Middleware |  |  Middleware |  |  Middleware |  |  (Conditional)  |  |
|  +-------------+  +-------------+  +-------------+  +-----------------+  |
+------------------------+---------------+-----------------------------------+
                         | gRPC / HTTP
+------------------------+----------------+----------------------------------+
|                         Go 微服务层（无状态）                               |
|  +----------+ +----------+ +----------+ +----------+ +----------+        |
|  |  用户服务  | | 会员等级  | | 商业化   | | 营销活动  | | 推荐服务  |        |
|  |  (User)  | |  (Member) | |(Commercial)| |(Promotion)| |(Recommend)|       |
|  +----------+ +----------+ +-----+----+ +----------+ +-----+----+        |
|                                  |                          |              |
|  +----------+ +----------+ +-----v----+ +----------+ +-----v----+        |
|  | 用户画像  | | A/B测试   | |Commercial| | 风控服务  | |Recommend |        |
|  |(Profile) | |   (AB)    | | -worker  | |  (Risk)  | | -worker  |        |
|  +----------+ +----------+ +----------+ +----------+ +----------+        |
+------------------------+---------------+-----------------------------------+
                         |
+------------------------+----------------+----------------------------------+
|                      事件总线 (Kafka)                                        |
|  Topics: balance_events | chat.completed | user.login | member.level_up      |
|          promotion.claimed | risk.triggered | report.generated              |
|  账本 Topic：balance_events（按 user_id 分区，全量资金事件，可重放）          |
+------------------------+---------------+-----------------------------------+
        | 同步读写                | HTTP + SSE
+-------v------------------------+------------------------------------------+
|  基础设施：Redis（扣款现场+事件缓存）  PostgreSQL（业务数据+投影）             |
+--------------------------------------------------------------------------+
                         | HTTP + SSE
+------------------------+----------------+----------------------------------+
|              Python Agent Core（有状态，独立部署）                          |
|  +----------+ +----------+ +----------+ +----------+ +----------+        |
|  | 对话引擎   | | MCP协议   | | Sandbox  | | RAG检索  | | 报表服务  |        |
|  | (核心)    | | (Skills)  | | (安全执行) | |(ChromaDB) | |(Report)  |        |
|  +----------+ +----------+ +----------+ +----------+ +----------+        |
|  +----------+ +----------+ +----------+                                 |
|  | 情绪雷达   | | 方言管理   | | 推荐话术   |  <- Plugin 层，可独立开关        |
|  |(Emotion)  | |(Dialect)  | |(RecommendPlugin)                       |
|  +----------+ +----------+ +----------+                                 |
+-------------------------------------------------------------------------+
```

### 2.2 通信协议

| 场景 | 协议 | 理由 |
|------|------|------|
| Gateway -> Go 微服务 | HTTP/1.1 + JSON | 外部接入标准，调试友好 |
| Go 微服务之间 | gRPC + Protobuf | 内部通信高效，强类型约束 |
| Go -> Python Agent | HTTP/1.1 + SSE | SSE 支持流式输出，Python 侧实现简单 |
| 扣款事件 -> Kafka | Producer 异步批量 | 高吞吐、持久化、可重放，承担账本角色 |
| 事件总线 -> 服务/Worker | Kafka Consumer Group | 广播/单播灵活，手动提交位点保证 at-least-once |

### 2.3 状态管理策略

| 服务/组件 | 状态性质 | 策略 |
|------|---------|------|
| Gateway | 无状态 | 不保存会话，JWT 自包含，可任意扩容 |
| User/Member/Promotion | 无状态 | 数据持久化到 PostgreSQL，Redis 做缓存 |
| Commercial 同步链路 | 无状态 | 扣款现场在 Redis，账本在 Kafka，自身不存状态 |
| Commercial-worker | 无状态 | 批量消费 Kafka 写 DB，位点存 water_mark 表 |
| Recommend | 无状态 | 规则配置加载到内存，Redis 缓存计算结果 |
| Python Agent Core | **有状态** | 对话上下文保存在内存，定期持久化到 PostgreSQL |
| Redis | **有状态（现场）** | AOF everysec + RDB 每日冷备；崩溃后可从 Kafka 重建 |

---

## 三、服务拆分与职责边界

### 3.1 服务清单

| 服务名 | 职责 | 端口 | 数据库 | 缓存 |
|--------|------|------|--------|------|
| Gateway | 路由、鉴权、限流、Feature Flag、A/B 分流、日志 | 8199 | - | Redis |
| User Service | 注册、登录、JWT 签发、用户信息管理 | 50051(gRPC) | PostgreSQL | Redis |
| Member Service | 好友度计算、等级管理、权益查询 | 50052(gRPC) | PostgreSQL | Redis |
| Commercial Service | 积分账户、充值、预扣、结算、对账、降级开关 | 50053(gRPC) | PostgreSQL | Redis |
| Commercial-worker | 消费 balance_events，按用户合并净额批量落库 | - | PostgreSQL | - |
| Promotion Service | 活动配置、资格校验、参与记录、优先级排序、充值活动核销 | 50054(gRPC) | PostgreSQL | Redis |
| Recommend Service | 规则匹配、评分排序、结果缓存 | 50055(gRPC) | PostgreSQL | Redis |
| Profile Service | 用户画像标签、行为数据聚合、LLM 报表触发、动态调整值计算 | 50056(gRPC) | PostgreSQL | Redis |
| AB Test Service | 实验配置、一致性哈希分流、埋点数据收集 | 50057(gRPC) | PostgreSQL | Redis |
| Risk Service | 风控规则检测、拦截记录、证据留存 | 50058(gRPC) | PostgreSQL | Redis |

### 3.2 服务间依赖关系

```
Gateway
+-- User Service（同步，登录/注册）
+-- Member Service（同步，查询等级）
+-- Commercial Service（同步，充值/余额/预扣结算；内部读写 Redis + 发 Kafka）
+-- Promotion Service（同步，活动 preview / 充值核销）
+-- Recommend Service（同步，获取推荐套餐 + LLM 话术）
+-- Profile Service（同步取动态调整值；异步 Kafka 更新画像）
+-- AB Test Service（同步，但可被 Feature Flag 跳过）
+-- Risk Service（同步，但失败不阻断主链路）
+-- Python Agent Core（同步 SSE，对话主链路）

Commercial-worker（独立进程）
+-- Kafka balance_events（批量消费 -> 合并净额 -> 批量写 DB）
```

**关键设计**：Risk Service 调用失败时，Gateway 记录日志但不阻断请求，避免风控服务故障导致整个系统不可用（Fail-Safe 策略）。

---

## 四、网关层设计

### 4.1 中间件链顺序

```
Request -> [Recovery] -> [RequestID] -> [Zap Log] -> [Rate Limit] -> 
          [Feature Flag] -> [JWT Auth] -> [A/B Split (Conditional)] -> 
          [Risk Check (Conditional)] -> Router Handler -> Response
```

**顺序理由**：
1. **Recovery 最前**：防止 panic 导致进程崩溃
2. **RequestID 第二**：后续所有日志、追踪都依赖此 ID
3. **Rate Limit 在 Auth 前**：防止恶意请求消耗 JWT 校验资源
4. **Feature Flag 在 Auth 后**：需要 user_id 才能做用户级实验开关
5. **A/B Split 在 Feature Flag 后**：只有开关开启时才执行

### 4.2 限流策略

| 维度 | 策略 | 实现 |
|------|------|------|
| 全局 QPS | 令牌桶，默认 10000 QPS | Redis + Lua 脚本 |
| 用户级 QPS | 滑动窗口，默认 100 QPS/用户 | Redis + ZSET |
| 对话接口 | 更严格，20 QPS/用户，防止刷对话扣费 | Redis + ZSET |
| 充值接口 | 最严格，5 QPM/用户，防止重复提交 | Redis + 固定窗口 |

### 4.3 Feature Flag 中间件

```go
// 伪代码，表达设计思路
func FeatureFlagMiddleware() gin.HandlerFunc {
    return func(c *gin.Context) {
        // 1. 从 Redis/本地缓存读取全局开关
        abEnabled := featureFlagClient.GetBool("ab_test_enabled")
        c.Set("ab_test_enabled", abEnabled)

        // 2. 读取实验级开关
        experimentID := c.GetHeader("X-Experiment-ID")
        if experimentID != "" {
            expEnabled := featureFlagClient.GetBool("exp_" + experimentID)
            c.Set("exp_enabled", expEnabled)
        }

        // 3. 商业化链路模式开关（redis 扣款 / db 直连降级）
        commercialMode := featureFlagClient.GetString("commercial_flow_mode")
        c.Set("commercial_flow_mode", commercialMode) // "redis" | "db_direct"

        // 4. 继续执行，后续中间件根据 c.Get 结果决定是否执行 A/B 逻辑
        c.Next()
    }
}
```

**关键设计**：
- Feature Flag 数据存储在 PostgreSQL，Gateway 启动时加载到本地内存，并监听 Redis Pub/Sub 实时更新
- 开关关闭时，A/B Split 中间件直接 c.Next()，不执行任何额外逻辑，请求延迟增加 < 1ms
- 支持用户级灰度：如 user_id % 100 < 10 的用户才开启某实验
- `commercial_flow_mode` 是本架构的核心开关，详见 [7.9 降级方案](#79-降级方案与-flow_version-机制)

---

## 五、用户服务

### 5.1 核心功能

- 用户注册（邮箱/手机号）
- 用户登录（密码 + JWT 签发）
- Token 刷新（Refresh Token 机制）
- 用户信息查询（gRPC 供其他服务调用）

### 5.2 JWT 设计

| Token 类型 | 有效期 | 存储位置 | 用途 |
|-----------|--------|---------|------|
| Access Token | 2 小时 | 客户端内存 | 常规请求鉴权 |
| Refresh Token | 7 天 | 客户端 + PostgreSQL | 刷新 Access Token |

**安全设计**：
- JWT Secret 存储在 K8s Secret，不在代码中硬编码
- Refresh Token 存储在 PostgreSQL refresh_tokens 表，支持吊销（登出时删除）
- Access Token 解析不查库，Refresh Token 刷新时查库校验

### 5.3 幂等性设计

注册和充值等关键操作支持幂等性：
- 客户端生成 Idempotency-Key（UUID），放在请求头
- Gateway 层先查 Redis `idem:{key}`，若存在则直接返回缓存结果
- 业务层执行成功后，将 Idempotency-Key -> response 写入 Redis，TTL 24 小时
- DB 层 transactions 表对 (user_id, idempotency_key, type) 建唯一索引兜底（见 15.4）

---

## 六、会员等级服务（好友度模型）

### 6.1 核心模型

采用好友度模型（类似社交产品的亲密度系统），等级命名为古代官职体系，契合 Agent 宫廷太监人设——陪伴越久，"伴驾资历"越深，从乡土小吏一路晋升到皇帝：

```
聊天 1 次 -> +1 亲密度分
连续 7 天聊天 -> 额外 +5 分（激励连续活跃）
超过 3 天未聊天 -> 每天 -1 分（衰减机制，防止刷分后沉默）
```

### 6.2 等级体系

| 等级 | 官职 | 品级叙事 | 分数区间 | 权益 |
|------|------|---------|---------|------|
| 1 | 里正 | 乡土小吏（无品） | 0 - 10 | 基础对话，无额外权益 |
| 2 | 县令 | 一县之主（七品） | 11 - 50 | 积分倍率 1.1x，解锁月卡购买 |
| 3 | 知府 | 一府之主（四品） | 51 - 200 | 积分倍率 1.2x，解锁无限畅聊活动资格 |
| 4 | 丞相 | 百官之首（一品） | 201 - 500 | 积分倍率 1.5x，专属套餐可见，优先客服 |
| 5 | 皇帝 | 九五之尊 | 500+ | 积分倍率 2.0x，所有活动优先参与，专属 Agent 皮肤 |

**命名设计说明**：抛开皇帝（至尊，固定顶格），其余四级按 1~9 品近似等差分布（无品 → 七品 → 四品 → 一品），相邻级别只差 2~3 品，不存在"昨天县令今天丞相"的叙事断层。行政辖区递进为：村 → 县 → 府 → 朝堂 → 天下。

**文案联动示例**：
- 升级推送："恭喜陛下，您已晋升为知府，执掌一府之地！无限畅聊活动已为您解锁。"
- 权益不足提示："知府及以上方可购买无限畅聊礼包，陛下当前为县令，再聊 40 句即可晋升。"

### 6.3 升级流程

```
用户完成对话
    -> Gateway 调用 Python Agent Core
    -> Python 返回对话结果
    -> Gateway 异步发送 chat.completed 事件到 Kafka
    -> Member Service 消费事件
        -> 更新 user_intimacy 表（chat_count++, score++）
        -> 检查是否满足升级条件
        -> 若升级：
            -> 更新 current_level
            -> 发送 member.level_up 事件到 Kafka
            -> 各服务消费事件更新缓存
```

**关键设计**：
- 升级判定在 Member Service 内部完成，不依赖外部服务
- 升级事件通过 Kafka 广播，Commercial Service 收到后更新积分倍率缓存，Recommend Service 收到后更新规则权重缓存
- 衰减任务：每日凌晨定时扫描 last_chat_at < now() - 3 days 的用户，执行衰减

### 6.4 并发控制

user_intimacy 表更新时可能并发（用户同时多端对话）：
- **乐观锁**：表增加 version 字段，更新时 WHERE version = old_version
- **降级方案**：乐观锁冲突时，将更新操作放入 Kafka 延迟队列，串行执行

---

## 七、商业化服务（Redis 扣款 + Kafka 账本 + 异步落库）

> 本章是 v3.1 的核心重构。设计目标：**Redis 扛并发、Kafka 当账本、DB 做异步投影**，三层各司其职，任何一层故障均可降级恢复。

### 7.1 核心设计认知

```
Redis   = 扣款现场：用户实时余额视图，同步读写，RT < 1ms，抗压层
Kafka   = 权威账本：全量资金事件的 append-only 日志，可持久化、可重放
DB      = 异步投影：由 Commercial-worker 消费 Kafka 批量构建，用于查询、对账、报表
```

三条铁律：

1. **账本只能补记，不能篡改**：任何资金事件必须先存在于 Kafka，才有最终效力；Redis/DB 与 Kafka 不一致时，以 Kafka 重放结果为准修正投影
2. **DB 永远只有一条写入路径**：worker 消费 Kafka 落库。对账修复、冻结释放不直接写 DB，一律补发事件到 Kafka 走正常消费链路
3. **冻结可以乐观，扣款必须悲观**：预扣只是占位（丢了重预扣即可），结算涉及真实资金变动，必须有兜底校验

### 7.2 核心功能

- 积分账户管理（余额、冻结余额、可用余额）
- 套餐管理（CRUD，等级限制）
- 充值（RECHARGE 事件，无需预扣）
- 预扣（对话前冻结积分，Redis Lua）
- 结算（对话后实际扣减，Redis Lua + Kafka 事件）
- 对账（三层口径，见 7.8）
- 降级开关（Redis 故障切换 DB 直连，见 7.9）

### 7.3 账户模型与 Redis 数据结构

**账户语义**（与 DB accounts 表同构）：

```
可用余额 available = total - frozen
充值     -> total + N, available + N
预扣     -> frozen + N, available - N
结算     -> total - M, frozen - N（M <= N，差额 N-M 退回 available）
取消     -> frozen - N, available + N
```

**Redis 数据结构**（单节点部署，配置见 19.x）：

| Key | 类型 | 内容 | 过期/淘汰 |
|-----|------|------|----------|
| `balance:{user_id}` | HASH | `total / frozen / available / version` | 不过期 |
| `balance_events:{user_id}` | STREAM | 原始事件队列，字段：`event_id / seq / type / amount / idempotency_key / prehold_id / flow_version / ts` | MAXLEN ≈ 1000 截断，TTL 7 天 |
| `prehold:{prehold_id}` | STRING(JSON) | 预扣记录：`user_id / amount / event_id / flow_version / created_at` | TTL 30 分钟 |
| `idem:{idempotency_key}` | STRING | 幂等结果缓存（response 摘要 + prehold_id） | TTL 24 小时 |

**为什么预扣记录放 Redis 并设 30 分钟 TTL**：预扣是"现场占位"，生命周期就是一次对话。TTL 兜底对话崩溃导致的冻结泄漏，超时后由孤儿冻结清理任务释放（见 7.8 层三）。

### 7.4 预扣额度计算策略（动态调整）

```
预扣额度 = 前3次对话实际消耗的平均值 + 动态调整值

其中：
- 前3次均值：用户最近3次 completed 对话的实际结算金额平均值（不足3次按实际次数）
- 动态调整值：由 Profile Service 提供（见 11.3），上线初期为 0
  -> 画像标签"对话复杂度"高（长对话、多轮次）-> 动态调整值 +5
  -> 画像标签"消费稳定"（历史波动小）-> 动态调整值 -2（减少冻结，提升体验）
  -> 画像标签"新用户"（历史数据不足3次）-> 动态调整值 +10（保守策略）
```

**设计理由**：
- 固定预扣额度（如每次预扣10积分）会导致：短对话实际只消耗3积分，结算后退回7积分，频繁预扣/释放增加系统开销
- 动态预扣能减少资金冻结时间，提升用户体验
- 动态调整值与用户画像挂钩，为后续精细化运营预留扩展点
- 兜底：预扣额度上限不超过用户当前可用余额，超出则直接返回"余额不足"触发推荐链路

### 7.5 预扣结算完整流程

#### 7.5.1 预扣 Prehold（Redis Lua，不落库）

```
对话开始
    -> Gateway 计算预扣额度（前3次均值 + 动态调整值）
    -> 读取 Feature Flag: commercial_flow_mode
        -> redis 模式（正常态）：走 7.5.2 Redis 链路
        -> db_direct 模式（降级态）：走 7.9 DB 直连链路
```

#### 7.5.2 Redis 链路（正常态，flow_version = 1）

```
Commercial.Prehold(user_id, amount, idempotency_key)
    -> 1. 幂等检查：GET idem:{idempotency_key}，命中直接返回缓存的 prehold_id
    -> 2. Lua 脚本原子执行（单线程 Redis 内天然串行，同用户并发预扣不会超扣）：

       -- KEYS[1] = balance:{user_id}
       -- KEYS[2] = balance_events:{user_id}
       -- ARGV: amount, event_id, idem_key, seq, ts
       local available = tonumber(redis.call('HGET', KEYS[1], 'available') or '0')
       if available < tonumber(ARGV[1]) then
           return {-1}                      -- 余额不足，返回错误码
       end
       redis.call('HINCRBY', KEYS[1], 'frozen', ARGV[1])
       redis.call('HINCRBY', KEYS[1], 'available', -ARGV[1])
       redis.call('XADD', KEYS[2], 'MAXLEN', '~', '1000', '*',
           'event_id', ARGV[2], 'seq', ARGV[4], 'type', 'PREHOLD',
           'amount', ARGV[1], 'idem_key', ARGV[3], 'flow_version', '1', 'ts', ARGV[5])
       return {1}

    -> 3. Lua 成功 -> 写 prehold:{prehold_id}（JSON，TTL 30min，含 flow_version=1）
    -> 4. 异步发 Kafka：topic balance_events，key = user_id（同用户事件进同分区保证有序），
         消息体 = Stream 中那条事件原文（event_id 一致）
    -> 5. 返回 prehold_id 给 Gateway（用户无感知 RT < 1ms）
    -> 6. 发 Kafka 失败（进程崩溃间隙）：事件仍在 Redis Stream 中，
         由对账层三 diff 发现后补发（见 7.8），不阻塞用户
```

#### 7.5.3 对话与结算

```
    -> Gateway 调用 Python Agent Core 进行对话（SSE 流式）
    -> 对话结束，Python 回调携 actual_amount（实际 token 消耗折算）
    -> Commercial.Settle(prehold_id, actual_amount)
        -> 读 prehold:{prehold_id} 得到 flow_version
        -> flow_version = 1（Redis 链路）：
            Lua 脚本原子执行：
              a. 校验 prehold 存在且未结算（幂等：同一 prehold_id 重复结算返回原结果）
              b. total -= actual_amount
              c. frozen -= prehold_amount
              d. available += (prehold_amount - actual_amount)   -- 退回多余部分
              e. XADD 一条 SETTLE 事件（携带 prehold_id 关联，event_id 新生成）
              f. DEL prehold:{prehold_id}
            -> 异步发 Kafka（SETTLE 事件）
        -> flow_version = 2（DB 直连链路，见 7.9）
```

**防御性处理**：若 actual_amount > prehold_amount（理论上不应发生）：
- 按 prehold_amount 结算，差额记录为 bad_debt
- 触发风控告警（可能是计费 bug 或恶意构造回调）

#### 7.5.4 充值（RECHARGE，无需预扣）

```
POST /api/v1/commercial/recharge
    -> 幂等检查（idem key）
    -> Redis Lua：total += N, available += N，XADD RECHARGE 事件
    -> 异步发 Kafka
    -> 若有 promotion_id：由 Promotion Service 同事务核销（见 8.7）
```

### 7.6 幂等性保证

| 操作 | 幂等键 | Redis 层 | DB 层兜底 |
|------|--------|---------|----------|
| 充值 | Idempotency-Key | `idem:{key}` SETNX，TTL 24h | transactions 唯一索引 (user_id, idempotency_key, type) |
| 预扣 | Idempotency-Key | `idem:{key}` 缓存 prehold_id | 同上；prehold 记录含 idem key |
| 结算 | prehold_id | prehold 记录存在性校验（结算后删除） | transactions 中 SETTLE 事件携带 prehold_id，可查询验证 |
| Kafka 消费 | event_id | - | worker 消费端按 event_id 幂等去重（INSERT ... ON CONFLICT DO NOTHING） |

**event_id 全局唯一**（UUID v4），贯穿 Redis Stream、Kafka、transactions 表三方，是对账 diff 的锚点。

### 7.7 Commercial-worker：批量净额落库

**职责**：消费 Kafka balance_events，按用户合并净额，批量写 PostgreSQL。这是 DB 的唯一写入路径。

```
消费参数：
    max.poll.records = 500        // 单次最多拉 500 条
    强制 flush 间隔 = 200ms       // 低峰兜底，保证延迟不超过 200ms
    分区-消费者对应               // 同用户事件同分区有序，预扣一定先于结算到达

处理逻辑（伪代码）：
for {
    msgs := consumer.Poll(500条 或 200ms超时)
    if len(msgs) == 0 { continue }

    merged := map[userID]*净额{}
    for msg := range msgs {
        event := parse(msg)
        if alreadyProcessed(event.event_id) { continue }   // 幂等去重
        u := merged[event.user_id]
        switch event.type {
        case PREHOLD:  u.frozen += event.amount            // 仅影响冻结
        case SETTLE:   u.total -= event.amount             // 净扣减
                       u.frozen -= 对应prehold金额          // 从 prehold 记录取
        case CANCEL:   u.frozen -= event.amount
        case RECHARGE: u.total += event.amount
        }
        u.events = append(u.events, event)
    }

    db.Transaction {
        for uid, m := range merged {
            UPDATE accounts SET total = total + m.total,
                                frozen = frozen + m.frozen,
                                version = version + 1
            WHERE user_id = uid
            INSERT transactions ... (m.events，逐条，含 balance_before/after 快照)
        }
        UPDATE water_mark SET max_offset = msgs.MaxOffset() WHERE partition = p
    }                                                    // 事务成功才落库

    consumer.Commit()                                    // 落库成功才提交位点
}
```

**关键设计**：
- **at-least-once + 消费端幂等**：位点在 DB 事务提交后才 Commit，崩溃重投时按 event_id 去重，保证不丢不重
- **净额合并**：预扣+结算合并成净额，流水仍逐条 INSERT（对账依据是流水，不能合并）
- **水位线 water_mark 表**：`partition_no / max_offset / updated_at`，记录"已安全落库的最大位点"，是对账层二的重放上界
- **收益**：每对话 DB 写入从 2 次事务降到 ~0.5 次（批量），预扣完全无 DB 压力，1000 QPS 对话场景 DB 仅承受 5~10 TPS 批量写入

### 7.8 对账机制（三层口径）

**触发节奏**：

| 频率 | 动作 |
|------|------|
| 每 200ms | worker 常态批量落库 |
| 每 5 分钟 | 轻量巡检：孤儿冻结清理 + 抽样比对 1000 个当日活跃用户 |
| 每日凌晨 2:00 | 全量三层对账，产出 account_reconciliation 表 + 差异邮件（configs/commercial.yml 中 reconciliation.alert_email） |

**总原则**：三层必须全部对上，当日账务才算闭环。任何一层 diff 不为零 → 进入对应异常处理流程，修复动作只有三种：**补发**（你有我无）、**重放修复**（我有你无，按 Kafka 结果修正投影）、**报警**（金额不符或 diff 后仍不平）。

#### 层一：DB 内部一致性（期初 + 流水 = 期末）

```
重放范围：water_mark 各分区 max_offset 之前的全部事件
执行：
    1. 从 water_mark 取各分区上界（如分区 0 = 8000），上次对账位点 7500
    2. 顺序拉取 Kafka 分区 (7500, 8000] 的全部事件，内存按 user_id 分组重放
       （Kafka 是日志不是数据库，无法按用户跳转查询，只能按位点顺序读再分拣——
         位点定区间，重放时分拣用户）
    3. 对每用户：理论余额 = Σ(RECHARGE) - Σ(SETTLE)
       比对 transactions 表流水汇总 vs accounts 表余额
    4. 同时校验连续性：本日期初 = 昨日期末
异常处理：
    - 流水汇总 ≠ 期末余额：以流水为准重算余额，写 RECONCILE_FIX 补偿流水，告警
    - 期初 ≠ 昨日期末：说明前一日对账遗漏，升级为人工处理
```

#### 层二：Kafka ↔ DB（验证投影完整性）

```
执行：
    1. 同层一的重放区间，得到 Kafka 侧事件清单 A（按 event_id 索引）
    2. 拉取 transactions 表中该区间的流水清单 B
    3. 逐条 event_id diff：
       Kafka 有、DB 没有        -> 消费丢失（worker 漏处理/事务回滚残留）
                                  -> 重新投递该事件区间，补落库
       DB 有、Kafka 没有        -> worker 写了无账本依据的数据（bug 或手动改库）
                                  -> 冻结该账户人工核查，P0 告警
       两边都有、金额对不上    -> 重复消费或合并净额计算错误
                                  -> 按 event_id 去重重放比对，修 DB
       diff 后仍对不上          -> 邮件报警，人工介入
```

#### 层三：Redis ↔ Kafka（验证现场正确性）

```
执行：
    1. 同样重放 Kafka 得权威余额
    2. 逐用户比对 balance:{user_id} 与 balance_events:{user_id} Stream
    3. 事件级 diff（注意：不能只看余额方向，同一方向有两个病因，必须按 event_id 区分）：

       Stream 有、Kafka 没有    -> 病因：Lua 成功但发 Kafka 前崩溃 / Kafka 当时异常
                                  -> Redis 是对的（用户确实被扣，服务确实给了）
                                  -> 从 Stream 取出原始事件（原 event_id），原样补发到 Kafka
                                  -> Redis 不动；worker 消费端幂等，重复也不重记账
       Kafka 有、Stream 没有    -> 病因：Redis 崩溃重启丢数据（AOF everysec 窗口）
                                  -> Kafka 是对的
                                  -> 把缺失事件按 seq 顺序重放到 Redis（余额 + Stream 补齐）
       同 event_id 金额不一致   -> 严重异常（正常流程不可能发生），直接 P0 报警
       diff 后仍对不上          -> 邮件报警

    4. 孤儿冻结清理（搭车执行）：
       扫描 prehold:* 中 created_at 超过 30 分钟未结算的记录
       -> Redis 释放冻结（frozen -= amount, available += amount）
       -> XADD 一条 CANCEL 事件 + 补发到 Kafka（走 worker 正常落库，
          不直接插 DB——DB 永远只有 worker 一条写入路径）
       -> 同用户频繁出现孤儿预扣（>3 次/天）-> 触发风控告警（疑似刷预扣接口）
```

#### 对账产出

- `account_reconciliation` 表：每用户每日一行（期初/期末/充值/消费/退款/差异/状态）
- 差异邮件分三段：层一 DB 内部差异、层二投影差异、层三现场差异（含补发/重放条数）
- `reconciliation_exceptions` 死信表：自动修复失败的账户，人工处理入口
- 连续 N 天同类 diff -> 熔断：切换 commercial_flow_mode = db_direct，排查后再恢复

### 7.9 降级方案与 flow_version 机制

**触发条件**：Redis 宕机 / 层三对账连续异常 / 手动切换。开关为 Feature Flag `commercial_flow_mode`（`redis` | `db_direct`），Gateway 本地缓存 + Pub/Sub 秒级生效。

**核心问题**：切换是瞬间的，对话是过程的。21:00:00 预扣的老对话，21:00:01 系统切换，21:00:30 结算——这笔结算必须回老流程处理（Redis 里还冻着钱），否则冻结释放不了。

**解法：预扣记录打 flow_version 标签**

```
预扣时：
    flow_version = 当前开关值（1 = Redis 链路 / 2 = DB 直连链路）
    预扣记录（Redis prehold:{id} 或 DB frozen_records 表）都带此字段，随 Kafka 事件传递

结算时：
    按 prehold 记录上的 flow_version 路由：
        v1 -> Redis Lua 结算（释放冻结 + 差额结算 + XADD 事件）
        v2 -> DB 事务结算（UPDATE accounts ... WHERE balance >= ? + INSERT 流水）
    两种结局都发 Kafka 事件，worker 落库逻辑完全一致
```

**降级态（db_direct）行为**：

```
新预扣（v2）：
    -> DB 事务：SELECT ... FOR UPDATE 或 UPDATE ... WHERE available >= N
       检查并冻结（即最早那版强一致方案，代码常驻）
    -> 同事务写 frozen_records 表（prehold_id, amount, flow_version=2）
    -> 发 Kafka PREHOLD 事件（worker 照常落库流水）

存量 v1 对话结算：
    -> Redis 挂了，v1 冻结无法实时释放 -> 不需要实时释放：
       结算事件照常发 Kafka 记账（settle 事件正常产生，worker 正常落库），
       Redis 那份冻结只是防超扣占位，等 Redis 恢复后由孤儿冻结清理统一释放 + 补 CANCEL
    -> 若 Redis 未挂只是对账异常：v1 结算走 Redis 路径正常释放
```

**恢复流程（Redis 修复后切回）**：

```
1. 保持 db_direct，先重建 Redis：
   flushall（仅 balance 相关 key）-> 从 Kafka 头（或昨日快照位点）重放全部
   balance_events -> 重建 balance hash 与 Stream
2. 重建校验：抽样比对 Redis 余额 vs Kafka 重放余额，100% 一致才继续
3. 检查残留 prehold:*：均已超 TTL 自然消亡；若有未过期 v1 记录且其对话已结束，
   按孤儿清理流程释放
4. 开关切回 redis 模式
5. 紧接着跑一轮全量三层对账确认无损
```

**面试话术**："Redis 故障不是一致性问题，是可用性问题——账本在 Kafka 里完好，切 DB 直连保可用，恢复后从账本重建现场，三层对账兜底验证。设计上从不把 Redis 当唯一真相源。"

### 7.10 Redis 部署与拓扑演进

**初期：单节点**（面试项目与上线初期）

```
配置：
    appendonly yes
    appendfsync everysec     # 最多丢 1s 数据，由层三对账兜底
    save 900 1               # RDB 每日冷备（可选，用于灾难恢复加速）
内存：余额 + 事件流，百万用户约 500MB，单机宽裕
```

**演进路线**（扣款链路代码零改动，这是对账设计的回报）：

| 阶段 | 拓扑 | 触发条件 | 新增风险 |
|------|------|---------|---------|
| 1 | 单节点 + AOF | 初期 | 崩溃窗口 ≤1s，层三兜底 |
| 2 | 主从 + 哨兵 | 可用性要求提升 | 主从切换可能丢数据（病因 B 回归），层三 event_id diff 原样接住 |
| 3 | Cluster | 数据量超单机内存或 QPS 超单机极限 | 分片、跨 slot Lua 限制，需调整 key 设计 |

---

## 八、营销活动服务

### 8.1 活动类型设计

初始化 4 个活动：

| 活动 | 类型 | 触发条件 | 奖励 | 限制 |
|------|------|---------|------|------|
| 迎新礼包 | 一次性 | 注册 7 天内 | 直接赠送 100 积分 | 每人限 1 次 |
| 无限畅聊 | 时段包 | 知府（3级）及以上可购买 | 购买后 24 小时内对话不扣费 | 时段包不互斥，取并集 |
| 充值送积分 | 比例赠送 | 任意充值 | 阶梯赠送：充10送10，充20送30，充50送80，充100送200 | 与满减互斥（charge_group） |
| 满减礼包 | 满减券 | 单次充值满 100 元 | 立减 10 元（实付 90 得 100 积分） | 每月限 2 次，与充值送积分互斥 |

### 8.2 活动元数据模型

```json
{
  "id": 1,
  "type": "WELCOME",
  "name": "迎新礼包",
  "priority": 100,
  "mutex_group": "welcome_group",
  "min_level": 1,
  "min_level_title": "里正",
  "rules": {
    "trigger": "REGISTER",
    "time_window_days": 7,
    "max_claims_per_user": 1,
    "reward": {"type": "POINTS", "amount": 100}
  },
  "stock": -1,
  "start_time": "2026-09-01T00:00:00Z",
  "end_time": "2026-12-31T23:59:59Z"
}
```

### 8.3 活动过期机制

所有活动均有到期时间，过期后自动作废：

```
活动过期判定：
    -> 每次用户请求参与/购买时，先检查 now() > end_time
    -> 若已过期：
        -> 返回"活动已结束"
        -> 异步更新 promotions 表 status = 'ENDED'
        -> 已领取但未使用的用户礼包，状态变为 EXPIRED（不可使用）
    -> 定时任务每日扫描：
        -> 扫描所有 status = 'ACTIVE' 且 end_time < now() 的活动
        -> 批量更新 status = 'ENDED'
        -> 扫描所有 user_promotions 中 status = 'CLAIMED' 且对应活动已结束的记录
        -> 批量更新 status = 'EXPIRED'
```

### 8.4 优先级与互斥策略

**优先级栈**（数值越大优先级越高）：

```
迎新礼包(100) > 充值送积分(60) > 满减礼包(40)
```

**注意**：无限畅聊不参与优先级排序，因为它与其他活动不互斥（详见 8.5 节）。

**互斥组**：
- welcome_group：迎新礼包（内部互斥，但只有一个）
- charge_group：充值送积分、满减礼包（同一用户同一笔充值只能享受其中一个）
- 无限畅聊：无互斥组，可与其他活动同时生效

**参与判定流程**：

```
用户请求参与活动 / 系统主动推荐活动
    -> 1. 过滤过期活动（now() <= end_time，否则返回"活动已结束"）
    -> 2. 过滤状态（status = ACTIVE）
    -> 3. 过滤等级（user_level >= min_level，文案展示官职名）
    -> 4. 过滤库存（stock == -1 || stock > 0）
    -> 5. 过滤已参与次数（user_promotions 表统计 < max_claims_per_user）
    -> 6. 按 priority DESC 排序（无限畅聊不参与排序，单独处理）
    -> 7. 检查互斥组：若用户已在同一 mutex_group 有 ACTIVE 状态的活动，跳过该活动
    -> 8. 返回第一个符合条件的非畅聊活动 + 所有有效的无限畅聊活动
```

### 8.5 无限畅聊的特殊处理（无互斥，取并集）

无限畅聊作为时段包，与其他活动不互斥，可同时拥有多个：

```
用户拥有多个无限畅聊礼包时：
    -> 取所有有效畅聊时段的并集（即合并时间段）
    -> 对话时检查当前时间是否落在任一畅聊时段内
    -> 若是：对话不扣费
    -> 若否：正常扣费

畅聊时段相交时的处理：
    -> 系统检测到用户新领取的畅聊时段与已有时段存在交集
    -> 向用户发送提醒："陛下已拥有重叠的无限畅聊时段，是否确认使用新礼包？"
    -> 用户确认后：新时段生效，交集时段内不重复计算（自然取并集）
    -> 用户取消后：新礼包退回库存，记录取消原因
```

### 8.6 库存扣减与一致性保证

采用 **Redis 预扣 + 数据库确认** 模式：

1. 用户参与活动前，先执行 DECR promotion_stock:{promotion_id}
2. 若 Redis 返回值 >= 0，允许参与，写入 user_promotions 表
3. 若 Redis 返回值 < 0，拒绝参与，并异步校准 Redis 与数据库库存
4. 定时任务每日校准：SUM(user_promotions WHERE promotion_id = X) + Redis stock = 初始库存

**理由**：活动参与是高并发场景（如大促），Redis 预扣避免数据库行锁竞争。库存允许短暂不一致（最终一致），但绝不超卖。校准机制和对账机制保证最终一致性。

### 8.7 活动入口：与充值链路一体化（v3.1 修订）

> 设计决策：不建独立商城。营销活动以"充值时可见、下单时核销"的方式接入充值链路，砍掉 goods/orders 两张表与订单状态机，业务更聚焦。

#### 充值预览接口

```
GET /api/v1/commercial/recharge/preview?amount=100
    -> 返回：
       {
         "package_options": [...],           // 套餐列表（含等级限制过滤）
         "available_promotions": [           // 实时跑 8.4 过滤+优先级+互斥逻辑
           {
             "promotion_id": 3,
             "name": "充值送积分",
             "description": "充100送200积分",
             "estimated_reward": 200,
             "priority": 60
           },
           {
             "promotion_id": 4,
             "name": "满减礼包",
             "description": "满100减10元",
             "priority": 40,
             "mutex_note": "与充值送积分互斥"
           }
         ],
         "time_pack_offer": {                // 知府及以上可见的无限畅聊购买入口
           "promotion_id": 2,
           "price": 20,
           "duration_hours": 24
         }
       }
    -> 前端充值页直接渲染：金额输入框 + 可用活动标签（默认勾选优先级最高者）
```

#### 充值下单与核销

```
POST /api/v1/commercial/recharge
    Body: { amount, idempotency_key, promotion_id? }

    -> 1. 幂等检查（idem key，见 7.6）
    -> 2. 若带 promotion_id，Promotion Service 二次校验（防绕过前端）：
         资格（等级/时间窗/互斥组/参与次数）-> 不通过则忽略活动按原价充值并提示
    -> 3. 执行充值（7.5.4 Redis Lua + Kafka 事件）
    -> 4. 同事务（Promotion 侧本地事务）：
         写 user_promotions 记录（status = CLAIMED）
         若活动有实际成本（满减少收 10 元）-> 记录 promotion_cost 流水
    -> 5. 发 promotion.claimed 事件到 Kafka（画像/推荐消费）
    -> 6. 返回：实付金额、到账积分、活动奖励明细
```

#### 无限畅聊购买与激活码礼包

- 无限畅聊：充值页活动区独立入口（`time_pack_offer`），知府（3级）及以上可见可买；购买成功即触发 8.5 的时段并集逻辑
- 激活码礼包：保留独立兑换入口 `POST /api/v1/promotion/gift/redeem`（不经过充值链路），兑换即充值 + 发礼包权益，幂等键防重复兑换，库存 Redis 预扣防超发

---

## 九、推荐服务（规则引擎 + LLM 话术）

> v3.1 修订：规则引擎负责"召回 + 排序"（定价决策权永远在服务端），LLM 只负责"话术生成"（太监人设口吻提示余额不足并推荐套餐）。

### 9.1 规则设计

5 条核心规则，每条规则受用户等级影响：

| 规则 | 触发条件 | 基础分 | 等级权重（里正/县令/知府/丞相/皇帝） | 说明 |
|------|---------|--------|------------------------|------|
| NEW_USER_WELCOME | 新用户，注册 < 3 天 | 100 | 1.0 / 0.8 / 0.5 / 0.3 / 0.1 | 新用户优先推荐体验卡，等级越高权重越低（老用户不需要迎新） |
| LOW_BALANCE_URGENT | 可用余额 < 50 | 80 | 0.5 / 0.8 / 1.0 / 1.2 / 1.5 | 余额不足时推荐充值，高等级用户权重更高（高等级用户价值大，优先挽留） |
| HEAVY_USER_UPGRADE | 聊天次数 > 100，等级 < 4 | 90 | 0.3 / 0.5 / 1.0 / 1.5 / 0.2 | 高活跃但等级未达丞相，推荐升级套餐。知府/丞相权重最高，皇帝已毕业权重降低 |
| CHURN_RISK_SAVE | 3 天未聊天 | 85 | 0.5 / 0.8 / 1.0 / 1.3 / 1.5 | 流失风险用户推荐唤醒活动，高等级用户流失成本高，权重更高 |
| HIGH_VALUE_RECOMMEND | 等级 >= 4（丞相）且 累计充值 > 500 | 120 | 0 / 0 / 0 / 1.0 / 2.0 | 高价值用户推荐专属高端套餐，如皇帝定制包 |

**关于 HIGH_VALUE_RECOMMEND 的说明**：
- 此规则与等级体系不重复：等级体系是"资格门槛"（如无限畅聊需要知府以上），而此规则是"推荐策略"（针对高价值用户推荐更贵的套餐）
- 评分用途：当用户触发推荐时，Recommend Service 计算所有匹配规则的得分，按得分排序返回 Top 3 推荐套餐
- 使用场景：Gateway 调用 Recommend.GetRecommendations(user_id)，结果用于前端弹窗展示"陛下，为您推荐"

**最终得分 = 基础分 x 等级权重 x 时间衰减因子**

时间衰减因子：同一规则 24 小时内重复触发，得分每次 x0.8，防止过度推荐。

### 9.2 规则配置化

规则存储在 YAML 文件，支持热更新（文件变更后自动重载）：

```yaml
rules:
  - id: "NEW_USER_WELCOME"
    name: "新用户欢迎"
    condition: "user.registered_at > now() - 3d"
    base_score: 100
    level_weights: [1.0, 0.8, 0.5, 0.3, 0.1]
    packages: ["experience_card"]
    cooldown_hours: 24

  - id: "HIGH_VALUE_RECOMMEND"
    name: "高价值用户推荐"
    condition: "user.level >= 4 AND user.total_recharge > 500"
    base_score: 120
    level_weights: [0, 0, 0, 1.0, 2.0]
    packages: ["emperor_custom_pack", "chancellor_annual_card"]
    cooldown_hours: 72
```

### 9.3 触发场景

推荐只在以下两个场景触发：

1. **用户登录时**：Gateway 在用户登录成功后，异步调用 Recommend.GetRecommendations(user_id)，结果缓存到 Redis，前端在首页展示"为您推荐"弹窗
2. **额度不足时**：
   - **真实余额不足**：用户发起对话前，Commercial 预扣返回余额不足错误，Gateway 同步调用 Recommend 获取充值推荐
   - **预扣额度不足**：极端情况，Gateway 同步调用 Recommend 获取充值推荐

**不触发的场景**：对话结束后、活动参与后、等级升级后等，均不触发推荐，避免过度打扰用户。

### 9.4 推荐 + LLM 话术完整链路（v3.1 新增）

```
触发场景（登录 / 额度不足）
    -> Recommend Service：规则匹配 -> 评分排序 -> Top 3 套餐（结构化数据，见 9.5）
    -> Gateway 组装推荐上下文：
       {
         "scene": "BALANCE_INSUFFICIENT",
         "user_level": "知府",
         "balance": 32,
         "candidates": [
           {"name": "月卡", "price": 30, "points": 300, "tag": "性价比之选"},
           {"name": "季卡", "price": 78, "points": 800, "tag": "老用户专享"},
           {"name": "无限畅聊礼包", "price": 20, "duration_hours": 24, "tag": "24小时不限量"}
         ]
       }
    -> 调用 Python Agent Core 的 RecommendPlugin：
       POST /api/v1/agent/recommend（SSE 流式）
    -> LLM Prompt 模板：
       "你是小喜子，宫廷太监人设。用户（{user_level}）当前余额 {balance}，
        不足以继续对话。请从以下候选套餐中挑选推荐（严禁编造价格，
        只能使用给定 JSON 中的名称和数字），用符合人设的口吻自然地：
        1) 婉转提示余额不足；2) 推荐 1-2 个套餐并说明理由；3) 语气恭敬带点小幽默。
        候选套餐：{candidates}"
    -> LLM 生成话术，SSE 流式返回
    -> 前端渲染：
       商品卡片（名称/价格/按钮）使用 Recommend Service 的结构化数据渲染
       LLM 话术作为推荐语展示
```

**防幻觉设计（面试必问"LLM 编造价格怎么办"）**：

| 防护 | 做法 |
|------|------|
| 输入封闭 | Prompt 明确"只能使用给定 JSON 中的名称和价格，禁止编造"，候选只传 3 个 |
| 展示解耦 | 前端价格、按钮、下单参数全部来自服务端结构化数据，**LLM 文本只做推荐语**，价格数字不经过 LLM |
| 兜底 | LLM 超时（3s）或失败 -> 服务端静态模板话术（"陛下，国库空虚，不妨看看这张月卡"） |
| 缓存 | 同一用户 5 分钟内重复触发，复用 Redis 缓存话术（key: recommend_llm:{user_id}），不重复调 LLM |

**成本控制**：规则引擎评分缓存（已有）+ LLM 话术缓存 + 仅两个触发场景，Token 消耗极小。报表服务的"聚合前置"思路同出一辙。

### 9.5 评分流程

```
触发场景（登录 / 额度不足）
    -> Gateway 调用 Recommend.GetRecommendations(user_id)
    -> Recommend Service:
        -> 1. 查询用户画像（Profile Service gRPC）
        -> 2. 查询用户等级（Member Service gRPC，含官职名）
        -> 3. 遍历所有规则，评估条件是否满足
        -> 4. 计算得分：base_score x level_weights[level-1] x decay_factor
        -> 5. 按得分降序排序，取 Top 3
        -> 6. 写入 Redis（key: recommend:{user_id}, TTL: 5min）
        -> 7. 返回结构化结果给 Gateway
    -> Gateway 走 9.4 的 LLM 话术链路（额度不足场景）或直接返回前端（登录场景）
```

### 9.6 缓存策略

- 推荐结果缓存：Redis，TTL 5 分钟，减少重复计算
- LLM 话术缓存：Redis，TTL 5 分钟
- 规则配置缓存：本地内存，YAML 变更时通过文件监听或 Redis Pub/Sub 刷新
- 用户画像缓存：Redis，TTL 10 分钟，Profile Service 负责维护

---

## 十、A/B 测试服务（Feature Flag）

### 10.1 核心设计

A/B 测试服务独立于主链路，通过 Feature Flag 控制是否启用。

### 10.2 数据模型

```sql
-- 实验配置
CREATE TABLE experiments (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    traffic_percent INT NOT NULL DEFAULT 100,
    status VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    start_time TIMESTAMP,
    end_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 实验分组
CREATE TABLE experiment_groups (
    id SERIAL PRIMARY KEY,
    experiment_id INT REFERENCES experiments(id),
    name VARCHAR(50) NOT NULL,
    config JSONB NOT NULL,
    weight INT NOT NULL DEFAULT 50
);

-- 用户分组记录
CREATE TABLE experiment_assignments (
    id SERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    experiment_id INT NOT NULL,
    group_id INT NOT NULL,
    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, experiment_id)
);
```

### 10.3 分流算法

采用一致性哈希，保证同一用户在同一实验中永远在同一组：

```go
func assignGroup(userID string, experimentID int, groups []Group) Group {
    hash := fnv32(experimentID + ":" + userID)
    totalWeight := sum(groups.weight)
    point := hash % totalWeight

    cumulative := 0
    for _, g := range groups {
        cumulative += g.weight
        if point < cumulative {
            return g
        }
    }
    return groups[0] // fallback
}
```

**关键设计**：
- 实验 ID 参与哈希，避免用户在所有实验中永远在同一组（如 user_id 取模的缺陷）
- 分组结果写入 experiment_assignments 表，保证一致性（即使哈希算法变更，已分配用户不变）
- 新用户首次进入实验时，查询数据库 -> 无记录 -> 计算分组 -> 写入数据库 -> 返回分组

### 10.4 Feature Flag 开关

```sql
CREATE TABLE feature_flags (
    flag_key VARCHAR(100) PRIMARY KEY,
    flag_value VARCHAR(255) NOT NULL DEFAULT 'false',  -- v3.1: 改为字符串，支持多值（如 commercial_flow_mode = redis/db_direct）
    description TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 初始化
INSERT INTO feature_flags VALUES 
('ab_test_enabled', 'false', 'A/B测试全局开关'),
('experiment_123_enabled', 'false', '实验123开关'),
('commercial_flow_mode', 'redis', '商业化链路模式：redis=正常 / db_direct=降级');
```

**开关策略**：
- 全局开关 ab_test_enabled 关闭时，Gateway 直接跳过 A/B 逻辑
- 实验级开关可独立控制，支持灰度发布
- 开关变更通过 Admin API 修改数据库，Gateway 通过 Redis Pub/Sub 实时感知

---

## 十一、用户画像服务

### 11.1 画像标签体系

三层标签：

| 维度 | 标签示例 | 数据来源 |
|------|---------|---------|
| 商业价值 | 累计充值金额、ARPU、LTV预测 | Commercial Service（Kafka 事件消费） |
| 行为特征 | 日均聊天次数、平均对话轮数、活跃时段、偏好方言 | Python Agent Core（Kafka 事件） |
| 流失风险 | 最近聊天时间、连续未登录天数、余额不足次数 | Member + Commercial Service |

### 11.2 画像更新机制

异步事件驱动：

```
chat.completed -> 更新行为特征（聊天次数、轮数）
balance.changed -> 更新商业价值（充值金额、消费金额）
member.level_up -> 更新等级标签
user.login -> 更新活跃时间、连续登录天数
```

### 11.3 画像数据在预扣额度中的应用

用户画像不仅用于推荐，还直接影响商业化服务的预扣额度计算：

```
动态调整值计算逻辑（Profile Service 提供）：
- 对话复杂度标签 = "高" -> 动态调整值 +5
- 消费稳定性标签 = "稳定" -> 动态调整值 -2
- 用户类型标签 = "新用户" -> 动态调整值 +10
- 历史预扣偏差均值 > 5 -> 动态调整值 +3（保守策略）

Commercial Service 在预扣前调用 Profile.GetDynamicAdjustment(user_id)
返回动态调整值，与前3次均值相加得到最终预扣额度（见 7.4）
```

### 11.4 LLM 报表服务

**触发方式**：定时任务（每日凌晨 2:00，与对账任务错峰）

**流程**：

```
定时任务触发
    -> Profile Service 从 PostgreSQL 聚合数据（近 7 天）
        -> 用户增长：新增注册、活跃用户、流失用户
        -> 商业数据：总充值、总消费、ARPU、套餐购买分布
        -> 等级分布：各官职（里正~皇帝）用户占比、晋升趋势
        -> 活动效果：各活动参与率、转化率、ROI
        -> 账务健康：三层对账通过率、补发/重放条数
    -> 数据预处理后，调用 Python Agent Core 的 ReportPlugin
        -> ReportPlugin 调用 LLM API，传入聚合数据和 Prompt 模板
        -> LLM 生成 Markdown 格式报表（含标题、数据表格、趋势分析、建议）
    -> ReportPlugin 通过 SMTP 发送邮件到指定邮箱
    -> 发送成功后，更新 user_reports 表记录
```

**成本控制**：
- SQL 聚合前置：先 GROUP BY 汇总，再传给 LLM，减少 Token 消耗
- Redis 缓存：相同数据 24 小时内不重复生成（report_cache:{date}）
- 异步执行：报表生成不阻塞主业务，失败时重试 3 次，仍失败则记录死信队列

---

## 十二、风控服务

### 12.1 风控规则

| 规则 | 触发条件 | 动作 | 证据留存 |
|------|---------|------|---------|
| 批量注册 | 同 IP 1 小时内注册 > 5 个账号 | 拦截注册，要求验证码 | IP、时间、注册账号列表 |
| 礼包滥用 | 同设备 1 小时内兑换 > 3 个礼包 | 冻结兑换，人工审核 | 设备指纹、礼包码、时间 |
| 积分异常 | 1 小时内预扣结算差额 > 阈值 | 触发告警，暂停账户 | 流水号、差额、时间窗口 |
| 对话刷单 | 1 分钟内对话 > 20 轮 | 限流，降低响应优先级 | user_id、对话频次 |
| 孤儿预扣滥用 | 同用户 > 3 次/天 | 告警，限制预扣 | user_id、prehold 记录 |
| 对账异常熔断 | 层三连续 N 天 diff | 切换 db_direct 模式 | 对账差异明细 |

### 12.2 设备指纹

简单实现（初期）：
- 采集用户代理、屏幕分辨率、时区、语言
- 拼接后 SHA256 哈希，作为设备唯一标识
- 存储在 Redis，TTL 7 天

### 12.3 拦截策略

**Fail-Safe 设计**：风控服务故障时，Gateway 不阻断请求，仅记录日志，避免风控故障导致系统不可用。

```go
func RiskCheckMiddleware() gin.HandlerFunc {
    return func(c *gin.Context) {
        result, err := riskClient.Check(c.Request.Context(), buildRiskRequest(c))
        if err != nil {
            // 风控服务故障，记录日志，放行请求
            zap.L().Error("risk service unavailable", zap.Error(err))
            c.Next()
            return
        }
        if result.Block {
            c.AbortWithStatusJSON(403, gin.H{"code": 10006, "message": "risk blocked"})
            return
        }
        c.Next()
    }
}
```

---

## 十三、事件总线

### 13.1 Kafka Topic 设计

| Topic | 生产者 | 消费者 | 用途 |
|-------|--------|--------|------|
| **balance_events** | Commercial Service | Commercial-worker（唯一落库者） | **资金账本**：充值/预扣/结算/取消全量事件，按 user_id 分区 |
| chat.completed | Python Agent Core | Member, Profile, Commercial | 更新好友度、画像 |
| user.login | User Service | Profile, Risk | 更新活跃时间、风控计数 |
| member.level_up | Member Service | Commercial, Recommend | 更新权益、推荐权重 |
| promotion.claimed | Promotion Service | Profile, Recommend | 更新画像 |
| risk.triggered | Risk Service | Alert System | 发送告警 |
| report.generated | Python Agent Core | Profile | 更新报表记录 |

**balance_events 消息体**（与 Redis Stream 事件同构）：

```json
{
  "event_id": "uuid-v4",
  "user_id": 1001,
  "seq": 42,
  "type": "SETTLE",
  "amount": 8,
  "currency": "POINTS",
  "idempotency_key": "uuid",
  "prehold_id": "ph_xxx",
  "flow_version": 1,
  "ts": "2026-09-14T00:00:00Z"
}
```

### 13.2 消费者设计

- **分区策略**：balance_events 按 user_id 哈希分区，同用户事件有序；分区数初期 6，可扩
- **批量消费**：Commercial-worker 每批 500 条或 200ms 强制 flush（见 7.7）
- **位点管理**：手动提交，且仅在 DB 事务成功后提交（at-least-once）
- **失败重试**：消费失败时重试 3 次，仍失败则写入死信队列（DLQ），不提交位点
- **幂等性**：按 event_id 去重（transactions 表 event_id 唯一索引）

---

## 十四、Go 层与 Python Agent Core 的交互

### 14.1 接口定义

Go Gateway 与 Python Agent Core 通过 HTTP + SSE 通信：

| 接口 | 方法 | 用途 |
|------|------|------|
| /api/v1/agent/chat | POST SSE | 对话主链路，流式返回 |
| /api/v1/agent/tools | POST | 调用 MCP Tools（笑话、方言、情绪） |
| /api/v1/agent/recommend | POST SSE | 推荐话术生成（v3.1 新增，走 RecommendPlugin） |
| /api/v1/agent/report | POST | 触发 LLM 报表生成 |
| /health | GET | 健康检查 |

### 14.2 对话流程

```
用户发送消息
    -> Gateway 鉴权、限流、Feature Flag 检查
    -> Gateway 调用 Risk Service（Fail-Safe）
    -> Gateway 查询用户等级（Member Service）
    -> Gateway 调用 Commercial.Prehold（计算动态预扣额度，Redis Lua）
    -> Gateway 调用 Python /api/v1/agent/chat（SSE 流式）
        -> Python Agent Core 加载启用的 Plugin
        -> 执行对话状态机
        -> 流式返回 Token
    -> Gateway 透传 SSE 到前端
    -> 对话结束
    -> Python 回调携 actual_amount
    -> Gateway 调用 Commercial.Settle（按 flow_version 路由，见 7.9）
    -> Commercial 发 SETTLE 事件到 Kafka（worker 落库）
    -> Gateway 发送 chat.completed 事件到 Kafka（画像/等级消费）
```

**资金动作时机澄清（v3.1 明确）**：余额真实扣减（结算）由"对话完成"这一系统事件驱动，秒级完成，绝非延迟到晚间对账。异步的只有"落库"（Redis 现场 -> Kafka 账本 -> worker 批量投影）。对账只做事后校验与补偿，绝不执行扣款。用户触发的是业务行为，资金处理由事件驱动——这是标准清结算模式。

### 14.3 Plugin 开关对 Go 层的影响

Go 层不感知 Plugin 细节，只通过 HTTP 调用 Python Agent Core。Plugin（情绪雷达、方言、RAG、报表、推荐话术）的启用/禁用完全由 Python 层的 plugin_manager.py 控制，Go 层无需修改代码。

---

## 十五、数据存储设计

### 15.1 存储选型

| 数据类型 | 存储 | 理由 |
|---------|------|------|
| 业务数据（用户、账户、交易） | PostgreSQL | ACID 事务，支持复杂查询，金融级可靠性 |
| 扣款现场（余额、冻结、事件缓存） | Redis | 高性能，Lua 原子操作，单机 5 万+ QPS |
| 资金账本 | Kafka（balance_events） | append-only、持久化、可重放、分区有序 |
| 缓存（会话、等级、推荐结果） | Redis | 高性能 KV，支持过期策略、分布式锁 |
| 事件流（业务事件） | Kafka | 高吞吐、持久化、可回溯 |
| 向量数据（RAG） | ChromaDB | 轻量级向量存储，适合初期 |
| 日志、追踪 | Elasticsearch（可选） | 初期可用文件 + Grafana Loki |

### 15.2 分库分表策略（初期）

**初期单库单表即可**，但预留分片键：

| 表 | 分片键 | 分片策略（未来） |
|---|--------|---------------|
| users | id | 按 user_id % 1024 分表 |
| user_intimacy | user_id | 同 users |
| accounts | user_id | 同 users |
| transactions | user_id | 同 users，按时间归档 |
| chat_sessions | user_id | 同 users，按时间分区 |

**理由**：用户量 < 100 万时单库足够，但代码中所有查询都通过 user_id 路由，未来分库分表时改动最小。

### 15.3 索引设计

**核心索引**（必须在创建表时建立）：

```sql
-- 用户表
CREATE UNIQUE INDEX idx_users_email ON users(email);
CREATE UNIQUE INDEX idx_users_phone ON users(phone);

-- 交易流水表（高并发查询）
CREATE INDEX idx_transactions_user_id_type ON transactions(user_id, type);
CREATE INDEX idx_transactions_created_at ON transactions(created_at);
CREATE UNIQUE INDEX idx_transactions_idempotency ON transactions(user_id, idempotency_key, type);
CREATE UNIQUE INDEX idx_transactions_event_id ON transactions(event_id);      -- v3.1: 对账 diff 锚点
CREATE INDEX idx_transactions_prehold ON transactions(prehold_id);            -- v3.1: 预扣结算关联

-- 好友度表
CREATE INDEX idx_user_intimacy_level ON user_intimacy(current_level);
CREATE INDEX idx_user_intimacy_score ON user_intimacy(current_score DESC);

-- 活动参与记录
CREATE INDEX idx_user_promotions_user_id ON user_promotions(user_id);
CREATE UNIQUE INDEX idx_user_promotions_unique ON user_promotions(user_id, promotion_id);

-- v3.1: worker 水位线表
CREATE TABLE water_mark (
    partition_no INT PRIMARY KEY,
    max_offset BIGINT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- v3.1: 降级模式 DB 冻结记录
CREATE TABLE frozen_records (
    prehold_id VARCHAR(64) PRIMARY KEY,
    user_id BIGINT NOT NULL,
    amount BIGINT NOT NULL,
    flow_version INT NOT NULL DEFAULT 2,
    status VARCHAR(20) DEFAULT 'FROZEN',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_frozen_user (user_id, status)
);
```

### 15.4 交易流水表结构（transactions）

**表用途**：记录所有积分相关的资金流动，由 Commercial-worker 从 Kafka 消费落库。是账务投影、幂等校验、三层对账的全部依据。

**表定义**（一期 DDL 以 **Go 仓 `XNCAgent-go`** 的 `deploy/init.sql` 为准；二期表随服务拆出再补，不预建 AB/画像/独立商城表）：

```sql
CREATE TABLE transactions (
    -- 主键
    id BIGSERIAL PRIMARY KEY,

    -- 账本锚点（v3.1 新增）
    event_id VARCHAR(64) NOT NULL UNIQUE,             -- 全局唯一事件 ID，贯穿 Redis Stream/Kafka/DB 三方
    seq_no BIGINT NOT NULL,                           -- 用户内单调递增序号（Redis INCR 生成），重放排序依据

    -- 业务关联
    user_id BIGINT NOT NULL,                          -- 用户ID，关联 users 表
    prehold_id VARCHAR(64),                           -- 预扣记录ID，仅结算/取消时关联预扣事件
    promotion_id BIGINT,                              -- 活动ID，仅活动奖励类流水关联

    -- 幂等性控制
    idempotency_key VARCHAR(64) NOT NULL,             -- 幂等键，客户端生成UUID，用于防止重复处理

    -- 链路版本（v3.1 新增）
    flow_version INT NOT NULL DEFAULT 1,              -- 1=Redis 链路 / 2=DB 直连降级链路

    -- 交易类型与状态
    type VARCHAR(20) NOT NULL,                        -- RECHARGE(充值)/PREHOLD(预扣)/SETTLE(结算)
                                                      -- /CANCEL(取消预扣)/REFUND(退款)/PROMOTION(活动奖励)
                                                      -- /RECONCILE_FIX(对账补偿)
    status VARCHAR(20) NOT NULL DEFAULT 'COMPLETED',  -- worker 落库即完成态；PENDING 仅用于对账中间态

    -- 金额信息（单位：分，避免浮点精度问题）
    amount BIGINT NOT NULL,                           -- 交易金额（分），正值表示增加，负值表示减少
    balance_before BIGINT NOT NULL,                   -- 交易前总余额（分）
    balance_after BIGINT NOT NULL,                    -- 交易后总余额（分）
    frozen_before BIGINT NOT NULL DEFAULT 0,          -- 交易前冻结余额（分）
    frozen_after BIGINT NOT NULL DEFAULT 0,           -- 交易后冻结余额（分）

    -- 业务描述
    description VARCHAR(255),                         -- 交易描述

    -- 审计字段
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- 约束
    CONSTRAINT fk_transactions_user FOREIGN KEY (user_id) REFERENCES users(id),
    CONSTRAINT uq_transactions_idempotency UNIQUE (user_id, idempotency_key, type)
);

-- 对账表（扩展 v3.1）
CREATE TABLE account_reconciliation (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    reconcile_date DATE NOT NULL,
    opening_balance BIGINT NOT NULL,
    closing_balance BIGINT NOT NULL,
    total_recharge BIGINT DEFAULT 0,
    total_spend BIGINT DEFAULT 0,
    total_refund BIGINT DEFAULT 0,
    expected_balance BIGINT NOT NULL,
    diff BIGINT NOT NULL,
    layer1_status VARCHAR(20),                        -- DB 内部：MATCHED/MISMATCHED
    layer2_status VARCHAR(20),                        -- Kafka↔DB
    layer3_status VARCHAR(20),                        -- Redis↔Kafka
    exception_detail JSONB,                           -- 差异明细（缺失/多余 event_id 清单）
    status VARCHAR(20) DEFAULT 'MATCHED',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, reconcile_date)
);

CREATE TABLE reconciliation_exceptions (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    layer INT NOT NULL,                               -- 1/2/3
    exception_type VARCHAR(50) NOT NULL,              -- MISSING_IN_DB / MISSING_IN_KAFKA /
                                                      -- AMOUNT_MISMATCH / AUTO_FIX_FAILED
    detail JSONB NOT NULL,
    status VARCHAR(20) DEFAULT 'PENDING',             -- PENDING/RESOLVED
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

**字段设计理由**：
- `event_id` + 唯一索引：三方对账的 diff 锚点，Redis Stream、Kafka、DB 同一事件共享同一 ID
- `seq_no`：用户内单调递增，Redis 丢数据后按 seq 顺序重放恢复
- `flow_version`：区分正常链路与降级链路，结算路由与审计追溯的依据
- `amount` 用 BIGINT（分）：避免浮点数精度问题，金融系统标准做法
- `balance_before/after`、`frozen_before/after`：记录交易前后快照，对账时快速核对，无需回溯计算
- DB 永远只有 worker 一条写入路径；对账修复一律补发 Kafka 事件，不直接 INSERT

---

## 十六、一致性保证方案

### 16.1 强一致性场景

| 场景 | 方案 | 理由 | 关联模块 |
|------|------|------|---------|
| 预扣（Redis 链路） | Redis Lua 原子操作 + 单用户单 key 串行 | 现场防超扣 | [7.5.2](#752-redis-链路正常态flow_version--1) |
| 结算/充值（Redis 链路） | Redis Lua + Kafka 事件 + worker 幂等落库 | 账本权威，投影可重建 | [7.5.3](#753-对话与结算) |
| 预扣/结算（DB 降级链路） | DB 事务 + 唯一索引 | 降级态的强一致保障 | [7.9](#79-降级方案与-flow_version-机制) |
| 活动库存 | Redis DECR + 数据库确认 + 每日校准 | 高并发下防超卖 | [8.6](#86-库存扣减与一致性保证) |
| 好友度更新 | 乐观锁 + 降级队列 | 并发更新不丢失 | [6.4](#64-并发控制) |

### 16.2 最终一致性场景

| 场景 | 方案 | 理由 | 关联模块 |
|------|------|------|---------|
| Redis 现场 -> DB 投影 | Kafka 异步，批量净额落库，200ms 内 | 抗压，允许秒级延迟 | [7.7](#77-commercial-worker批量净额落库) |
| 用户画像更新 | Kafka 异步消费 | 允许秒级延迟 | [11.2](#112-画像更新机制) |
| 推荐结果更新 | Redis 缓存 + 异步刷新 | 允许 5 分钟延迟 | [9.6](#96-缓存策略) |
| 等级权益同步 | Kafka 事件广播 | 允许秒级延迟 | [6.3](#63-升级流程) |
| 报表生成 | 定时任务异步执行 | 允许小时级延迟 | [11.4](#114-llm-报表服务) |

### 16.3 对账兜底体系（v3.1 核心）

三层对账的完整逻辑见 [7.8](#78-对账机制三层口径)。此处总结一致性保障的分层防线：

```
防线 1：操作层    Redis Lua 原子 + 幂等键 + 唯一索引（不超扣、不重扣）
防线 2：账本层    Kafka 持久化 + 分区有序 + 手动位点（不丢账）
防线 3：投影层    worker 幂等消费 + 净额合并事务（不重账）
防线 4：巡检层    5 分钟轻量抽检 + 孤儿冻结清理（及时发现）
防线 5：对账层    每日三层全量对账 + event_id diff + 自动修复（最终兜底）
防线 6：熔断层    连续异常自动切 db_direct，人工确认后恢复（止血）
```

### 16.4 活动库存的一致性详细设计

```
用户请求参与活动
    -> 1. Redis 预扣：DECR promotion_stock:{promotion_id}
        -> 返回值 >= 0：继续参与
        -> 返回值 < 0：拒绝参与，进入校准流程
    -> 2. 数据库确认：写入 user_promotions 表（事务）
        -> 成功：参与完成
        -> 失败：Redis 补偿（INCR promotion_stock），回滚预扣
    -> 3. 异步校准（定时任务）：
        -> 每日凌晨统计：SUM(user_promotions.claimed) + Redis stock = 初始库存
        -> 若不一致：以数据库为准，修正 Redis 值，记录差异日志
```

### 16.5 分布式事务（Saga 模式）

对于跨服务的长时间事务（如购买无限畅聊 -> 扣费 -> 发放时段权益 -> 检查等级），采用 Saga 模式：

```
1. Gateway 调用 Commercial.Prehold（冻结积分）
2. Gateway 调用 Promotion.Claim（参与活动/发放时段权益）
3. Gateway 调用 Member.UpdateLevel（检查升级）
4. 若全部成功：Commercial.Settle（确认扣费）
5. 若任一步失败：执行补偿操作
   - Commercial.CancelPrehold（释放冻结）
   - Promotion.Revoke（收回活动奖励）
```

**补偿策略**：
- 每个步骤记录本地事务日志（saga_log 表）
- 定时任务扫描未完成的 Saga，自动执行补偿或重试
- 补偿操作必须幂等

---

## 十七、扩展性与性能设计

### 17.1 水平扩展

| 服务 | 扩展方式 | 瓶颈 |
|------|---------|------|
| Gateway | K8s HPA（CPU/内存/QPS） | 无状态，任意扩容 |
| Commercial 同步链路 | HPA | Redis 单节点 QPS（5 万+，远未触及） |
| Commercial-worker | 增加 Kafka 分区 + 消费者实例 | DB 批量写入吞吐，可平滑扩容 |
| User/Member/Promotion | K8s HPA | 数据库连接数，需连接池管理 |
| Recommend | K8s HPA | Redis 带宽，可升级集群模式 |
| Python Agent Core | 固定 Pod 数 + 负载均衡 | GPU/内存，LLM 推理是瓶颈 |
| Kafka | 增加分区 + 消费者组扩容 | 磁盘 I/O |
| Redis | 见 7.10 拓扑演进 | 单机内存/QPS 上限 |

### 17.2 读写分离

- PostgreSQL 主从复制，读请求（查询余额、查询等级）走从库
- 写请求（worker 批量落库）走主库
- GORM 配置读写分离数据源

### 17.3 缓存策略

| 缓存对象 | 存储 | TTL | 更新策略 |
|---------|------|-----|---------|
| 余额现场 | Redis hash | 不过期 | 变更时 Lua 原子更新；丢失从 Kafka 重建 |
| 预扣记录 | Redis string | 30min | 结算/取消即删，超时孤儿清理 |
| 事件队列 | Redis Stream | 7 天（MAXLEN 1000 截断） | 仅作补发源，非权威 |
| 用户等级 | Redis | 10min | 升级时主动失效 |
| 推荐结果 | Redis | 5min | 行为变化时主动失效 |
| 活动配置 | 本地内存 | 永久 | 文件变更/Admin API 刷新 |
| Feature Flag | 本地内存 | 永久 | Redis Pub/Sub 刷新 |

### 17.4 异步化

- 对话扣费：预扣同步（Redis），落库异步（worker 200ms 内）
- 画像更新：Kafka 异步消费
- 报表生成：定时任务异步执行
- 对账：定时任务异步执行

---

## 十八、可观测性设计

### 18.1 日志

- **格式**：JSON 结构化日志，包含 request_id, user_id, event_id, service, level, message, timestamp
- **收集**：Zap 写入标准输出，Filebeat/Fluentd 采集到 Elasticsearch 或 Loki
- **采样**：错误日志 100% 采集，INFO 日志 10% 采样（高并发场景）

### 18.2 指标（Metrics）

| 指标 | 类型 | 用途 |
|------|------|------|
| http_requests_total | Counter | QPS |
| http_request_duration_seconds | Histogram | P50/P95/P99 延迟 |
| http_requests_failed_total | Counter | 错误率 |
| commercial_prehold_total / commercial_prehold_failed_total | Counter | 预扣成功率（Redis 链路健康度） |
| commercial_recharge_total | Counter | 充值成功率 |
| commercial_kafka_lag | Gauge | worker 消费堆积（资金到账延迟） |
| commercial_reconcile_diff_total | Counter | 对账差异数（按层打 label） |
| commercial_flow_mode | Gauge | 当前链路模式（1=redis, 2=db_direct） |
| member_level_up_total | Counter | 升级人数（按官职打 label） |
| promotion_claim_total | Counter | 活动参与率 |
| kafka_consumer_lag | Gauge | 消息堆积监控 |

### 18.3 链路追踪（OpenTelemetry）

- Gateway 生成 Trace ID，透传到所有下游服务
- gRPC 调用携带 Trace Context
- Redis Lua、Kafka 生产/消费埋点（携带 event_id）
- Python Agent Core 接入 OTel，追踪 LLM API 调用延迟
- Jaeger UI 展示完整调用链：预扣 -> 对话 -> 结算 -> Kafka -> worker 落库全链路可查

### 18.4 告警规则

| 告警 | 条件 | 级别 |
|------|------|------|
| 服务宕机 | Pod 重启次数 > 3/5min | P0 |
| 错误率飙升 | 5min 错误率 > 1% | P0 |
| 延迟飙升 | P99 延迟 > 500ms | P1 |
| 资金到账延迟 | commercial_kafka_lag > 5000 | P0 |
| 对账差异 | 任一层 diff > 0 | P0（邮件 + 告警） |
| Redis 宕机 | 探测失败 | P0（自动触发降级预案） |
| Kafka 不可用 | 生产失败率 > 1% | P0 |
| 风控拦截 | 1h 内拦截数 > 100 | P1 |

---

## 十九、部署与运维

### 19.1 容器化

- Go 服务 Dockerfile 在 **`XNCAgent-go`**，多阶段构建（基于 golang:1.23-alpine），目标镜像大小 < 50MB
- Python Agent Core 镜像在 **`XNCAgent`** 单独构建（基于 python:3.11-slim）
- 本地开发：compose 在 Go 仓 `deploy/docker-compose.yaml`（Postgres 15、Redis 7、Kafka、Jaeger 已在文件中）；业务进程一期追加进同一文件，不要在 Python 仓再复制一份 infra

### 19.2 K8s 部署

```yaml
# 每个服务统一配置
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {service-name}
spec:
  replicas: 2
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    spec:
      containers:
      - name: {service-name}
        image: xncagent/{service-name}:latest
        resources:
          requests:
            memory: "128Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
        livenessProbe:
          httpGet:
            path: /health
            port: 8199
          initialDelaySeconds: 10
          periodSeconds: 30
        readinessProbe:
          httpGet:
            path: /ready
            port: 8199
          initialDelaySeconds: 5
          periodSeconds: 10
```

### 19.3 CI/CD 流水线

```
Developer Push -> GitHub Actions
    -> 1. Lint (golangci-lint)
    -> 2. Unit Test (go test -race -cover)
    -> 3. Integration Test (docker-compose 启动依赖，跑集成测试)
    -> 4. Build Docker Image
    -> 5. Push to Docker Hub (main 分支)
    -> 6. Deploy to Staging (main 分支)
    -> 7. Smoke Test (main 分支)
```

### 19.4 配置管理

- 环境配置通过 K8s ConfigMap + Secret 注入
- 数据库密码、JWT Secret、SMTP 密码等存储在 K8s Secret
- 应用配置（Feature Flag、活动配置）通过 Admin API 热更新，不重启服务
- Redis 配置（AOF everysec + RDB 每日）通过 redis.conf ConfigMap 注入

### 19.5 Redis 部署形态

| 阶段 | 形态 | 配置 |
|------|------|------|
| 初期（当前） | 单节点 Pod | appendonly yes, appendfsync everysec, save 900 1；emptyDir + 定期 RDB 导出到 PV |
| 演进期 | 主从 + 哨兵 | 哨兵 3 节点，自动故障转移 |
| 成熟期 | Cluster | 3 主 3 从，按 user_id 哈希槽分片（需调整 key 设计） |

---

## 二十、风险清单与应对

| 风险 | 影响 | 概率 | 应对策略 | 关联模块 |
|------|------|------|---------|---------|
| Kafka 发送失败（Lua 后崩溃间隙） | 中（账本漏记） | 中 | Stream 缓存 + 层三 diff 补发 | [7.8 层三](#78-对账机制三层口径) |
| Redis 崩溃重启丢 ≤1s 数据 | 中 | 中 | AOF everysec + 层三 diff 重放修复 + db_direct 降级 | [7.9](#79-降级方案与-flow_version-机制) |
| worker 消费延迟 | 中（余额显示滞后） | 中 | lag 监控告警 + 扩分区扩消费者 | [7.7](#77-commercial-worker批量净额落库) |
| Kafka 集群不可用 | 高（账本停摆） | 低 | P0 监控；短期切 db_direct + worker 暂停；恢复后补发 | [7.9](#79-降级方案与-flow_version-机制) |
| 积分超扣/重复扣 | 高（资损） | 低 | Lua 原子 + 幂等键 + 三层对账 + 熔断 | [7.6/7.8](#76-幂等性保证) |
| 活动库存超卖 | 高（资损） | 中 | Redis 预扣 + 数据库确认 + 定时校准 | [8.6](#86-库存扣减与一致性保证) |
| Kafka 消息丢失 | 中 | 低 | Producer acks=all + 手动提交位点 + DLQ | [13.2](#132-消费者设计) |
| Redis 缓存击穿 | 中 | 中 | 热点数据永不过期 + 互斥锁重建 | [17.3](#173-缓存策略) |
| LLM API 超时/限流 | 中 | 高 | 超时 3s + 静态模板兜底 + 话术缓存 | [9.4](#94-推荐--llm-话术完整链路v31-新增) |
| Python Agent Core 崩溃 | 高 | 低 | K8s 健康检查自动重启 + 会话持久化恢复 | [14.2](#142-对话流程) |
| 风控服务故障阻断主链路 | 高 | 低 | Fail-Safe 放行 + 日志记录 | [12.3](#123-拦截策略) |
| 会员等级并发更新丢失 | 中 | 中 | 乐观锁 + 降级队列 | [6.4](#64-并发控制) |
| 活动互斥逻辑漏洞 | 高 | 低 | 互斥组单元测试覆盖所有组合 | [8.4](#84-优先级与互斥策略) |
| 无限畅聊时段相交未提醒 | 低 | 中 | 相交检测 + 用户确认弹窗 | [8.5](#85-无限畅聊的特殊处理无互斥取并集) |
| 活动过期未清理 | 中 | 低 | 实时检查 + 定时任务批量清理 | [8.3](#83-活动过期机制) |
| 切换降级时存量对话结算异常 | 中 | 低 | flow_version 路由 + 孤儿冻结 TTL 兜底 | [7.9](#79-降级方案与-flow_version-机制) |

---

## 附录：关键设计决策记录（ADR）

### ADR-001：为什么采用 Go + Python 混合架构？

- **决策**：Go 负责高并发微服务和业务编排，Python 负责 Agent 核心和 LLM 推理
- **理由**：Go 的 goroutine 模型适合高并发网关和微服务；Python 的 LLM 生态（LangChain、ChromaDB、MCP SDK）更成熟。通过 gRPC/HTTP 解耦，两边可独立扩缩容。
- **替代方案**：纯 Python（FastAPI）-> 性能不足；纯 Go -> LLM 生态薄弱
- **后果**：需要维护两套技术栈，团队需具备双语能力

### ADR-002：为什么自研规则引擎而非使用 Drools？

- **决策**：自研 YAML + 表达式规则引擎
- **理由**：初期规则数量少（5 条），Drools 引入复杂依赖和 JVM 开销。自研引擎足够轻量，且能灵活加入用户等级权重因子。未来规则复杂后可平滑迁移到 AviatorScript。
- **替代方案**：Drools、QLExpress、AviatorScript
- **后果**：规则表达能力有限，复杂规则（如嵌套条件）支持不足

### ADR-003：为什么采用预扣结算而非实时扣费？

- **决策**：对话前预扣，对话后结算
- **理由**：LLM 对话耗时不确定（1-30 秒），实时扣费可能导致对话中断时扣费状态不一致。预扣结算将扣费拆分为两阶段，保证对话完成前积分已冻结，对话完成后精确结算。
- **替代方案**：实时扣费 -> 对话中断时可能已扣费但未完成服务
- **后果**：实现复杂度增加，需要处理预扣超时释放（如对话异常中断）

### ADR-004：为什么 Feature Flag 放在 Gateway 层而非服务层？

- **决策**：Gateway 统一控制 Feature Flag，服务层无感知
- **理由**：Feature Flag 是流量入口级别的控制，放在 Gateway 可以避免请求进入无意义的服务逻辑。同时集中管理便于运维操作（一键开关）。
- **替代方案**：各服务自行判断 -> 逻辑分散，难以统一管理
- **后果**：Gateway 需要维护 Feature Flag 缓存，增加轻微内存开销

### ADR-005：为什么活动库存用 Redis 预扣而非数据库悲观锁？

- **决策**：Redis 原子递减预扣，数据库异步确认
- **理由**：活动参与是高并发场景（如大促），数据库行锁会导致大量请求阻塞。Redis 预扣性能高，允许短暂不一致（最终一致），但绝不超卖。
- **替代方案**：数据库悲观锁 -> 性能差；乐观锁 -> 冲突率高
- **后果**：需要定时校准 Redis 与数据库库存，增加运维复杂度

### ADR-006：为什么预扣额度采用动态计算而非固定值？

- **决策**：预扣额度 = 前3次均值 + 动态调整值
- **理由**：固定预扣额度会导致短对话频繁预扣/释放，增加系统开销且用户体验差。动态计算根据用户历史行为预估，减少资金冻结时间。动态调整值与用户画像挂钩，为精细化运营预留扩展点。
- **替代方案**：固定预扣额度（如每次10积分）-> 简单但体验差
- **后果**：计算逻辑复杂，需要维护用户最近3次对话记录，且新用户历史不足时需要兜底策略

### ADR-007：结算为什么由事件驱动而非用户直接触发？对账为什么不参与扣款？

- **决策**：余额真实扣减（结算）由 chat.completed 事件驱动，秒级完成；对账只做事后校验与补偿，绝不执行扣款
- **理由**：用户触发的是业务行为（对话），资金处理由系统事件驱动——这是标准清结算模式（类比银行：刷卡是用户动作，扣款是银行系统按事件处理）。若对账任务直接扣款，则无幂等键、无事务保护、不可追溯，是资损高危设计。
- **替代方案**：对账时批量补扣 -> 无幂等保护，且用户余额视图与真实权益长时间不符
- **后果**：需要事件溯源机制保证"事件一定到达"（见 ADR-008）

### ADR-008：为什么 Kafka 是权威账本，Redis 和 DB 都是投影？（v3.1 核心）

- **决策**：资金事件的唯一权威存储是 Kafka balance_events；Redis 是同步扣款现场（给用户的实时余额视图），DB 是 Commercial-worker 消费 Kafka 构建的异步投影
- **理由**：
  1. 抗压：Redis 单机 5 万+ QPS 承担同步扣款，DB 只承受 worker 批量净额写入（5~10 TPS），高峰期数据库不再是瓶颈
  2. 可重建：Redis 崩溃可从 Kafka 重放重建，DB 投影偏差可按 Kafka diff 修复——任何投影都不是真相源，没有单点数据丢失风险
  3. 不解析 AOF/主从日志：tail AOF 格式私有且脆弱，Kafka 本来就是为"持久化日志 + 消费"设计的，业务事件直接写 Kafka 是标准玩法
- **替代方案**：Redis 集群扛全部 + DB 强同步 -> 运维复杂度高且仍无账本；直接 DB 扣款 -> 高峰期 DB 被打爆
- **后果**：引入三层对账体系与 worker 组件；Kafka 可用性升级为 P0 监控对象

### ADR-009：降级为什么用 flow_version 标签而非整体切换？（v3.1 核心）

- **决策**：商业化链路通过 Feature Flag 整体切换 redis / db_direct 模式，但每笔预扣记录打 flow_version 标签，结算按标签路由
- **理由**：切换是瞬间的，对话是过程的。切换前发起的对话（v1，冻结在 Redis）必须在结算时回老流程处理，否则冻结无法释放。按版本路由后，新旧链路可长时间混跑，存量业务自然走完，无需等待"切换前数据排空"。
- **替代方案**：切换时强制等待 30 分钟（prehold TTL）再切 -> 切回恢复时同样要排空，操作窗口长；或直接 Flush Redis -> 在途冻结全部丢失，资损
- **后果**：预扣记录与事件均需携带 flow_version 字段；worker 落库逻辑需兼容两种链路的事件

### ADR-010：营销活动为什么不做独立商城，而是并入充值链路？（v3.1）

- **决策**：砍掉 goods/orders 两张表与订单状态机，活动以"充值 preview 展示 + 下单时核销"方式接入
- **理由**：项目初期商品形态只有"套餐 + 3 种活动"，独立商城是过度设计；充值链路天然具备幂等、事务、风控能力，活动核销复用即可。无限畅聊走活动区入口，激活码走独立兑换接口，形态清晰。
- **替代方案**：独立商城 + 订单系统 -> 多出订单状态机、支付对接、库存两套逻辑，面试演示复杂度爆炸
- **后果**：未来若出现实物商品或复杂营销玩法（拼团、预售），需回归商城模型——届时以 orders 表为中心重建

---

*本文档为 Go 微服务层架构设计 v3.1，覆盖商业化扣款链路重构（Redis + Kafka 账本 + 异步落库 + 三层对账）、官职等级体系、充值活动一体化、LLM 推荐话术四大变更。Go 实现在仓库 `XNCAgent-go`；Python Agent Core 架构详见仓库 `XNCAgent` 的 `doc/架构`。所有设计决策均基于生产可落地、可扩展、可回滚的原则。*
