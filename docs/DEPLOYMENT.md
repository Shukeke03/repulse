# Legion / WSL2 部署

本模板针对 Legion 上的 Ubuntu 24.04 WSL2，Linux 用户为 `shuxing`。公开访问地址为 **https://legion-wsl.tailb5f334.ts.net:8443/**，默认打开腕端页面，工作台位于 `/#/workbench`。

```text
公网浏览器 HTTPS :8443
  → Tailscale Funnel（TLS 终止）
  → 127.0.0.1:8766 独立 Nginx（整站 Basic 认证、Origin 检查、WebSocket 代理）
  → 127.0.0.1:8765 单 worker FastAPI
  → QEMU / FreeRTOS 信号处理
```

现有的 Tailscale `443 → 8787` 保持 tailnet 私有。本部署只增加 `8443 → 8766`。独立代理不读取 `/etc/nginx/nginx.conf` 或其他站点配置；Legion 首次安装 Nginx 时将默认 `nginx.service` 保持禁用，避免额外监听 80 端口。

## Legion 当前的临时免密码设置

用户已暂时关闭线上 Nginx 的 Basic 认证。该设置保存在服务器的 `/etc/repulse/nginx.conf`，仓库 `deploy/nginx.conf` 仍保留认证模板。**后续更新应保留服务器当前的免密码覆盖，不直接用仓库模板替换线上 Nginx 配置。** 本文后面的认证文件与 `401` 验收说明用于启用认证的部署；Legion 当前免密码状态下，正常请求不应再弹出登录框。恢复认证需遵循用户后续的明确安排。

## 路径与运行边界

| 项目 | 路径或地址 |
| --- | --- |
| 仓库 | `/home/shuxing/repulse` |
| Python 环境 | `/home/shuxing/repulse/.venv` |
| 公开数据 | `/home/shuxing/repulse/data/public` |
| 运行记录、SQLite、导出及 QEMU 临时输入 | `/home/shuxing/.local/share/repulse` |
| Nginx 独立配置 | `/etc/repulse/nginx.conf` |
| 认证哈希文件 | `/etc/repulse/htpasswd`，不进入 Git |
| 独立 Nginx PID | `/run/repulse-proxy.pid` |
| 独立 Nginx 临时目录 | `/run/repulse-proxy/` |
| 独立 Nginx 日志 | `/var/log/repulse-proxy/` |

`repulse.service` 以 `shuxing` 运行，设置 BLAS 线程数为 1，限制为 2 个 CPU 的时间预算、2 GiB 内存、128 个任务。它启用 `NoNewPrivileges`、独立临时目录和只读系统目录，保留对仓库、数据及 QEMU semihosting 文件的访问。运行目录不与源码更新一起替换。

