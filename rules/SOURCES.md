# 域名与规则来源

核对日期：2026-11-07。规则文件是快照，不是未来新增域名的保证。

- `apple.yaml`：[MetaCubeX Apple 分类](https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/apple.yaml)。包含 Apple、iCloud、CDN 和地区/品牌域名；分类范围较宽。
- `openai.yaml`：[MetaCubeX OpenAI 分类](https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/openai.yaml)。
- `official-additions.yaml`：补充 [Apple 官方网络主机/端口说明](https://support.apple.com/101555) 和 [OpenAI 官方网络建议](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps) 中的认证、静态资源、内容和共享第三方依赖。共享域名对应的其他服务也可能被纳入分流。
- Apple IP 段：官方文档列出的 `17.0.0.0/8`、`2403:300::/32`、`2620:149::/32`、`2a01:b740::/32`。
- [Mihomo inline 规则集合](https://wiki.metacubex.one/config/rule-providers/)：打包进生成的配置，避免其他 Mac 的文件路径差异。
- [Mihomo 规则匹配实现](https://raw.githubusercontent.com/MetaCubeX/mihomo/v1.19.24/tunnel/tunnel.go)：不支持 UDP 的节点可能被跳过而继续匹配，因此显式 REJECT 目标 UDP，不能只写 `udp: false` 就声称不会泄漏。

更新分类文件后运行测试，再重新执行本项目的 `macos plan` / `macos deploy --apply`。文件内嵌进内存策略，不生成含凭据的 preview 文件。分类列表不是未来全部域名的保证；需要该范围时使用 `--strict`。
