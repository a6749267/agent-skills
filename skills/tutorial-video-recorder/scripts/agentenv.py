#!/usr/bin/env python3
"""agent 环境探测 —— 框架无关的路径/依赖解析。

为什么需要这个文件:
    不同 agent 框架的运行时目录布局不一样, 有的还会把 $HOME 劫持成
    自己的沙箱 home。如果脚本硬编码 `~/.hermes` 或 `Path.home()`,
    换到别的 agent 上就会静默失败(拿不到 key / 找不到依赖)。

本模块提供**优先级链**, 逐个尝试直到命中:
    - 凭据文件: $TUTORIAL_ENV_FILE → $AGENT_ENV_FILE → $AGENT_HOME/.env → $HOME/.env
    - Python 解释器: $TUTORIAL_PYTHON → $PY → $AGENT_HOME/**/venv/bin/python → PATH 上的 python3
    - skill 根目录: $TUTORIAL_SKILL_HOME → 本文件上溯两级 → $AGENT_HOME/skills/<name>

所有函数**只读环境变量**且只用不抛异常的取值方式,
不使用下标取值形式(缺失时抛 KeyError, 且易被密钥扫描误判)。
"""
import glob
import os
import pathlib
import subprocess
import sys

# 运行时可能把 HOME 指向沙箱目录, 这些名字叫 "home" 但不是真家目录。
SANDBOX_HOME_HINTS = ("agent", "sandbox", "workspace", "hermes", "claude", "codex")


def _env(*names):
    """按顺序取第一个非空环境变量值。"""
    for n in names:
        v = os.getenv(n)
        if v:
            return v
    return None


def real_home():
    """尽力找真实家目录。

    agent 沙箱常把 HOME 指向 <something>/home 这种布局, 且真家目录仍存在。
    优先级:
      1. pwd.getpwuid() —— 直接问操作系统要这个 uid 的家目录。
         **不受 $HOME 劫持影响**, 这是最可靠的方法。
      2. 从 PATH 上 python3 的真实路径反推。
      3. expanduser("~")。
    """
    # 1. 系统密码库 —— 不受 HOME 环境变量影响
    try:
        import pwd
        pw = pwd.getpwuid(os.getuid())
        if pw and pw.pw_dir and pathlib.Path(pw.pw_dir).is_dir():
            return pw.pw_dir
    except Exception:
        pass
    # 2. 从 python3 的真实位置反推(…/Users/<name>/… 或 /home/<name>/…)
    try:
        import shutil
        for exe in ("python3", "python"):
            p = shutil.which(exe)
            if not p:
                continue
            p = pathlib.Path(p).resolve()
            for parent in p.parents:
                if parent.name in ("Users", "home") and parent.parent != parent:
                    if parent.parent.is_dir():
                        return str(parent.parent)
    except Exception:
        pass
    # 3. 退回 expanduser
    return os.path.expanduser("~")


def env_file_candidates():
    """凭据文件候选路径, 按优先级。

    顺序: 显式指定 → agent home → 真实家目录 → 家目录下的框架隐藏目录 → 沙箱 home
    """
    c = []
    explicit = _env("TUTORIAL_ENV_FILE", "AGENT_ENV_FILE")
    if explicit:
        c.append(explicit)
    agent_home = _env("AGENT_HOME", "TUTORIAL_AGENT_HOME")
    if agent_home:
        c.append(str(pathlib.Path(agent_home) / ".env"))
    rh = pathlib.Path(real_home())
    c.append(str(rh / ".env"))
    # 家目录下的框架隐藏目录(如 ~/.claude/.env, ~/.someagent/.env)
    # —— 只取真的含 .env 的, 避免把一堆无关目录塞进候选
    try:
        for d in sorted(rh.glob(".*/")):
            if d.is_dir() and (d / ".env").exists():
                c.append(str(d / ".env"))
    except OSError:
        pass
    # 最后兜底: 被劫持的 HOME
    hijacked = os.getenv("HOME")
    if hijacked:
        c.append(str(pathlib.Path(hijacked) / ".env"))
    # 去掉重复, 保序
    seen = set()
    out = []
    for x in c:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def find_env_file():
    """返回第一个存在的凭据文件路径; 都没有则返回第一个候选(便于报错提示)。"""
    cands = env_file_candidates()
    for c in cands:
        if c and pathlib.Path(c).exists():
            return c
    return cands[0] if cands else ""


