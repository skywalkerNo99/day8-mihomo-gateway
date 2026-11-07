# 可选 Linux 容器

仅适用于希望容器化的 Linux 环境或隔离验证。**macOS 正式部署按 macos.md 原生运行。** 容器内仍是同一套 Linux 原生 mihomo 与 Python Muon 程序，没有 Day8 客户端。

```sh
docker build -f containers/Dockerfile -t day8-mihomo-gateway:local .
docker run --rm -it --name day8-gateway \
  -p 127.0.0.1:17890:17890 \
  --mount "type=bind,src=$(pwd)/secrets/deployment.enc.json,dst=/app/secrets/deployment.enc.json,readonly" \
  day8-mihomo-gateway:local
```

在交互终端隐藏输入 vault 密码。镜像不包含 vault、密码或旧客户端数据；只读挂载的文件是密文。配置通过 stdin 到 mihomo，不生成明文配置卷。只发布 host loopback 的应用代理端口；适配器/controller 不发布。

另一个终端验证：

```sh
docker exec day8-gateway python3 gateway status
docker exec day8-gateway python3 gateway verify
docker stop -t 15 day8-gateway
```

容器内监督进程可恢复 mihomo 子进程。容器整体重启后仍需交互解锁，所以不要用保存密码或无 TTY 的 restart policy 假装支持无人值守。这里没有启用 TUN、特权容器或整机路由；需要那种部署时先明确接管范围和 Linux 网络权限。
