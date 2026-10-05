# XNC 生产自动部署

在腾讯云轻量（Ubuntu 22.04，root）上轮询两个仓库的 `v*` tag，有新 tag 就部署。脚本放在本仓库 `deploy/`，拷到服务器 `/opt/xnc/deploy/` 后不再跟着 git 走。

## 服务器路径

| 用途 | 路径 |
| --- | --- |
| Python 服务 | `/opt/xnc/XNCAgent`，systemd 服务 `xncagent` |
| 前端静态文件 | `/opt/xnc/XNCAgent-frontend`，nginx 的 root 是 `dist` |
| 已部署版本 | `/opt/xnc/.deployed_version`（`backend=<tag>`、`frontend=<tag>`） |
| 运行中的脚本 | `/opt/xnc/deploy/deploy.sh` |
| 部署日志 | `/opt/xnc/deploy/deploy.log` |
| 可选通知 | `/opt/xnc/deploy/env` 里的 `WEBHOOK_URL` |

后端健康检查是 `curl -sf http://127.0.0.1:8199/health`。前端是 `curl -sf http://127.0.0.1/` 返回 200，并且页面引用的 `index-*.js` 要在刚发布的目录里。

## 首次部署

在自己的机器上把本目录传到服务器，例如：

```bash
scp -r deploy root@<服务器>:~
```

然后在服务器上：

```bash
cd ~/deploy
bash setup.sh
systemctl list-timers xnc-deploy.timer
bash /opt/xnc/deploy/deploy.sh
tail -n 80 /opt/xnc/deploy/deploy.log
```

`setup.sh` 会做这些事：

1. 把 Node.js 装到 `/opt/node22`（v22.12.0），并把 `/usr/local/bin/node`、`npm` 指过去。`node -v` 应为 `v22.12.x`。
2. 把前端 `dist` 改成软链：`dist` → `releases/dist-manual`。
3. 用两个仓库的 `git describe --tags --abbrev=0` 写 `.deployed_version`，读不到 tag 就写 `v0.0.0`。
4. 把 `deploy.sh` 拷到 `/opt/xnc/deploy/deploy.sh` 并 `chmod +x`。
5. 安装并启用 `xnc-deploy.timer`。

`list-timers` 里能看到 `xnc-deploy.timer` 才算定时器在跑。机器如果已经开机超过 2 分钟，`Persistent=true` 可能在 `enable --now` 时立刻跑一次。服务最长允许跑 30 分钟（`TimeoutStartSec=1800`），避免 npm 安装被 systemd 默认的 90 秒超时杀掉。

初始化成功之后不要为了改脚本再跑 `setup.sh`，它会按当前检出的 tag 重写 `.deployed_version`。更新脚本用下面的拷贝命令。

## 定时器在做什么

每 5 分钟（开机 2 分钟后开始）以 root 跑一次 `deploy.sh`：

- 拿不到 `/opt/xnc/deploy/.lock` 就退出 0，避免上一次还没跑完又叠一次。
- `git ls-remote` / `git fetch` 失败会隔 5 秒再试，一共 3 次。单次 git 网络操作超过 120 秒会中断，避免 SSH 挂死占住锁。
- 远端 tag 用 `sort -V` 取最大的一个，和 `.deployed_version` 一致就跳过。带 `-rc` 的 tag 也会参与比较。
- 后端：`git fetch` → `git checkout -f <tag>` → `systemctl restart xncagent` → 等 5 秒 → 健康检查。失败则 checkout 回旧 tag 再 restart，并在日志和通知里写明回滚结果。
- 前端：`npm install --no-audit --no-fund`（不用 `npm ci`）→ 摘掉 `dist` 软链再 `npm run build` → 把产物挪到 `releases/dist-<tag>/` → `ln -sfn` 切过去。检查失败就把软链切回切换前 `readlink` 记下的目录。成功后只留最近 3 个 `releases/dist-*`。
- 一边成功、一边失败时，成功的那一边保持新版本，失败的那一边回滚，进程以非 0 退出。
- 两侧都是「已是当前 tag」时不发通知。只要有一边真的部署或失败，并且设置了 `WEBHOOK_URL`，就 POST 一段纯文本（`--max-time 10`）。

构建那几秒到一两分钟里，nginx 的 `dist` 软链是摘掉的，首页会暂时打不开。这是为了不让 vite 顺着软链把上一版产物清掉。

## 通知

`/opt/xnc/deploy/env`（没有这个文件就跳过通知）：

```
WEBHOOK_URL=https://example.com/hook
```

不要写 `export`。改这个文件不用 `daemon-reload`，下次定时器起来会读到。

POST 的 `Content-Type` 是 `text/plain`，正文类似：

```
XNC 部署失败
backend: 失败（健康检查失败，已回滚到 v0.1.1，健康检查通过）
frontend: 成功（v0.1.2）
```

## 更新部署脚本

`/opt/xnc/deploy/deploy.sh` 是一份拷贝，仓库里的新脚本不会自动生效。

```bash
cp /opt/xnc/XNCAgent/deploy/deploy.sh /opt/xnc/deploy/deploy.sh
chmod +x /opt/xnc/deploy/deploy.sh
cp /opt/xnc/XNCAgent/deploy/xnc-deploy.service /etc/systemd/system/xnc-deploy.service
cp /opt/xnc/XNCAgent/deploy/xnc-deploy.timer /etc/systemd/system/xnc-deploy.timer
systemctl daemon-reload
systemctl restart xnc-deploy.timer
```

单元文件只有在后端仓库已经 checkout 到含这些文件的提交时，上面的 `cp` 才能找到它们。否则从你上传的 `deploy/` 目录拷。

## 日常排查

```bash
systemctl list-timers xnc-deploy.timer
systemctl status xnc-deploy.timer
journalctl -u xnc-deploy.service -n 200 --no-pager
tail -n 200 /opt/xnc/deploy/deploy.log
cat /opt/xnc/.deployed_version
readlink /opt/xnc/XNCAgent-frontend/dist
curl -sf http://127.0.0.1:8199/health
curl -sf -o /dev/null -w '%{http_code}\n' http://127.0.0.1/
```

手动再部署一次：

```bash
bash /opt/xnc/deploy/deploy.sh
```
