# Q6A / ARM Ubuntu 局域网部署

目标：`radxa-dragon-q6a`，Radxa Dragon Q6A，Ubuntu 24.04.5 LTS，arm64 / aarch64。**不使用 Nginx，也不需要 Node.js；默认端口 30127。**

## 运行结构

```text
手机 / 电脑浏览器
       │ http://设备IP:30127
       ▼
systemd → 单个 Python 3.12 / Uvicorn / FastAPI 进程
                    ├─ 静态页面 HTML / CSS / JavaScript
                    └─ /api/ 图像处理接口
```

前后端源代码仍独立存放，部署时在同一个端口提供，省去反向代理。静态文件采用明确的公开文件名单，不暴露服务脚本、配置或测试图片。后端固定 **1 个 worker**，内存中的图片任务不会被分散到不同进程。

默认监听 `0.0.0.0:30127`，仅允许本机、RFC1918 私网和 IPv4 链路本地地址；生产配置关闭开发测试集和接口文档。不安装、修改、重启或卸载设备上已有的 Web 服务器，不改防火墙，不更新内核或整机系统。

## Windows 打包、传输

```powershell
.venv\Scripts\python.exe scripts/package_deploy.py
scp artifacts/wechat-avatar-mask-arm64.tar.gz artifacts/wechat-avatar-mask-arm64.tar.gz.sha256 用户名@设备IP:~/
ssh 用户名@设备IP
```

发布包只包括后端、默认头像、4 个静态前端文件、生产依赖和部署脚本。不包含 `test.data`、处理结果、Windows `.venv`、Node 开发服务器或用户上传图片。文本统一为 LF，附带整个包及逐文件 SHA256 校验。

## Q6A 安装

```bash
sha256sum -c wechat-avatar-mask-arm64.tar.gz.sha256
tar -xzf wechat-avatar-mask-arm64.tar.gz
cd wechat-avatar-mask
bash deploy/install.sh --check
sudo bash deploy/install.sh
```

脚本安装 Python 3.12、venv、curl、证书和网络查询工具，创建无登录权限的服务账号、独立版本 venv 及开机服务。生产依赖只使用 ARM64 预编译包；缺少 wheel 时失败退出，不在板子上源码编译。

完成后打印实际地址，例如 **`http://192.168.1.50:30127`**。这里使用 Q6A 的 IP，不是 Windows 电脑的开发地址。

自定义端口或绑定固定局域网地址：

```bash
sudo bash deploy/install.sh --port 30128
sudo bash deploy/install.sh --listen 192.168.1.50 --port 30127
```

绑定具体 IP 前建议在路由器保留该地址。已安装依赖软件包时可以加 `--skip-apt`。已有安装的端口/地址默认保留；需要改变时显式传入对应参数。

## 更新、维护与回滚

更新时重新打包、传输、解压到新的源目录，再执行同一安装脚本。每次使用独立代码目录和 venv，准备完成后才切换 `current`。激活或健康检查失败会恢复旧链接和本项目配置，apt 安装的软件包不自动卸载。

```bash
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh status
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh health
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh logs 100
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh versions
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh rollback
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh restart
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh stop
```

`rollback 版本号` 可以回到 `versions` 中的指定版本，例如 `rollback 20261107T120000Z-1234`。回滚失败会恢复原版本。更新保留管理员 env 和资源配置；更新/回滚有互斥锁。旧版本不自动删除，避免意外失去回退目标。

**重启、更新或回滚都会清除内存任务，需重新选择截图。** 原图不会写进版本目录，没有图片数据迁移步骤。

## 配置与资源

| 路径 | 内容 |
| --- | --- |
| `/opt/wechat-avatar-mask/releases/版本号/` | 代码、独立 venv、实际依赖版本记录 |
| `/opt/wechat-avatar-mask/current` / `previous` | 当前 / 上一个成功版本 |
| `/etc/wechat-avatar-mask.env` | 监听地址、端口、上传和任务限制 |
| `/etc/systemd/system/wechat-avatar-mask.service` | 单个 Web 应用服务 |
| `/etc/systemd/system/wechat-avatar-mask.service.d/resources.conf` | 进程资源上限 |
| `/var/lib/wechat-avatar-mask-deploy/` | 激活前配置备份 |

脚本按设备实际可见内存生成默认值：

| 可见内存 | 单批缓存 | 总任务缓存 | 单图像素 | 进程内存上限 |
| --- | --- | --- | --- | --- |
| 1.5–3 GB（建议至少 2 GB） | 128 MiB | 192 MiB | 1200 万 | 1100 MiB |
| 3–6 GB | 192 MiB | 320 MiB | 1600 万 | 1536 MiB |
| ≥6 GB | 220 MiB | 440 MiB | 1600 万 | 2048 MiB |

缓存限制不等于进程峰值，解码和 PNG/ZIP 导出还需要工作内存。低内存设备应减少单批图片或同时使用人数；达到 systemd 内存上限时进程可能重启。前端会读取服务端实际上传和像素限制。

调整 env 后运行 `manage.sh restart`；修改 resources.conf 后先 `sudo systemctl daemon-reload` 再重启。服务以普通账号运行、代码只读，上传期间框架可能使用私有临时目录，随服务停止清理。未声称安全擦除设备存储，不包含账号或公网访问功能。

## 排查连接

- 手机和 Q6A 位于同一局域网；访客 Wi-Fi / AP 隔离可能阻止互访。
- `manage.sh health` 检查设备本机页面和 API，不能证明路由器允许手机访问。
- 启用 UFW 时可手动按真实内网段开放，例如 `sudo ufw allow from 192.168.1.0/24 to any port 30127 proto tcp`。脚本不会自动改防火墙。
- 端口被其他程序占用时安装会退出，不停止那个程序。
- 默认头像在发布包中；刷新浏览器后自选头像需重新上传。

## 验证范围

已检查 ARM64 / Python 3.12 二进制依赖解析、Bash 语法、配置生成和发布包；对单服务页面、接口和局域网限制做必要验证。**尚未在 Q6A 实际执行安装，不跑全量图片回归。** 首次安装会执行依赖导入、`pip check` 和服务健康检查。

参考：[Ubuntu Python 版本](https://ubuntu.com/developers/docs/reference/availability/python/)、[OpenCV ARM64 包](https://pypi.org/project/opencv-python-headless/)、[FastAPI 文件响应](https://fastapi.tiangolo.com/advanced/custom-response/)、[systemd 服务](https://www.freedesktop.org/software/systemd/man/260/systemd.service.html)。
