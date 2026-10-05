#!/usr/bin/env bash
# 轮询 XNCAgent / XNCAgent-frontend 的 v* tag，不一致就部署。
# 由 xnc-deploy.timer 以 root 启动。拿不到锁直接退出 0。
set -euo pipefail

DEPLOY_DIR="/opt/xnc/deploy"
LOCK_FILE="${DEPLOY_DIR}/.lock"
LOG_FILE="${DEPLOY_DIR}/deploy.log"
VERSION_FILE="/opt/xnc/.deployed_version"
BACKEND_DIR="/opt/xnc/XNCAgent"
FRONTEND_DIR="/opt/xnc/XNCAgent-frontend"
BACKEND_HEALTH="http://127.0.0.1:8199/health"
FRONTEND_HEALTH="http://127.0.0.1/"

FE_ROLLBACK_LINK=""
NOTIFIED=0
DEPLOY_STARTED=0
BACKEND_STATUS="未执行"
FRONTEND_STATUS="未执行"

export HOME="${HOME:-/root}"
export GIT_TERMINAL_PROMPT=0
export PATH="/usr/local/bin:/opt/node22/bin:${PATH:-/usr/bin:/bin}"
umask 022

log() {
  local msg="[$(date '+%F %T')] $*"
  printf '%s\n' "${msg}" >> "${LOG_FILE}"
  # stderr 不会被 $() 吃掉，手动执行时终端也能看到。
  printf '%s\n' "${msg}" >&2
  if command -v logger >/dev/null 2>&1; then
    logger -t xnc-deploy -- "$*" || true
  fi
}

on_err() {
  # ERR 陷阱里 LINENO 不可靠，只记失败的命令文本。
  log "命令失败: ${BASH_COMMAND}"
}

restore_frontend_link() {
  local old_link="$1"
  local dist="${FRONTEND_DIR}/dist"
  if [[ -z "${old_link}" ]]; then
    log "frontend: 没有旧软链目标，无法恢复"
    return 1
  fi
  if [[ "${dist}" != "${FRONTEND_DIR}/dist" ]]; then
    log "frontend: 拒绝操作意外路径 ${dist}"
    return 1
  fi
  # -L 必须写在 -d 前面：指向目录的软链也会让 -d 为真。
  # 软链只用 rm -f，避免删掉软链指着的旧产物。
  if [[ -L "${dist}" ]]; then
    rm -f "${dist}" || return 1
  elif [[ -d "${dist}" ]]; then
    rm -rf "${dist}" || return 1
  elif [[ -e "${dist}" ]]; then
    rm -f "${dist}" || return 1
  fi
  if ! ln -sfn "${old_link}" "${dist}" || [[ ! -L "${dist}" ]]; then
    log "frontend: 恢复软链失败，目标 ${old_link}"
    return 1
  fi
  log "frontend: dist -> ${old_link}"
}

cleanup_on_exit() {
  local code=$?
  local link=""
  trap - ERR
  set +e
  if [[ "${BACKEND_STATUS}" == 进行中* ]]; then
    BACKEND_STATUS="失败（脚本异常退出）"
  fi
  if [[ -n "${FE_ROLLBACK_LINK}" ]]; then
    link="${FE_ROLLBACK_LINK}"
    FE_ROLLBACK_LINK=""
    if restore_frontend_link "${link}"; then
      case "${FRONTEND_STATUS}" in
        进行中*|*切回*失败*)
          FRONTEND_STATUS="失败（脚本异常退出，软链已切回 ${link}）"
          ;;
      esac
    else
      FRONTEND_STATUS="失败（软链切回 ${link} 失败；${FRONTEND_STATUS}）"
    fi
  elif [[ "${FRONTEND_STATUS}" == 进行中* ]]; then
    FRONTEND_STATUS="失败（脚本异常退出）"
  fi
  if [[ "${code}" -ne 0 && "${NOTIFIED}" -eq 0 && "${DEPLOY_STARTED}" -eq 1 ]]; then
    NOTIFIED=1
    notify "$(printf '%s\n' "XNC 部署失败" "backend: ${BACKEND_STATUS}" "frontend: ${FRONTEND_STATUS}")" || true
  fi
  exit "${code}"
}

