# ARM Ubuntu 部署与更新

适用于 Q6A / Ubuntu 24.04 arm64，建议内存 ≥2 GB。仅局域网使用，默认 `http://设备IP:30127`，不使用 Nginx。

## 发布

提交代码后推送版本标签（例如 `v0.4.0`），GitHub Actions 自动生成 Release：

```bash
git tag v0.4.0
git push origin v0.4.0
```

发布包含 ARM64 / Python 3.12 二进制依赖、源码和 SHA256 校验。CI 进行依赖导入和语法检查，不跑全量图片测试。预发布标签可使用 `v0.4.0-rc.1`；默认更新只跟随稳定版。首次需启用仓库 Actions。

## 首次安装

从 GitHub Release 下载 `wechat-avatar-mask-arm64.tar.gz` 和同名 `.sha256`，传到设备后执行：

```bash
sha256sum -c wechat-avatar-mask-arm64.tar.gz.sha256
tar -xzf wechat-avatar-mask-arm64.tar.gz
cd wechat-avatar-mask
sudo bash deploy/install.sh
```

完成后显示访问地址并启用开机启动。可用 `--port`、`--listen` 更改监听配置。系统依赖仅首次安装时下载，脚本不改防火墙。

## 更新

旧包安装的服务器，先在设备终端执行一次以下命令，接入新版更新功能。使用独立临时目录，保留现有端口和配置，无需提前停服务：

```bash
(
set -e
cd "$(mktemp -d)"
release=https://github.com/cossoledad/WeChatAvatarMask/releases/download/v0.4.0
curl -fL --retry 3 "$release/wechat-avatar-mask-arm64.tar.gz" -o wechat-avatar-mask-arm64.tar.gz
curl -fL --retry 3 "$release/wechat-avatar-mask-arm64.tar.gz.sha256" -o wechat-avatar-mask-arm64.tar.gz.sha256
sha256sum -c wechat-avatar-mask-arm64.tar.gz.sha256
tar -xzf wechat-avatar-mask-arm64.tar.gz
sudo bash wechat-avatar-mask/deploy/install.sh --skip-apt
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh health
)
```

完成后刷新浏览器。旧版只存于内存的任务和浏览器临时头像无法迁移，需要重新上传一次；新版起更新保留头像库及缓存。

后续更新只需：

```bash
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh update
# 只查看版本 / 更新到指定标签：
bash /opt/wechat-avatar-mask/current/deploy/manage.sh update --check
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh update v0.4.0
```

需要设备能访问 GitHub。脚本下载并校验发布包，准备新环境后切换；激活失败恢复旧版本，保留配置、头像和任务缓存。私有仓库可通过 `GH_TOKEN` 环境变量提供读取权限，运行 sudo 时显式保留该变量。

```bash
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh rollback
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh status
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh logs
sudo bash /opt/wechat-avatar-mask/current/deploy/manage.sh stop
```

代码：`/opt/wechat-avatar-mask/releases/`；配置：`/etc/wechat-avatar-mask.env`；头像及图片缓存：**`/var/lib/wechat-avatar-mask/cache.sqlite3`**；更新包：`/var/cache/wechat-avatar-mask/releases/`。数据和旧版本不自动清理，需关注磁盘空间。回滚到尚无持久存储功能的旧版时数据仍保留，但该旧版无法读取。

不通过 CI 时，可运行 `python scripts/package_deploy.py` 本地打包；这类包不含依赖，安装时需连接 PyPI。
