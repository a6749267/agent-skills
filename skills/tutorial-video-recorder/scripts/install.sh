#!/usr/bin/env bash
# real-app-tutorial-video 依赖自检 + 安装
#
#   ./install.sh            # 只检查, 缺什么报什么
#   ./install.sh --fix      # 尝试安装缺的
set -uo pipefail

FIX=0
[ "${1:-}" = "--fix" ] && FIX=1

# ── 选解释器 ──────────────────────────────────────────────────
# ⚠ 别只信 $PY/python3 —— 依赖可能装在 hermes 的 venv 里。
#   依次尝试: $PY → $HERMES_HOME/hermes-agent/venv/bin/python → 当前 python3
#   哪个能 import playwright 就用哪个。
pick_python() {
  local cands=()
  [ -n "${PY:-}" ] && cands+=("$PY")
  [ -n "${HERMES_HOME:-}" ] && cands+=("$HERMES_HOME/hermes-agent/venv/bin/python")
  cands+=("python3" "python")
  for c in "${cands[@]}"; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c "import playwright" >/dev/null 2>&1; then echo "$c"; return 0; fi
      FIRST_OK="$c"
    fi
  done
  [ -n "${FIRST_OK:-}" ] && { echo "$FIRST_OK"; return 0; }
  echo "python3"
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
CHROMIUM=""
for d in "$HOME/Library/Caches/ms-playwright"/chromium-* \
         "$HOME/.cache/ms-playwright"/chromium-* \
         "${PLAYWRIGHT_BROWSERS_PATH:-/nonexistent}"/chromium-*; do
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
# 🚨 不要用 $HOME —— agent 环境把 HOME 劫持成 agent home,
#    真正的 .env 在 $HERMES_HOME/.env。见 pitfalls E1。
ENV_FILE=""
for c in "${HERMES_ENV:-}" "${HERMES_HOME:-}/.env" "$HOME/.env"; do
  [ -n "$c" ] && [ -f "$c" ] && { ENV_FILE="$c"; break; }
done
if [ -n "$ENV_FILE" ] && grep -q "^${KEY_NAME}=" "$ENV_FILE" 2>/dev/null; then
  ok "$KEY_NAME 已配置 ($ENV_FILE)"
else
  printf '  ⚠️  %s 未配置 —— 没有旁白就只能出无声视频\n' "$KEY_NAME"
  [ -n "$ENV_FILE" ] && printf '      (.env 找到于 %s, 但没有这个键)\n' "$ENV_FILE"
fi

say ""
if [ $MISS -eq 0 ]; then
  say "✅ 依赖齐全, 可以开录"
  exit 0
else
  say "❌ 有缺失项 —— 跑 ./install.sh --fix 尝试补齐"
  exit 1
fi
