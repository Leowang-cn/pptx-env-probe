"""宿主机侧调用：用 soffice 容器把 PPTX 转成 PDF。

沿用 chromium-runner 的调用模式：临时工作目录 -> docker run -> 读 result.json。
本文件既是可运行脚本（Python 调用），也可被 FastAPI 直接 import。

命令行用法：
    python3 host_convert.py 输入.pptx 输出.pdf
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

IMAGE = os.getenv("SOFFICE_RUNNER_IMAGE", "olmath-soffice-runner:bookworm")
CONTAINER_SCRIPT = Path(__file__).resolve().parent / "container_convert.py"


class ConvertError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def convert(source: Path, destination: Path, target_format: str = "pdf",
            timeout_s: int = 300) -> Path:
    """把 source 转换并写到 destination。失败抛 ConvertError，带明确错误码。"""
    if not CONTAINER_SCRIPT.is_file():
        raise ConvertError("script_missing", f"缺少容器脚本：{CONTAINER_SCRIPT}")
    if not source.is_file():
        raise ConvertError("input_missing", f"输入文件不存在：{source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="soffice-convert-"))
    try:
        # 容器内是非 root（uid 1000），挂载目录必须归它所有
        _make_writable_for_container(work)
        staged = work / source.name
        shutil.copy2(source, staged)
        # 脚本本身也要能被容器内用户读取
        staged_script = work / "container_convert.py"
        shutil.copy2(CONTAINER_SCRIPT, staged_script)
        os.chmod(staged_script, 0o644)

        argv = [
            "docker", "run", "--rm",
            # 默认 64MB，LibreOffice 处理较大演示文稿会崩
            "--shm-size=1g",
            "--network", "bridge",
            "-v", f"{work}:/work",
            "-e", f"CONVERT_SOURCE={staged.name}",
            "-e", f"CONVERT_FORMAT={target_format}",
            "-e", f"CONVERT_TIMEOUT_S={timeout_s}",
            IMAGE,
            "python3", "/work/container_convert.py",
        ]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=timeout_s + 120, check=False
            )
        except subprocess.TimeoutExpired as error:
            raise ConvertError("convert_timeout", "LibreOffice 容器执行超时") from error

        if proc.stderr:
            print(f"[soffice-convert] {proc.stderr.strip()}", flush=True)

        result = _read_result(work / "result.json")
        if not result.get("ok"):
            raise ConvertError(
                result.get("code", "convert_failed"),
                result.get("message", "LibreOffice 转换失败"),
            )

        produced = work / result.get("file", f"{staged.stem}.{target_format}")
        if not produced.is_file() or produced.stat().st_size == 0:
            raise ConvertError("convert_no_output", "容器没有返回有效的转换结果")

        shutil.move(str(produced), str(destination))
    finally:
        shutil.rmtree(work, ignore_errors=True)

    return destination


def _read_result(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConvertError(
            "convert_failed",
            "转换容器未返回结果，请查看服务日志中 [soffice-convert] 的输出",
        ) from error


def _make_writable_for_container(path: Path) -> None:
    """容器内 uid 1000 需要写权限。chown 失败时退回 0777，保证仍能转换。"""
    try:
        os.chown(path, 1000, 1000)
    except (PermissionError, OSError):
        os.chmod(path, 0o777)


def main() -> int:
    if len(sys.argv) != 3:
        print("用法: python3 host_convert.py 输入.pptx 输出.pdf", file=sys.stderr)
        return 2

    source = Path(sys.argv[1]).resolve()
    destination = Path(sys.argv[2]).resolve()
    fmt = destination.suffix.lstrip(".") or "pdf"

    try:
        convert(source, destination, target_format=fmt)
    except ConvertError as error:
        print(f"转换失败 [{error.code}]: {error}", file=sys.stderr)
        return 1

    size = destination.stat().st_size
    print(f"转换成功: {destination} ({size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
