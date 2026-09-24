# 课件处理环境探针

独立部署的 FastAPI 服务，通过 `/api/health` 和 `/api/probe` 获取服务器能力事实，用于确定课件渲染、预览和任务执行方案。不依赖课件主仓库的文档、素材或生成工具。

不写入业务数据，但完整探测会在 `data/` 下创建测试文件、重启保留标记及渲染产物，也可能启动短生命周期的测试容器。报告包含服务器环境信息，仅供可信内网使用，不要直接暴露到公网。

## 独立仓库结构

将本目录的**内容**作为新仓库根目录，不要在新仓库中再套一层 `probe/`。需要包含 `.gitignore` 等隐藏文件，但不要迁移 `.venv/`、`data/`、缓存或本地环境配置。

```text
.gitignore
Procfile
README.md
requirements.txt
run.py
main.py
soffice-runner/Dockerfile
soffice-runner/container_convert.py
soffice-runner/host_convert.py
soffice-runner/README.md
```

`run.py` 是启动入口，`main.py` 包含探测接口，`soffice-runner/` 是可选的 LibreOffice 容器工具。无需复制主仓库根目录的 `Procfile` 或 `requirements.txt`，本目录已有独立版本。

## 部署步骤

1. 创建新的空仓库，将上述文件放在仓库根目录后提交、推送。
2. 在部署后台新建项目，填写新仓库地址；建议使用已配置访问权限的 SSH 地址。
3. 按下表配置，执行部署。
4. 先访问 `/api/health` 确认服务启动，再访问 `/api/probe` 获取完整 JSON 报告。

| 后台字段 | 独立仓库配置 |
| --- | --- |
| 部署目录 | 使用新项目的独立目录，例如 `/data/deploys/pptx-env-probe`；不要覆盖现有主项目目录 |
| 启动命令 | `.venv/bin/python3 run.py` |
| 健康检查路径 | `/api/health` |
| 验收脚本 | 留空，未提供专用验收脚本 |
| 环境准备命令 | 见下方两行 |
| 补充文件 | 基础探测无需提供 |

在仓库根目录执行环境准备命令：

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

仓库根目录的 `Procfile` 已声明启动命令；若平台支持自动识别，可以采用该配置，否则显式填写表中的命令。不要使用 `python3 -m http.server`，静态文件服务无法运行探针接口。

### 端口与本地启动

`run.py` 直接读取平台注入的 `PORT`，不依赖平台对 `$PORT` 或 `{PORT}` 的文本替换。未注入时端口为 `8000`；`HOST` 默认是 `0.0.0.0`。平台的访问端口必须与服务监听端口一致。

安装依赖后，在仓库根目录运行：

```sh
PORT=8000 HOST=127.0.0.1 .venv/bin/python3 run.py
```

本地检查地址为 `http://127.0.0.1:8000/api/health`。生产部署不应将 `HOST` 设置为 `127.0.0.1`，除非平台明确要求仅监听回环地址。

### 与原仓库的过渡兼容

尚未迁移时，原仓库仍使用 `.venv/bin/python3 probe/run.py`，原根目录配置无需改变。新仓库使用 `.venv/bin/python3 run.py`。两者不要混用。

独立仓库减少课件素材和历史文件的拉取，但不会减少 Python 依赖的安装耗时。依赖版本暂时保持原有基线，以便对照服务器探测结果。

## 接口的分工

| 接口 | 用途 | 耗时 |
| --- | --- | --- |
| `/api/health` | 部署验收入口（平台会调它）。只返回轻量信息：Python 版本、能否起子进程、LibreOffice 是否存在 | 快 |
| `/api/probe` | 完整报告。含字体枚举、PPTX→PDF 实测、Redis/MinIO 连通性、依赖版本核对 | 首次较慢（要跑一次真实转换） |
| `POST /api/probe/pptx` | 临时检测真实 PPTX 的 PDF 转换与首页 PNG 渲染，不调用共享存储探测 | 转换超时为 180 秒 |

### 真实课件检查

仅供可信内网使用，接口没有身份认证。请求体直接发送 PPTX 二进制，不使用 multipart；上限为 20 MiB。空文件或无效 PPTX 返回 400，超过大小限制返回 413，缺少 LibreOffice 返回 503。

```sh
curl --fail-with-body -X POST \
	-H 'Content-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation' \
	--data-binary @lesson.pptx http://127.0.0.1:8000/api/probe/pptx
```