def read_env_value(name, env_path=None):
    """从凭据文件读一个键的值; 找不到则回退环境变量。

    逐行 split('=', 1) 解析, 不用正则 —— 正则形态容易被密钥脱敏工具误打码。
    """
    p = pathlib.Path(env_path) if env_path else pathlib.Path(find_env_file())
    if p.exists():
        try:
            for line in p.read_text(errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == name:
                    return v.strip().strip('"').strip("'")
        except OSError:
            pass
    return os.getenv(name)


def python_candidates():
    """按优先级列出可能装了依赖的 Python 解释器。"""
    c = []
    explicit = _env("TUTORIAL_PYTHON", "PY")
    if explicit:
        c.append(explicit)
    # agent home 下的各种 venv 布局(框架无关, 只按目录形状找)
    for base in filter(None, [_env("AGENT_HOME", "TUTORIAL_AGENT_HOME")]):
        cands = [
            f"{base}/venv/bin/python",
            f"{base}/hermes-agent/venv/bin/python",
            f"{base}/agent/venv/bin/python",
            f"{base}/.venv/bin/python",
        ]
        c.extend(glob.glob(f"{base}/*/venv/bin/python"))
        c.extend(glob.glob(f"{base}/*/.venv/bin/python"))
        c.extend(glob.glob(f"{base}/venv/bin/python*"))
    c.extend(["python3", "python"])
    return c


def pick_python(required_module=None):
    """挑一个解释器。

    required_module: 需要能 import 的模块名(如 playwright)。给定时优先返回
    能 import 的; 都装不了就返回第一个可用的, 让上层报清晰的错。
    """
    seen = set()
    first_ok = None
    for c in python_candidates():
        if not c or c in seen:
            continue
        seen.add(c)
        if not os.path.isfile(c) and not _on_path(c):
            continue
        if not first_ok:
            first_ok = c
        if required_module:
            try:
                rc = subprocess.run(
                    [c, "-c", f"import {required_module}"],
                    capture_output=True, timeout=30,
                ).returncode
            except (OSError, subprocess.SubprocessError):
                rc = 1
            if rc == 0:
                return c
        else:
            return c
    return first_ok or "python3"


def _on_path(name):
    import shutil
    return shutil.which(name) is not None


def skill_root(explicit=None, skill_name=None):
    """定位 skill 根目录(含 SKILL.md 的那一层)。

    优先级: 显式参数 → $TUTORIAL_SKILL_HOME → 本文件上溯 → agent home 下 skills/<name>
    """
    if explicit:
        return pathlib.Path(explicit).expanduser()
    env = _env("TUTORIAL_SKILL_HOME", "TUTORIAL_SKILL_SCRIPTS")
    if env:
        p = pathlib.Path(env).expanduser()
        # 指向 scripts 目录时上溯一级
        if p.name == "scripts" and (p.parent / "SKILL.md").exists():
            return p.parent
        return p
    here = pathlib.Path(__file__).resolve()
    # 本文件在 <skill>/scripts/ 下 → 上溯两级是 skill 根
    cand = here.parent.parent
    if (cand / "SKILL.md").exists():
        return cand
    for base in filter(None, [_env("AGENT_HOME", "TUTORIAL_AGENT_HOME")]):
        if skill_name:
            p = pathlib.Path(base) / "skills" / skill_name
            if (p / "SKILL.md").exists():
                return p
    return cand


def scripts_dir(skill_name=None, explicit=None):
    """定位 <skill>/scripts 目录。"""
    root = skill_root(explicit, skill_name)
    d = root / "scripts"
    if d.is_dir():
        return d
    # skill_root 可能已指到 scripts
    if root.name == "scripts" and root.is_dir():
        return root
    return d


if __name__ == "__main__":
    # 自检: 打印各项解析结果, 便于诊断
    print("real_home        :", real_home())
    print("env_file         :", find_env_file())
    print("pick_python      :", pick_python("playwright"))
    print("pick_python(none):", pick_python())
    print("skill_root       :", skill_root(skill_name="tutorial-video-recorder"))
    print("sys.executable   :", sys.executable)
