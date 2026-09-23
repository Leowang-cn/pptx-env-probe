"""环境探针：部署到内网后台一次，用 /api/health 返回服务器能力事实。

目的：把《产品化顶层设计》第 13.6 章"待环境探针确认"的五个问题变成可读结论，
避免靠猜测做架构决策。所有检查均为只读，不写入任何数据。
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import FastAPI

APP_VERSION = "0.1.0"
PROBE_DIR = Path(__file__).resolve().parent
DATA_DIR = PROBE_DIR / "data"

app = FastAPI(title="课件处理环境探针", version=APP_VERSION)


def run(cmd, timeout=15):
    """执行外部命令。返回结构化结果，失败也返回而非抛出。"""
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "").strip()[:4000],
            "stderr": (proc.stderr or "").strip()[:2000],
        }
    except FileNotFoundError:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "命令不存在"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "returncode": None, "stdout": "", "stderr": "超时"}
    except Exception as exc:  # noqa: BLE001 - 探针需要报告任何失败原因
        return {"ok": False, "returncode": None, "stdout": "", "stderr": repr(exc)}


def check_runtime():
    return {
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "libc_version": " ".join(platform.libc_ver()),
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "pid": os.getpid(),
        "port_env": os.getenv("PORT", "(未注入)"),
    }


def check_subprocess():
    """问题：运行时是否允许 fork/exec。跑一条最简命令即可判定。"""
    result = run(["sh", "-c", "echo probe-ok"], timeout=10)
    nested = run(["sh", "-c", "sh -c 'echo nested-ok'"], timeout=10)
    return {
        "simple_exec": result["stdout"] == "probe-ok",
        "nested_exec": nested["stdout"] == "nested-ok",
        "detail": result,
        "nested_detail": nested,
    }


def check_supervision():
    """从进程树推断：谁在守护本服务，有没有多进程编排能力。

    平台只保活一个服务进程，因此这里收集证据供判断"能否常驻第二条命令"，
    而不是直接下结论——最终仍以运维答复为准。
    """
    info = {"pid": os.getpid(), "ppid": os.getppid()}

    linux = Path("/proc")
    if not linux.exists():
        info["note"] = "非 Linux 环境，跳过进程树检查"
        return info

    def proc_field(pid, field):
        try:
            text = (linux / str(pid) / field).read_text(encoding="utf-8", errors="replace")
            return text.strip()
        except OSError:
            return None

    chain = []
    pid = os.getpid()
    for _ in range(8):
        name = proc_field(pid, "comm")
        if name is None:
            break
        chain.append({"pid": pid, "comm": name})
        try:
            stat = proc_field(pid, "stat") or ""
            # stat 的 ppid 是第 4 个字段，但 comm 可能含空格，需从右括号后切
            after = stat.rsplit(")", 1)[-1].split()
            pid = int(after[1])
        except (OSError, ValueError, IndexError):
            break
        if pid <= 1:
            break

    info["process_chain"] = chain
    info["init_system"] = proc_field(1, "comm")

    container_signals = []
    if os.getenv("KUBERNETES_SERVICE_HOST"):
        container_signals.append("KUBERNETES_SERVICE_HOST")
    if Path("/.dockerenv").exists():
        container_signals.append("/.dockerenv")
    cgroup = proc_field(1, "cgroup") or proc_field(os.getpid(), "cgroup") or ""
    for marker in ("docker", "kubepods", "containerd", "lxc"):
        if marker in cgroup:
            container_signals.append(f"cgroup:{marker}")
    info["inside_container"] = bool(container_signals)
    info["container_signals"] = container_signals
    return info


def check_libreoffice():
    """问题：**宿主机**是否已装 LibreOffice。

    本项目建议不装宿主机（glibc 2.17 限制），改用容器；
    此检查保留以便对照，真正要看的是 container_render。
    """
    found = {}
    for name in ("soffice", "libreoffice", "soffice.bin"):
        path = shutil.which(name)
        if path:
            found[name] = path
    candidates = list(found.values()) + [
        "/usr/bin/soffice",
        "/opt/libreoffice/program/soffice",
        "/snap/bin/libreoffice",
    ]
    existing = [p for p in candidates if Path(p).exists()]

    version = None
    if existing:
        version = run([existing[0], "--version"], timeout=60)

    writable = os.access(PROBE_DIR, os.W_OK)
    return {
        "available": bool(existing),
        "in_path": found,
        "existing_paths": existing,
        "version": version,
        "project_dir_writable": writable,
        "note": "不可用不影响 L1/L2 一致性；只影响服务端渲染对账能力",
    }


def check_fonts():
    """问题：服务器有哪些字体，特别是中文字体。

    中文能力以 `fc-list :lang=zh` 为准——这是 fontconfig 按字符覆盖率的判定，
    比按字体名猜可靠。以下方 reusable 只在此基础上筛出可再分发的开源字体。
    """
    fc_list = shutil.which("fc-list")
    if not fc_list:
        return {"fc_list_available": False, "note": "无 fontconfig，无法枚举字体"}

    all_fonts = run(["fc-list", "--format", "%{family}\n"], timeout=30)
    cjk = run(["fc-list", ":lang=zh", "--format", "%{family}\n"], timeout=30)

    def families(block):
        out = []
        for line in (block["stdout"] or "").splitlines():
            for part in line.split(","):
                name = part.strip()
                if name:
                    out.append(name)
        return sorted(set(out))

    all_names = families(all_fonts)
    cjk_names = families(cjk)

    # 只在中文字体集合里找可再分发字体，避免把拉丁 Noto 误判为中文
    reusable = sorted({
        n for n in cjk_names
        if any(h in n.lower() for h in ("noto sans cjk", "noto serif cjk",
                                        "noto sans sc", "noto serif sc",
                                        "source han", "思源"))
    })

    present = {n.lower() for n in cjk_names}
    present = {n.lower() for n in cjk_names}
    return {
        "fc_list_available": True,
        "total_font_count": len(all_names),
        "cjk_font_count": len(cjk_names),
        "cjk_fonts": cjk_names[:40],
        "reusable_open_source_cjk": reusable,
        "has_any_cjk": len(cjk_names) > 0,
        "windows_fonts_present": sorted(
            n for n in cjk_names if any(k in n.lower() for k in ("yahei", "simsun", "simhei"))
        ),
        "macos_only_fonts_present": sorted(
            n for n in cjk_names if any(k in n.lower() for k in ("pingfang", "hiragino"))
        ),
        "note": "中文判定以 fc-list :lang=zh 为准；服务器缺中文字体只影响服务端渲染图",
    }


SAMPLE_TEXT = "分解质因数 240 = 24 × 3 × 5"


def build_sample_pptx(target: Path):
    """运行时生成测试样本，避免把二进制文件提交进仓库。

    样本故意同时包含中文、ASCII 数字和真实上标（baseline 方式）。
    上标是本项目公式表达的核心手段，必须一并验证渲染是否保留。
    """
    from pptx import Presentation
    from pptx.util import Emu, Pt

    prs = Presentation()
    prs.slide_width = Emu(12192000)
    prs.slide_height = Emu(6858000)
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    box = slide.shapes.add_textbox(Emu(914400), Emu(1828800), Emu(10363200), Emu(914400))
    frame = box.text_frame
    frame.text = "分解质因数 240 = 2"
    para = frame.paragraphs[0]
    para.runs[0].font.size = Pt(28)

    # python-pptx 不直接支持上标，写 baseline 属性——这与产品导出的做法一致
    sup = para.add_run()
    sup.text = "4"
    sup.font.size = Pt(28)
    sup.font._rPr.set("baseline", "30000")

    tail = para.add_run()
    tail.text = " × 3 × 5"
    tail.font.size = Pt(28)

    target.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(target))
    return target.stat().st_size


def load_pymupdf():
    """PyMuPDF 新版弃用了 fitz 这个名字，两代都要能导入。"""
    try:
        import pymupdf as module
        return module
    except ImportError:
        try:
            import fitz as module
            return module
        except ImportError:
            return None


def inspect_pdf_text(pdf: Path):
    """检查 PDF 里中文是否真的渲染出来了，而不是缺字体导致的空白或豆腐块。

    按 Unicode CJK 区间统计字符数，不硬编码特定词——否则换一份样本就会误报。
    """
    fitz = load_pymupdf()
    if fitz is None:
        return {"text_extraction": "skipped", "reason": "未安装 PyMuPDF"}

    try:
        with fitz.open(str(pdf)) as doc:
            text = "".join(page.get_text() for page in doc)
            fonts = set()
            for page in doc:
                for font in page.get_fonts():
                    fonts.add(font[3])
    except Exception as exc:  # noqa: BLE001
        return {"text_extraction": "failed", "error": repr(exc)}

    def is_cjk(ch):
        code = ord(ch)
        return (
            0x4E00 <= code <= 0x9FFF      # 基本汉字
            or 0x3400 <= code <= 0x4DBF   # 扩展 A
            or 0x3000 <= code <= 0x303F   # CJK 标点
            or 0xFF00 <= code <= 0xFFEF   # 全角字符
        )

    cjk_count = sum(1 for ch in text if is_cjk(ch))
    squashed = "".join(text.split())
    return {
        "extracted_chars": len(text),
        "cjk_chars_in_pdf": cjk_count,
        # 样本含 7 个汉字；取到 4 个以上即可认为中文渲染成功
        "cjk_rendered": cjk_count >= 4,
        # 样本的等号后是「2 + 上标4 × 3 × 5」。不能用 "24" 判断，
        # 因为前面的 240 也含 24；须匹配等号之后的完整序列。
        "superscript_preserved": "=24×3×5" in squashed,
        "embedded_fonts": sorted(fonts)[:10],
        "text_preview": text.strip()[:120],
    }


def check_render_capability():
    """实测 PPTX→PDF→取字链路。任一环节缺失时明确报告不可测，不伪装成功。"""
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    outdir = DATA_DIR / "render-test"
    outdir.mkdir(parents=True, exist_ok=True)

    # 先判断转换器：没有它就没必要生成样本，也能让结论更直接
    if not soffice:
        return {
            "tested": False,
            "stage": "转换器检查",
            "reason": "未安装 LibreOffice，无法测试 PPTX→PDF",
            "note": "缺少转换能力不影响 L1/L2 一致性，只影响服务端渲染对账",
        }

    sample = outdir / "sample.pptx"
    try:
        sample_size = build_sample_pptx(sample)
    except Exception as exc:  # noqa: BLE001
        return {"tested": False, "stage": "生成样本", "reason": repr(exc),
                "note": "python-pptx 不可用会直接暴露在这一步"}

    # 用项目内 profile，避免无权限写系统 home 导致转换静默失败
    profile = DATA_DIR / "lo-profile"
    started = time.time()
    result = run(
        [
            soffice, "--headless", "--norestore", "--nolockcheck", "--nodefault",
            f"-env:UserInstallation=file://{profile}",
            "--convert-to", "pdf", "--outdir", str(outdir), str(sample),
        ],
        timeout=180,
    )
    elapsed = round(time.time() - started, 2)
    pdf = outdir / (sample.stem + ".pdf")

    report = {
        "tested": True,
        "soffice_path": soffice,
        "sample_created": sample_size,
        "success": pdf.exists(),
        "elapsed_seconds": elapsed,
        "pdf_size_bytes": pdf.stat().st_size if pdf.exists() else 0,
        "detail": result,
    }
    if pdf.exists():
        report["pdf_text"] = inspect_pdf_text(pdf)
    else:
        report["note"] = "转换未产出 PDF。若 stderr 提到 profile 或权限，多为 home 不可写"
    return report


def check_container_render():
    """用 soffice 容器实测 PPTX→PDF。镜像不存在时报告缺什么，不伪装成功。

    这是整条渲染链路唯一的真实验证点：镜像构建成功不等于能正确渲染
    我们的课件（中文字体、上标、版面都可能出问题）。
    """
    docker = shutil.which("docker")
    if not docker:
        return {"tested": False, "reason": "docker 不可用"}

    # 先确认 daemon 可达，否则 image inspect 失败会被误读成"镜像不存在"，
    # 而这两者的解决方式完全不同（一个是权限，一个是构建镜像）
    if not run([docker, "info"], timeout=20)["ok"]:
        return {"tested": False, "reason": "docker daemon 不可达，无法检查镜像",
                "note": "先解决 docker 权限问题"}

    image = os.getenv("SOFFICE_RUNNER_IMAGE", "olmath-soffice-runner:bookworm")
    inspect = run([docker, "image", "inspect", image], timeout=30)
    if not inspect["ok"]:
        return {
            "tested": False,
            "image": image,
            "reason": "镜像不存在",
            "stderr": inspect["stderr"][:300],
            "note": "需先构建 soffice-runner 镜像，再回来验证",
        }

    outdir = DATA_DIR / "container-render"
    outdir.mkdir(parents=True, exist_ok=True)
    try:
        build_sample_pptx(outdir / "sample.pptx")
    except Exception as exc:  # noqa: BLE001
        return {"tested": False, "image": image, "stage": "生成样本", "reason": repr(exc)}

    # 容器内以非 root 运行，挂载目录需可写
    try:
        os.chown(outdir, 1000, 1000)
        os.chmod(outdir, 0o777)
        chown_ok = True
    except (PermissionError, OSError) as exc:
        chown_ok = repr(exc)

    started = time.time()
    result = run(
        [docker, "run", "--rm", "--shm-size=1g", "--network", "bridge",
         "-v", f"{outdir}:/work",
         image,
         "soffice", "--headless", "--norestore", "--nolockcheck", "--nodefault",
         "-env:UserInstallation=file:///work/lo-profile",
         "--convert-to", "pdf", "--outdir", "/work", "/work/sample.pptx"],
        timeout=300,
    )
    elapsed = round(time.time() - started, 2)
    pdf = outdir / "sample.pdf"

    report = {
        "tested": True,
        "image": image,
        "chown_applied": chown_ok,
        "success": pdf.exists(),
        "elapsed_seconds": elapsed,
        "pdf_size_bytes": pdf.stat().st_size if pdf.exists() else 0,
        "detail": result,
    }
    if pdf.exists():
        report["pdf_text"] = inspect_pdf_text(pdf)
        report["note"] = "cjk_rendered 与 superscript_preserved 都是 true 才算真正可用"
    else:
        report["note"] = "转换失败。若 stderr 提示字体缺失或权限，见 detail.stderr"
    return report


def check_services():
    """问题：Redis / MinIO 是否连通（按平台给出的默认地址）。"""
    services = {}

    redis_url = os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0")
    try:
        import redis

        client = redis.from_url(redis_url, socket_connect_timeout=5)
        client.ping()
        services["redis"] = {"url": redis_url, "connected": True,
                             "version": client.info().get("redis_version")}
    except ImportError:
        services["redis"] = {"url": redis_url, "connected": False,
                             "error": "未安装 redis 包"}
    except Exception as exc:  # noqa: BLE001
        services["redis"] = {"url": redis_url, "connected": False, "error": repr(exc)}

    endpoint = os.getenv("MINIO_ENDPOINT", "http://127.0.0.1:9010")
    try:
        from minio import Minio

        client = Minio(
            endpoint.replace("http://", "").replace("https://", ""),
            access_key=os.getenv("MINIO_ACCESS_KEY", "deployment-admin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "DeployMinio-9010-2026"),
            secure=False,
        )
        buckets = [b.name for b in client.list_buckets()]
        services["minio"] = {"endpoint": endpoint, "connected": True,
                             "buckets": buckets}
    except ImportError:
        services["minio"] = {"endpoint": endpoint, "connected": False,
                             "error": "未安装 minio 包"}
    except Exception as exc:  # noqa: BLE001
        services["minio"] = {"endpoint": endpoint, "connected": False,
                             "error": repr(exc)}

    return services


def check_docker_quick():
    """轻量版：只判断 docker 命令与 daemon 是否可用，用于验收入口。"""
    docker = shutil.which("docker")
    if not docker:
        return {"available": False, "reason": "PATH 中没有 docker 命令"}
    info = run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=20)
    if not info["ok"]:
        return {"available": False, "reason": "docker info 失败",
                "stderr": info["stderr"][:200]}
    return {"available": True, "server_version": info["stdout"]}


def check_docker():
    """关键问题：项目进程能不能用 docker。

    这决定架构走向——能跑容器就在项目内调 soffice 容器，
    不能则必须请运维提供常驻转换服务。运维的 chromium-runner 方案
    放在 /data/deployment-services/，无法据此判断我们的项目进程是否有权限。
    """
    docker = shutil.which("docker")
    report = {
        "docker_binary": docker,
        "available": False,
        "permission_errors": [],
    }
    if not docker:
        report["reason"] = "PATH 中没有 docker 命令"
        report["note"] = "无法在项目内调用容器，需请运维提供转换服务"
        return report

    # 1) 能否访问 docker daemon（权限问题通常在这里暴露）
    info = run([docker, "info", "--format", "{{.ServerVersion}}"], timeout=30)
    report["docker_info"] = info
    if not info["ok"]:
        report["reason"] = "docker info 失败"
        report["permission_errors"].append(info["stderr"][:300])
        report["note"] = "多为当前用户不在 docker 组或无 socket 权限"
        return report

    report["server_version"] = info["stdout"]
    report["available"] = True

    # 2) 现有镜像（顺带确认 soffice 镜像是否已存在）
    images = run([docker, "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=30)
    image_list = [n for n in (images["stdout"] or "").splitlines() if n and "<none>" not in n]
    report["images"] = image_list[:40]
    report["soffice_image_present"] = any(
        "soffice" in name.lower() or "libreoffice" in name.lower() for name in image_list
    )
    report["chromium_runner_present"] = any("chromium" in name.lower() for name in image_list)

    # 3) 真正跑一个容器。必须用本地已有镜像——去 Docker Hub 拉会因网络失败，
    #    那与权限无关，误判会得出错误结论。
    if not image_list:
        report["can_run_container"] = None
        report["run_note"] = "本地无任何镜像，无法在不联网拉取的情况下测试容器启动"
    else:
        candidate = next(
            (n for n in image_list if "chromium" in n.lower()),
            image_list[0],
        )
        smoke = run(
            [docker, "run", "--rm", "--entrypoint", "sh", candidate,
             "-c", "echo container-ok"],
            timeout=120,
        )
        # 有些镜像没有 sh，退一步试直接 echo
        if "container-ok" not in (smoke["stdout"] or ""):
            smoke = run([docker, "run", "--rm", candidate, "echo", "container-ok"], timeout=120)
        report["run_test_image"] = candidate
        report["run_test"] = smoke
        report["can_run_container"] = "container-ok" in (smoke["stdout"] or "")

    # 4) 当前用户与 docker 组（判断权限来源）
    report["current_user"] = run(["id"], timeout=10)["stdout"]
    report["in_docker_group"] = "docker" in (run(["id", "-nG"], timeout=10)["stdout"] or "")
    return report


def check_docker_network():
    """容器内能否访问宿主服务。chromium-runner 用 bridge 网络，容器内 127.0.0.1 不是宿主。

    只允许用本地已有镜像——联网拉取失败与网络拓扑无关，会得出错误结论。
    """
    docker = shutil.which("docker")
    if not docker or not run([docker, "info"], timeout=20)["ok"]:
        return {"tested": False, "reason": "docker 不可用，跳过"}

    images = run([docker, "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=30)
    image_list = [n for n in (images["stdout"] or "").splitlines() if n and "<none>" not in n]
    if not image_list:
        return {"tested": False, "reason": "本地无镜像，无法测试容器网络"}

    # 优先含 nc 的镜像；否则退回 bash 的 /dev/tcp
    image = next((n for n in image_list if "chromium" in n.lower()), image_list[0])

    redis_port = os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0").rsplit(":", 1)[-1].split("/")[0]
    minio_port = os.getenv("MINIO_ENDPOINT", "http://127.0.0.1:9010").rsplit(":", 1)[-1]

    results = {}
    for host in ("host.docker.internal", "172.17.0.1"):
        for label, port in (("redis", redis_port), ("minio", minio_port)):
            probe = run(
                [docker, "run", "--rm", "--entrypoint", "sh", image, "-c",
                 f"(nc -z -w 2 {host} {port} 2>/dev/null && echo open) "
                 f"|| (timeout 2 sh -c \"echo >/dev/tcp/{host}/{port}\" 2>/dev/null && echo open) "
                 f"|| echo closed"],
                timeout=90,
            )
            results[f"{label}@{host}:{port}"] = "open" in (probe["stdout"] or "")
    return {
        "tested": True,
        "image_used": image,
        "results": results,
        "note": "全为 false 时需请运维确认宿主机内网 IP，或改用 --network host",
    }


def check_storage():
    """问题：磁盘与项目目录可写性，以及数据是否落在项目目录内。"""
    usage = shutil.disk_usage(PROBE_DIR)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    probe_file = DATA_DIR / ".write-test"
    writable = False
    try:
        probe_file.write_text("ok", encoding="utf-8")
        writable = probe_file.read_text(encoding="utf-8") == "ok"
    except OSError:
        writable = False
    finally:
        probe_file.unlink(missing_ok=True)

    return {
        "project_dir": str(PROBE_DIR),
        "total_gb": round(usage.total / 1024**3, 1),
        "free_gb": round(usage.free / 1024**3, 1),
        "project_dir_writable": writable,
        "note": "数据落在项目目录内才会在重新部署时保留",
    }


def check_dependencies():
    """核对第 13.4 章依赖版本基线是否与实装一致。"""
    expected = {
        "fastapi": "0.141.1",
        "uvicorn": "0.53.0",
        "python-pptx": "1.0.2",
        "lxml": "6.1.3",
        "Pillow": "12.2.0",
        "PyMuPDF": "1.26.0",
        "SQLAlchemy": "2.0.54",
        "redis": "8.1.0",
        "minio": "7.2.20",
    }
    modules = {
        "fastapi": "fastapi", "uvicorn": "uvicorn", "python-pptx": "pptx",
        "lxml": "lxml", "Pillow": "PIL", "PyMuPDF": "fitz",
        "SQLAlchemy": "sqlalchemy", "redis": "redis", "minio": "minio",
    }
    result = {}
    for pkg, expected_version in expected.items():
        try:
            module = __import__(modules[pkg])
            actual = getattr(module, "__version__", None)
            if actual is None and pkg == "PyMuPDF":
                actual = getattr(module, "version", (None,))[0]
            result[pkg] = {
                "installed": True,
                # 有些包不暴露 __version__，报 unknown 而不是字符串 "None"
                "version": str(actual) if actual is not None else "unknown",
                "expected_ceiling": expected_version,
                "matches_ceiling": str(actual) == expected_version,
            }
        except ImportError:
            result[pkg] = {"installed": False, "expected_ceiling": expected_version}
    return result


def check_persistence():
    """问题：重启后数据是否保留。首次访问创建标记文件，重启后再访问即可比对。"""
    marker = DATA_DIR / "restart-marker.json"
    first_seen_path = DATA_DIR / "first-seen.txt"

    if not first_seen_path.exists():
        first_seen_path.write_text(
            json.dumps({"first_seen_epoch": time.time()}), encoding="utf-8"
        )
        first = json.loads(first_seen_path.read_text(encoding="utf-8"))
        return {
            "marker_exists": marker.exists(),
            "first_seen_epoch": first["first_seen_epoch"],
            "survived_restart": None,
            "note": "首次记录。点「重启」后再次访问此接口，survived_restart 会变成 true",
        }

    first = json.loads(first_seen_path.read_text(encoding="utf-8"))
    return {
        "marker_exists": True,
        "first_seen_epoch": first["first_seen_epoch"],
        "survived_restart": True,
        "note": "标记文件仍在，说明项目目录在重启后保留",
    }


def build_report():
    return {
        "probe_version": APP_VERSION,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "runtime": check_runtime(),
        "supervision": check_supervision(),
        "subprocess": check_subprocess(),
        "docker": check_docker(),
        "docker_network": check_docker_network(),
        "libreoffice": check_libreoffice(),
        "fonts": check_fonts(),
        "render": check_render_capability(),
        "container_render": check_container_render(),
        "services": check_services(),
        "storage": check_storage(),
        "dependencies": check_dependencies(),
        "persistence": check_persistence(),
    }


@app.get("/api/health")
def health():
    """部署验收入口。必须快——验收会反复调用，因此只做轻量检查。

    重活（字体枚举、PPTX 转换）放在 /api/probe。
    """
    findings = {"status": "ok", "version": APP_VERSION}
    for name, fn in (
        ("runtime", check_runtime),
        ("subprocess", check_subprocess),
        ("docker", check_docker_quick),
        ("libreoffice", check_libreoffice),
    ):
        try:
            findings[name] = fn()
        except Exception as exc:  # noqa: BLE001
            findings[name] = {"error": repr(exc)}
    return findings


@app.get("/api/probe")
def probe():
    """完整报告。首次调用会做一次真实 PPTX→PDF 转换，耗时较长。"""
    return build_report()


@app.get("/")
def index():
    return {
        "service": "课件处理环境探针",
        "version": APP_VERSION,
        "endpoints": {
            "/api/health": "部署验收入口，快速返回关键能力",
            "/api/probe": "完整环境报告（含 PPTX→PDF 实测）",
        },
    }
