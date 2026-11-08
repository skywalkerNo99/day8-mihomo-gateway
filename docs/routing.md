# 路由与验收

macOS 现有 Clash 和独立 mihomo 共用策略生成器。普通公网 TCP 走 Day8，包括国内、未知域名和公网 IP。住宅 SOCKS5 的拨号固定使用 Day8，只接收 Apple/ChatGPT/OpenAI 专用域名和 Apple 专用网段。回环或局域网通过 TUN 时直连。Day8 节点的 TCP 引导连接按节点地址和端口放行。旧 profile 留作恢复。

专用域名快照在 `rules/residential-domains.yaml`。域名后缀包含根域和子域。Stripe、Cloudflare 验证页等共享主机按普通公网规则处理。用户访问同一共享域名时，只凭域名无法区分它属于哪个应用。无法识别的裸 IP 和未来新增域名走 Day8；核对实际连接后可更新清单。业务 DNS 的 DoH 上游经 Day8。整机部署还需检查应用自带 DNS、TUN 排除项和 IPv6。

公网 UDP 在当前版本显式拒绝。TUN 模式中已识别的住宅域名 UDP 也先于局域网直连规则被拒绝。Linux 业务容器需要按 [容器部署](container.md) 只接入隔离网络，防止绕过应用代理直接发包。住宅规则后有同范围的拒绝规则，Day8 兜底后有最终拒绝。住宅故障时住宅目标应失败，普通请求继续；Day8 故障时两类公网新连接均应失败。

## 验证命令

- `.venv/bin/python scripts/smoke_routes.py` 使用实际 mihomo 和两个模拟 SOCKS 跳点，检查普通、专用与共享域名，分别断开住宅和 Day8 再恢复。测试只访问回环，不需要密码。
- `.venv/bin/python scripts/check_live.py` 交互解锁。通过 Muon 向真实住宅入口认证并请求 UDP ASSOCIATE，另经临时 mihomo 做 ChatGPT HTTPS trace。返回码 0 表示协商获准；UDP 数据面还必须用真实数据报验证。
- 两跳 UDP 验收要求先实现独立 Day8 UDP，然后通过住宅中继取得带事务标识的公网回包，检查回包来源和远端看到的源 IP。住宅 SOCKS5 的 TCP 控制连接和协商出的 UDP 中继都必须经过 Day8。
- 上述测试通过后，才在现有 macOS TUN 上复核实际接管和回滚。有限域名采样无法证明所有未来依赖都已覆盖。

macOS 的历史 `--strict` 模式将所有公网 TCP 送到住宅；本项目默认分流选择 `day8-default`。
