# 排障

不要提供密码、完整 config、controller secret 或原始 Day8 数据给模型。先描述命令、退出码与脱敏状态。

| 情况 | 检查 |
|---|---|
| `bootstrap` 下载失败 | 当前网络能否访问 PyPI/GitHub；可在当前终端临时设置不含凭据的 HTTPS_PROXY；不要切换主机代理。固定 SHA 不匹配必须中止 |
| Python/venv 不可用 | Python 3.11+，Linux 安装对应 `python3-venv`；确认 native wheel 支持当前系统 |
| 密码输入后退出 | 看明确的空密码/确认不一致提示；不会保存半成品。早期程序曾错误强制 12 字符，当前已取消；建议使用强密码 |
| 错误密码/文件损坏 | 重新输入；不要试图从模型或 Git 恢复密码。检查密文是否被截断；认证失败不会输出明文 |
| 本地运行失败 | 检查三个端口是否被占用、`.tools/mihomo -v` 是否可运行、同 checkout 是否已有监督进程；尝试前台 `run`。不要打印完整配置 |
| status 连接失败 | 监督进程未运行，或 clone 路径太长超过系统 Unix socket 限制；使用更短目录。重启后必须再次解锁 |
| macOS 安装或迁移不完整 | 使用 `gateway macos preflight` 核验；管理员授权仅在本机终端输入。已有旧兼容安装用 `gateway macos migrate`，不能执行普通 upgrade |
| 原配置含空值或 emoji 时加载失败 | 更新到当前修复版：可选 mapping 的 YAML null 会被规范化；传给核心的内层配置使用 UTF-8，不生成 YAML 不接受的 surrogate 转义。不要删除用户原有代理名称来绕过问题 |
| macOS verify 失败 | `gateway verify` 检查分流核心住宅出口；`gateway macos check` 额外验收系统 TUN、规则关联及 PF。不能以仅有回环成功判定整机部署完成 |
| 域名路由出现未知命令 | 旧安装尚未迁移；执行 `gateway macos migrate` 后重开 App。完整后台始终支持域名规则、日志和住宅编辑 |
| 出口不匹配 | verify 保留 `ok: false`，不要接受其他 IP。检查连接是否新建、是否进入目标核心、规则链与严格模式 |
| HTTP/3/UDP 功能异常 | TCP-only 设计显式 REJECT；可让应用回退 TCP。不要删除 REJECT 造成默认出口泄漏 |
| 子核心退出 | 监督进程会尝试恢复；已有 TCP 会话需重试。若主监督进程已退出，重新运行并输入密码 |
| 切换后要回退 | macOS 使用 `gateway macos rollback` 停止网关并恢复原网络；不运行兼容服务。Linux 原生独立模式用 `stop` |

CLI 对未知异常采用脱敏错误提示，避免解析错误把配置片段打印出来。需要进一步排查时，在本机用合成测试资料复现；不要直接去掉真实配置场景的脱敏保护。
