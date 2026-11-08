# Day8 默认出口与住宅分流

macOS 可由 Day8 Gateway 原生接管整机 TUN，也支持接入已有的 ClashX Meta。Linux 可独立运行 mihomo 或使用容器。两端共用住宅域名清单和原生 Muon 适配器。部署账户和节点参数保存在密码加密的 vault 中。

## 开始部署

需要 Git、Python 3.11+、curl；macOS 支持 Apple Silicon / Intel，Linux 支持 ARM64 / x86_64。Linux 的 Python 可能还需系统包 `python3-venv`。bootstrap 在项目目录安装依赖与校验过 SHA-256 的官方 mihomo，不修改系统代理或 TUN。

```sh
git clone git@github.com:skywalkerNo99/day8-mihomo-gateway.git
cd day8-mihomo-gateway
python3 gateway bootstrap
```

- **macOS 单 App 或接入现有 Clash MetaX**：按 [macOS 原生部署](docs/macos.md) 操作。安装原生管理 App 和免密登录服务，可分别监测 Day8、1024proxy 及核验 ChatGPT 出口。
- **Linux 或 macOS 独立网关**：按 [独立原生部署](docs/native.md) 操作。`./gateway run --detach` 解密后启动原生后台进程，默认仅监听 `127.0.0.1:17890`；不会自动切换整机网络。
- **可选 Linux 容器**：[Linux 容器](docs/container.md)，仅用于需要容器的 Linux 环境或隔离验收。
- **让模型接手部署**：先读 [AGENTS.md](AGENTS.md)，再按对应平台文档；不要向模型发送密码。

## Apple / ChatGPT / OpenAI 路由范围

| 受接管的公网 TCP | 路径 |
|---|---|
| Apple、ChatGPT/OpenAI 的专用域名和已收录的 Apple 网段 | mihomo → Day8 → 住宅代理 → 目标 |
| Mac 单 App 模式下其余 .cn 域名或分流时解析到中国大陆 IP 的域名 | 物理网卡 DIRECT |
| 其余域名和公网 IP | mihomo → Day8 → 目标 |

当前 Muon 适配器仅实现 TCP。两类公网 UDP 均显式拒绝。Linux 上要求受保护的业务容器只连接 `day8_protected`，使忽略代理的 UDP 也无法直发公网。macOS 整机部署使用显式拒绝公网 UDP 的策略。详见 [路由与验证](docs/routing.md)。

## 加密与运行

加密使用 Argon2id（64 MiB、3 次、4 lanes）派生密钥及 AES-256-GCM。密码不放入命令参数、环境变量、Git、日志或钥匙串。解密配置在内存中构造，通过 stdin 送入独立 mihomo，或通过回环控制 API 热加载至现有 macOS mihomo；不会生成明文配置文件。

后台进程活着时可以自动重启 mihomo 子进程。macOS 登录服务从登录钥匙串解锁，并在 Clash 重载时自动恢复策略。独立原生临时模式重启后需要重新解锁。Linux 容器支持首次输入密码登记、随后自动重启，见 [容器部署](docs/container.md)。密码忘记后无法从本仓库恢复凭据。

## 维护

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
python3 scripts/audit.py
./gateway rekey
```

规则来源见 [rules/SOURCES.md](rules/SOURCES.md)，协议说明见 [docs/protocol.md](docs/protocol.md)，已有验收与边界见 [docs/validation.md](docs/validation.md)。本项目适配的是已分析的 Day8 Muon 版本，不宣称兼容未来所有协议变更。

`ci/github-actions.yml.example` 是可选自动化模板，当前未启用工作流；已在本机 macOS 和隔离 Linux 环境执行检查。启用说明见验收文档。
