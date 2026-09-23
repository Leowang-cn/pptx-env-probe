# soffice-runner：LibreOffice 转换容器

把 PPTX 渲染成 PDF，用于产品质量校验（版面是否崩、中文是否出方块、上标是否保留）。

**不在宿主机安装 LibreOffice。** 宿主机是 CentOS 7 / glibc 2.17，现代 LibreOffice 装不上；且依赖树大会影响机器上其他项目。本方案沿用 `olmath-chromium-runner` 的同一套模式。

## 目录

| 文件 | 运行位置 | 作用 |
| --- | --- | --- |
| `Dockerfile` | 构建时 | 镜像定义，由运维构建 |
| `container_convert.py` | 容器内 | 调用 soffice，结果写 `/work/result.json` |
| `host_convert.py` | 宿主机 | 准备临时目录、起容器、读结果、搬运产物 |

## 运维需要做的

### 1. 构建镜像

```sh
cd soffice-runner
docker build -t olmath-soffice-runner:bookworm .
```

镜像基于 `debian:bookworm-slim`，容器内 glibc 2.36，不受宿主机 2.17 限制。已包含 `fonts-noto-cjk`，否则中文课件会渲染成方块（与 chromium-runner 同一个坑）。

### 2. 确认项目进程能执行 docker

这是关键的未知项。我们已把检测写进探针的 `/api/probe`，请看 `docker.can_run_container`：

- 能为 `true` → 项目内可直接调容器，无需额外服务
- 为 `false` 或 `null` → 需要确认是权限问题还是本地无镜像，见 `docker.permission_errors`
- 若确实无权 → 需要请运维提供一个常驻的转换服务，架构需相应调整

### 3. 确认容器访问宿主服务的地址

`docker_network.results` 会显示容器内经 `host.docker.internal` 和 `172.17.0.1` 能否连通 Redis(6380) / MinIO(9010)。若全为 `false`，请告知宿主机内网 IP。

## 本地用法

```sh
python3 host_convert.py 课件.pptx 课件.pdf
```

作为库调用：

```python
from host_convert import convert, ConvertError

try:
    convert(Path("in.pptx"), Path("out.pdf"))
except ConvertError as e:
    print(e.code, e)   # 明确的错误码，不是笼统的"转换失败"
```

## 错误码

| 错误码 | 含义 |
| --- | --- |
| `script_missing` | 找不到容器脚本 |
| `input_missing` | 输入文件不存在 |
| `convert_timeout` | 容器执行超时 |
| `convert_no_output` | soffice 未产出文件（含退出码 0 但无产出的静默失败） |
| `convert_failed` | 容器未返回结果，通常是容器崩了 |

## 设计上刻意做的几件事

- **转换前删除同名旧产物**。否则目录里残留的旧 PDF 会被当成新结果，质量校验完全失真。
- **不信任退出码**。soffice 返回 0 却不出文件是真实存在的情况，必须显式检查产物是否存在且非空。
- **不设 ENTRYPOINT**。调用方显式传完整命令，与 chromium-runner 一致。
- **日志走 stderr，结果走 result.json**。宿主机侧才能拿到明确错误码，而不是只有一句"容器失败了"。
- **不联网拉镜像做冒烟测试**。拉取失败与权限无关，会得出错误结论。