# 成功时只把该次标准输出交回调用方；失败尝试的输出丢掉，避免重试把两次 ls-remote 拼在一起。
retry() {
  local attempt=1
  local max=3
  local outfile errfile err
  outfile="$(mktemp)"
  errfile="$(mktemp)"
  while true; do
    if "$@" >"${outfile}" 2>"${errfile}"; then
      cat "${outfile}"
      rm -f "${outfile}" "${errfile}"
      return 0
    fi
    err="$(tr '\n' ' ' < "${errfile}" || true)"
    if (( attempt >= max )); then
      log "命令失败（已尝试 ${max} 次）: $* | ${err}"
      rm -f "${outfile}" "${errfile}"
      return 1
    fi
    log "命令失败，5 秒后重试（${attempt}/${max}）: $* | ${err}"
    : >"${outfile}"
    sleep 5
    attempt=$((attempt + 1))
  done
}

git_in() {
  local dir="$1"
  shift
  git -C "${dir}" -c "safe.directory=${dir}" "$@"
}

git_net() {
  local dir="$1"
  shift
  timeout -k 5 120 git -C "${dir}" -c "safe.directory=${dir}" "$@"
}

read_deployed() {
  local key="$1"
  local line
  if [[ ! -f "${VERSION_FILE}" ]]; then
    printf ''
    return 0
  fi
  while IFS= read -r line || [[ -n "${line}" ]]; do
    case "${line}" in
      "${key}="*)
        printf '%s' "${line#*=}"
        return 0
        ;;
    esac
  done < "${VERSION_FILE}"
  printf ''
}

write_deployed() {
  local key="$1"
  local value="$2"
  local backend frontend tmp
  backend="$(read_deployed backend)"
  frontend="$(read_deployed frontend)"
  case "${key}" in
    backend) backend="${value}" ;;
    frontend) frontend="${value}" ;;
    *)
      log "未知版本键: ${key}"
      return 1
      ;;
  esac
  tmp="$(mktemp "${DEPLOY_DIR}/version.XXXXXX")" || return 1
  if ! printf 'backend=%s\nfrontend=%s\n' "${backend}" "${frontend}" > "${tmp}"; then
    rm -f "${tmp}"
    log "写入临时版本文件失败"
    return 1
  fi
  if ! mv -f "${tmp}" "${VERSION_FILE}"; then
    rm -f "${tmp}"
    log "替换 ${VERSION_FILE} 失败"
    return 1
  fi
  log "已更新 ${VERSION_FILE}: ${key}=${value}"
}