每次样例或上传检查均使用独立临时目录和 LibreOffice profile，完成或异常退出后清理原件与产物。上传响应不包含正文摘录、图片内容或样例专属上标判断。

- `success` 表示转换命令成功退出且产出非空 PDF；不等同于视觉保真或整条预览链通过。
- `detail` 保留命令执行诊断；退出码为零但没有 PDF 时仍报告失败。
- `page_count`、`png_rendered`、`png_bytes` 表示 PDFium 能否读取 PDF 并将首页编码为 PNG；不验证其他页面。
- `pdf_text.cjk_chars_in_pdf` 仅统计可提取的中文字符，不能证明字体外观或动画正确。
- `pypdfium2_version`、`png_error` 用于区分 PDF 转换与 PNG 渲染问题。

PDFium 固定为 `4.30.0`，与 Aippt 预览链保持一致。镜像可查询到包不代表该版本已在目标服务器安装成功；部署后仍需实测。LibreOffice profile 参数顺序已调整，是否解决服务器 5.3.6 的失败也须现场验证。

本地回归测试：

```sh
.venv/bin/python -m unittest discover -s tests -v
```

测试使用模拟的 LibreOffice 执行结果和真实 PDFium 编码，不替代服务器真实 PPTX 转换验收。

## 它能回答哪些问题

| 问题 | 看哪个字段 |
| --- | --- |
| **项目进程能不能用 docker** | `docker.available`、`docker.can_run_container`、`docker.permission_errors` |
| 容器内能否访问宿主 Redis/MinIO | `docker_network.results` |
| 容器内 LibreOffice 能否渲染我们的课件 | `container_render.success`、`container_render.pdf_text` |
| 宿主机有没有 LibreOffice | `libreoffice.available`（本项目建议不用宿主机版） |
| 服务器有哪些中文字体 | `fonts.cjk_fonts`、`fonts.reusable_open_source_cjk` |
| 能否创建子进程 | `subprocess.simple_exec`、`subprocess.nested_exec` |
| 谁在守护这个服务 | `supervision.process_chain`、`supervision.init_system`、`supervision.inside_container` |
| Redis / MinIO 通不通 | `services.redis.connected`、`services.minio.connected` |
| 磁盘与目录可写 | `storage.free_gb`、`storage.project_dir_writable` |
| 依赖版本是否与文档基线一致 | `dependencies.*.matches_ceiling` |
| 重启后数据是否保留 | `persistence.survived_restart` |

## 与 soffice-runner 的配合

`container_render` 需要先构建 `olmath-soffice-runner` 镜像（见 [soffice-runner/README.md](soffice-runner/README.md)）。镜像不存在时该项报 `reason: 镜像不存在`，这是预期行为，不影响其他检查。

镜像就绪后，`container_render.pdf_text.cjk_rendered` 与 `superscript_preserved` 都是 `true`，才说明渲染链路真正可用——**镜像构建成功不等于能正确渲染我们的课件**。

## 重启保留测试

1. 第一次访问 `/api/probe`，记下 `persistence.first_seen_epoch`（此时 `survived_restart` 为 `null`）。
2. 回后台点「重启」。
3. 服务恢复后再次访问 `/api/probe`。
4. `survived_restart` 变成 `true` 表示项目目录在重启后保留；若 `first_seen_epoch` 变了，说明目录被重置。

这一条直接决定中间文件、SQLite 库和任务检查点能不能放项目目录。

## 依赖版本

原有依赖基线来自部署后台「环境信息 → 依赖版本查询」；新增 PDFium 的固定版本及验证边界见上文。该机器从内网镜像装包，且 gcc 4.8.5 / glibc 2.17，需要现场编译的依赖装不上。

新增依赖前请先在后台查询确认，再改这个文件。

## 已知限制

- `render` 检查依赖 `python-pptx` 生成样本；该包若装不上，会明确报 `stage: 生成样本`，而不是伪装成转换失败。
- 宿主机没有 LibreOffice 时，`render.tested` 为 `false`。这是**预期情况**——本项目走容器，真正要看的是 `container_render`。
- `container_render` 需要先有 `olmath-soffice-runner` 镜像；没有时报 `镜像不存在`，不会伪装成转换失败。
- `docker.can_run_container` 为 `null` 表示本地无任何镜像，无法在不联网拉取的前提下测试。这**不代表没有权限**。
- `supervision` 通过进程树做推断，不是权威结论。
