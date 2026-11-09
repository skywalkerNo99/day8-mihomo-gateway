# 验收记录与边界

日期：2026-11-07。本文不包含真实节点、用户名、密码或预期 IP。预期 IP 在加密 vault 中，验收只记录匹配布尔值。

## 已完成

- macOS ARM64 / Python 3.14：14 项协议、退出清理、加密和路由测试全部通过。
- Linux ARM64 / Python 3.12：相同 14 项测试全部通过。Linux 环境用容器隔离提供，不代表 macOS 部署使用容器。
- 官方 Mihomo v1.19.24：macOS/Linux 均接受内嵌域名/IP rule-providers、AND UDP REJECT 与严格模式，均通过内存 payload API 加载及子核心监督重启测试。
- macOS 独立原生真实链：初始三次、子核心重启后三次 ChatGPT trace 均匹配 vault 中预期 IP，共 6/6。验收端口独立，未切换主机系统路由。
- 用户在本机终端完成 vault 密码两次隐藏输入，加密程序回读校验成功。仓库扫描未发现已知真实节点/凭据明文。没有把密码交给模型。
- 主机原 Clash profile 与恢复备份逐字节一致；验收期间 Day8.app 未运行。

## Linux 首请求问题

首次真实链路三次检查中，一次 curl TLS 错误（35）、两次出口匹配。有限复核的启动首请求出现一次代理连接错误（7），随后两次匹配；该次子核心重启后的三次全部匹配。这些失败没有被重试隐藏，也没有观察到其他出口 IP。

检查发现就绪判断只探测了 controller HTTP；它不能证明 mixed-port 已开始服务。程序已增加真实 SOCKS5 无认证协商作为就绪条件。TLS 失败的原因没有仅凭退出码确定，不能全部归因于这个就绪问题。

修改后有限验收：Linux 启动后三次、子核心重启后三次 ChatGPT trace 全部匹配预期出口，共 6/6；人工故障注入前子核心重启计数为 0。此结论不抹去前面的失败，也不扩展成长期稳定保证。

## 未证明的事项

- 没有在本轮对用户主 Clash 做正式切换；macOS 接入 API 在独立原生核心验证，不把它写成已完成用户 GUI 全流程验收。
- 没有重新运行一小时稳定性测试；历史隔离压测由用户提前停止，不能写成完整一小时通过。
- trace 验证的是 ChatGPT 站点看到的连接 IP，不是登录、聊天、全部 Apple 服务或所有流量永远不变的证明。
- Mac Intel / Linux x86_64 的官方二进制固定版本与 checksum 已准备，但当前本地验收平台是 ARM64。`ci/github-actions.yml.example` 提供 macOS/Linux CI 模板，当前没有启用：推送使用的 OAuth 凭据缺少 workflow scope。需要自动 CI 时，由有该权限的维护者把模板放入 `.github/workflows/test.yml` 再提交。
- 严格策略保证进入该核心、受支持的公网 TCP 路由不回退；不代表没有进入核心的连接也受控。隔离原生验收保持主机既有 TUN 不变，不宣称证明 Day8 节点连接在物理链路上完全绕开原主机 TUN。

## 可重复检查

```sh
python3 gateway bootstrap
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/smoke_native.py
python3 scripts/audit.py
./gateway run --detach
./gateway verify
./gateway stop
```

前四条不需要真实凭据或密码；后三条需要用户本机解锁，并实际访问远端。`smoke_native.py` 使用 TEST-NET 合成配置、随机回环端口，不访问 Day8 或更改系统路由。

## 历史全住宅 TCP 模式的本机验证

用户明确要求后，已将现有 macOS Mihomo v1.19.14 切到原生 Muon 适配器 + residual 的严格策略，TUN 保持启用，共 7 条规则。首次遇到原配置可选 mapping 为 null、以及 emoji 代理名称的 surrogate 转义问题，均在切换成功前修复；失败时原配置已恢复。修复已通过 15 项测试、完整原配置结构的 v1.19.14/v1.19.24 解析及内存 API 验证。

正式切换后三次 ChatGPT trace 全部匹配 vault 预期出口，独立再次请求也匹配。原生适配器同时有回环 SOCKS 入站及 Day8 出站连接，核心观测到的 Day8 bootstrap 连接均为 DIRECT。该核心 chains 仅显示 Residual-egress，因此额外使用这些 socket 证据核对内部 dialer 通道。

仅关闭了 25 条切换前遗留的 Apple/OpenAI 相关旧连接；复核时该范围没有旧出口连接。原 profile 文件未修改，Day8.app 未运行，当前通道不依赖容器。这是本机实际切换的增量记录，不把前一日隔离验收改写成当时已切换，也不扩展为长期稳定性证明。

## 默认 Day8 分流的验证（2026-11-08）

在独立回环核心经新策略访问 ChatGPT 的 HTTPS trace 一次，TLS 请求成功，主机名和预期出口一致。另通过独立 Muon 到住宅入口，用户名密码认证成功，对 UDP ASSOCIATE 收到 SOCKS5 回复码 0x09。这次没有建立住宅 UDP 中继，也没有确认 UDP 出口。临时核心已退出，未将此策略应用到系统 TUN。

