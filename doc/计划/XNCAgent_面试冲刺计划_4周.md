# XNCAgent 面试冲刺计划（4周）

> **目标岗位**：AI Agent 后端工程师（Go + Python 混合架构）
> **时间窗口**：2026.09.03 - 2026.09.30
> **状态**：离职冲刺期，每日有效投入 8 小时

---

## 一、总体技术架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Go 微服务层（新建）                        │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ API网关   │ │ 用户服务  │ │ 商业化   │ │ 推荐服务  │        │
│  │ (Gin)    │ │ (注册/登录)│ │ (积分/套餐)│ │ (规则引擎)│        │
│  │          │ │          │ │ 礼包管理  │ │ A/B分流   │        │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘        │
│       └─────────────┴─────────────┴────────────┘              │
│  ┌────────────────────────────────────────────────────┐       │
│  │         事件总线 (Kafka / Redis Streams)           │       │
│  └────────────────────────────────────────────────────┘       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ 用户画像  │ │ 风控服务  │ │ 监控告警  │ │ OpenTel. │        │
│  │ 服务     │ │         │ │(Prom/Grafana)│ 追踪     │        │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
└──────────────────────────┬──────────────────────────────────┘
                           │ gRPC + HTTP
┌──────────────────────────┴──────────────────────────────────┐
│           Python Agent Core（现有 dev 骨架保留）              │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐        │
│  │ 情绪雷达  │ │ MCP协议   │ │ Sandbox  │ │ RAG检索  │        │
│  │ 方言管理  │ │ Skills   │ │ 安全执行  │ │ ChromaDB │        │
│  │ 安全树洞  │ │ 工具生态  │ │         │ │         │        │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘        │
└─────────────────────────────────────────────────────────────┘
```

### 面试话术要点

> "项目采用 Go + Python 混合架构。Go 层负责高并发微服务、商业化、推荐、A/B 测试，利用 goroutine 实现事件驱动的异步 Pipeline；Python 层保留 Agent 核心，通过 MCP 协议对外暴露 Skills。服务间用 gRPC 通信，Gateway 层接入 OpenTelemetry 做全链路追踪，最终部署在 K8s 上，GitHub Actions 做 CI/CD。"

---

## 二、学习资源索引（按模块分类，可直接点击）

### Go 语言基础（Java 老兵快速转 Go）

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **Go 官方安装包** | https://go.dev/dl/ | 下载 Go 1.23 | 10 分钟 |
| **Go 语言圣经（中文版）** | https://books.studygolang.com/gopl-zh/ | 语法+并发核心教材 | Day 1-2，每天 2 小时 |
| **Go by Example（中文版）** | https://gobyexample-cn.github.io/ | 查标准库用法，像查 JDK 文档 | 边写边查 |
| **Uber Go 编码规范** | https://github.com/uber-go/guide/blob/master/style.md | Go 编码风格，面试时代码专业 | Day 2，通读一遍 |
| **100 Go Mistakes** | https://100go.co/ | 避坑，避免写出 Java 风格的 Go | 碎片时间看 |
| **Go 官方文档** | https://go.dev/doc/ | 标准库参考 | 随时查 |

### Go Web 框架与生态

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **Gin 官方文档** | https://gin-gonic.com/docs/ | Web 框架，API 网关 | Day 2-3 |
| **GORM 官方文档** | https://gorm.io/docs/ | ORM，数据库操作 | Day 3-4 |
| **Viper 配置管理** | https://github.com/spf13/viper | 配置文件读取 | Day 2 |
| **Zap 日志库** | https://github.com/uber-go/zap | 高性能日志 | Day 2 |
| **JWT-Go 库** | https://github.com/golang-jwt/jwt | JWT 签发校验 | Day 3 |
| **Go 密码学 bcrypt** | https://pkg.go.dev/golang.org/x/crypto/bcrypt | 密码加密 | Day 3 |

### gRPC 与 Protocol Buffers

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **gRPC 官方文档（Go）** | https://grpc.io/docs/languages/go/ | gRPC 服务端/客户端 | Day 4-6 |
| **Protocol Buffers 指南** | https://protobuf.dev/programming-guides/proto3/ | .proto 文件语法 | Day 4 |
| **grpcurl 工具** | https://github.com/fullstorydev/grpcurl | gRPC 接口调试 | Day 4 |
| **gRPC-Gateway** | https://github.com/grpc-ecosystem/grpc-gateway | HTTP 转 gRPC | Day 6 |

### 消息队列与事件驱动

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **Kafka 官方文档** | https://kafka.apache.org/documentation/ | 消息队列核心概念 | Day 12 |
| **Sarama（Go Kafka 客户端）** | https://github.com/IBM/sarama | Go 连接 Kafka | Day 12 |
| **Redis 官方文档** | https://redis.io/docs/ | 缓存、会话存储 | Day 2 |
| **go-redis 客户端** | https://github.com/redis/go-redis | Go 连接 Redis | Day 2 |

### 可观测性（监控 + 链路追踪）

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **OpenTelemetry Go** | https://opentelemetry.io/docs/languages/go/ | 全链路追踪 | Day 15 |
| **Prometheus Go Client** | https://github.com/prometheus/client_golang | Metrics 埋点 | Day 16 |
| **Grafana 官方文档** | https://grafana.com/docs/ | 仪表盘搭建 | Day 16 |
| **Jaeger 官方文档** | https://www.jaegertracing.io/docs/ | 链路追踪 UI | Day 15 |

### 容器与 K8s

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **Docker 官方文档** | https://docs.docker.com/ | Dockerfile 编写 | Day 22 |
| **Docker Compose 文档** | https://docs.docker.com/compose/ | 本地编排 | Day 1 |
| **Kubernetes 官方文档** | https://kubernetes.io/docs/home/ | K8s 核心概念 | Day 23-24 |
| **Kind（本地 K8s）** | https://kind.sigs.k8s.io/ | 本地 K8s 测试 | Day 23 |
| **minikube** | https://minikube.sigs.k8s.io/docs/ | 本地 K8s 另一种方案 | Day 23 |

### CI/CD

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **GitHub Actions 文档** | https://docs.github.com/en/actions | CI/CD 流水线 | Day 25 |
| **GitHub Actions Go 示例** | https://github.com/actions/setup-go | Go 项目 Actions 模板 | Day 25 |

### Python MCP 协议

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **MCP 官方文档** | https://modelcontextprotocol.io/ | MCP 协议规范 | Day 5 |
| **MCP Python SDK** | https://github.com/modelcontextprotocol/python-sdk | Python MCP Server/Client | Day 5 |
| **MCP Inspector 工具** | https://modelcontextprotocol.io/docs/tools/inspector | MCP 接口调试 | Day 5 |

### 算法与面试

| 资源 | 链接 | 用途 | 建议用时 |
|------|------|------|---------|
| **LeetCode 中国站** | https://leetcode.cn/ | 每日手写训练 | 每天 30 分钟 |
| **Go 语言实现常见算法** | https://github.com/TheAlgorithms/Go | 参考实现 | 碎片时间 |

---

## 三、4 周周计划

### Week 1：Go 微服务骨架 + 基础设施 + 商业化雏形（9.03 - 9.09）

**本周目标**：Go 项目能编译运行，基础设施（PostgreSQL/Redis/Kafka）本地可用，API 网关通，用户服务能注册登录，gRPC 协议定义完成，Python 层接入 MCP 协议，商业化服务骨架搭好。

| 模块 | 具体内容 | 验收标准 |
|------|---------|---------|
| **项目初始化** | Go 1.23+ 环境，go modules，标准目录结构（`cmd/`, `internal/`, `pkg/`, `api/`, `configs/`, `deploy/`） | `go build ./...` 全项目编译通过 |
| **Docker Compose** | 本地基础设施：PostgreSQL 15, Redis 7, Kafka + Zookeeper, Jaeger（链路追踪） | `docker-compose -f deploy/docker-compose.yml up -d` 全部健康 |
| **API 网关** | Gin 框架，统一 Response 封装，路由分组（`/api/v1/`），中间件链（Zap 日志、Recovery、CORS、RequestID） | `curl /health` 返回标准 JSON，日志带 RequestID |
| **用户服务** | 注册/登录 API，bcrypt 加密，JWT（access + refresh token），PostgreSQL 表（users, refresh_tokens） | 注册→登录→访问受保护接口全流程通 |
| **gRPC 协议** | `.proto` 定义：UserService, CommercialService, RecommendationService, ProfileService | `protoc` 生成 Go 代码，能编译通过 |
| **gRPC 实现** | 用户服务的 gRPC Server + Gateway HTTP 转 gRPC 客户端 | Gateway 通过 gRPC 调用用户服务查询用户信息 |
| **Python MCP** | Python 层实现 MCP Server，暴露情绪雷达、方言切换、笑话工具为 MCP Tools | Go Gateway 通过 stdio/sse 调用 Python MCP Tools |
| **商业化骨架** | 积分账户表、套餐表、交易流水表；充值 API、余额查询、预扣/结算骨架 | 商业化服务独立可运行，表结构正确 |
| **本周复盘** | 联调所有服务，补单元测试，写架构文档 | 全链路通，代码 Push dev 分支 |

**本周学习文档**：
- Day 1：[Go 官方安装包](https://go.dev/dl/) + [Go 语言圣经](https://books.studygolang.com/gopl-zh/) 第1-2章
- Day 2：[Gin 官方文档](https://gin-gonic.com/docs/) + [Uber Go 编码规范](https://github.com/uber-go/guide/blob/master/style.md)
- Day 3：[GORM 官方文档](https://gorm.io/docs/) + [JWT-Go](https://github.com/golang-jwt/jwt)
- Day 4：[gRPC 官方文档（Go）](https://grpc.io/docs/languages/go/) + [Protocol Buffers 指南](https://protobuf.dev/programming-guides/proto3/)
- Day 5：[MCP 官方文档](https://modelcontextprotocol.io/) + [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- Day 6：[grpcurl 工具](https://github.com/fullstorydev/grpcurl) + [gRPC-Gateway](https://github.com/grpc-ecosystem/grpc-gateway)
- Day 7：[Go by Example](https://gobyexample-cn.github.io/) 查漏补缺

---

### Week 2：推荐 + A/B + 礼包 + 事件驱动（9.10 - 9.16）

**本周目标**：推荐服务能按规则打分，A/B 测试能分流，礼包能生成兑换，Kafka 事件通。

| 模块 | 具体内容 | 验收标准 |
|------|---------|---------|
| **商业化核心** | 预扣 + 结算完整逻辑（事务），套餐购买，余额变更流水 | 充值 100 → 预扣 10 → 结算 8 → 余额 92 |
| **礼包系统** | 激活码生成（UUID v4）、兑换 API、过期处理、使用记录 | 管理员生成礼包 → 用户兑换 → 余额增加 |
| **规则引擎 V1** | YAML 配置 + 表达式解析，5 条核心规则（NEW_USER_WELCOME, LOW_BALANCE_URGENT, HEAVY_USER_UPGRADE 等） | 输入用户画像 → 返回推荐套餐列表 + 得分排序 |
| **推荐服务** | 规则匹配 → 加权评分 → Redis 缓存结果 | 用户画像变化后 5 秒内 Redis 更新推荐 |
| **A/B 测试服务** | 实验配置 CRUD、一致性哈希分流算法、用户分组记录、埋点事件表 | 同一用户永远在同一组，流量比例可调 |
| **用户画像服务** | 三层标签体系（商业价值/行为特征/流失风险），画像写入/查询 API | 推荐服务能读取标签做规则匹配 |
| **Kafka 事件** | Agent Core 发 `chat.completed` / `user.login` / `balance.changed` 事件 | Kafka 消费者能收到事件并更新画像 |
| **本周复盘** | 商业化 + 推荐 + A/B + 礼包全流程联调，补集成测试 | 注册→对话→扣费→推荐→A/B 埋点全链路通 |

**本周学习文档**：
- Day 8：[Go by Example](https://gobyexample-cn.github.io/) 正则表达式 + 文件读取
- Day 9：[Redis 官方文档](https://redis.io/docs/) 数据类型 + 过期策略
- Day 10：[100 Go Mistakes](https://100go.co/) 并发相关章节
- Day 11：[Go 密码学 crypto](https://pkg.go.dev/golang.org/x/crypto) UUID 生成
- Day 12：[Kafka 官方文档](https://kafka.apache.org/documentation/) 核心概念 + [Sarama](https://github.com/IBM/sarama)
- Day 13：[go-redis 客户端](https://github.com/redis/go-redis) 高级用法
- Day 14：[100 Go Mistakes](https://100go.co/) 测试相关章节

---

### Week 3：工程化 + Python 高级 + 风控 + 监控（9.17 - 9.23）

**本周目标**：推荐服务异步消费事件，风控能检测羊毛党，OpenTelemetry 全链路追踪，Python Sandbox + RAG，压测。

| 模块 | 具体内容 | 验收标准 |
|------|---------|---------|
| **事件驱动推荐** | 推荐服务消费 Kafka 事件，异步更新画像，异步计算推荐结果写 Redis | 用户对话后 5 秒内 Redis 出现新的推荐结果 |
| **风控服务** | 设备指纹（简单 hash）、批量注册检测（同 IP 注册数）、礼包滥用检测 | 同一 IP 1 小时内注册 5 个账号 → 触发风控拦截 |
| **OpenTelemetry** | Gateway + 各服务接入 OTel，Jaeger 展示调用链，Redis/Kafka 埋点 | Jaeger UI 能看到一次请求的完整链路 |
| **监控告警** | Prometheus metrics（QPS/延迟/错误率），Grafana 仪表盘 | `curl /metrics` 返回 Prometheus 格式数据 |
| **Python Sandbox** | 工具调用走受限子进程（timeout 5s、内存限制 128MB、禁止网络） | 恶意代码（如 `while True`）被强制终止 |
| **Python RAG** | ChromaDB 向量存储，文档向量化，RAG 检索增强对话 | 上传文档后能基于文档内容回答 |
| **Python 记忆持久化** | 多轮对话历史写入 PostgreSQL，支持会话恢复 | 重启服务后历史对话不丢失 |
| **本周复盘** | 全链路压测（并发 50 用户），修 bug，补集成测试 | 50 并发用户不崩溃、不丢消息 |

**本周学习文档**：
- Day 15：[OpenTelemetry Go](https://opentelemetry.io/docs/languages/go/) + [Jaeger 官方文档](https://www.jaegertracing.io/docs/)
- Day 16：[Prometheus Go Client](https://github.com/prometheus/client_golang) + [Grafana 官方文档](https://grafana.com/docs/)
- Day 17：[Go by Example](https://gobyexample-cn.github.io/) 时间 + 哈希
- Day 18：[Python subprocess 文档](https://docs.python.org/3/library/subprocess.html) 安全执行
- Day 19：[ChromaDB 文档](https://docs.trychroma.com/) 向量存储
- Day 20：[GORM 高级用法](https://gorm.io/docs/advanced_query.html) 事务 + 预加载
- Day 21：[100 Go Mistakes](https://100go.co/) 性能相关章节

---

### Week 4：K8s/CI-CD 攻坚 + 面试弹药（9.24 - 9.30）

**本周目标**：能一键部署到 K8s，CI/CD 流水线通，有压测报告，有 Demo 视频，面试话术熟。

| 模块 | 具体内容 | 验收标准 |
|------|---------|---------|
| **Docker 化** | 每个服务独立 Dockerfile（多阶段构建），镜像 < 50MB | `make docker-build` 生成 5 个镜像 |
| **K8s 部署** | Deployment, Service, ConfigMap, Secret, Ingress(Nginx) | `kubectl apply -f deploy/k8s/` 全部 Running |
| **K8s 进阶** | HPA 自动扩缩容、滚动更新、健康检查探针 | 手动触发扩容，Pod 数增加 |
| **CI/CD** | GitHub Actions：PR 触发测试 → main 分支触发构建 → 推送 Docker Hub | 提交代码后 Actions 全绿 |
| **Makefile** | 一键：`make build` / `make test` / `make docker-build` / `make deploy` | `make all` 完成构建+测试+镜像 |
| **压测报告** | k6 压测：注册/登录/对话/充值接口，出具 QPS/延迟/错误率报告 | 压测报告截图，关键接口 P99 < 200ms |
| **Demo 录制** | 3 分钟视频：注册 → 对话（情绪切换）→ 扣费 → 推荐弹窗 → A/B 测试 | 视频文件 `demo/demo.mp4` |
| **面试话术** | 项目亮点、技术难点、如果重来怎么改进、架构演进思路 | 能不看稿讲 15 分钟 |
| **最终复盘** | 全链路最终测试、补 README、整理文档、Push 所有代码 | 仓库 Ready for Interview |

**本周学习文档**：
- Day 22：[Docker 官方文档](https://docs.docker.com/) 多阶段构建
- Day 23：[Kubernetes 官方文档](https://kubernetes.io/docs/home/) 核心概念 + [Kind](https://kind.sigs.k8s.io/)
- Day 24：[Kubernetes 工作负载](https://kubernetes.io/docs/concepts/workloads/) Deployment + HPA
- Day 25：[GitHub Actions 文档](https://docs.github.com/en/actions) + [setup-go](https://github.com/actions/setup-go)
- Day 26：[k6 压测工具](https://k6.io/docs/) 脚本编写
- Day 27：[100 Go Mistakes](https://100go.co/) 最后查漏补缺
- Day 28：项目文档整理，不再看新文档

---

## 四、最近 7 天详细天计划

### Day 1（9.3 周三）

**今日目标**：Go 环境就绪，项目初始化，Docker Compose 基础设施跑起来。

| 时间段 | 任务 | 具体动作 | 验收标准 |
|--------|------|---------|---------|
| **10:58-12:00** | Go 环境 | 安装 Go 1.23，配置 GOPATH/GOROOT，IDE 安装 Go 插件 | `go version` 输出 1.23+ |
| **14:00-15:30** | 项目初始化 | `go mod init github.com/JerryDtj/XNCAgent-go`，按目录规范创建 `cmd/gateway`, `cmd/user`, `cmd/commercial`, `cmd/recommend`, `internal/`, `pkg/`, `api/proto/`, `configs/`, `deploy/` | `tree -L 2` 目录结构符合规范 |
| **15:30-17:00** | Docker Compose | 写 `deploy/docker-compose.yml`：PostgreSQL 15（带初始化脚本）、Redis 7、Kafka + Zookeeper、Jaeger | `docker-compose up -d` 后 `docker ps` 看到 4 个容器 Running |
| **19:00-20:00** | 数据库初始化 | 写 `deploy/init.sql`：创建 `xncagent` 数据库，用户表初步结构 | 能连上 PostgreSQL 并看到数据库 |
| **20:00-21:00** | 复盘 + Push | 写今日 commit log，确认所有基础设施健康，Push 到 dev 分支 | GitHub dev 分支能看到今日提交 |

**今日学习文档**：
- [Go 官方安装包](https://go.dev/dl/)
- [Go 语言圣经](https://books.studygolang.com/gopl-zh/) 第1章：入门
- [Docker Compose 文档](https://docs.docker.com/compose/compose-file/)

**今日硬约束**：21:00 前必须 Push 代码。

---

### Day 2（9.4 周四）

**今日目标**：API 网关搭起来，中间件链跑通，健康检查接口可用，gRPC 协议定义完成。

| 时间段 | 任务 | 具体动作 | 验收标准 |
|--------|------|---------|---------|
| **09:00-10:30** | Gateway 骨架 | `cmd/gateway/main.go` 启动 Gin，`pkg/response` 统一封装 `{code, message, data}` | `go run cmd/gateway/main.go` 启动不报错 |
| **10:30-12:00** | 中间件 | Zap 日志（JSON 格式、自动轮转）、Recovery（panic 不崩溃）、CORS、RequestID（uuid） | 故意抛 panic → 服务不崩，日志带 RequestID |
| **14:00-15:30** | 路由分组 | `/api/v1/health`, `/api/v1/users/*` 路由注册，`internal/router` 模块 | `curl /api/v1/health` 返回 `{"code":0,"message":"ok"}` |
| **15:30-17:00** | 配置管理 | Viper 读取 `configs/gateway.yml`（端口、日志级别、数据库连接串） | 改配置文件端口能生效 |
| **19:00-20:00** | 错误码规范 | `pkg/errcode` 定义业务错误码（0=成功，10001=参数错误，10002=未授权等） | 错误返回统一格式 |
| **20:00-21:00** | gRPC 协议 | `api/proto/user.proto`：UserService（GetUser, CreateUser, ValidateToken） | `protoc` 生成 Go 代码无报错 |
| **21:00** | 复盘 + Push | 今日代码 Review，确认中间件链顺序正确，Push | `curl` 测试通过，代码已 Push |

**今日学习文档**：
- [Gin 官方文档](https://gin-gonic.com/docs/)
- [Uber Go 编码规范](https://github.com/uber-go/guide/blob/master/style.md)
- [Protocol Buffers 指南](https://protobuf.dev/programming-guides/proto3/)

---

### Day 3（9.5 周五）

**今日目标**：用户服务注册/登录通，JWT 签发校验通，数据库表创建，用户服务 gRPC 跑通。

| 时间段 | 任务 | 具体动作 | 验收标准 |
|--------|------|---------|---------|
| **09:00-10:30** | 用户服务骨架 | `cmd/user/main.go` 启动，GORM 连接 PostgreSQL，自动迁移 `users` 表 | `users` 表出现在 PostgreSQL 中 |
| **10:30-12:00** | 注册 API | `POST /api/v1/users/register`：校验参数、bcrypt 加密密码、写入数据库 | 重复注册返回"用户已存在"，密码是密文 |
| **14:00-15:30** | 登录 API | `POST /api/v1/users/login`：校验密码、签发 JWT（access token 2h + refresh token 7d） | 登录返回双 token，解析 token 能得到 user_id |
| **15:30-17:00** | JWT 中间件 | Gateway 层 JWT 校验中间件，白名单（/health, /register, /login） | 不带 Token 访问受保护接口返回 401 |
| **19:00-20:00** | 用户服务 gRPC | 用户服务启动 gRPC Server（端口 50051），实现 GetUser | `grpcurl` 或客户端能调通 GetUser |
| **20:00-21:00** | gRPC Client | Gateway 初始化 gRPC 连接池（用户服务），实现 HTTP→gRPC 转发 | Gateway 调用 `/api/v1/users/{id}` 实际走 gRPC |
| **21:00** | 复盘 + Push | Postman 跑通注册→登录→访问受保护接口全流程，Push | Postman 集合截图保存 |

**今日学习文档**：
- [GORM 官方文档](https://gorm.io/docs/)
- [JWT-Go 库](https://github.com/golang-jwt/jwt)
- [Go 密码学 bcrypt](https://pkg.go.dev/golang.org/x/crypto/bcrypt)
- [gRPC 官方文档（Go）](https://grpc.io/docs/languages/go/)

---

### Day 4（9.6 周六）

**今日目标**：商业化服务骨架搭起来，积分账户表、套餐表、充值 API 通，预扣结算骨架完成。

| 时间段 | 任务 | 具体动作 | 验收标准 |
|--------|------|---------|---------|
| **09:00-10:30** | 商业化服务骨架 | `cmd/commercial/main.go` 启动，GORM 连接 DB，自动迁移 `accounts`, `packages`, `transactions` 表 | 3 张表出现在 PostgreSQL |
| **10:30-12:00** | 套餐管理 | 初始化套餐数据（体验卡/月卡/年卡），`GET /api/v1/packages` 查询套餐列表 | 返回 3 个套餐，价格正确 |
| **14:00-15:30** | 充值 API | `POST /api/v1/commercial/recharge`：用户充值积分，写入交易流水（事务） | 充值 100 → 账户余额 100 → 流水记录正确 |
| **15:30-17:00** | 余额查询 | `GET /api/v1/commercial/balance`：查询当前积分余额 | 返回准确余额 |
| **19:00-20:00** | 预扣逻辑 | `POST /api/v1/commercial/prehold`（冻结积分），`POST /api/v1/commercial/settle`（结算） | 预扣 10 → 可用余额 90，冻结 10 |
| **20:00-21:00** | 复盘 + Push | Postman 跑通套餐→充值→余额→预扣骨架，Push | 商业化服务独立可运行 |

**今日学习文档**：
- [GORM 事务文档](https://gorm.io/docs/transactions.html)
- [Go by Example](https://gobyexample-cn.github.io/) 时间 + 字符串处理

---

### Day 5（9.7 周日）

**今日目标**：Python 层接入 MCP 协议，Go Gateway 能调用 Python Agent 工具。

| 时间段 | 任务 | 具体动作 | 验收标准 |
|--------|------|---------|---------|
| **09:00-10:30** | MCP Server | Python 层安装 `mcp` SDK，实现 `XNCAgentMCPServer`，暴露 `get_joke`, `switch_dialect`, `detect_emotion` 三个 Tools | MCP Inspector 能看到 3 个 Tools |
| **10:30-12:00** | MCP 客户端 | Go 层实现 MCP Client（stdio 方式），能调用 Python MCP Server | Go 程序启动 Python MCP Server 子进程 |
| **14:00-15:30** | 工具调用链路 | Gateway 增加 `/api/v1/agent/tools` 接口，转发到 MCP Client | `curl` 调用后返回笑话/方言/情绪结果 |
| **15:30-17:00** | 情绪雷达集成 | Python `agent.py` 整合 MCP Tools，对话时根据情绪选择 Tool | 输入"起床了"→调用 detect_emotion→返回催起太监 |
| **19:00-20:00** | 流式输出 | Python 层 SSE 流式输出，Go Gateway 透传 SSE 到前端 | `curl` 能看到流式逐字返回 |
| **20:00-21:00** | 复盘 + Push | 联调 Go→Python→LLM 全链路，Push | 输入问题后能看到小喜子流式回复 |

**今日学习文档**：
- [MCP 官方文档](https://modelcontextprotocol.io/)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [MCP Inspector](https://modelcontextprotocol.io/docs/tools/inspector)
- [gRPC-Gateway](https://github.com/grpc-ecosystem/grpc-gateway)

---

### Day 6（9.8 周一）

**今日目标**：商业化预扣结算联调，安全树洞集成，会话记忆骨架。

| 时间段 | 任务 | 具体动作 | 验收标准 |
|--------|------|---------|---------|
| **09:00-10:30** | 预扣结算联调 | 对话发起时预扣 → LLM 返回后结算 → 多退少补 | 一次完整对话后余额正确，流水完整 |
| **10:30-12:00** | 安全树洞 | Python 层负面情绪状态机（连续两次触发→进入树洞模式→沉默陪伴） | 连续发"烦死了""想哭"→回复"奴才把嘴闭上了" |
| **14:00-15:30** | 会话记忆 | 内存缓存（sync.Map）存储多轮上下文，支持清空 | 同一 session_id 能记住上文 |
| **15:30-17:00** | 方言切换 | IP 推断（简单 map 映射）、手动切换、方言词汇替换 | 输入"说粤语"→切换粤语回复 |
| **19:00-20:00** | 主动找乐子 | 冷场检测（"嗯、哦、好"）→触发小游戏（数字射霉运） | 输入"嗯"→触发小游戏 |
| **20:00-21:00** | 复盘 + Push | 全链路联调（对话→扣费→情绪→方言→树洞），Push | 全链路通，代码已 Push |

**今日学习文档**：
- [Go by Example](https://gobyexample-cn.github.io/) Map + 闭包
- [100 Go Mistakes](https://100go.co/) 并发相关章节

---

### Day 7（9.9 周二）Week 1 复盘日

**今日目标**：Week 1 全链路联调，补测试，写架构文档，代码全部 Push。

| 时间段 | 任务 | 具体动作 | 验收标准 |
|--------|------|---------|---------|
| **09:00-10:30** | 全链路联调 | 注册→登录→调用 MCP 工具（笑话）→充值→查询余额→预扣→对话→情绪切换 | Postman 集合能一键跑通 |
| **10:30-12:00** | 单元测试 | 用户服务（注册/登录/JWT）测试覆盖率 > 60%，商业化服务（充值/余额）测试 | `go test ./... -cover` 覆盖率截图 |
| **14:00-15:30** | 集成测试 | Gateway→gRPC→用户服务→商业化服务 集成测试 | `go test ./tests/...` 通过 |
| **15:30-17:00** | 架构文档 | `docs/architecture-week1.md`：画架构图、技术选型理由、模块职责 | 文档包含架构图（文字版或截图） |
| **19:00-20:00** | 代码 Review | 检查：错误处理、日志脱敏、SQL 注入、JWT Secret 未硬编码 | 无硬编码密钥，无 panic 裸抛 |
| **20:00-21:00** | Week 1 复盘 | 写 `docs/weekly-review-1.md`：完成项、阻塞项、Week 2 调整、今日 commit log | 文档 + 代码全部 Push dev 分支 |

**Week 1 最终验收清单**：
- [ ] `docker-compose up` 启动全部基础设施
- [ ] `make build` 编译全部 Go 服务
- [ ] Postman 一键跑通：注册→登录→MCP 工具→充值→余额查询
- [ ] `go test ./... -cover` 覆盖率 > 50%
- [ ] 架构文档 + 复盘文档完成

---

## 五、Cursor 使用策略

### 可用 Cursor 生成的代码（低风险）

| 代码类型 | 原因 |
|---------|------|
| DTO/Entity/POJO 定义 | 纯数据结构，无业务逻辑 |
| 数据库迁移脚本 | 标准化 SQL |
| Dockerfile/K8s YAML | 模板化配置 |
| 简单的 CRUD Handler | 标准化流程 |
| 单元测试样板 | 重复劳动 |
| gRPC 生成的代码 | protoc 自动生成 |

### 必须手写的代码（高风险，面试必问）

| 代码类型 | 原因 |
|---------|------|
| **核心扣费/结算逻辑** | 涉及事务、并发、精度，面试必问 |
| **规则引擎评分算法** | 面试核心考点 |
| **A/B 分流算法** | 一致性哈希是经典面试题 |
| **JWT/加密逻辑** | 安全相关，必须懂每一行 |
| **风控检测逻辑** | 金融安全思维体现 |
| **中间件链设计** | 体现架构能力 |
| **goroutine + channel 并发模式** | Go 核心能力体现 |

### 使用原则

1. **生成后必须逐行审阅**：能解释每一行是干嘛的，为什么这样写
2. **不要直接复制粘贴**：至少改几个变量名、调一下结构
3. **核心业务逻辑先手写骨架**：用 Cursor 补边角，不要反过来
4. **每天 30 分钟手写训练**：用 Cursor 的同时，每天手写一个小算法（Go 实现），保持手感

---

## 六、每日 LeetCode 手写训练（保持手感）

面试可能考察算法题（中等难度），每天 30 分钟手写 Go：

| 日期 | 题号 | 题目 | 关联项目模块 | 链接 |
|------|------|------|-------------|------|
| Day 1 | 146 | LRU 缓存 | 会话记忆、Redis 缓存 | [LeetCode 146](https://leetcode.cn/problems/lru-cache/) |
| Day 2 | 912 | 排序数组 | 规则引擎排序 | [LeetCode 912](https://leetcode.cn/problems/sort-an-array/) |
| Day 3 | 3 | 无重复字符最长子串 | 字符串处理 | [LeetCode 3](https://leetcode.cn/problems/longest-substring-without-repeating-characters/) |
| Day 4 | 215 | 数组第 K 大元素 | 规则评分排序 | [LeetCode 215](https://leetcode.cn/problems/kth-largest-element-in-an-array/) |
| Day 5 | 208 | 实现 Trie（前缀树） | 关键词匹配、情绪路由 | [LeetCode 208](https://leetcode.cn/problems/implement-trie-prefix-tree/) |
| Day 6 | 380 | O(1) 时间插入、删除和获取随机元素 | 礼包激活码随机抽取 | [LeetCode 380](https://leetcode.cn/problems/insert-delete-getrandom-o1/) |
| Day 7 | 146 | LRU 缓存（再写一遍） | 巩固 | [LeetCode 146](https://leetcode.cn/problems/lru-cache/) |

**参考实现**：[TheAlgorithms/Go](https://github.com/TheAlgorithms/Go)（仅作参考，面试必须手写）

---

## 七、防拖延硬约束

1. **每日 21:00 强制 Push**：不管代码多烂，必须 Push。GitHub 的绿格子是进度条。
2. **文档冻结令**：`docs/` 下的场景话术 md（起床赖床.md 等）**不再修改**，直接当静态资源。架构文档只在复盘时写，不提前写。
3. **每周二 Demo 日**：必须能跑通一个可演示的里程碑，跑不通周三继续。
4. **核心业务手写**：扣费结算、规则引擎、A/B 分流、风控逻辑必须手写，不能用 Cursor。

---

## 八、面试定级话术准备

### P6/高级开发

> "我用 Go 从零实现了一个情绪感知 Agent 的商业化微服务集群，核心难点在于用 goroutine 实现了情绪分析、方言推断、安全检查的并行 Pipeline，相比串行处理延迟降低了 60%。商业化模块采用了预扣+结算模式，参考了金融系统的账务一致性设计。"

### P7/技术专家（冲刺方向）

> "项目采用 Go+Python 混合架构，Go 层负责高并发网关和业务编排（微服务、事件驱动、规则引擎、A/B 测试），Python 层负责模型推理和 Agent 核心。通过 gRPC 解耦，两边可以独立扩缩容。我重点解决了 SSE 流式输出的背压问题、会话状态的并发安全问题、以及积分结算的分布式事务一致性。"

### 常见问题预设

**Q：为什么用 Go 不用 Java？**
> "Agent 场景需要高并发会话管理和低延迟流式推送，Go 的 goroutine+channel 模型比 Java 线程更轻量，标准库的 HTTP/SSE 支持也更简洁。10 年后端经验让我理解工程化，Go 让我把性能做到极致。"

**Q：规则引擎为什么自研而不是用 Drools？**
> "初期需要快速迭代和热更新，Drools 过重且引入复杂依赖。自研 YAML+表达式引擎足够覆盖 5 条核心规则，后续规则复杂后可平滑迁移到 AviatorScript。"

**Q：A/B 测试怎么保证分流一致性？**
> "采用 `(experimentId + userId).hashCode()` 的一致性哈希，避免 userId 取模导致用户在所有实验中永远在同一组。"

---

*计划制定时间：2026-09-03*  
*执行分支：dev*  
*目标：10 月面试，完整项目 + Go 微服务 + 商业化 + 推荐系统 + A/B 测试*
