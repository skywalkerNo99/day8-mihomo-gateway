# macOS 单 App 部署

macOS 只支持 Day8 Gateway 单 App。系统服务接管 TUN，用户登录服务运行分流核心和 Muon 出站，SwiftUI 管理 App 提供域名路由、连接日志、住宅配置和监测。关闭窗口不停止网络服务。无需 Clash 或 Docker。

## 新 Mac 安装

需要 Python 3.11+、Git、curl 和 Xcode Command Line Tools（Swift 编译器）。准备本仓库的加密 vault，在普通用户的本机终端执行：

```sh
python3 gateway bootstrap
./gateway macos install
scripts/build-macos-app.sh
open "$HOME/Applications/Day8 Gateway.app"
```

首次隐藏输入 vault 密码，将 vault 专用派生密钥登记到当前用户登录钥匙串。系统 TUN 和 PF 安装需要管理员授权，也只在本机终端隐藏输入。不要用 root 运行整个安装器。

安装器无需现有代理 profile 或控制器。先启动用户分流核心并用独立回环入口验证固定 ChatGPT 出口，随后安装校验过 SHA-256 的系统核心及 PF 规则，再退出检测到的原 Clash 并移除其登录项。原代理已运行时，安装器先读取本机实际选中配置、controller 和 TUN 状态，保存仅用于故障恢复的路径与状态。迁移阶段失败会停止网关并恢复原网络，不会运行兼容服务。

| 服务 | 职责 |
|---|---|
| 系统 LaunchDaemon `io.day8gateway.tun` | 建立 `utun63`，提供本机回环入口 7890；TUN 拒绝公网 UDP，独立 PF 规则阻断物理接口绕行的公网 UDP（IPv4 和 IPv6），保留局域网和组播例外。 |
| 用户 LaunchAgent `io.day8gateway.macos` | 钥匙串解锁后启动分流核心（回环入口 17890）和 Muon（回环入口 17899），启用域名规则、连接日志和住宅编辑。与系统核心通过回环加密连接通信。 |
| Day8 Gateway App | 查看状态、核验整机出口、编辑配置。退出管理 App 后后台继续运行。 |

Day8 和住宅凭据仅在用户服务内存中加载。系统服务只持有固定 TUN 配置和本地转发密钥。用户服务未就绪时系统核心无法转发。保护范围从系统 TUN 与 PF 规则成功建立开始；FileVault 解锁及用户登录仍由 macOS 负责。

## 迁移已有旧安装

已登记 vault 的旧 Clash 兼容安装只能迁移，不能作为部署模式继续升级。更新仓库后，在本机普通用户终端运行：

```sh
./gateway macos preflight
./gateway macos migrate
scripts/build-macos-app.sh
```

迁移先验证回环出口，更新登录服务代码和 launchd 参数，启动完整分流核心，再切换系统 TUN。验收会从系统路由发起四个 HTTPS 请求、关联真实连接与规则、比对 ChatGPT trace 出口、确认物理网卡 UDP 被 PF 阻断，并重启两个服务后再验一次。旧 `--profile` / `--core-config` 和临时 `macos deploy` 入口已移除。旧配置文件不作为新服务的运行依赖。

```sh
# 复验系统分流、住宅出口、PF 和原 Clash 退出状态（管理员授权）
./gateway macos check
launchctl print system/io.day8gateway.tun
launchctl print "gui/$(id -u)/io.day8gateway.macos"
```

系统核心在仅管理员可访问的工作目录保存域名与虚拟 IP 的对应关系。维护重启使用 SIGTERM，使核心可以保存映射。

## 故障恢复

```sh
./gateway macos rollback
```

回退停止系统 TUN、删除本项目 PF 规则、禁用用户登录服务，恢复迁移前记录的原代理及配置。不会重新启动兼容模式。没有原代理的新安装则回到系统原有直连。确认恢复后可用 `./gateway macos remove` 卸载。需要重新部署时先卸载再重新安装。

