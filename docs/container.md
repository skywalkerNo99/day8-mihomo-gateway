# Linux 容器

在目标 Linux 主机安装 Docker Engine 和 Compose，克隆仓库。从仓库根目录运行：

```sh
docker compose build day8-gateway
docker compose run --rm enroll
docker compose up -d day8-gateway
```

第二条命令在终端隐藏提示 vault 密码。它验证凭据，然后把仅适用于这份 vault 的解密密钥写入该 Docker 主机的专用卷。第三条命令后台启动代理，以后重启容器或重新创建服务都从只读卷解锁。Docker 服务随主机启动且容器此前处于运行状态时，`restart: unless-stopped` 会把它重新拉起。手动停止后用 `docker compose up -d` 启动。

### 业务容器的 UDP 出口限制

本配置使用 Docker Engine 28+、Compose 2.33.1+。网关同时连接内部网络 `day8_protected` 和出口网络 `day8_uplink`。要求 UDP 不绕行的业务容器**只接入内部网络**，其应用代理设为 `http://day8-gateway:17890`。内部网络关闭 IPv6 且不设宿主桥接网关地址，网关容器关闭 IP 转发。忽略代理的应用无法经默认路由直接访问公网。当前网关对所有公网 UDP 拒绝，覆盖未知 IP、QUIC 和自定义 UDP 端口；支持的公网 TCP 继续按规则分流。

在业务容器自己的 Compose 文件中，把下面的网络设置合并到实际服务，替换原有公网网络。代理环境变量适用于支持它们的应用。

```yaml
services:
  app:
    # 保留业务自己的 image、command 等设置。
    networks: [protected]
    environment:
      HTTPS_PROXY: http://day8-gateway:17890
      HTTP_PROXY: http://day8-gateway:17890

networks:
  protected:
    external: true
    name: day8_protected
```

升级的顺序是先在网关项目执行 `docker compose up -d --build`，再用更新后的业务 Compose 重建业务容器。用 `docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' 容器名` 核对只列出 `day8_protected`。如果改过网关 Compose 项目名，使用相应的网络名前缀。旧 `day8_default`、普通 bridge、host 网络和额外出口都会绕过这层隔离。内部网络只连接受管业务和这个代理，不接入其他转发器。

宿主机也可显式访问 `127.0.0.1:17890`，业务容器的网络隔离仅约束接入内部网络的容器。已有手动部署占用宿主机 17890 时，完成 enroll 后在切换时停止对应旧容器，或选择另一个宿主端口。

```sh
docker compose restart day8-gateway
docker compose exec -T day8-gateway python3 gateway status
docker compose exec -T day8-gateway python3 gateway verify
```

等服务就绪后执行 status / verify。verify 通过代理访问 ChatGPT 并比对预期出口。日常升级用 `docker compose up -d --build`；`docker compose down` 保留密钥卷。更换主机、删除卷、变更 Compose 项目名或更换加密 vault 后，重新执行 enroll，再启动或重启服务。

原始密码只在首次登记时用于密钥派生。Docker 卷保存的是等价于解锁当前 vault 的秘密，目录权限 0700，密钥文件权限 0600。能读取卷及密文的管理员或备份系统可以解密这份配置。运行服务只读挂载密钥卷，备份加密配置时将该卷视同凭据。撤销本机自动启动和解锁，可对本项目执行 `docker compose down -v`；这会移除服务和本项目的密钥卷。已经复制的密钥仍能解开匹配的旧密文。

如需每次手动解锁，镜像原有的交互式 `docker run --rm -it` 方式仍可使用。在 Mac 上正式部署请参照 [macOS 原生接入](macos.md)。
