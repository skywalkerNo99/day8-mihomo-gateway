# macOS / Linux 独立原生运行

两端都运行原生 mihomo + 原生 Python 适配器，不需要 Day8 客户端或容器。需要 Python 3.11+、Git、curl；Linux 安装对应发行版的 `python3-venv`。Linux libc/wheel 不兼容时优先用常见 glibc 发行版，或可选 Linux 容器。bootstrap 不安装系统包、不修改系统路由。

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

上面的 curl 会直接显示真实出口；不要把输出提交到仓库。`gateway verify` 只报告是否匹配预期值，适合保留脱敏验收记录。主 Clash 在另一端口继续工作时，可用此模式隔离验收。

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

## Linux 整机 TUN（可选，显式切换）

只有用户明确要求整机接管时才做。先验证普通本地代理可用；确认没有其他正在管理路由的 TUN。需要 `/dev/net/tun` 和 root 或等效权限。bootstrap 先以普通用户完成，再从交互终端运行：

```sh
sudo ./gateway run --tun
```

输入系统授权密码与 vault 密码，二者都不发给模型。前台运行便于 Ctrl-C 回退。启动参数会启用 Mihomo auto-route 和 DNS hijack；Day8 节点的 bootstrap DIRECT 规则避免自环，私有 IPv4 地址直连，所有其余 TCP 走 residual、UDP 拒绝。

停止后核对 TUN/默认路由已撤销；若进程被 SIGKILL 导致残留，按发行版与 Mihomo 文档清理残留接口/路由，不能盲删系统默认路由。IPv6 在生成配置中关闭；不要据此假设系统自身 IPv6 一定被所有版本 TUN 完整接管，应额外核对或显式禁用系统 IPv6。

macOS 独立 TUN 也需要系统授权及适合的签名/网络权限，优先采用已有客户端管理的 TUN 接入方式。不要同时启用两套 TUN。
