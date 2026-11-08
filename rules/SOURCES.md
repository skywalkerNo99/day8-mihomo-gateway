# 域名与规则来源

核对日期：2026-11-07。规则文件是快照，不是未来新增域名的保证。

- `apple.yaml`：[MetaCubeX Apple 分类](https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/apple.yaml)。包含 Apple、iCloud、CDN 和地区/品牌域名；分类范围较宽。
- `openai.yaml`：[MetaCubeX OpenAI 分类](https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/openai.yaml)。
- `official-additions.yaml`：补充 [Apple 官方网络主机/端口说明](https://support.apple.com/101555) 和 [OpenAI 官方网络建议](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps) 中的认证、静态资源、内容和共享第三方依赖。共享域名对应的其他服务也可能被纳入分流。
- Apple IP 段：官方文档列出的 `17.0.0.0/8`、`2403:300::/32`、`2620:149::/32`、`2a01:b740::/32`。
- [Mihomo inline 规则集合](https://wiki.metacubex.one/config/rule-providers/)：打包进生成的配置，避免其他 Mac 的文件路径差异。
- [Mihomo 规则匹配实现](https://raw.githubusercontent.com/MetaCubeX/mihomo/v1.19.24/tunnel/tunnel.go)：不支持 UDP 的节点可能被跳过而继续匹配，因此显式 REJECT 目标 UDP，不能只写 `udp: false` 就声称不会泄漏。

当前生效文件是 `residential-domains.yaml`。它从上述来源选取服务专用后缀、国家或地区 Apple 域名及专用 CDN 主机。上游完整分类及历史补充文件仅供核对。通用验证码、支付、证书、客服等共享主机走默认 Day8。更新生效清单后运行两种分流测试，并检查代表域名及共享域名。

## 中国大陆 IP 归属

IP Geolocation by [DB-IP](https://db-ip.com/db/download/ip-to-country-lite)，使用 2026-11 的 IP to Country Lite，许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。`cn-networks.txt` 是提取 country code 为 CN 的地址范围再无损合并为 IPv4/IPv6 CIDR 的派生版本。HK、MO、TW 分列，不归入 CN。

更新时下载官方月份 CSV.gz，用官方页面公布的未压缩 CSV SHA1 校验，重生成文本并随服务升级部署：

```sh
python3 scripts/import_cn_networks.py dbip-country-lite-2026-11.csv.gz e2061015c43ec91c94a12191a6a613279eb94c0c
```
