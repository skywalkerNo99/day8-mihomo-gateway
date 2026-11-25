# 路由与验收

Mac 单 App 模式按住宅、用户自定义、国内直连、Day8 的顺序匹配公网 TCP。Linux 单容器 TUN 按住宅、国内直连、Day8 的顺序匹配公网 TCP。国内直连覆盖 .cn 域名和 CN 网段的目标 IP，包括直接使用 IP 的连接。Mac 关闭管理 App 后由 launchd 服务继续处理。显式应用代理模式的基础策略将普通公网 TCP 交给 Day8。住宅 SOCKS5 的拨号固定使用 Day8，用于已收录的 Apple、ChatGPT/OpenAI、Claude/Anthropic 和 Google 域名、Apple 专用网段和明确指定的住宅规则。回环或局域网通过 TUN 时直连。Day8 节点的 TCP 引导连接按节点地址和端口放行。旧 profile 留作恢复。

域名快照在 `rules/residential-domains.yaml`。域名后缀包含根域和子域。Google 规则覆盖搜索地区域名（含 `google.cn`）、登录、Gemini、Antigravity、Google API（含 `storage.googleapis.com`）、静态资源和 YouTube。Stripe、Cloudflare 验证页和通用遥测等共享主机按普通公网规则处理，专用租户主机单独收录。Antigravity 网页与登录命中 `google` 或 `google.com` 后缀，客户端 API 与下载命中 `googleapis.com` 或 `gvt1.com` 后缀。App Engine、Cloud Run 和 Firebase Hosting 等共享租户域名继续走默认 Day8。未命中任何分流规则的域名和裸 IP 走 Day8。业务 DNS 的 DoH 上游经 Day8。整机部署还需检查应用自带 DNS、TUN 排除项和 IPv6。

公网 UDP 在当前版本显式拒绝。TUN 模式中已识别的住宅域名 UDP 也先于局域网直连规则被拒绝。Linux 显式应用代理模式需要业务容器只接入隔离网络；单容器 TUN 模式由共享网络命名空间内的防火墙拒绝公网 UDP，并关闭 IPv6。部署和验收见 [容器部署](container.md)。住宅规则后有同范围的拒绝规则，Day8 兜底后有最终拒绝。住宅故障时住宅目标应失败，普通请求继续；依赖 Day8 的单跳和住宅连接在第一跳故障时均应失败。

## 验证命令

- `.venv/bin/python scripts/smoke_routes.py` 使用实际 mihomo 和两个模拟 SOCKS 跳点，检查普通、专用与共享域名，分别断开住宅和 Day8 再恢复。测试只访问回环，不需要密码。
- `.venv/bin/python scripts/check_live.py` 交互解锁。通过 Muon 向真实住宅入口认证并请求 UDP ASSOCIATE，另经临时 mihomo 做 ChatGPT HTTPS trace。返回码 0 表示协商获准；UDP 数据面还必须用真实数据报验证。
- 两跳 UDP 验收要求先实现独立 Day8 UDP，然后通过住宅中继取得带事务标识的公网回包，检查回包来源和远端看到的源 IP。住宅 SOCKS5 的 TCP 控制连接和协商出的 UDP 中继都必须经过 Day8。
- 上述测试通过后，才在现有 macOS TUN 上复核实际接管和回滚。有限域名采样无法证明所有未来依赖都已覆盖。

macOS 的历史 `--strict` 模式将所有公网 TCP 送到住宅；本项目默认分流选择 `day8-default`。
