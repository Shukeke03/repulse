# Ubuntu / WSL2 私有部署与运维

在线预览已取消，项目不再提供公网体验入口。2026 年 11 月 3 日已关闭并核验 RePulse 原有的 8443 公网转发；已有的 443 服务仍仅供私有网络访问。应用与现存代理均只监听回环地址。

默认通过部署主机本地浏览器或 SSH 端口转发使用：

```text
本机浏览器 http://127.0.0.1:8765
  → 本机应用，或 SSH 转发到服务器 127.0.0.1:8765
  → 单 worker FastAPI
  → QEMU / FreeRTOS 信号处理
```

默认首页是腕端预览，工作台位于 `/#/workbench`。以下说明保留 Ubuntu 24.04 / WSL2 的环境、systemd 服务、数据备份和回滚方法，不配置公网发布。

## 私有访问

直接在部署主机打开 [http://127.0.0.1:8765](http://127.0.0.1:8765)。从其他电脑访问时，在客户端执行：

```sh
ssh -N -L 8765:127.0.0.1:8765 user@server
```

将 `user@server` 替换为可通过 SSH 连接的私有主机和账号，保持该命令运行，再打开本机 `http://127.0.0.1:8765`。若客户端 8765 已被占用，可将转发参数改为 `18765:127.0.0.1:8765`，并访问本机 18765 端口。SSH 连接负责访问认证；此方式不依赖 Nginx 的认证设置。

已有的独立 Nginx 可继续保留在 `127.0.0.1:8766`，但默认私用直接转发应用端口即可。现存服务器配置与仓库模板可能不同，不要为了源码更新而整份覆盖代理配置或改变其认证开关。

## 路径与运行边界

部署模板默认 Linux 用户为 `shuxing`；安装到其他账号或目录时，需同步调整 unit 中的 `User`、`Group`、`WorkingDirectory`、`PATH` 与数据路径。

| 项目 | 模板路径或地址 |
| --- | --- |
| 仓库 | `/home/shuxing/repulse` |
| Python 环境 | `/home/shuxing/repulse/.venv` |
| 公开数据 | `/home/shuxing/repulse/data/public` |
| 运行记录、SQLite、导出及 QEMU 临时输入 | `/home/shuxing/.local/share/repulse` |
| 应用监听 | `127.0.0.1:8765` |
| 可选独立代理监听 | `127.0.0.1:8766` |
| 独立 Nginx 配置 | `/etc/repulse/nginx.conf` |
| 可选认证哈希文件 | `/etc/repulse/htpasswd`，不进入 Git |
| 独立 Nginx PID | `/run/repulse-proxy.pid` |
| 独立 Nginx 临时目录 | `/run/repulse-proxy/` |
| 独立 Nginx 日志 | `/var/log/repulse-proxy/` |

`repulse.service` 以普通用户运行，设置 BLAS 线程数为 1，限制为 2 个 CPU 的时间预算、2 GiB 内存、128 个任务。它启用 `NoNewPrivileges`、独立临时目录和只读系统目录，保留对仓库、数据及 QEMU semihosting 文件的访问。运行目录不与源码更新一起替换。

`repulse-proxy.service` 是可选的独立 Nginx 实例，不读取 `/etc/nginx/nginx.conf` 或其他站点配置。检查或重载它时需明确使用 `/etc/repulse/nginx.conf`；默认 `nginx.service` 不能替代此实例。代理的 Origin、Host 和协议设置应与实际私有访问地址一致；如果只是使用 SSH 转发，可直接连接 8765，避免混用旧代理的 HTTPS 转发设置。

## 准备环境与应用服务

以下命令在 Ubuntu 中执行。已有环境、数据和构建产物时跳过相应步骤；WSL 需要预先启用 systemd。

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends git make python3-venv qemu-system-arm gcc-arm-none-eabi libnewlib-arm-none-eabi
cd /home/shuxing
git clone https://github.com/Shukeke03/repulse.git
cd /home/shuxing/repulse
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m scripts.download_dataset
make -C firmware TARGET=qemu
install -d -m 700 /home/shuxing/.local/share/repulse
sudo install -o root -g root -m 644 deploy/repulse.service /etc/systemd/system/repulse.service
sudo systemd-analyze verify /etc/systemd/system/repulse.service
sudo systemctl daemon-reload
sudo systemctl enable --now repulse.service
curl --fail http://127.0.0.1:8765/api/bootstrap
```

仓库中的 `requirements.lock.txt` 和 Conda lock 文件是 macOS 环境快照。Ubuntu 使用 `pyproject.toml` 声明的依赖安装，不直接套用 macOS 的完整快照。新建演示只使用 FreeRTOS / QEMU，Python 仍用于服务、数据预处理、回放、离线比较和算法校验。

如保留已有代理，其配置、认证文件和 unit 分别维护；不要在应用首次安装步骤中默认启用代理。默认 `nginx.service` 无需启动，也无需额外监听 80 或 443 端口。

## 日常运行与检查

```sh
sudo systemctl status repulse.service --no-pager
sudo journalctl -u repulse.service -n 80 --no-pager
sudo systemctl restart repulse.service
ss -ltnp
```

确认应用端口仍只绑定 `127.0.0.1`。若正在使用可选代理，可检查并重载该独立实例：

```sh
sudo systemctl status repulse-proxy.service --no-pager
sudo journalctl -u repulse-proxy.service -n 80 --no-pager
sudo tail -n 50 /var/log/repulse-proxy/error.log
sudo /usr/sbin/nginx -t -c /etc/repulse/nginx.conf
sudo systemctl reload repulse-proxy.service
```

若服务因连续失败触发启动限制，修正日志中的原因后执行 `sudo systemctl reset-failed repulse.service`，再启动；可选代理同理。

通过本地或 SSH 连接检查腕端首页、工作台、API 文档、一次完整 QEMU 演示和独立回放。浏览器 Network / WS 中的 `/api/live` 和回放连接应返回 `101` 并持续接收消息。界面使用当前页面地址连接 WebSocket，HTTP 本地访问使用 `ws://`。

若主机还运行 Tailscale，可用 `tailscale serve status` 和 `tailscale funnel status` 只读核对：RePulse 的 8443 公网入口应保持关闭，原有 443 私有服务保持不变。应用更新和回滚均不重置其他服务的网络配置，也不恢复已取消的公网入口。

## 更新与回滚

更新前确保没有正在处理的演示，服务器仓库没有未保存修改。以下步骤停止应用后备份运行目录，并记录代码、依赖和应用 unit；每次使用独立备份目录。已有代理配置和认证设置保持不变。

```sh
cd /home/shuxing/repulse
git status --short
REPULSE_BACKUP_DIR="/home/shuxing/.local/state/repulse-deploy/$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$REPULSE_BACKUP_DIR"
git rev-parse HEAD > "$REPULSE_BACKUP_DIR/revision"
.venv/bin/python -m pip freeze --exclude-editable > "$REPULSE_BACKUP_DIR/dependencies-before.txt"
sudo cp /etc/systemd/system/repulse.service "$REPULSE_BACKUP_DIR/repulse.service"
sudo systemctl stop repulse.service
tar -C /home/shuxing/.local/share -czf "$REPULSE_BACKUP_DIR/runtime.tar.gz" repulse
git pull --ff-only
.venv/bin/python -m pip install -e .
make -C firmware TARGET=qemu
sudo install -o root -g root -m 644 deploy/repulse.service /etc/systemd/system/repulse.service
sudo systemd-analyze verify /etc/systemd/system/repulse.service
sudo systemctl daemon-reload
sudo systemctl start repulse.service
.venv/bin/python -m pip freeze > "$REPULSE_BACKUP_DIR/dependencies-after.txt"
curl --fail http://127.0.0.1:8765/api/bootstrap
```

若另行修改可选代理，先备份 `/etc/repulse/nginx.conf` 与 `/etc/systemd/system/repulse-proxy.service`，保留现有认证文件；先通过独立 `nginx -t -c` 检查再 reload。普通应用更新无需覆盖这些文件。

若中途出错，先处理报错或回滚，不继续运行后面的启动步骤。将 `REPULSE_BACKUP_DIR` 设置为实际备份目录后，代码与应用服务可按以下步骤回滚：

```sh
cd /home/shuxing/repulse
sudo systemctl stop repulse.service
git switch --detach "$(cat "$REPULSE_BACKUP_DIR/revision")"
.venv/bin/python -m pip install -r "$REPULSE_BACKUP_DIR/dependencies-before.txt"
.venv/bin/python -m pip install --no-deps -e .
make -C firmware TARGET=qemu
sudo install -o root -g root -m 644 "$REPULSE_BACKUP_DIR/repulse.service" /etc/systemd/system/repulse.service
sudo systemctl daemon-reload
sudo systemctl start repulse.service
curl --fail http://127.0.0.1:8765/api/bootstrap
```

代码回滚不会覆盖新产生的记录。只有确认需要恢复数据时，才在应用停止后另外恢复 `runtime.tar.gz`，并先保留当前运行目录。上述回滚将 Git 切换到指定提交；后续恢复跟踪主分支时执行 `git switch main`。代理或访问方式的回滚不能重新启用已取消的在线预览。

## Windows 与 WSL 常驻

`systemctl enable` 负责 Ubuntu 实例启动后的 Linux 服务自启；systemd 服务本身不会让 WSL 实例一直存活。Windows 关机、重启、睡眠，或执行 `wsl --shutdown` 都会使私有访问暂时不可用。

现有部署通过 Windows 登录任务运行 `wsl.exe -d Ubuntu-24.04 --exec /bin/sleep infinity` 保持实例存活，再由 systemd 启动应用和已有的可选代理。部署时已确认该任务与保活进程运行；未为验收强制重启 Windows。Windows 重启后需要用户登录，没有配置自动登录或修改电源设置。

Linux 模板不会创建 Windows 任务。可在任务计划程序中检查、禁用或删除相应保活任务；仍需在方便时验证整机重启并登录后的服务恢复。私有访问同样要求主机保持开机和联网。