latest_from_ls_remote() {
  local remote="$1"
  local line name
  local -a names=()
  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    name="${line##*refs/tags/}"
    name="${name%%^*}"
    [[ "${name}" =~ ^v[0-9] ]] || continue
    names+=("${name}")
  done <<< "${remote}"
  if ((${#names[@]} == 0)); then
    printf ''
    return 0
  fi
  printf '%s\n' "${names[@]}" | sort -V | tail -n 1
}

detect_latest_tag() {
  local dir="$1"
  local remote=""
  if ! remote="$(retry git_net "${dir}" ls-remote --tags origin 'v*')"; then
    return 1
  fi
  latest_from_ls_remote "${remote}"
}

valid_tag() {
  [[ "$1" =~ ^v[0-9A-Za-z._+-]+$ ]]
}

side_ok() {
  case "$1" in
    成功*|跳过*) return 0 ;;
    *) return 1 ;;
  esac
}

rollback_backend() {
  local old_tag="$1"
  if [[ -z "${old_tag}" ]]; then
    log "backend: 没有旧 tag，无法回滚"
    printf '%s' "无法回滚（无旧 tag）"
    return 0
  fi
  if ! git_in "${BACKEND_DIR}" checkout -f "${old_tag}" >>"${LOG_FILE}" 2>&1; then
    log "backend: 回滚 checkout ${old_tag} 失败"
    printf '%s' "回滚失败（checkout ${old_tag} 失败）"
    return 0
  fi
  if ! systemctl restart xncagent >>"${LOG_FILE}" 2>&1; then
    log "backend: 回滚后 systemctl restart xncagent 失败"
    printf '%s' "回滚失败（已 checkout ${old_tag}，restart 失败）"
    return 0
  fi
  sleep 5
  if curl -sf --max-time 10 -o /dev/null "${BACKEND_HEALTH}"; then
    log "backend: 已回滚到 ${old_tag}，健康检查通过"
    printf '%s' "已回滚到 ${old_tag}，健康检查通过"
    return 0
  fi
  log "backend: 已 checkout ${old_tag} 并 restart，健康检查仍失败"
  printf '%s' "已回滚到 ${old_tag}，但健康检查仍失败"
  return 0
}

deploy_backend() {
  local new_tag="$1"
  local old_tag rb
  old_tag="$(read_deployed backend)"
  if [[ "${new_tag}" == "${old_tag}" ]]; then
    BACKEND_STATUS="跳过（已是 ${new_tag}）"
    log "backend 已是 ${new_tag}，跳过"
    return 0
  fi
  if ! valid_tag "${new_tag}"; then
    BACKEND_STATUS="失败（非法 tag ${new_tag}，未切换）"
    log "backend: 拒绝非法 tag ${new_tag}"
    return 0
  fi
  BACKEND_STATUS="进行中（${old_tag:-<空>} -> ${new_tag}）"
  log "backend 开始部署: ${old_tag:-<空>} -> ${new_tag}"

  if ! retry git_net "${BACKEND_DIR}" fetch --tags origin >>"${LOG_FILE}"; then
    BACKEND_STATUS="失败（git fetch 失败，仍为 ${old_tag:-<空>}）"
    return 0
  fi
  if ! git_in "${BACKEND_DIR}" checkout -f "${new_tag}" >>"${LOG_FILE}" 2>&1; then
    BACKEND_STATUS="失败（git checkout ${new_tag} 失败，仍为 ${old_tag:-<空>}）"
    return 0
  fi

  log "backend 已 checkout ${new_tag}，重启 xncagent"
  if ! systemctl restart xncagent >>"${LOG_FILE}" 2>&1; then
    log "backend: systemctl restart xncagent 失败，开始回滚"
    rb="$(rollback_backend "${old_tag}")"
    BACKEND_STATUS="失败（restart 失败，${rb}）"
    return 0
  fi
  sleep 5
  if ! curl -sf --max-time 10 -o /dev/null "${BACKEND_HEALTH}"; then
    log "backend: 健康检查 ${BACKEND_HEALTH} 失败，开始回滚"
    rb="$(rollback_backend "${old_tag}")"
    BACKEND_STATUS="失败（健康检查失败，${rb}）"
    return 0
  fi

  if ! write_deployed backend "${new_tag}"; then
    BACKEND_STATUS="失败（已是 ${new_tag} 且健康检查通过，但写入版本文件失败）"
    return 0
  fi
  BACKEND_STATUS="成功（${new_tag}）"
  log "backend 部署成功 ${new_tag}"
  return 0
}

# 软链恢复成功才清掉 FE_ROLLBACK_LINK，失败则留给退出陷阱再试一次。
finish_frontend_rollback() {
  local old_link="$1"
  local old_tag="$2"
  local why="$3"
  local git_rb=""
  if restore_frontend_link "${old_link}"; then
    FE_ROLLBACK_LINK=""
  fi
  git_rb="$(rollback_frontend_git "${old_tag}")"
  if [[ -z "${FE_ROLLBACK_LINK}" ]]; then
    FRONTEND_STATUS="失败（${why}，软链已切回 ${old_link}，${git_rb}）"
  else
    FRONTEND_STATUS="失败（${why}，软链切回 ${old_link} 失败，${git_rb}）"
  fi
}

rollback_frontend_git() {
  local old_tag="$1"
  if [[ -z "${old_tag}" ]]; then
    log "frontend: 没有旧 tag，跳过代码回滚"
    printf '%s' "无旧 tag 可回滚代码"
    return 0
  fi
  if git_in "${FRONTEND_DIR}" checkout -f "${old_tag}" >>"${LOG_FILE}" 2>&1; then
    log "frontend: 代码已回到 ${old_tag}"
    printf '%s' "代码已回到 ${old_tag}"
    return 0
  fi
  log "frontend: 代码回滚到 ${old_tag} 失败"
  printf '%s' "代码回滚到 ${old_tag} 失败"
  return 0
}

npm_install() {
  (
    cd "${FRONTEND_DIR}" || exit 1
    # 不用 npm ci。去掉 NODE_ENV，避免 production 下跳过 vite / typescript。
    env -u NODE_ENV npm install --no-audit --no-fund
  ) >>"${LOG_FILE}" 2>&1
}

npm_build() {
  (
    cd "${FRONTEND_DIR}" || exit 1
    env -u NODE_ENV npm run build
  ) >>"${LOG_FILE}" 2>&1
}

verify_frontend() {
  local release_abs="$1"
  local body code js line found=""
  body="$(mktemp)"
  code="$(curl -sS -o "${body}" -w '%{http_code}' --max-time 10 "${FRONTEND_HEALTH}" || true)"
  if [[ "${code}" != "200" ]]; then
    log "frontend: ${FRONTEND_HEALTH} 返回 ${code:-连接失败}，期望 200"
    rm -f "${body}"
    return 1
  fi
  js=""
  while IFS= read -r line; do
    js="${line}"
    break
  done < <(grep -oE 'index-[A-Za-z0-9._-]+\.js' "${body}" || true)
  rm -f "${body}"
  if [[ -z "${js}" ]]; then
    log "frontend: 首页 HTML 没有引用 index-*.js"
    return 1
  fi
  found="$(find "${release_abs}" -type f -name "${js}" -print -quit)"
  if [[ -z "${found}" ]]; then
    log "frontend: ${js} 不在新产物目录 ${release_abs}"
    return 1
  fi
  log "frontend: 健康检查通过，${js} 位于 ${found}"
  return 0
}

cleanup_frontend_releases() {
  local current="" name n
  local -a newest=()
  local -A keep=()
  current="$(readlink "${FRONTEND_DIR}/dist" || true)"
  current="$(basename "${current}")"
  mkdir -p "${FRONTEND_DIR}/releases"
  # %T@ 是修改时间。最近 = 刚发布的目录，不是版本号最大。
  while IFS=$'\t' read -r _ name; do
    [[ -n "${name}" ]] || continue
    case "${name}" in
      dist-*) newest+=("${name}") ;;
      *) log "frontend: 跳过非产物目录 ${name}" ;;
    esac
  done < <(find "${FRONTEND_DIR}/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@\t%f\n' | sort -nr)

  local kept=0
  if [[ -n "${current}" && -d "${FRONTEND_DIR}/releases/${current}" ]]; then
    keep["${current}"]=1
    kept=1
  fi
  if ((${#newest[@]} > 0)); then
    for n in "${newest[@]}"; do
      if [[ -n "${keep[${n}]:-}" ]]; then
        continue
      fi
      if (( kept >= 3 )); then
        break
      fi
      keep["${n}"]=1
      kept=$((kept + 1))
    done
    for n in "${newest[@]}"; do
      if [[ -n "${keep[${n}]:-}" ]]; then
        continue
      fi
      if ! rm -rf "${FRONTEND_DIR}/releases/${n}"; then
        log "frontend: 删除 releases/${n} 失败"
        return 1
      fi
      log "frontend: 已删除旧产物 releases/${n}"
    done
  fi
  log "frontend: releases 保留 ${kept} 个"
}

deploy_frontend() {
  local new_tag="$1"
  local old_tag="" old_link="" release_rel="" release_abs="" git_rb=""
  old_tag="$(read_deployed frontend)"
  if [[ "${new_tag}" == "${old_tag}" ]]; then
    FRONTEND_STATUS="跳过（已是 ${new_tag}）"
    log "frontend 已是 ${new_tag}，跳过"
    return 0
  fi
  if ! valid_tag "${new_tag}"; then
    FRONTEND_STATUS="失败（非法 tag ${new_tag}，未切换）"
    log "frontend: 拒绝非法 tag ${new_tag}"
    return 0
  fi
  FRONTEND_STATUS="进行中（${old_tag:-<空>} -> ${new_tag}）"
  log "frontend 开始部署: ${old_tag:-<空>} -> ${new_tag}"

  if ! retry git_net "${FRONTEND_DIR}" fetch --tags origin >>"${LOG_FILE}"; then
    FRONTEND_STATUS="失败（git fetch 失败，仍为 ${old_tag:-<空>}）"
    return 0
  fi
  if ! git_in "${FRONTEND_DIR}" checkout -f "${new_tag}" >>"${LOG_FILE}" 2>&1; then
    FRONTEND_STATUS="失败（git checkout ${new_tag} 失败，仍为 ${old_tag:-<空>}）"
    return 0
  fi
  if [[ ! -L "${FRONTEND_DIR}/dist" ]]; then
    git_rb="$(rollback_frontend_git "${old_tag}")"
    FRONTEND_STATUS="失败（dist 不是软链，未构建，${git_rb}）"
    return 0
  fi

  log "frontend: npm install --no-audit --no-fund"
  if ! npm_install; then
    git_rb="$(rollback_frontend_git "${old_tag}")"
    FRONTEND_STATUS="失败（npm install 失败，软链未切换，${git_rb}）"
    return 0
  fi

  # 先记下旧目标，再摘掉软链。若直接对着软链 build，vite 会清空上一版产物。
  old_link="$(readlink "${FRONTEND_DIR}/dist")"
  log "frontend: 切换前 dist -> ${old_link}"
  if [[ -z "${old_link}" ]]; then
    git_rb="$(rollback_frontend_git "${old_tag}")"
    FRONTEND_STATUS="失败（readlink dist 为空，未切换，${git_rb}）"
    return 0
  fi

  FE_ROLLBACK_LINK="${old_link}"
  if ! rm -f "${FRONTEND_DIR}/dist"; then
    FE_ROLLBACK_LINK=""
    git_rb="$(rollback_frontend_git "${old_tag}")"
    FRONTEND_STATUS="失败（无法摘掉 dist 软链，未构建，${git_rb}）"
    return 0
  fi

  log "frontend: npm run build"
  if ! npm_build; then
    finish_frontend_rollback "${old_link}" "${old_tag}" "构建失败"
    return 0
  fi
  if [[ ! -d "${FRONTEND_DIR}/dist" || -L "${FRONTEND_DIR}/dist" ]]; then
    finish_frontend_rollback "${old_link}" "${old_tag}" "构建后 dist 不是真实目录"
    return 0
  fi

  release_rel="releases/dist-${new_tag}"
  release_abs="${FRONTEND_DIR}/${release_rel}"
  case "${release_rel}" in
    releases/dist-v*) ;;
    *)
      finish_frontend_rollback "${old_link}" "${old_tag}" "产物路径非法"
      return 0
      ;;
  esac
  # 切换前的目录和即将写入的目录是同一个时，不能先删掉它。
  if [[ "${old_link}" == "${release_rel}" || "${old_link}" == "${release_abs}" ]]; then
    finish_frontend_rollback "${old_link}" "${old_tag}" "新产物目录与切换前目录相同"
    return 0
  fi
  mkdir -p "${FRONTEND_DIR}/releases"
  if ! rm -rf "${release_abs}"; then
    finish_frontend_rollback "${old_link}" "${old_tag}" "清理同名旧产物失败"
    return 0
  fi
  if ! mv "${FRONTEND_DIR}/dist" "${release_abs}"; then
    finish_frontend_rollback "${old_link}" "${old_tag}" "移动产物失败"
    return 0
  fi
  if ! ln -sfn "${release_rel}" "${FRONTEND_DIR}/dist"; then
    finish_frontend_rollback "${old_link}" "${old_tag}" "切换软链失败"
    return 0
  fi
  log "frontend: 已切换 dist -> ${release_rel}"

  if ! verify_frontend "${release_abs}"; then
    if ln -sfn "${old_link}" "${FRONTEND_DIR}/dist"; then
      log "frontend: 健康检查失败，软链已切回 ${old_link}"
      FE_ROLLBACK_LINK=""
      git_rb="$(rollback_frontend_git "${old_tag}")"
      FRONTEND_STATUS="失败（健康检查失败，软链已切回 ${old_link}，${git_rb}）"
    else
      log "frontend: 健康检查失败，且软链切回 ${old_link} 失败"
      git_rb="$(rollback_frontend_git "${old_tag}")"
      FRONTEND_STATUS="失败（健康检查失败，软链切回 ${old_link} 失败，${git_rb}）"
    fi
    return 0
  fi

  FE_ROLLBACK_LINK=""
  if ! write_deployed frontend "${new_tag}"; then
    FRONTEND_STATUS="失败（页面已切到 ${new_tag}，但写入版本文件失败）"
    return 0
  fi
  if ! cleanup_frontend_releases; then
    log "frontend: 清理旧产物失败，本次发布仍视为成功"
  fi
  FRONTEND_STATUS="成功（${new_tag}）"
  log "frontend 部署成功 ${new_tag}"
  return 0
}

notify() {
  local text="$1"
  NOTIFIED=1
  if [[ -z "${WEBHOOK_URL:-}" ]]; then
    log "未设置 WEBHOOK_URL，跳过通知"
    return 0
  fi
  if curl --max-time 10 -fsS -o /dev/null -X POST \
    -H "Content-Type: text/plain; charset=utf-8" \
    --data-binary "${text}" \
    "${WEBHOOK_URL}" 2>/dev/null; then
    log "已发送部署通知"
  else
    log "发送部署通知失败（不影响部署结果）"
  fi
  return 0
}

main() {
  local latest="" rc=0 title=""
  DEPLOY_STARTED=1
  log "======== 开始部署 ========"

  local dir
  for dir in "${BACKEND_DIR}" "${FRONTEND_DIR}"; do
    if [[ ! -d "${dir}/.git" ]]; then
      log "找不到 git 仓库: ${dir}"
      BACKEND_STATUS="失败（仓库不存在或不是 git 仓库）"
      FRONTEND_STATUS="失败（仓库不存在或不是 git 仓库）"
      notify "$(printf '%s\n' "XNC 部署失败" "backend: ${BACKEND_STATUS}" "frontend: ${FRONTEND_STATUS}")"
      exit 1
    fi
  done

  if ! latest="$(detect_latest_tag "${BACKEND_DIR}")"; then
    BACKEND_STATUS="失败（获取远端 tag 失败，未切换）"
  elif [[ -z "${latest}" ]]; then
    BACKEND_STATUS="失败（远端没有 v* tag，未切换）"
  else
    deploy_backend "${latest}"
  fi

  if ! latest="$(detect_latest_tag "${FRONTEND_DIR}")"; then
    FRONTEND_STATUS="失败（获取远端 tag 失败，未切换）"
  elif [[ -z "${latest}" ]]; then
    FRONTEND_STATUS="失败（远端没有 v* tag，未切换）"
  else
    deploy_frontend "${latest}"
  fi

  log "部署结束 backend=[${BACKEND_STATUS}] frontend=[${FRONTEND_STATUS}]"
  if ! side_ok "${BACKEND_STATUS}" || ! side_ok "${FRONTEND_STATUS}"; then
    rc=1
    title="XNC 部署失败"
  else
    title="XNC 部署成功"
  fi
  if [[ "${BACKEND_STATUS}" == 跳过* && "${FRONTEND_STATUS}" == 跳过* ]]; then
    log "两侧都无需部署，不发通知"
  else
    notify "$(printf '%s\n' "${title}" "backend: ${BACKEND_STATUS}" "frontend: ${FRONTEND_STATUS}")"
  fi
  exit "${rc}"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  if [[ "$(id -u)" -ne 0 ]]; then
    printf '%s\n' "[失败] 请用 root 执行" >&2
    exit 1
  fi

  mkdir -p "${DEPLOY_DIR}"
  touch "${LOG_FILE}"
  trap on_err ERR
  trap cleanup_on_exit EXIT

  # 锁住 /opt/xnc/deploy/.lock，直到本进程退出。拿不到锁就退出 0。
  exec 9>>"${LOCK_FILE}"
  if ! flock -n 9; then
    log "已有部署在执行，本次退出"
    exit 0
  fi

  main
fi
