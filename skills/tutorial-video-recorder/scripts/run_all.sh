#!/usr/bin/env bash
# 一键跑完整条管线
#
#   ./run_all.sh project.json
#
# 步骤: TTS → 录制 → 合成 → 验收
# 任一步失败即停。
set -euo pipefail

CFG="${1:-project.json}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 选解释器: 优先能 import playwright 的那个(依赖常装在 hermes venv 里)
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

echo "=== 0. 依赖自检 ==="
bash "$HERE/install.sh"

WORK=$(python3 -c "import json,sys;print(json.load(open('$CFG'))['paths']['work_dir'])")
SCRIPT="${SCRIPT:-script.json}"

echo ""
echo "=== 1. 生成旁白 ==="
$PY "$HERE/tts.py" --script "$SCRIPT" --out-dir "$WORK/audio"

echo ""
echo "=== 2. 录制 ==="
$PY "$HERE/recorder.py" --config "$CFG"

echo ""
echo "=== 3. 合成 (横屏 + 字幕) ==="
$PY "$HERE/compose.py" --config "$CFG"

echo ""
echo "=== 4. 验收 (针对带字幕的成片本身) ==="
$PY "$HERE/verify.py" --config "$CFG" --video "$WORK/out/final_sub.mp4" --amplify

echo ""
echo "✅ 管线跑完"
echo ""
echo "⚠️  验收通过 ≠ 可以发。还要人工确认:"
echo "   ① 数字自洽(读到的金额 vs 源头算式)"
echo "   ② 画面是不是空态(模型判断模块在不在)"
echo "   ③ 字幕断句读起来是否自然(不通顺就改文案, 别用算法硬凑)"
echo "   ④ 字幕有没有遮住关键数字"
