# LibreOffice 转换容器：把 PPTX 渲染为 PDF / PNG
#
# 为什么不装宿主机：宿主机是 CentOS 7 / glibc 2.17，
# 现代 LibreOffice 装不上，且依赖树大会影响机器上其他项目。
# 本镜像沿用 olmath-chromium-runner 的同一套模式。
#
# 构建（由运维执行）：
#   docker build -t olmath-soffice-runner:bookworm .
#
# 用法见同目录 README.md 与 host_convert.py。

FROM debian:bookworm-slim

# 只装 impress，不装 writer/calc/base，镜像小很多。
# fonts-noto-cjk 必须有：没有它课件里的中文会渲染成方块，
# 与 chromium-runner 里遇到的是同一个坑。
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources \
 && apt-get update && apt-get install -y --no-install-recommends \
      libreoffice-impress \
      libreoffice-core \
      fonts-noto-cjk \
      fonts-liberation \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# 非 root 运行，与 chromium-runner 一致（uid 1000）。
# 调用方挂载进来的目录必须是 1000:1000 且可写，否则转换结果写不出来。
RUN useradd -m -u 1000 runner
USER runner
WORKDIR /work

ENV HOME=/home/runner \
    PYTHONUNBUFFERED=1

# 刻意不设 ENTRYPOINT：调用方显式传完整命令（含 -env:UserInstallation），
# 与 chromium-runner 的做法一致，也便于以后在同一镜像里跑别的检查脚本。
#
# 说明：这里不预置 registrymodifications.xcu。
# 调用时用 -env:UserInstallation 指向挂载目录，LibreOffice 会在其中
# 自建 profile；写 /etc/libreoffice 在该参数生效后也不会被读取。
# 首次运行向导用 --nodefault / --norestore 等命令行参数规避即可。
