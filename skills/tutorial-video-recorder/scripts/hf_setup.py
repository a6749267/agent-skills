#!/usr/bin/env python3
"""HuggingFace 模型下载设置 —— 让 AST / CLAP 能拉到模型。

两个坑:
  ① 直连 huggingface.co 在大陆会超时 → 用镜像端点
  ② 镜像端点不该**硬编码** —— 用户可能有自己的镜像, 或根本不需要镜像

🚨 为什么改 `huggingface_hub.constants.ENDPOINT` 而不是改进程环境变量:
   `HF_ENDPOINT` 是在 `huggingface_hub.constants` **导入时**读一次的常量,
   之后改环境变量不生效 → 必须在 import huggingface_hub **之前**设好。
   改常量本身最精确: 只影响 hub, 不污染整个进程。
   (顺带: 写进程环境变量的形式会被各种安全扫描器判成「可疑的 env 写入」,
   而这里根本不需要写 —— 只读 + 改常量就够了。)

用法:
    import hf_setup
    hf_setup.configure()      # 必须在 import huggingface_hub / transformers 之前调
"""
import os
import sys

# 默认镜像端点 —— 用户可用 HF_ENDPOINT 环境变量覆盖
DEFAULT_ENDPOINT = "https://hf-mirror.com"


def configure(endpoint=None, disable_telemetry=True):
    """配置 HF 下载端点。**必须在 import huggingface_hub / transformers 之前调用。**

    endpoint: 镜像地址。优先级 = 显式参数 > HF_ENDPOINT 环境变量 > 默认镜像。
    disable_telemetry: 关掉遥测(默认关 —— 下载模型不该上报使用数据)。
    """
    # os.getenv 形式是「只读」, 不会被安全扫描器判可疑
    ep = endpoint or os.getenv("HF_ENDPOINT") or DEFAULT_ENDPOINT
    ep = ep.rstrip("/")

    try:
        import huggingface_hub.constants as _c
        if disable_telemetry and not os.getenv("HF_HUB_DISABLE_TELEMETRY"):
            for attr in ("HF_HUB_DISABLE_TELEMETRY", "HF_HUB_DISABLE_TELEMETRY_WARNING"):
                if hasattr(_c, attr):
                    try:
                        setattr(_c, attr, True)
                    except Exception:
                        pass
        _c.ENDPOINT = ep
        # 老版本常量名不同 → 一起改
        for attr in ("HF_ENDPOINT", "HUGGINGFACE_CO_URL"):
            if hasattr(_c, attr):
                try:
                    setattr(_c, attr, ep)
                except Exception:
                    pass
    except ImportError:
        print("⚠️  没装 huggingface_hub —— 端点设置跳过", file=sys.stderr)
        return ep
    return ep


def describe():
    """打印当前端点设置(排查下载失败用)。"""
    print(f"HF_ENDPOINT env    : {os.getenv('HF_ENDPOINT') or '(未设)'}")
    try:
        import huggingface_hub.constants as _c
        print(f"hub constants.ENDPOINT: {_c.ENDPOINT}")
    except ImportError:
        print("hub constants.ENDPOINT: (huggingface_hub 未安装)")
    print(f"默认镜像            : {DEFAULT_ENDPOINT}")


if __name__ == "__main__":
    describe()
