# 路由范围与验收

固定链：`Residual-egress` 为第二跳 SOCKS5，`dialer-proxy: Day8-native`；后者连接回环 Muon 适配器，适配器独立实现 Day8 协议。没有 `fallback`、`url-test` 或 residual 故障时转默认 VPN 的规则。

严格模式将所有受接管的公网 TCP 交给这条链，因此 Apple/OpenAI 使用未来新域名时仍走 residual。私有 IPv4/回环及 Day8 引导连接例外；UDP 显式 REJECT。独立 mihomo 默认严格模式，macOS 现有核心接入用 `--strict`。

域名模式内嵌 Apple、OpenAI 与官方依赖三个域名集合，以及 Apple 官方 IP 段。优先级为 Day8 引导规则 → 目标 UDP 拒绝 → 目标 TCP 的 residual 路由 → 原配置其余规则。列表来源与核对日期见 `rules/SOURCES.md`。它不能穷尽未来新增域名，也可能涵盖共享第三方服务。

验收分别核对：

1. 应用请求是否进入这套 mihomo。系统代理、TUN、应用自行指定代理的覆盖范围不同。
2. 新连接匹配的规则、链是否为 residual + Day8。旧连接不会因热加载自动迁移。
3. `gateway verify` 三次 ChatGPT trace 是否全部匹配 vault 中的预期出口。检查站点是 ChatGPT 自身，TLS 正常校验，未使用 `curl -k`。
4. 故障时请求失败且不回退其他出口；应用在网络/核心恢复后重新连接。

一台电脑上的有限采样不能证明所有请求永远稳定或所有客户端都无绕过。主机原有 TUN 若捕获适配器的 Day8 TCP，引导连接仍可能先经过该主机原代理；隔离验收时不能把“不修改主机路由”误写成“已证明第一跳物理直连”。正式部署在负责 TUN 的同一核心应用 bootstrap DIRECT 规则后，应核对其实际新连接。

Apple/OpenAI 应用可能使用 QUIC、推送、共享 CDN、IPv6 或自定义 DNS。严格 TCP 策略防止回落，但 UDP 功能可能受限。若要支持 UDP，需要实现并验证 Muon 的 UDP 协议，不能直接删除 REJECT。
