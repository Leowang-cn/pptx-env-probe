"""启动入口：自己读 PORT，不依赖平台如何替换端口占位符。

部署后台的文档写 `$PORT`，但已部署项目在界面上显示成 `{PORT}`，
两种约定无法从外部确定。由本脚本直接读环境变量，绕开这个不确定性。
"""

import os
import sys
from pathlib import Path

PROBE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROBE_DIR))

import uvicorn  # noqa: E402


def main() -> None:
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    print(f"[probe] starting on {host}:{port}", flush=True)
    uvicorn.run("main:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
