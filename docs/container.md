# Linux 容器

在目标 Linux 主机安装 Docker Engine 和 Compose，克隆仓库。从仓库根目录运行：

```sh
docker compose build day8-gateway
docker compose run --rm enroll
docker compose up -d day8-gateway
```

第二条命令在终端隐藏提示 vault 密码。它验证凭据，然后把仅适用于这份 vault 的解密密钥写入该 Docker 主机的专用卷。第三条命令后台启动代理，以后重启容器或重新创建服务都从只读卷解锁。Docker 服务随主机启动且容器此前处于运行状态时，`restart: unless-stopped` 会把它重新拉起。手动停止后用 `docker compose up -d` 启动。

宿主机应用使用 `127.0.0.1:17890`。接入同一 Compose 网络（默认 `day8_default`）的业务容器使用 `day8-gateway:17890`。该端口服务于显式配置代理的应用，当前公网转发支持 TCP。已有手动部署占用宿主机 17890 时，先完成 enroll，再于切换时停止对应旧容器或给新服务选择另一个宿主端口。

```sh
docker compose restart day8-gateway
docker compose exec -T day8-gateway python3 gateway status
docker compose exec -T day8-gateway python3 gateway verify
```

等服务就绪后执行 status / verify。verify 通过代理访问 ChatGPT 并比对预期出口。日常升级用 `docker compose up -d --build`；`docker compose down` 保留密钥卷。更换主机、删除卷、变更 Compose 项目名或更换加密 vault 后，重新执行 enroll，再启动或重启服务。

原始密码只在首次登记时用于密钥派生。Docker 卷保存的是等价于解锁当前 vault 的秘密，目录权限 0700，密钥文件权限 0600。能读取卷及密文的管理员或备份系统可以解密这份配置。运行服务只读挂载密钥卷，备份加密配置时将该卷视同凭据。撤销本机自动启动和解锁，可对本项目执行 `docker compose down -v`；这会移除服务和本项目的密钥卷。已经复制的密钥仍能解开匹配的旧密文。

如需每次手动解锁，镜像原有的交互式 `docker run --rm -it` 方式仍可使用。在 Mac 上正式部署请参照 [macOS 原生接入](macos.md)。