本机主 Clash 复核为原有 44 条规则、TUN 开启、mixed-port 7890。规则、profile、系统代理均与试验前基线相同。

## Linux 业务容器 UDP 隔离验收

在 Docker Engine 28.4 的独立 Compose 项目使用合成凭据和临时网络实测。出口侧对照容器向 UDP 接收端发送探测并收到回显。只接入内部网络的业务容器可以访问网关 SOCKS 入口，分别指定 ChatGPT、OpenAI、Apple 域名的 UDP 请求均未收到回包。业务容器尝试直接向出口侧接收端、IPv4 和 IPv6 测试地址发送 UDP，内核均拒绝发送；停止网关后结果相同。接收端仅记录了对照探测。此范围要求业务容器只连接 protected 网络。

## 原生 App 和登录服务验收（2026-11-08）

在一台 Apple Silicon Mac 编译、签名、安装并打开管理 App。实际窗口分别显示 Day8 和经 Day8 的住宅代理 HTTPS 延迟。主动验证三次，ChatGPT trace 的域名和预期住宅 IP 均匹配。通过停止 LaunchAgent、让运行中的 Clash 重载已选启动文件并重新启动服务，观察到 TUN 启用、启动拒绝规则阻断新的 HTTPS 请求、服务使用新 PID 免密恢复分流。Apple/OpenAI 命中住宅规则，普通站点命中 Day8，核心日志记录了 Apple 和 ChatGPT 的 UDP REJECT。本轮未重启整台 Mac。

在完全断网的 Linux arm64 容器中，使用真实 mihomo、合成 Muon、SOCKS5 和 HTTPS 测试服务，分别验证两跳健康、Docker 容器枚举和按目标下发、更新后出口检查及连接日志。错误密文和不可达候选节点均未覆盖上次有效更新，回退后两条链路均可用。此为离线组件互通验证；真实运营节点的出口验证来自上述 Mac。


## macOS 单 App 收敛代码验收（2026-11-09）

macOS 安装入口及用户登录服务已收敛为单 App。移除了临时 Clash 接入、adapter-only 运行和历史全住宅 strict 构造入口；旧安装保留一次迁移，故障恢复停止网关并恢复原网络。

本机 ARM64 / Python 3.14 的 52 项测试通过，包括无旧标记时启动完整核心、始终进行系统 TUN 核验、无 Clash 新安装检查、迁移前失败不停止旧代理、回退不启动 adapter-only 服务，以及升级失败恢复代码和 launchd 参数。实际复现并修复了 launchd 异步注销导致立即 bootstrap 报错的问题；修复后真实登录服务重载与回环出口预检通过。原生合成回环验收通过：8 条 TCP 路由、2 条 UDP 拒绝、两跳断路及恢复，子核心监督重启。App 编译、签名验证和仓库安全审计通过。

本机已迁移至单 App。初次验收发现，系统核心重启后，部分站点仍能建立 HTTPS，但未能关联预期分流。通过隔离的回环 DNS 复现了虚拟地址映射随查询顺序改变。系统核心现保存 fake-IP 映射，通过 SIGTERM 正常退出，再由 launchd 拉起新进程。

修复后，系统服务与用户登录服务重启均通过验收。四个系统 HTTPS 请求均经 utun63，普通站点命中 Day8，Apple、OpenAI 和 ChatGPT 命中住宅出口。ChatGPT 自身 trace 与预期 IP 匹配，经 7890 入口独立复验三次也全部匹配。物理接口 UDP 阻断计数通过，ClashX 已退出且登录项已移除。管理 App 的后台接口成功添加临时域名规则，加密保存，重启后读回并删除。连接日志和住宅配置读取正常。

## Linux 单容器及国内分流验收（2026-11-09）

Linux 实机部署已将 Muon、mihomo TUN、DNS 和住宅分流收进同一个代理容器，业务容器共享其网络命名空间。复用已登记的加密 vault 和只读密钥卷，独立测试容器重启后仍能恢复出口。公网 UDP 探测增加了命名空间内核拒绝计数；IPv6 在该命名空间关闭。

国内分流与 Mac 共用 CN 网段数据和规则生成器。真实 `.cn` HTTPS 目标及国内裸 IP TCP 连接命中 DIRECT，普通海外 HTTPS 目标命中 Day8，ChatGPT trace 命中住宅第二跳且预期出口匹配。独立回环核心同时验证了 `.cn`、裸 IP、普通目标和住宅规则的优先级。连接 stdout 日志使用东八区日期、毫秒及时区偏移，并过滤上游引导连接及原始诊断信息。

App 的 Docker Context 发现及按容器 ID 推送已通过实际验证。独立 Linux TUN 实例成功切换到不同节点，不可达候选恢复原节点和规则，加密更新文件保持上次有效内容；重启后可恢复保存的节点。TUN 更新期间先保留新旧引导规则，核验后提交，App 对旧 TUN 运行版本先行拒绝推送。当前 66 项单元测试和仓库安全审计通过。
