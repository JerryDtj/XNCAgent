#!/usr/bin/env bash
# 一次性初始化。在服务器上人工执行：bash setup.sh
# 与本脚本同目录需要有 deploy.sh、xnc-deploy.service、xnc-deploy.timer。
set -euo pipefail

NODE_VERSION="v22.12.0"
NODE_URL="https://registry.npmmirror.com/-/binary/node/${NODE_VERSION}/node-${NODE_VERSION}-linux-x64.tar.xz"
NODE_PREFIX="/opt/node22"
FRONT_DIR="/opt/xnc/XNCAgent-frontend"
BACK_DIR="/opt/xnc/XNCAgent"
VERSION_FILE="/opt/xnc/.deployed_version"
DEPLOY_DIR="/opt/xnc/deploy"
STEP="启动"

fail() {
  echo "[失败] $*" >&2
  exit 1
}

ok() {
  echo "[成功] $*"
}

trap 'echo "[失败] 执行中断，步骤：${STEP}" >&2' ERR

if [[ "$(id -u)" -ne 0 ]]; then
  fail "请用 root 执行"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for required in deploy.sh xnc-deploy.service xnc-deploy.timer; do
  if [[ ! -f "${SCRIPT_DIR}/${required}" ]]; then
    fail "同目录缺少 ${required}（当前目录 ${SCRIPT_DIR}）"
  fi
done

STEP="安装 Node.js ${NODE_VERSION}"
echo "========== ${STEP} =========="
mkdir -p /usr/local/bin /opt

current=""
if [[ -x "${NODE_PREFIX}/bin/node" ]]; then
  current="$("${NODE_PREFIX}/bin/node" -v 2>/dev/null || true)"
fi

if [[ "${current}" == "${NODE_VERSION}" ]]; then
  ok "已存在 ${NODE_PREFIX}（${current}），跳过下载"
else
  tmpdir="$(mktemp -d)"
  tarball="${tmpdir}/node.tar.xz"
  echo "下载 ${NODE_URL}"
  curl -fL --retry 3 --retry-delay 5 --retry-all-errors -o "${tarball}" "${NODE_URL}" \
    || fail "下载 Node.js 失败"
  rm -rf "/opt/node-${NODE_VERSION}-linux-x64"
  tar -xJf "${tarball}" -C /opt
  rm -rf "${tmpdir}"
  if [[ ! -d "/opt/node-${NODE_VERSION}-linux-x64" ]]; then
    fail "解压后未找到 /opt/node-${NODE_VERSION}-linux-x64"
  fi
  rm -rf "${NODE_PREFIX}"
  mv "/opt/node-${NODE_VERSION}-linux-x64" "${NODE_PREFIX}"
  ok "已安装到 ${NODE_PREFIX}"
fi

ln -sfn "${NODE_PREFIX}/bin/node" /usr/local/bin/node
ln -sfn "${NODE_PREFIX}/bin/npm" /usr/local/bin/npm
hash -r || true

node_ver="$(/usr/local/bin/node -v)"
echo "node -v => ${node_ver}"
case "${node_ver}" in
  v22.12.*)
    ok "node -v 为 ${node_ver}"
    ;;
  *)
    fail "node -v 得到 ${node_ver}，期望 v22.12.x"
    ;;
esac

if ! /usr/local/bin/npm -v >/dev/null; then
  fail "npm 无法执行"
fi
ok "npm 可用：$(/usr/local/bin/npm -v)"

STEP="把前端 dist 改成软链"
echo "========== ${STEP} =========="
if [[ ! -d "${FRONT_DIR}" ]]; then
  fail "找不到前端目录 ${FRONT_DIR}"
fi
mkdir -p "${FRONT_DIR}/releases"

if [[ -L "${FRONT_DIR}/dist" ]]; then
  ok "dist 已是软链 -> $(readlink "${FRONT_DIR}/dist")，保持不动"
elif [[ -d "${FRONT_DIR}/dist" ]]; then
  if [[ -e "${FRONT_DIR}/releases/dist-manual" ]]; then
    fail "releases/dist-manual 已存在，且 dist 仍是真实目录，请人工处理后再执行"
  fi
  mv "${FRONT_DIR}/dist" "${FRONT_DIR}/releases/dist-manual"
  ln -s releases/dist-manual "${FRONT_DIR}/dist"
  if [[ "$(readlink "${FRONT_DIR}/dist")" != "releases/dist-manual" ]]; then
    fail "软链目标不是 releases/dist-manual"
  fi
  ok "dist -> releases/dist-manual"
else
  fail "${FRONT_DIR}/dist 不存在，无法改造成软链"
fi

read_local_tag() {
  local dir="$1"
  local tag=""
  if [[ -d "${dir}/.git" ]]; then
    tag="$(git -C "${dir}" describe --tags --abbrev=0 2>/dev/null || true)"
  fi
  if [[ -z "${tag}" ]]; then
    printf '%s' "v0.0.0"
  else
    printf '%s' "${tag}"
  fi
}

STEP="初始化 ${VERSION_FILE}"
echo "========== ${STEP} =========="
backend_tag="$(read_local_tag "${BACK_DIR}")"
frontend_tag="$(read_local_tag "${FRONT_DIR}")"
if [[ ! -d "${BACK_DIR}/.git" ]]; then
  echo "[提示] ${BACK_DIR} 不是 git 仓库，backend 记为 ${backend_tag}"
fi
if [[ "${backend_tag}" == "v0.0.0" ]]; then
  echo "[提示] backend 未读到 tag，写入 v0.0.0"
fi
if [[ "${frontend_tag}" == "v0.0.0" ]]; then
  echo "[提示] frontend 未读到 tag，写入 v0.0.0"
fi
printf 'backend=%s\nfrontend=%s\n' "${backend_tag}" "${frontend_tag}" > "${VERSION_FILE}"
echo "----- ${VERSION_FILE} -----"
cat "${VERSION_FILE}"
ok "已写入部署版本文件"

STEP="安装 deploy.sh"
echo "========== ${STEP} =========="
mkdir -p "${DEPLOY_DIR}"
cp "${SCRIPT_DIR}/deploy.sh" "${DEPLOY_DIR}/deploy.sh"
chmod +x "${DEPLOY_DIR}/deploy.sh"
ok "已复制到 ${DEPLOY_DIR}/deploy.sh 并 chmod +x"

STEP="安装并启用 systemd timer"
echo "========== ${STEP} =========="
cp "${SCRIPT_DIR}/xnc-deploy.service" /etc/systemd/system/xnc-deploy.service
cp "${SCRIPT_DIR}/xnc-deploy.timer" /etc/systemd/system/xnc-deploy.timer
systemctl daemon-reload
systemctl enable --now xnc-deploy.timer
ok "xnc-deploy.timer 已 enable --now"
systemctl list-timers xnc-deploy.timer --no-pager

echo ""
ok "初始化完成。接着可手动执行：bash ${DEPLOY_DIR}/deploy.sh，然后 tail -f ${DEPLOY_DIR}/deploy.log"
