# Linux 容器

## 单容器 TUN 与共享网络命名空间

业务容器通过 `network_mode: service:proxy` 使用代理网络时，使用 `compose.tun.yaml`。常驻的 `proxy` 容器内同时运行 Python 监督器、Muon TCP 适配器和 mihomo TUN；住宅第二跳也由这个 mihomo 处理。

```sh
docker compose -f compose.tun.yaml build proxy
docker compose -f compose.tun.yaml run --rm enroll
docker compose -f compose.tun.yaml up -d proxy
docker compose -f compose.tun.yaml exec -T proxy python3 gateway verify
```

将实际业务服务的 `network_mode` 设置为 `service:proxy`，并等待 `proxy` 的健康检查通过。业务服务共享 TUN 的网络命名空间。Apple/OpenAI 专用目标优先使用 Day8 → 住宅第二跳；其余 `.cn` 域名及 CN 网段目标（包括裸 IP）使用容器上联网卡 DIRECT；剩余公网 TCP 使用 Day8。国内网段和 Mac 使用同一份 `cn-networks` 数据及匹配规则。

代理监听本命名空间的 `127.0.0.1:7890`，DNS 监听 `127.0.0.1:1053`。容器启动时安装 TCP/UDP 53 的 DNS 重定向和 `DAY8_UDP` 出口链，允许回环、局域网和组播 UDP，拒绝公网 UDP。容器 sysctl 关闭 IPv6，避免 IPv6 UDP 绕行。

此模式需要 `/dev/net/tun`、`NET_ADMIN` 和独立的 Docker 网络命名空间。使用仓库提供的 namespaced sysctl；不要设为 `network_mode: host`。运行命令为 `run-container-tun`，首次登记后使用只读密钥卷自动解锁。重建共享网络的代理时，也要重建引用它的业务容器，提前结束业务中的运行任务并保留数据及回退配置。

验收应从业务容器发起 ChatGPT trace 并在内存中比较预期出口，再验证普通 HTTPS；还要验证本命名空间的 UDP 拒绝计数和重启自动解锁。原业务镜像、挂载和停止后的原容器可保留用于回退。恢复时先停止新业务与新代理，再启动原代理和原业务。

## 显式应用代理

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

## 双节点监测与连接输出

```sh
docker compose exec -T day8-gateway python3 gateway monitor
docker compose exec -T day8-gateway python3 gateway monitor --once --json
docker compose logs -f --tail 30 day8-gateway
```

monitor 每 30 秒分别对 Day8 第一跳和 Day8 → 1024 住宅出站发起 HTTPS 探测。单次模式在任一路径探测失败时返回非零退出码。连接输出来自实际 mihomo 核心，每条使用东八区 `UTC+08:00` 日期、时间和毫秒，例如 `2026-11-09T12:00:00.123+08:00 [TCP] chatgpt.com:443 via Residual-egress`。业务域名可能出现在日志中，上游节点的引导连接记录会被过滤。通过实际目标服务验证住宅出口使用 `python3 gateway verify`。

Mac 管理 App 的「Linux 容器」页面通过 Docker Context 查找运行中的网关。它支持 `run-container-tun` 单容器部署以及原有 `day8-gateway` 显式代理服务，业务 Compose 服务可以使用自己的名称。页面按容器 ID 记录目标，推送时再次检查运行状态及网关入口，避免重建后向同名的其他容器推送。

远程 Linux 可配置 SSH Context，例如 `docker context create my-gateway --docker host=ssh://user@host`。在页面选择 Context，点击「查找容器」，选择目标，再点击「将本机当前 Day8 线路应用到此容器」。App 将当前本机 Day8 线路封装成认证加密包，经容器 stdin 交给网关；住宅配置、路由策略和预期出口继续使用远端已登记的配置。更新通过远端 ChatGPT 出口核验后，加密保存到 state 卷，重启时重新加载。

单容器 TUN 需要支持节点更新版本 2 的镜像，App 会在推送前检查。TUN 更新会先保留新旧节点的引导直连规则，核验新节点后提交新规则；失败时恢复原节点及规则。升级镜像后要同时重建共享该网络命名空间的业务容器。密钥卷、密文和更新包须对应同一份基础 vault。
