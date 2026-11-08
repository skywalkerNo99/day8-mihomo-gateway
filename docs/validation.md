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