## 从 App 修改 residual 住宅代理

在单 App 模式下打开「配置与账户 → 编辑 residual 配置…」。填写 SOCKS5 接入地址、端口、用户名、密码和预期出口 IP。原地址和端口未变时，密码留空可沿用。更换接入地址或端口须重新输入密码。预期出口填写目标站点应看到的公网 IP。

点「验证并保存」后，后台先经当前 Day8 线路单独测试候选代理。ChatGPT 的 trace 匹配预期值后，后台热加载配置，再从实际分流核心核验、加密保存。失败时继续使用旧配置。已建立的连接可能继续使用原出口，新连接使用新配置。修改只应用于本机；与基础 vault 绑定的加密覆盖文件保存在登录服务的 `.local/residual.enc`，下次登录自动加载。Linux 容器及仓库部署副本单独管理。

## 日常操作

- **线路监测** 每 30 秒分别通过 mihomo 指定 Day8 第一跳和经 Day8 的 residual 出站进行 HTTPS 探测。窗口显示最近 20 个样本、时延和分流核心总连接数。「核验出口」会比对 ChatGPT 自身 trace 与 vault 中的预期值。核验同时检查整机 TUN 分流。
- **配置与账户** 打开官方桌面客户端，在「账号 → 购买会员」中办理充值，会员信息和订单由官方处理。日常代理流量使用独立 Muon 服务。
- **节点更新** 先在官方 App 刷新。管理 App 的读取按钮检查本机 Day8 的 TOML 是否有可唯一识别、包含完整认证字段的 Muon 出站。本机验证的 Day8 1.1.0 文件只包含入口和路由，无法据此自动同步账户节点。遇到此状态会显示原因。可选用服务方或管理员提供的 Muon 节点 JSON，字段见 [凭据格式](security.md) 的 `day8` 对象，或导入由同一基础 vault 密钥认证的 `.enc` 节点包。导入仅更新 Day8；住宅凭据和出口预期值取自基础 vault。尝试新节点期间可能短暂影响新连接，出口验证通过后保存加密更新，失败则恢复上个节点。
- **Linux 容器** 输入目标 Docker Context（远程主机可使用 SSH Context），查找容器，选中指定目标后点击应用。仅列出运行中的 Compose `day8-gateway` 服务。要求目标运行已升级的本项目，且已登记与 Mac 相同的基础 vault。更新经 Docker exec 的 stdin 传入，目标独立完成认证及住宅出口验证。[Linux 配置与监控](container.md)

## 登录、升级和卸载

登录钥匙串解锁后，launchd 自动恢复用户分流核心；系统核心由 LaunchDaemon 保持运行。两者异常退出均会重试。已有 TCP 会话断开后由应用重试，不保证续传。

```sh
./gateway status
./gateway monitor --once
./gateway verify
./gateway macos upgrade
scripts/build-macos-app.sh
```

升级同时更新代码、规则和登录服务参数；重新打开管理窗口以加载新版本。基础 vault、Python 环境或系统核心需要更换时，先回退并卸载，再安装。用户服务日志位于当前用户的 `Library/Application Support/Day8Gateway/service.log`；不要提交日志或原始诊断。

```sh
./gateway macos remove
```

卸载会先对已安装 TUN 执行故障恢复，再删除项目服务及加密覆盖文件。可在「钥匙串访问」中删除服务名 `io.day8gateway.macos.vault-key` 的条目以撤销本机解锁能力。

## 线路列表、测速和切换

「Day8 线路」显示运行中适配器实际使用的节点名称。管理服务按节点参数生成带密钥的标识，并与加密目录匹配；参数无法唯一匹配时显示未知。安装自带的账户目录是保存时的快照，首次打开即可离线列出，也可在未运行官方客户端时切换。切换前后始终沿用原有住宅规则，验证 ChatGPT 住宅出口成功后才保存；失败则回退，已建立的连接可能仍在旧线路上。

