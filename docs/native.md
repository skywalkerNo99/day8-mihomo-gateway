# Linux 独立原生运行与回环诊断

Linux 运行原生 mihomo + 原生 Python 适配器，不需要 Day8 客户端或容器。macOS 正式部署只使用[单 App](macos.md)，本页回环命令仅供独立诊断，不得与已安装单 App 的固定端口冲突。需要 Python 3.11+、Git、curl；Linux 安装对应发行版的 `python3-venv`。Linux libc/wheel 不兼容时优先用常见 glibc 发行版，或可选 Linux 容器。bootstrap 不安装系统包、不修改系统路由。

```sh
python3 gateway bootstrap
./gateway run --detach
./gateway status
./gateway verify
```

`run` 交互隐藏输入 vault 密码。后台监督进程保留解密资料在 RAM，启动原生 Muon 适配器与 mihomo。默认端口：

| 用途 | 地址 |
|---|---|
| HTTP / SOCKS5 应用代理 | `127.0.0.1:17890` |
| Muon 本地 SOCKS5 适配器 | `127.0.0.1:17889` |
| Mihomo controller（随机认证，仅内部使用） | `127.0.0.1:17891` |

默认只接受回环连接，不启用 TUN、系统代理。为应用显式配置 HTTP/SOCKS 代理，或测试：

```sh
curl --noproxy '' --proxy http://127.0.0.1:17890 https://chatgpt.com/cdn-cgi/trace
```

上面的 curl 会直接显示真实出口；不要把输出提交到仓库。`gateway verify` 只报告是否匹配预期值，适合保留脱敏验收记录。已有主代理运行时，使用不同回环端口隔离验收。

如果默认端口被占用：

```sh
./gateway run --detach --port 17892 --adapter-port 17893 --controller-port 17894
./gateway verify
```

同一 checkout 同时只运行一个监督进程，换模式前先停止原进程。`verify` 会使用该进程实际的端口。

## 停止、断线与重启

```sh
./gateway stop
```

网络恢复后，新请求重新建立 Muon 与 residual 连接；应用需要重试失败请求。已有 TCP 会话断开后不会无缝续传。mihomo 子进程退出时监督进程尝试重启，配置仍从内存供给；重启失败则停止网关，重新运行并解锁。监督进程退出或机器重启后必须重新输入密码。

前台调试可用 `./gateway run`，按 Ctrl-C 停止。日志不打印完整配置。不要通过环境变量、shell 脚本或 service unit 保存密码。

## 整机 TUN

当前适配器仅实现 TCP。Linux 共享业务网络的单容器部署使用 [容器 TUN 模式](container.md)，由 `run-container-tun` 配置 DNS 接管、公网 UDP 拒绝和命名空间约束。宿主机原生 `run --tun` 需要另外核对 IPv4/IPv6 路由、DNS 和 UDP 出口，不能直接沿用容器验收结果。
