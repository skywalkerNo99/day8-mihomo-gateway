# 域名与规则来源

核对日期：2026-11-07。规则文件是快照，不是未来新增域名的保证。

- `apple.yaml`：[MetaCubeX Apple 分类](https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/apple.yaml)。包含 Apple、iCloud、CDN 和地区/品牌域名；分类范围较宽。
- `openai.yaml`：[MetaCubeX OpenAI 分类](https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/openai.yaml)。
- `official-additions.yaml`：补充 [Apple 官方网络主机/端口说明](https://support.apple.com/101555) 和 [OpenAI 官方网络建议](https://help.openai.com/en/articles/9247338-network-recommendations-for-chatgpt-errors-on-web-and-apps) 中的认证、静态资源、内容和共享第三方依赖。共享域名对应的其他服务也可能被纳入分流。
- Apple IP 段：官方文档列出的 `17.0.0.0/8`、`2403:300::/32`、`2620:149::/32`、`2a01:b740::/32`。
- [Mihomo inline 规则集合](https://wiki.metacubex.one/config/rule-providers/)：打包进生成的配置，避免其他 Mac 的文件路径差异。
- [Mihomo 规则匹配实现](https://raw.githubusercontent.com/MetaCubeX/mihomo/v1.19.24/tunnel/tunnel.go)：不支持 UDP 的节点可能被跳过而继续匹配，因此显式 REJECT 目标 UDP，不能只写 `udp: false` 就声称不会泄漏。

当前生效文件是 `residential-domains.yaml`。它从上述来源选取服务专用后缀、国家或地区 Apple 域名及专用 CDN 主机。上游完整分类及历史补充文件仅供核对。通用验证码、支付、证书、客服等共享主机走默认 Day8。更新生效清单后运行两种分流测试，并检查代表域名及共享域名。

## Claude / Anthropic

核对日期：2026-11-15。此前生效清单没有 Claude/Anthropic 规则。本次补充 18 个专用域名后缀（含根域及任意层级子域），以及两个精确匹配的专用 CDN/遥测租户主机。

- [Claude Code 官方网络访问要求](https://code.claude.com/docs/en/network-config#network-access-requirements)：API、认证、MCP、下载、浏览器桥接和 Artifact 域名，包括 `api.anthropic.com`、`platform.claude.com`、`sso.claude.com`、`downloads.claude.ai`、`bridge.claudeusercontent.com` 和 `*.frame.claudeusercontent.com`，均由主域后缀覆盖。
- [MetaCubeX Anthropic 分类](https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/anthropic.yaml) 及其 [domain-list-community 来源](https://github.com/v2fly/domain-list-community/blob/master/data/anthropic)：补充产品、短链和内容域名，以及 `servd-anthropic-website.b-cdn.net`、`o1158394.ingest.us.sentry.io`。这些是社区分类的快照，不代表 Anthropic 对所有域名的官方确认。
- `github.com`、`registry.npmjs.org`、Datadog、Sentry、共享 CDN 父域和 Cloudflare 验证域名按普通公网规则处理。Claude 访问外部网站和第三方模型网关时按目标域名分流。

本清单覆盖上述已知专用域名及其未来子域，不保证覆盖未来新注册的主域、无法识别域名的裸 IP 或共享主机下的专用 URL 路径。不得用 `claude`/`anthropic` 字符串关键词替代主域边界匹配。

## Google / Gemini / Antigravity / YouTube

核对日期为 2026-11-25。搜索地区域名来自 [Google 支持的搜索域名](https://www.google.com/supported_domains)。Google 登录、API、内容托管、静态资源和更新域名参考 [Chrome 官方网络要求](https://support.google.com/chrome/a/answer/6334001) 和 [Android Enterprise 官方网络要求](https://support.google.com/work/android/answer/10513641)。其他 Google 服务及 YouTube 地区和播放域名参考 domain-list-community 的 [Google](https://github.com/v2fly/domain-list-community/blob/master/data/google) 与 [YouTube](https://github.com/v2fly/domain-list-community/blob/master/data/youtube) 分类。清单按后缀匹配，例如 `googleapis.com` 同时覆盖 Gemini API 和 Google Cloud Storage。

[Antigravity 企业网络要求](https://antigravity.google/docs/enterprise#network-allowlists-and-mtls-endpoints)列出的全局、地区和 mTLS API 均由 `googleapis.com` 后缀覆盖。[Antigravity 官方下载页](https://antigravity.google/download)使用 `antigravity.google`、`storage.googleapis.com` 和 `edgedl.me.gvt1.com`。

## 中国大陆 IP 归属

IP Geolocation by [DB-IP](https://db-ip.com/db/download/ip-to-country-lite)，使用 2026-11 的 IP to Country Lite，许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。`cn-networks.txt` 是提取 country code 为 CN 的地址范围再无损合并为 IPv4/IPv6 CIDR 的派生版本。HK、MO、TW 分列，不归入 CN。

更新时下载官方月份 CSV.gz，用官方页面公布的未压缩 CSV SHA1 校验，重生成文本并随服务升级部署：

```sh
python3 scripts/import_cn_networks.py dbip-country-lite-2026-11.csv.gz e2061015c43ec91c94a12191a6a613279eb94c0c
```