在官方 Day8 主窗口点击当前线路，打开「服务器」面板。回到管理 App 点「刷新官方列表」，首次按 macOS 提示授予管理 App 辅助功能权限。刷新读取官方面板此刻展示的名称、编号和延迟。新增但尚无 Muon 参数的线路会列出并标记。要更新这些参数，在「配置与账户」中导入可信账户线路 JSON（含 `servers` 和 `credentials`）。导入成功后以加密形式保存在本机，优先于随安装附带的目录。

「全部测速」用每个已配置的 Muon 节点对固定 HTTPS 目标完成一次请求，最多并发四条，不替换当前系统节点。在 macOS 可识别物理上联网卡且节点为 IP 地址时，测速连接绑定该网卡，防止候选节点的流量被当前节点再次代理。其他情况下使用系统路由。官方面板给出的延迟单列为“官方”。退出官方 App 后仍可测速和切换已保存的节点。

## 后台定时测速

登录服务默认每 30 分钟对加密目录中的所有可测 Day8 节点运行一轮 HTTPS 探测，每轮最多并发四条。在「Day8 线路」页底部可以选择 5、15、30、60 分钟或关闭。管理窗口退出后服务继续工作。首次没有结果时立即测一轮，登录或唤醒后逾期只补一轮。定时与手动请求共用一个任务，结果只用于显示。间隔与最近一轮结果在用户私有目录保存；目录更新后旧成绩失效。

```sh
PYTHONPATH=src .venv/bin/python -m day8_gateway.desktop probe-status
```

## 域名路由和连接日志

单 App 模式下，打开「域名路由」，选择精确域名、后缀（含域名本身及其子域）或关键词，再选择 Day8、Day8 → 1024、DIRECT（直连）或拒绝。DIRECT 将匹配的 TCP 连接绑定到当前上网网卡，目的站点看到本地网络出口。网关继续使用原有 DNS 策略；缺少可用上联网卡时拒绝该连接。添加、编辑或删除后，新连接按新规则匹配。自定义规则从上到下匹配；内置住宅域名、Apple 网段和 UDP 拒绝规则优先。自定义规则用本机已登记的 vault 密钥加密保存，重新登录后自动加载。

「连接日志」显示用户分流核心记录的连接，包括已结束的短连接。输入片段（如 `openai`）可忽略大小写搜索，输入 `*.apple.com` 可匹配子域，`?` 匹配单个字符。日志仅在内存中保留最近 1,500 条，重启登录服务后清空。时间为本机接收路由记录的时间。系统 TUN 或防火墙提前丢弃的 UDP 不经过用户分流核心，因此不会出现在这里。

### 国内域名及 IP 默认直连与后台运行

在 Mac 单 App 模式中，住宅规则、自定义规则优先。剩余 TCP 请求中，域名以 `.cn` 结尾，或目标 IP 命中本机 `cn-networks` 网段表，则绑定上联网卡直连。IP 判定同时覆盖域名解析结果和直接使用 IP 的连接，例如 SSH 配置中的 IP 地址。归属不明及其他国家或地区的普通公网目标按原策略走 Day8。日志中的 DomainSuffix(cn) 和 RuleSet(cn-networks) 分别对应后缀和 IP 判定。IP 地理库是估计值，CDN 的解析结果会随地点和时间变化。对返回多个地址的域名，IP 规则按核心此次解析选中的地址判断，不代表域名所有地址均在大陆。[网段版本与更新步骤](../rules/SOURCES.md)

菜单栏的 Day8 Gateway 图标可隐藏或重新打开管理窗口。关闭窗口或按 ⌘Q 退出管理 App 后，系统 TUN 和登录服务继续分流、记录连接和执行已启用的定时测速。下次登录时 launchd 自动恢复服务。重新打开 Day8 Gateway 即可查看后台积累的内存日志。
