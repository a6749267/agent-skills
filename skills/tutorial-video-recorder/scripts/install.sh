#!/usr/bin/env bash
# tutorial-video-recorder 依赖自检 + 安装
#
#   ./install.sh            # 只检查, 缺什么报什么
#   ./install.sh --fix      # 尝试安装缺的
#
# 框架无关: 解释器探测走"优先级链 + 能 import 就用"的思路,
# 不假设依赖装在某个 agent 的 venv 里。
set -uo pipefail

FIX=0
[ "${1:-}" = "--fix" ] && FIX=1

# ── 真实家目录 ────────────────────────────────────────────────
# 🚨 agent 沙箱常把 $HOME 劫持成自己的 home(如 .../home), 真家目录在别处。
#    直接问操作系统要(pwd.getpwuid) —— 不受 HOME 环境变量影响。
REAL_HOME="$(python3 -c 'import pwd,os;print(pwd.getpwuid(os.getuid()).pw_dir)' 2>/dev/null || echo "$HOME")"

# ── 选解释器 ──────────────────────────────────────────────────
# ⚠ 别只信 $PY/python3 —— 依赖常装在别的解释器/venv 里。
#   依次尝试: $TUTORIAL_PYTHON → $PY → $AGENT_HOME 下的各种 venv → python3
#   用第一个能 import playwright 的。
pick_python() {
  local cands=()
  [ -n "${TUTORIAL_PYTHON:-}" ] && cands+=("$TUTORIAL_PYTHON")
  [ -n "${PY:-}" ] && cands+=("$PY")
  if [ -n "${AGENT_HOME:-}" ]; then
    cands+=(
      "$AGENT_HOME/venv/bin/python"
      "$AGENT_HOME/hermes-agent/venv/bin/python"
      "$AGENT_HOME/agent/venv/bin/python"
      "$AGENT_HOME/.venv/bin/python"
    )
    for g in "$AGENT_HOME"/*/venv/bin/python "$AGENT_HOME"/*/.venv/bin/python; do
      [ -x "$g" ] && cands+=("$g")
    done
  fi
  cands+=("python3" "python")

  local FIRST_OK=""
  for c in "${cands[@]}"; do
    command -v "$c" >/dev/null 2>&1 || [ -x "$c" ] || continue
    if "$c" -c "import playwright" >/dev/null 2>&1; then echo "$c"; return 0; fi
    [ -z "$FIRST_OK" ] && FIRST_OK="$c"
  done
  echo "${FIRST_OK:-python3}"
}

PY="$(pick_python)"
MISS=0

say()  { printf '%s\n' "$*"; }
ok()   { printf '  ✅ %s\n' "$*"; }
bad()  { printf '  ❌ %s\n' "$*"; MISS=1; }

say "=== 1. Python 运行环境 ==="
ok "$PY → $($PY --version 2>&1)"

say ""
say "=== 2. Python 包 ==="
for mod in playwright Vision Quartz PIL numpy; do
  if $PY -c "import $mod" >/dev/null 2>&1; then
    ok "$mod"
  else
    bad "$mod 未安装"
    [ $FIX -eq 1 ] && $PY -m pip install -q "$mod" 2>/dev/null && ok "$mod 已安装"
  fi
done
if $PY -c "import psycopg2" >/dev/null 2>&1; then
  ok "psycopg2 (灌库可选)"
else
  printf '  ⚠️  psycopg2 未安装 —— 只有需要灌数据库业务数据时才必需\n'
fi

say ""
say "=== 3. 系统工具 ==="
for c in ffmpeg ffprobe; do
  if command -v "$c" >/dev/null 2>&1; then
    ok "$c → $(command -v $c)"
  else
    bad "$c 缺失 (brew install ffmpeg)"
  fi
done

say ""
say "=== 4. Playwright Chromium (全量, 不是 headless shell) ==="
# record_video 的 screencast 在 headless_shell 上不支持 —— 必须是完整 chromium
# 用 REAL_HOME(见下方 pwd.getpwuid) 而不是 $HOME —— 后者可能是 agent 沙箱 home
CHROMIUM=""
for d in "${PLAYWRIGHT_BROWSERS_PATH:-/nonexistent}"/chromium-* \
         "$REAL_HOME/Library/Caches/ms-playwright"/chromium-* \
         "$REAL_HOME/.cache/ms-playwright"/chromium-* \
         "$HOME/Library/Caches/ms-playwright"/chromium-* \
         "$HOME/.cache/ms-playwright"/chromium-* \
         "${AGENT_HOME:-/nonexistent}"/home/Library/Caches/ms-playwright/chromium-* \
         "${AGENT_HOME:-/nonexistent}"/Library/Caches/ms-playwright/chromium-*; do
  # 🚨 排除 headless_shell —— screencast 不支持
  case "$d" in *headless*) continue;; esac
  [ -d "$d" ] && { CHROMIUM="$d"; break; }
done
if [ -n "$CHROMIUM" ]; then
  ok "$CHROMIUM"
else
  bad "没找到完整 chromium (playwright install chromium)"
  [ $FIX -eq 1 ] && $PY -m playwright install chromium 2>&1 | tail -3
fi

say ""
say "=== 5. TTS (可选, 不做配音可跳过) ==="
KEY_NAME="MINIMAX_CN_API_KEY"
# 🚨 不要依赖 $HOME —— agent 沙箱常把 HOME 劫持成自己的 home,
#    真正的凭据文件在别处。见 pitfalls E1。
#    优先问操作系统要真实家目录(pwd.getpwuid), 再扫家目录下的框架隐藏目录。
#    (REAL_HOME 已在文件顶部定义)
ENV_FILE=""
for c in "${TUTORIAL_ENV_FILE:-}" "${AGENT_ENV_FILE:-}" "${AGENT_HOME:-}/.env" "$REAL_HOME/.env"; do
  [ -n "$c" ] && [ -f "$c" ] && { ENV_FILE="$c"; break; }
done
# 家目录下的框架隐藏目录(只有真含 .env 的才算)
if [ -z "$ENV_FILE" ]; then
  for c in "$REAL_HOME"/.*/.env; do
    [ -f "$c" ] && { ENV_FILE="$c"; break; }
  done
fi
if [ -n "$ENV_FILE" ] && grep -q "^${KEY_NAME}=" "$ENV_FILE" 2>/dev/null; then
  ok "$KEY_NAME 已配置 ($ENV_FILE)"
else
  printf '  ⚠️  %s 未配置 —— 没有旁白就只能出无声视频\n' "$KEY_NAME"
  [ -n "$ENV_FILE" ] && printf '      (凭据文件找到于 %s, 但没有这个键)\n' "$ENV_FILE"
fi

say ""
if [ $MISS -eq 0 ]; then
  say "✅ 依赖齐全, 可以开录"
else
  say "❌ 有缺失项 —— 跑 ./install.sh --fix 尝试补齐"
  MISS=1
fi

say ""
say "=== 6. 管线自检 ==="
if "$PY" "$(dirname "${BASH_SOURCE[0]}")/selfcheck.py"; then
  say "✅ 自检通过"
else
  say "❌ 自检有失败项 —— 先修好再录(录坏了不会报错, 只会画面里出现假数据)"
  MISS=1
fi

exit $MISS
