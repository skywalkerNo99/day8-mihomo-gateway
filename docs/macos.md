# macOS 原生接入

macOS 不依赖容器。优先复用电脑上现有 Clash MetaX / ClashX 的原生 mihomo，通过原生 Muon 适配程序连 Day8，再到 residual。也可用 [独立原生模式](native.md)。

## 准备

1. clone 私有仓库并执行 `python3 gateway bootstrap`。不要安装或打开 Day8 客户端。
2. 找到这台电脑实际使用的 Clash profile 与核心配置，保留原有备份。前者包含原规则/代理，后者包含 `external-controller` 与 `secret`。不同版本的路径不同；不要直接复制其他人的绝对路径。
3. 在现有客户端确认 TUN 已启用，以便覆盖整机受支持的流量。程序不会替你启用 TUN、设置系统代理或要求系统密码；若没有启用，会拒绝声称完成整机切换。
4. 确认 `17889` 空闲。适配器仅监听回环。新的 Day8 连接必须能从当前网络建立。

在终端设置两个实际文件路径（路径不是密码）：

```sh
PROFILE='/实际路径/当前使用的配置.yaml'
CORE_CONFIG='/实际路径/核心config.yaml'
```

## 规划并切换

先查看新策略。普通公网走 Day8，Apple/OpenAI 的已知专用域名和 Apple 网段加住宅第二跳。

```sh
./gateway macos plan --profile "$PROFILE" --core-config "$CORE_CONFIG"
```

当前版本会在切换前报告 Muon 仅支持 TCP，并阻止默认分流的 `deploy --apply`。本机验证使用下方独立检查命令，输入一次 vault 密码。它会创建临时回环核心、经 Day8 测试住宅 SOCKS5 UDP 协商、请求一次 ChatGPT HTTPS trace，再停止临时核心。结果只显示协商返回码和 IP 是否匹配。

```sh
.venv/bin/python scripts/check_live.py
```

`--strict` 是历史全住宅 TCP 模式。它会改变普通公网的出口，不符合默认 Day8 的部署要求。

## 验证

```sh
./gateway status
./gateway verify --proxy http://127.0.0.1:7890
```

把 `7890` 换成这台 Mac 主 Clash 的实际 HTTP/mixed 端口。验证会向 `https://chatgpt.com/cdn-cgi/trace` 请求三次，要求 `h=chatgpt.com` 且 `ip` 与加密配置中的预期值一致，三次都满足才返回 `ok: true`。它确认该站点看到的连接 IP，不代表已完成登录、聊天、WebSocket 或订阅功能验证。

在 Clash 连接页检查新连接使用 `Residual-egress`。部分核心的 `/connections` 只返回这个出口名称，不把内部 `dialer-proxy` 的 `Day8-native` 加入 chains；不能仅凭数组缺少后者就判定绕过。还应核对生成配置的固定 dialer-proxy、本地适配器的 SOCKS 入站与 Day8 出站 socket，以及核心中 Day8 bootstrap 连接为 DIRECT。不要输出完整配置或真实节点地址。

规则更新不迁移已有 TCP 会话；验证/业务应重新建立连接。若旧会话仍在使用原出口，仅关闭相关应用连接再重试，不批量断开用户其他连接。

## 回退与重启

```sh
./gateway macos restore --profile "$PROFILE" --core-config "$CORE_CONFIG"
```

恢复未修改的原 profile，再停原生适配器。不要只 `gateway stop` 留下仍指向已停适配器的主 Clash 策略。

主 Clash/app/core 重启或重新加载订阅，会丢失内存热加载策略。此时重新执行 deploy，并再次输入密码。整机重启后同样如此。本项目不会通过保存密码自动解锁，也不会向订阅文件写入解密后的凭据。

需要连续运行而不依赖 GUI 核心生命周期时，改用 [独立原生模式](native.md)，同时只保留一套负责系统 TUN 的 mihomo。独立模式安装本身不会替你切换系统网络。
