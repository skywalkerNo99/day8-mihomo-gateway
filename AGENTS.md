# 给接手模型的部署约定

先阅读 README.md、docs/security.md 与目标平台文档。该仓库的目标是 macOS/Linux 可独立部署，无 Day8 客户端运行时依赖。

1. **macOS 原生部署，禁止把 Docker 当成其正式运行依赖。** macOS 仅支持单 App：系统原生 mihomo TUN + 用户分流核心 + 本地适配程序，不依赖 Clash。Linux 原生为默认，容器可选。
2. 用户密码只由用户在本机终端交互隐藏输入。不要索要聊天密码，不使用参数、环境变量、文件、录屏或剪贴板来传递密码。不要读取/截取用户输入中的密码。
3. 真实节点、密钥、用户名、密码、预期出口 IP 仅允许放在加密 vault。不要解密导出到磁盘、打印完整配置/API 认证信息或上传诊断原文。严禁将旧实验目录、原始 Day8 数据、备份、日志、二进制或本机路径打包提交。
4. bootstrap、单元测试与独立回环端口验收可直接进行。**实际切换系统路由、启用 TUN、替换用户现有配置，需用户明确要求该次切换。** 只做文档/仓库整理时不得顺手切换当前网络。不要停止用户当前代理或 Day8 客户端以做无关验证。
5. macOS 新安装按 docs/macos.md 使用 `gateway macos install`，不要求 Clash profile/controller。已有旧安装只走 `gateway macos migrate`；迁移必须读取该电脑实际 profile、controller 与当前 TUN 状态。登录钥匙串及 LaunchAgent 自动恢复完整分流核心，不得保留 adapter-only 分支。验收用 `gateway macos check`，故障恢复用 `gateway macos rollback`；回退停止网关并恢复原网络，不是兼容部署。卸载用 `gateway macos remove`。
6. Mac 单 App 和 Linux 单容器 TUN 下 .cn 域名和 CN 网段目标（包括裸 IP 连接）默认 DIRECT，排在住宅和自定义规则之后。其余普通公网走 Day8，Apple/OpenAI 专用目标加住宅第二跳。请维护 rules/residential-domains.yaml；共享第三方域名留在默认路径。历史 --strict 会将所有公网 TCP 交给住宅，不能用于本方案。
7. 不能用 fallback/url-test 把 residual 换成默认 VPN，也不能只写 `udp: false`。必须保留 UDP REJECT，断路时失败；重连需要应用重试，不承诺恢复已有 TCP 会话。
8. 通过 `./gateway verify` 验证 ChatGPT trace 的预期 IP，不能只用普通 IP 查询站点代替。控制 socket 返回匹配布尔值，不把真实出口写进仓库。macOS 必须额外核验系统 TUN，不能只测回环代理。部分核心的连接 chains 仅显示 Residual-egress；内部 dialer-proxy 需要结合固定配置、原生适配器 socket 与 bootstrap DIRECT 连接核对，不要求链数组一定包含 Day8-native。不要恢复用户已叫停的长时压测。
9. 修改后运行测试、`scripts/audit.py`，检查 git diff 与 staged 文件。发布 GitHub 时核对目标个人账户、private 状态，不输出访问令牌。密码完成加密之前不能声称交付包含可部署凭据。
10. Linux TUN、Docker 特权配置和系统代理切换都与普通回环代理不同，按文档明确执行并保留回退方式。任何测试都不能影响用户正在使用的主代理。

## 文件地图

- `gateway`：标准库 bootstrap + venv CLI 启动器。
- `src/day8_gateway/adapter.py`：平台无关 Muon TCP ↔ SOCKS5。
- `config.py`：固定两跳、内嵌域名/IP 规则；不写配置。
- `vault.py`：有版本的认证加密与原子保存。
- `runtime.py`：原生监督、内存配置、0600 Unix 控制 socket。
- `controller.py`：只允许回环 Mihomo API，支持内存 payload 热加载。
- `cli.py`：终端隐藏输入、部署/恢复/验证命令。
- `secrets/deployment.enc.json`：用户已加密的最小部署资料；首次部署由用户解锁；Linux 容器登记专用密钥卷后支持非交互重启。
- `scripts/mihomo-lock.json`：四个平台的官方固定版本与 archive SHA-256。

没有密码或已登记的容器卷时，可以安装依赖、测试和规划部署；首次真实出站验证需要本地解锁。应准备好命令再让用户在本机输入。