`repulse-proxy.service` 启动独立的 Nginx master，worker 降权为 `www-data`。认证设置放在整个 `server` 上，覆盖首页、静态资源、API、API 文档及 WebSocket 握手。只接受空 `Origin` 或 `https://legion-wsl.tailb5f334.ts.net:8443`；空 Origin 的脚本客户端仍须提供账号密码。代理向应用固定传递 `X-Forwarded-Proto: https`，以保持 HTTPS 页面中的 WebSocket 使用 `wss://`。配置依据 [Nginx Basic 认证](https://nginx.org/en/docs/http/ngx_http_auth_basic_module.html)和 [WebSocket 代理说明](https://nginx.org/en/docs/http/websocket.html)。

## 准备环境

以下命令在 Ubuntu 中执行。已有环境、数据和构建产物时跳过相应步骤。需预先启用 WSL 的 systemd，并保证 `tailscale status` 能看到 `legion-wsl`。

```sh
sudo apt-get update
sudo apt-get install --no-install-recommends git make python3-venv qemu-system-arm gcc-arm-none-eabi libnewlib-arm-none-eabi nginx-light apache2-utils
cd /home/shuxing
git clone https://github.com/Shukeke03/repulse.git
cd /home/shuxing/repulse
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
.venv/bin/python -m scripts.download_dataset
make -C firmware TARGET=qemu
install -d -m 700 /home/shuxing/.local/share/repulse
```

仓库中的 `requirements.lock.txt` 和 Conda lock 文件是 macOS 环境快照。Ubuntu 运行环境使用 `pyproject.toml` 声明的依赖安装，不直接套用 macOS 的完整快照。新建演示只使用 FreeRTOS / QEMU，Python 仍用于服务、数据预处理、回放、离线比较和算法校验。

## 安装服务与认证

先安装配置和 unit；认证文件单独在服务器生成。仓库只保存模板，不保存密码或认证哈希。

```sh
cd /home/shuxing/repulse
sudo install -d -o root -g www-data -m 750 /etc/repulse
sudo install -o root -g root -m 644 deploy/nginx.conf /etc/repulse/nginx.conf
sudo install -o root -g root -m 644 deploy/repulse.service /etc/systemd/system/repulse.service
sudo install -o root -g root -m 644 deploy/repulse-proxy.service /etc/systemd/system/repulse-proxy.service
```

首次创建账号时执行下列命令，在终端提示中输入密码；`repulse` 是可自行替换的登录用户名。已有认证文件时去掉 `-c`，以免覆盖已有账号。更新密码同样使用不带 `-c` 的命令。[htpasswd 选项说明](https://httpd.apache.org/docs/2.4/programs/htpasswd.html)

```sh
sudo htpasswd -B -c /etc/repulse/htpasswd repulse
sudo chown root:www-data /etc/repulse/htpasswd
sudo chmod 640 /etc/repulse/htpasswd
sudo install -d -o root -g root -m 755 /run/repulse-proxy
sudo install -d -o root -g root -m 750 /var/log/repulse-proxy
sudo /usr/sbin/nginx -t -c /etc/repulse/nginx.conf
sudo systemd-analyze verify /etc/systemd/system/repulse.service /etc/systemd/system/repulse-proxy.service
sudo systemctl daemon-reload
sudo systemctl enable --now repulse.service repulse-proxy.service
```

Nginx 的运行及日志目录以后由 unit 自动创建。上面的手工创建用于首次在启动服务前执行配置检查。若服务因连续失败触发启动限制，修正日志中的原因后执行 `sudo systemctl reset-failed repulse.service repulse-proxy.service`，再启动。

## 只发布 Funnel 8443

先检查本地服务。第一条请求应返回 `401`，第二条会询问密码并应返回 `200`，第三条在正确认证下仍应因 Origin 不匹配返回 `403`。

```sh
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8766/api/bootstrap
curl -u repulse -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8766/api/bootstrap
curl -u repulse -H 'Origin: https://example.invalid' -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8766/api/bootstrap
tailscale serve status
sudo tailscale funnel --bg --https=8443 http://127.0.0.1:8766
tailscale funnel status
tailscale serve status
```

确认状态中 `443 → 8787` 仍是私有服务，`8443 → 8766` 是公网 Funnel。Funnel 首次启用可能给出网页授权链接，需要 tailnet 管理权限完成；它依赖 MagicDNS、HTTPS 证书和 Funnel 节点属性。`--bg` 会保存配置，并在 Tailscale 或 Linux 实例重新启动后恢复。该地址使用 Tailscale 的 `.ts.net` 域名，Funnel 自身不能直接绑定其他自有域名。[Funnel 要求](https://tailscale.com/docs/features/tailscale-funnel)、[CLI 与持久运行](https://tailscale.com/docs/reference/tailscale-cli/funnel)

从不连接 tailnet 的浏览器打开公网地址，登录后检查首页、工作台和 API 文档；在开发者工具的 Network / WS 中确认 `/api/live` 返回 `101` 并持续接收消息。再执行一次 QEMU 演示及独立回放，检查回放 WebSocket。所有页面与接口都共用这层认证。

## 日常运行

```sh
sudo systemctl status repulse.service repulse-proxy.service --no-pager
sudo journalctl -u repulse.service -u repulse-proxy.service -n 80 --no-pager
sudo tail -n 50 /var/log/repulse-proxy/error.log
sudo systemctl restart repulse.service
sudo systemctl reload repulse-proxy.service
```

应用修改后重启 `repulse.service`；Nginx 配置修改后先用独立 `-c` 路径检查，再 reload `repulse-proxy.service`。默认 `nginx.service` 的启停不会替代本部署的代理实例。

只关闭此次公网入口时执行：

```sh
sudo tailscale funnel --https=8443 off
```

这只移除 8443，不清空其他 Serve / Funnel 配置。重新发布时使用前面的完整 `--bg --https=8443` 命令。

## 更新与回滚

更新前确保没有正在处理的演示，服务器仓库没有未保存修改。以下操作停止应用后备份运行目录，并记录当前代码和配置；认证文件继续保留在 `/etc/repulse/htpasswd`。每次更新使用独立备份目录。更新命令保留服务器当前 Nginx 配置及免密码覆盖；若确需变更代理配置，先比较仓库模板与线上文件，再合并所需改动。

```sh
cd /home/shuxing/repulse
git status --short
REPULSE_BACKUP_DIR="/home/shuxing/.local/state/repulse-deploy/$(date +%Y%m%d-%H%M%S)"
install -d -m 700 "$REPULSE_BACKUP_DIR"
git rev-parse HEAD > "$REPULSE_BACKUP_DIR/revision"
.venv/bin/python -m pip freeze --exclude-editable > "$REPULSE_BACKUP_DIR/dependencies-before.txt"
sudo cp /etc/repulse/nginx.conf "$REPULSE_BACKUP_DIR/nginx.conf"
sudo cp /etc/systemd/system/repulse.service "$REPULSE_BACKUP_DIR/repulse.service"
sudo cp /etc/systemd/system/repulse-proxy.service "$REPULSE_BACKUP_DIR/repulse-proxy.service"
sudo systemctl stop repulse.service
tar -C /home/shuxing/.local/share -czf "$REPULSE_BACKUP_DIR/runtime.tar.gz" repulse
git pull --ff-only
.venv/bin/python -m pip install -e .
make -C firmware TARGET=qemu
sudo install -o root -g root -m 644 deploy/repulse.service /etc/systemd/system/repulse.service
sudo install -o root -g root -m 644 deploy/repulse-proxy.service /etc/systemd/system/repulse-proxy.service
sudo /usr/sbin/nginx -t -c /etc/repulse/nginx.conf
sudo systemctl daemon-reload
sudo systemctl start repulse.service
sudo systemctl restart repulse-proxy.service
.venv/bin/python -m pip freeze > "$REPULSE_BACKUP_DIR/dependencies-after.txt"
```

若中途出错，先处理报错或回滚，不继续运行后面的启动步骤。配置及代码回滚时，先将 `REPULSE_BACKUP_DIR` 设置为实际备份目录，确认其中 Nginx 配置保留当前免密码覆盖，再执行：

```sh
cd /home/shuxing/repulse
sudo systemctl stop repulse.service
git switch --detach "$(cat "$REPULSE_BACKUP_DIR/revision")"
.venv/bin/python -m pip install -r "$REPULSE_BACKUP_DIR/dependencies-before.txt"
.venv/bin/python -m pip install --no-deps -e .
make -C firmware TARGET=qemu
sudo install -o root -g root -m 644 "$REPULSE_BACKUP_DIR/nginx.conf" /etc/repulse/nginx.conf
sudo install -o root -g root -m 644 "$REPULSE_BACKUP_DIR/repulse.service" /etc/systemd/system/repulse.service
sudo install -o root -g root -m 644 "$REPULSE_BACKUP_DIR/repulse-proxy.service" /etc/systemd/system/repulse-proxy.service
sudo /usr/sbin/nginx -t -c /etc/repulse/nginx.conf
sudo systemctl daemon-reload
sudo systemctl start repulse.service
sudo systemctl restart repulse-proxy.service
```

回滚代码不会覆盖新产生的记录。只有确认需要恢复数据时，才在应用停止后另外恢复 `runtime.tar.gz`，并先保留当前运行目录。上述回滚将 Git 切换到指定提交；后续恢复跟踪主分支时执行 `git switch main`。Funnel 8443 配置在应用更新中保持不变。

## Windows 与 WSL 常驻

`systemctl enable` 负责 Ubuntu 实例启动后的 Linux 服务自启；**systemd 服务本身不会让 WSL 实例一直存活**。Windows 的启动任务和 WSL 保活需单独配置与验证。Windows 关机、重启、睡眠，或执行 `wsl --shutdown` 都会使网站离线；Funnel 不能代替服务器持续运行。[Microsoft WSL / systemd 说明](https://learn.microsoft.com/en-us/windows/wsl/systemd)

Legion 已另行配置 Windows 计划任务 `RePulse-Legion-WSL`：用户 `ShuXing` 登录后，以隐藏窗口运行 `wsl.exe -d Ubuntu-24.04 --exec /bin/sleep infinity`，保持实例存活，再由 systemd 启动应用、代理与 Tailscale。该任务配置为异常退出后重试，部署时已确认任务与保活进程正在运行。Windows 重启后需要该用户登录；没有配置自动登录，也没有改变电源设置。

Linux 模板不会创建 Windows 任务。可在任务计划程序中查看、禁用或删除 `RePulse-Legion-WSL`。部署时不强制重启 Windows 或 WSL，以免中断现有服务；仍需在方便时验证整机重启并登录后的恢复。Legion 必须保持开机、联网，Windows 睡眠期间网站不可用。
