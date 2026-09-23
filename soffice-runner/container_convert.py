"""容器内执行：把 /work 下的 PPTX 转换为 PDF。

在 soffice-runner 容器里运行，由 host_convert.py 调用。
结果写 result.json，日志写 stderr——与 chromium-runner 的约定一致，
这样宿主机侧能拿到明确的错误码而不是只有一句"容器失败了"。
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
from pathlib import Path

WORK = Path("/work")
OUT_RESULT = WORK / "result.json"


def fail(code: str, message: str) -> None:
    OUT_RESULT.write_text(
        json.dumps({"ok": False, "code": code, "message": message}, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"[convert] FAILED {code}: {message}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    source = WORK / os.getenv("CONVERT_SOURCE", "sample.pptx")
    if not source.is_file():
        fail("input_missing", f"输入文件不存在：{source}")

    target_format = os.getenv("CONVERT_FORMAT", "pdf").strip() or "pdf"
    timeout_s = int(os.getenv("CONVERT_TIMEOUT_S", "300"))

    # profile 必须落在可写目录；容器内以非 root 运行
    profile = WORK / "lo-profile"
    profile.mkdir(parents=True, exist_ok=True)

    # 转换前清掉同名旧产物，避免把上一次的结果误判为本次成功
    expected = source.with_suffix(f".{target_format}")
    expected.unlink(missing_ok=True)
    for stale in glob.glob(str(WORK / f".~lock.*#{source.name}#")):
        Path(stale).unlink(missing_ok=True)

    argv = [
        "soffice",
        "--headless",
        "--norestore",
        "--nolockcheck",
        "--nodefault",
        "--nofirststartwizard",
        f"-env:UserInstallation=file://{profile}",
        "--convert-to", target_format,
        "--outdir", str(WORK),
        str(source),
    ]
    print(f"[convert] {' '.join(argv)}", file=sys.stderr)

    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except subprocess.TimeoutExpired:
        fail("convert_timeout", f"转换超过 {timeout_s} 秒未完成")

    if proc.stdout:
        print(f"[convert] stdout: {proc.stdout.strip()}", file=sys.stderr)
    if proc.stderr:
        print(f"[convert] stderr: {proc.stderr.strip()}", file=sys.stderr)

    if not expected.is_file() or expected.stat().st_size == 0:
        # soffice 有时返回 0 但没产出文件，必须显式判失败
        fail(
            "convert_no_output",
            f"未生成 {expected.name}（soffice 退出码 {proc.returncode}）",
        )

    OUT_RESULT.write_text(
        json.dumps(
            {
                "ok": True,
                "file": expected.name,
                "size_bytes": expected.stat().st_size,
                "soffice_returncode": proc.returncode,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"[convert] OK {expected.name} {expected.stat().st_size} bytes", file=sys.stderr)


if __name__ == "__main__":
    main()
