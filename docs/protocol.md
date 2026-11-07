# Muon 适配范围

本实现来自对已安装 Day8 版本 Muon TCP 协议的分析与独立互通验证。没有调用 Day8 进程、macOS 框架、宿主代理 API，也没有分发 Day8 二进制。运行依赖为 Python、BLAKE3、cryptography，原生支持 macOS/Linux。

关键参数在加密配置的 `day8` 中：远端 server/port、cipher、server_password、user_password、max_padding。支持 aes-128-gcm / aes-256-gcm。server 与 user 两份口令通过 BLAKE3 context 派生各自 PSK。

- Server PSK context：`nebrix-muon-server-psk`。
- User PSK context：`nebrix-muon-user-psk`。
- Identity context：`muon 2026 identity subkey`；AES identity block 校验用户派生值。
- Session context：`shadowsocks 2022 session subkey`；会话 salt 与 PSK 派生 AES-GCM 密钥，12-byte little-endian 单调 nonce。
- 请求头包含时间戳、目标地址与随机 padding；数据按加密长度、加密载荷拆帧。
- 响应校验类型、时间戳与 request salt 绑定。特别处理零长度初始 ACK：没有额外 payload tag，后面直接接普通长度帧。

每个 SOCKS CONNECT 建立独立 Muon 连接，第二跳 residual SOCKS5 认证由 mihomo 完成。没有实现 UDP ASSOCIATE，因此规则必须拒绝 UDP。服务器协议升级可能导致不兼容，届时保留失败状态并分析新格式，不能通过回退默认出口掩盖错误。

测试含模拟服务器端 identity/请求校验、AES-128/256、分片、多帧、大载荷、错误 salt/认证 tag 拒绝，以及闲置半握手客户端的 SIGINT/SIGTERM 清理。真实链路验收见 validation.md。

参考实现依据：[BLAKE3](https://github.com/BLAKE3-team/BLAKE3)、[cryptography AEAD](https://cryptography.io/en/latest/hazmat/primitives/aead/)、[Argon2id](https://cryptography.io/en/latest/hazmat/primitives/key-derivation-functions/)。Muon 专用字段以实际分析及测试为依据，不把普通 Shadowsocks 配置直接冒充 Day8 配置。
