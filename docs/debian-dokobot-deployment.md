# Debian + Xvfb + Dokobot 手动部署

本文将芯闻采集台部署为以下结构：

```text
Nginx :80
  |-- /            -> /opt/semicrawler/dist
  `-- /api, /docs  -> FastAPI 127.0.0.1:8000

FastAPI (用户 semicrawler)
  -> dokobot read --local
  -> ~/.dokobot/bridges/*.sock
  -> Dokobot Chrome 扩展
  -> Xvfb :99 中的 Chrome
```

Chrome 仍然以普通有界面模式运行，只是界面绘制在 Xvfb 虚拟显示器中。首次安装扩展或处理验证码时，通过仅监听本机的临时 VNC 查看该界面。

## 1. 前提

- Debian 12 或 13，`amd64/x86_64` 架构。
- 具有 `root` 或 `sudo` 权限。
- 服务器能访问 npm、Google Chrome 软件源和 Chrome Web Store。
- 下列命令中的 `<仓库地址>`、服务器地址和域名需要替换为实际值。

Google 官方没有提供 Debian ARM64 Chrome 包。ARM64 服务器不能直接套用本文的 Chrome 服务模板，应改用 Dokobot 支持的其他浏览器并同步修改二进制、profile 和 Native Messaging 路径。

本应用目前没有登录鉴权。不要直接暴露到公网；应限制防火墙来源、放在 VPN/内网后，或在 Nginx 前增加认证。

## 2. 安装系统依赖

```bash
sudo apt update
sudo apt install -y \
  ca-certificates curl git gnupg nginx x11vnc xvfb
```

安装 Node.js 22：

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node --version
npm --version
```

安装 Google Chrome Stable：

```bash
curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
  | sudo gpg --dearmor --yes -o /usr/share/keyrings/google-chrome.gpg

echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" \
  | sudo tee /etc/apt/sources.list.d/google-chrome.list >/dev/null

sudo apt update
sudo apt install -y google-chrome-stable
google-chrome-stable --version
```

创建专用用户和目录：

```bash
sudo useradd --create-home --shell /bin/bash semicrawler
sudo install -d -o semicrawler -g semicrawler /opt/semicrawler
sudo install -d -o semicrawler -g semicrawler /var/lib/semicrawler/data
sudo install -d -o root -g semicrawler -m 0750 /etc/semicrawler
```

如果用户已经存在，`useradd` 报错可以忽略，但应确认其 home 是 `/home/semicrawler`：

```bash
getent passwd semicrawler
```

## 3. 安装应用

从 Git 仓库部署：

```bash
sudo -u semicrawler -H git clone <仓库地址> /opt/semicrawler
```

如果代码已经上传到该目录，只需修正所有者：

```bash
sudo chown -R semicrawler:semicrawler /opt/semicrawler
```

以应用用户安装 `uv`、Python 3.12 和依赖。Debian 12 自带 Python 3.11，不能直接运行本项目，所以这里让 `uv` 安装独立的 Python 3.12：

```bash
sudo -u semicrawler -H sh -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'

sudo -u semicrawler -H bash -lc '
  cd /opt/semicrawler
  ~/.local/bin/uv python install 3.12
  ~/.local/bin/uv sync --frozen --no-dev
  npm ci
  npm run build
'
```

安装 Dokobot CLI 并记录实际路径：

```bash
sudo npm install -g @dokobot/cli@latest
command -v dokobot
dokobot --version
```

默认通常是 `/usr/bin/dokobot`。如果 `command -v` 返回其他路径，稍后要修改 `/etc/semicrawler/semicrawler.env`。

## 4. 安装服务模板

```bash
cd /opt/semicrawler

sudo cp deploy/debian/semicrawler-xvfb.service /etc/systemd/system/
sudo cp deploy/debian/semicrawler-chrome.service /etc/systemd/system/
sudo cp deploy/debian/semicrawler-api.service /etc/systemd/system/

sudo cp deploy/debian/semicrawler.env.example \
  /etc/semicrawler/semicrawler.env
sudo chown root:semicrawler /etc/semicrawler/semicrawler.env
sudo chmod 0640 /etc/semicrawler/semicrawler.env

sudo cp deploy/debian/nginx-semicrawler.conf \
  /etc/nginx/sites-available/semicrawler
sudo ln -sfn /etc/nginx/sites-available/semicrawler \
  /etc/nginx/sites-enabled/semicrawler
sudo rm -f /etc/nginx/sites-enabled/default
```

检查 `/etc/semicrawler/semicrawler.env`：

```bash
sudo editor /etc/semicrawler/semicrawler.env
```

默认内容为：

```ini
SEMICRAWLER_DATA_DIR=/var/lib/semicrawler/data
SEMICRAWLER_DOKOBOT_EXECUTABLE=/usr/bin/dokobot
SEMICRAWLER_DOKOBOT_HOME=/home/semicrawler
```

检查模板和前端文件：

```bash
sudo nginx -t
test -f /opt/semicrawler/dist/index.html
sudo systemd-analyze verify \
  /etc/systemd/system/semicrawler-xvfb.service \
  /etc/systemd/system/semicrawler-chrome.service \
  /etc/systemd/system/semicrawler-api.service
```

先只启动虚拟显示器和 Chrome：

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now semicrawler-xvfb.service
sudo systemctl enable --now semicrawler-chrome.service
sudo systemctl status semicrawler-xvfb.service semicrawler-chrome.service
```

注意：Chrome 服务没有使用 `--no-sandbox`。服务以非 root 用户运行，保留 Chrome sandbox 更安全。

## 5. 通过临时 VNC 安装扩展

在服务器 SSH 会话中启动仅监听回环地址的 VNC：

```bash
sudo -u semicrawler -H env DISPLAY=:99 \
  x11vnc -display :99 -localhost -forever -shared -rfbport 5900
```

保持该命令运行。在自己的电脑上另开一个终端，建立 SSH 隧道：

```bash
ssh -N -L 5900:127.0.0.1:5900 <SSH用户>@<服务器地址>
```

使用任意 VNC 客户端连接 `127.0.0.1:5900`。在 Chrome 中：

1. 打开 Dokobot Chrome Web Store 页面：`https://chromewebstore.google.com/detail/dokobot/dlbiigchkpmpijahmlofleeemiomaneo`。
2. 安装并启用扩展。
3. 打开 `chrome://extensions`，确认 Dokobot 已启用。

VNC 使用的正是 systemd Chrome 服务的持久 profile：

```text
/home/semicrawler/.config/google-chrome
```

不要另开使用同一个 profile 的 Chrome 进程，否则会遇到 profile lock。

## 6. 安装 Dokobot Native Messaging Bridge

保持 VNC 和 Chrome 打开，在服务器的另一个 SSH 会话执行：

```bash
sudo -u semicrawler -H /usr/bin/dokobot install-bridge --browser chrome
```

命令显示等待扩展连接时，回到 VNC 中的 `chrome://extensions`，点击 Dokobot 的重新加载按钮。正常情况下命令会显示 `Bridge connected`。

如果 CLI 不在 `/usr/bin/dokobot`，换成前面 `command -v dokobot` 的结果。若第一次安装超时，重启 Chrome 后再执行一次：

```bash
sudo systemctl restart semicrawler-chrome.service
sudo -u semicrawler -H /usr/bin/dokobot install-bridge --browser chrome
```

验证桥接及真实读页：

```bash
sudo -u semicrawler -H /usr/bin/dokobot doko list
sudo -u semicrawler -H /usr/bin/dokobot read --local \
  https://dokobot.ai --timeout 90
```

必须在输出中看到在线的本地浏览器，并成功返回网页正文。完成后按 `Ctrl+C` 停止 `x11vnc`；不要停止 Chrome 和 Xvfb 服务。VNC 端口因为只监听 `127.0.0.1`，不会暴露到公网。

## 7. 启动 API 和 Nginx

```bash
sudo systemctl enable --now semicrawler-api.service
sudo systemctl enable --now nginx

curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1/api/health
```

也可以运行仓库内的联合检查：

```bash
sudo -u semicrawler -H \
  bash /opt/semicrawler/deploy/debian/check-deployment.sh
```

浏览器访问：

- 工作台：`http://<服务器地址>/`
- API 文档：`http://<服务器地址>/docs`

在工作台“API配置”页面保存 OpenAI 兼容模型地址、模型名和 API Key，然后添加“联网搜索”来源执行一次小规模任务。

## 8. 日常运维

查看状态和日志：

```bash
sudo systemctl status \
  semicrawler-xvfb semicrawler-chrome semicrawler-api nginx
sudo journalctl -u semicrawler-api -f
sudo journalctl -u semicrawler-chrome -f
```

更新应用：

```bash
sudo -u semicrawler -H bash -lc '
  cd /opt/semicrawler
  git pull --ff-only
  ~/.local/bin/uv sync --frozen --no-dev
  npm ci
  npm run build
'

sudo systemctl restart semicrawler-api
sudo systemctl reload nginx
```

备份 SQLite。备份前暂停 API，避免复制到正在写入的数据库：

```bash
sudo systemctl stop semicrawler-api
sudo cp -a /var/lib/semicrawler/data/semi_crawler.db \
  /var/lib/semicrawler/data/semi_crawler.db.backup
sudo systemctl start semicrawler-api
```

## 9. 常见故障

### `未找到 Dokobot CLI`

```bash
command -v dokobot
sudo systemctl show semicrawler-api -p Environment
sudo cat /etc/semicrawler/semicrawler.env
```

把 `SEMICRAWLER_DOKOBOT_EXECUTABLE` 改成绝对路径，然后执行：

```bash
sudo systemctl restart semicrawler-api
```

### `No local bridge running` 或 `Bridge process not responding`

确认三个进程使用同一个用户和 HOME：

```bash
ps -o user,pid,cmd -C Xvfb -C chrome -C node
sudo -u semicrawler -H ls -la /home/semicrawler/.dokobot/bridges
sudo -u semicrawler -H /usr/bin/dokobot doko list
```

然后重启 Chrome，并通过临时 VNC 在 `chrome://extensions` 重新加载 Dokobot。

### `Bridge is running but Chrome extension is not connected`

Chrome profile 中没有扩展、扩展被禁用，或者 Chrome 启动的是另一个 profile。检查 Chrome 服务必须包含：

```text
--user-data-dir=/home/semicrawler/.config/google-chrome
```

重新打开临时 VNC检查扩展状态。

### 搜索结果为空或出现验证码

Dokobot 会读取 Google/Bing 搜索页，服务器 IP 可能触发验证码。通过临时 VNC 在同一个 Chrome profile 中完成验证后重试。不要通过提高并发绕过站点限制。

### Chrome 启动失败

```bash
sudo journalctl -u semicrawler-chrome -n 100 --no-pager
sudo -u semicrawler -H find /home/semicrawler/.config/google-chrome \
  -maxdepth 1 -name 'Singleton*' -ls
```

不要在服务运行时删除 `Singleton*` 文件。先确认没有其他 Chrome 使用该 profile；只有在所有 Chrome 进程都已停止且日志明确显示残留锁时，才处理残留锁文件。
