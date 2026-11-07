# Day8 → residual 原生网关

macOS / Linux 均由 **mihomo** 路由，通过本项目的原生 Python Muon 适配程序连接 Day8，再经过 residual SOCKS5 出口。**macOS 正式部署不使用容器，也不需要 Day8 客户端。** Linux 可选容器封装；容器曾用于隔离验证，不是 macOS 的部署依赖。

```text
macOS 现有 Clash MetaX / Mihomo（原生）
  └─ Residual-egress SOCKS5，dialer-proxy=Day8-native
       └─ 127.0.0.1 原生 Python 适配程序 ── Muon ── Day8 ── residual ── 目标站点

macOS / Linux 独立模式（原生）
  应用 ── 本地 mihomo ── 本地 Python 适配程序 ── Day8 ── residual ── 目标站点
```

两跳指 Day8 和 residual 两个远端跳点。本地适配程序不提供远端出口。实际节点地址、协议密钥、residual 凭据及预期出口 IP 都在 `secrets/deployment.enc.json` 中，部署时由用户在终端隐藏输入密码。

## 开始部署

需要 Git、Python 3.11+、curl；macOS 支持 Apple Silicon / Intel，Linux 支持 ARM64 / x86_64。Linux 的 Python 可能还需系统包 `python3-venv`。bootstrap 在项目目录安装依赖与校验过 SHA-256 的官方 mihomo，不修改系统代理或 TUN。

```sh
git clone git@github.com:skywalkerNo99/day8-mihomo-gateway.git
cd day8-mihomo-gateway
python3 gateway bootstrap
```

- **macOS 接入现有 Clash MetaX**：按 [macOS 原生部署](docs/macos.md) 操作。现有 mihomo 直接路由两跳；不转发到容器。
- **Linux 或 macOS 独立网关**：按 [独立原生部署](docs/native.md) 操作。`./gateway run --detach` 解密后启动原生后台进程，默认仅监听 `127.0.0.1:17890`；不会自动切换整机网络。
- **可选 Linux 容器**：[Linux 容器](docs/container.md)，仅用于需要容器的 Linux 环境或隔离验收。
- **让模型接手部署**：先读 [AGENTS.md](AGENTS.md)，再按对应平台文档；不要向模型发送密码。

## Apple / ChatGPT / OpenAI 路由范围

macOS 提供两种策略：

| 策略 | 行为 | 覆盖边界 |
|---|---|---|
| `--strict`（要求“全部”时推荐） | 除本地地址和 Day8 引导连接外，所有 TCP 请求经 residual；UDP 拒绝 | 被该 mihomo 接管的连接，无需猜测未来域名；其他公网服务也使用 residual |
| 域名模式（不加 `--strict`） | Apple、OpenAI、官方依赖域名与 Apple IP 段优先经 residual，其余沿用原规则 | 内置列表为快照；不能保证未来新增或未知域名全部命中 |

独立网关默认采用严格策略。两跳节点均固定，无自动回落 VPN / DIRECT。TCP 路径不可用时连接失败。Muon 当前只支持 TCP，因此必须显式拒绝目标 UDP，不能仅依赖节点 `udp: false`；HTTP/3 通常回退 TCP，但依赖 UDP 的功能可能不可用。

严格策略的前提是流量确实进入该 mihomo：macOS 通常需要现有 TUN，Linux 可配置应用代理或显式开启 TUN。只开 HTTP 系统代理不能覆盖忽略代理的应用。参见 [路由与验证](docs/routing.md)。

## 加密与运行

加密使用 Argon2id（64 MiB、3 次、4 lanes）派生密钥及 AES-256-GCM。密码不放入命令参数、环境变量、Git、日志或钥匙串。解密配置在内存中构造，通过 stdin 送入独立 mihomo，或通过回环控制 API 热加载至现有 macOS mihomo；不会生成明文配置文件。

后台进程活着时可以自动重启 mihomo 子进程。整台机器重启、主监督进程退出、macOS Clash 核心重启后，需要用户再次输入密码部署。项目不保存密码来实现无人值守解锁。密码忘记后无法从本仓库恢复凭据。

## 维护

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
python3 scripts/audit.py
./gateway rekey
```

规则来源见 [rules/SOURCES.md](rules/SOURCES.md)，协议说明见 [docs/protocol.md](docs/protocol.md)，已有验收与边界见 [docs/validation.md](docs/validation.md)。本项目适配的是已分析的 Day8 Muon 版本，不宣称兼容未来所有协议变更。

`ci/github-actions.yml.example` 是可选自动化模板，当前未启用工作流；已在本机 macOS 和隔离 Linux 环境执行检查。启用说明见验收文档。
