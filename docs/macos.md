# macOS 原生管理 App 和 Clash 整机路由

ClashX Meta 负责 TUN 和规则。登录服务运行 Muon 并恢复路由。SwiftUI 管理 App 显示实测状态，关闭窗口后网关继续工作。Mac 上无需 Docker。管理远程 Linux 容器时需要 Docker CLI 和已配置的 Docker Context。

## 在新 Mac 安装

1. 安装 ClashX Meta，在客户端启用规则模式、TUN 和登录时启动。把 Clash 放在稳定的应用目录。安装 Xcode Command Line Tools（含 Swift 编译器）和 Python 3.11 或更新版本。
2. 克隆仓库，运行 README 的 bootstrap。确认本机当前选中的 Clash 配置文件及包含外部控制器地址的核心配置路径。安装器要求可访问回环控制器，并使用当前用户的默认登录钥匙串。
3. 在该用户的终端运行以下命令。替换前两行的路径。首次在终端隐藏输入 vault 密码。系统如果询问钥匙串访问，由用户完成。

```sh
PROFILE="$HOME/.config/clash.meta/当前选中的配置.yaml"
CORE_CONFIG="$HOME/.config/clash.meta/config.yaml"
.venv/bin/python scripts/macos_persistent.py install --profile "$PROFILE" --core-config "$CORE_CONFIG"
scripts/build-macos-app.sh
open "$HOME/Applications/Day8 Gateway.app"
```

构建脚本使用当前 Mac 的 Swift 编译器和架构，在用户应用目录安装经过本地 ad-hoc 签名的 App。迁移到另一台 Mac 后重新编译、登记钥匙串一次。Clash 使用原有入口端口，本地 Muon 默认只监听回环端口 17899。

## 日常操作

- **线路监测** 每 30 秒分别通过 mihomo 指定 Day8 第一跳和经 Day8 的 1024proxy 出站进行 HTTPS 探测。窗口显示最近 20 个样本、时延和当前 Clash 总连接数。「核验出口」会请求三次 ChatGPT 自身的 trace，并与 vault 中的预期值比对。
- **配置与账户** 打开官方桌面客户端，在「账号 → 购买会员」中办理充值，会员信息和订单由官方处理。日常代理流量使用独立 Muon 服务。
- **节点更新** 先在官方 App 刷新。管理 App 的读取按钮检查本机 Day8 的 TOML 是否有可唯一识别、包含完整认证字段的 Muon 出站。本机验证的 Day8 1.1.0 文件只包含入口和路由，无法据此自动同步账户节点。遇到此状态会显示原因。可选用服务方或管理员提供的 Muon 节点 JSON，字段见 [凭据格式](security.md) 的 `day8` 对象，或导入由同一基础 vault 密钥认证的 `.enc` 节点包。导入仅更新 Day8；住宅凭据和出口预期值取自基础 vault。尝试新节点期间可能短暂影响新连接，出口验证通过后保存加密更新，失败则恢复上个节点。
- **Linux 容器** 输入目标 Docker Context（远程主机可使用 SSH Context），查找容器，选中指定目标后点击应用。仅列出运行中的 Compose `day8-gateway` 服务。要求目标运行已升级的本项目，且已登记与 Mac 相同的基础 vault。更新经 Docker exec 的 stdin 传入，目标独立完成认证及住宅出口验证。[Linux 配置与监控](container.md)

## 登录、重载和日志

登录后，launchd 从 `~/Library/LaunchAgents/io.day8gateway.macos.plist` 拉起服务，异常退出会重试。Clash 加载已选的 `Day8-Residential.yaml` 时先执行拒绝规则，服务从解锁的登录钥匙串读取本 vault 的派生密钥，再把运行配置加载到核心内存。FileVault 解锁、用户登录和 Clash 自身启动仍由系统和用户的登录项设置负责；保护范围从 TUN 接管开始。

```sh
./gateway status
./gateway monitor --once
./gateway verify
launchctl print "gui/$(id -u)/io.day8gateway.macos"
tail -f "$HOME/Library/Application Support/Day8Gateway/service.log"
```

日志文件记录服务启动错误。实时分流记录在 Clash 日志和连接页查看。普通公网 TCP 命中 `Day8-native`，Apple/OpenAI 专用目标命中 `Residual-egress`，公网 UDP 显式拒绝。单个目标可同时出现 TCP 成功和 UDP 拒绝。

## 升级与回退

更新仓库后，用以下命令更新已安装的服务和管理 App，关闭并重新打开管理窗口以加载新版本。基础 vault 或 Python 运行环境变更时，先回退，再执行首次安装步骤。

```sh
.venv/bin/python scripts/macos_persistent.py upgrade
scripts/build-macos-app.sh
```

```sh
.venv/bin/python scripts/macos_persistent.py remove
```

回退操作选择原 Clash 配置并卸载登录服务。管理 App 可以从用户应用目录移走。如需撤销本机解锁能力，在「钥匙串访问」中删除服务名为 `io.day8gateway.macos.vault-key` 的相应条目。

单次实验仍可在未安装登录服务的 Mac 上使用 `./gateway macos deploy --profile "$PROFILE" --core-config "$CORE_CONFIG" --block-public-udp --apply`，回退用 `./gateway macos restore --profile "$PROFILE" --core-config "$CORE_CONFIG"`。该临时模式只适用于当前核心会话。

## 线路列表、测速和切换

「Day8 线路」显示运行中适配器实际使用的节点名称。管理服务按节点参数生成带密钥的标识，并与加密目录匹配；参数无法唯一匹配时显示未知。安装自带的账户目录是保存时的快照，首次打开即可离线列出，也可在未运行官方客户端时切换。切换前后始终沿用原有住宅规则，验证 ChatGPT 住宅出口成功后才保存；失败则回退，已建立的连接可能仍在旧线路上。

在官方 Day8 主窗口点击当前线路，打开「服务器」面板。回到管理 App 点「刷新官方列表」，首次按 macOS 提示授予管理 App 辅助功能权限。刷新读取官方面板此刻展示的名称、编号和延迟。新增但尚无 Muon 参数的线路会列出并标记。要更新这些参数，在「配置与账户」中导入可信账户线路 JSON（含 `servers` 和 `credentials`）。导入成功后以加密形式保存在本机，优先于随安装附带的目录。

「全部测速」用每个已配置的 Muon 节点对固定 HTTPS 目标完成一次请求，最多并发四条，不替换当前系统节点。在 macOS 可识别物理上联网卡且节点为 IP 地址时，测速连接绑定该网卡，防止候选节点的流量被当前节点再次代理。其他情况下使用系统路由。官方面板给出的延迟单列为“官方”。退出官方 App 后仍可测速和切换已保存的节点。

## 后台定时测速

登录服务默认每 30 分钟对加密目录中的所有可测 Day8 节点运行一轮 HTTPS 探测，每轮最多并发四条。在「Day8 线路」页底部可以选择 5、15、30、60 分钟或关闭。管理窗口退出后服务继续工作。首次没有结果时立即测一轮，登录或唤醒后逾期只补一轮。定时与手动请求共用一个任务，结果只用于显示。间隔与最近一轮结果在用户私有目录保存；目录更新后旧成绩失效。

```sh
PYTHONPATH=src .venv/bin/python -m day8_gateway.desktop probe-status
```
