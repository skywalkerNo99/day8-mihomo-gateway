# 凭据与密码

仓库保存最小部署资料的认证密文，不保存 Day8 原始应用数据、登录令牌、设备信息、原 Clash 配置或实验日志。

vault 版本 1：Argon2id（64 MiB / 3 iterations / 4 lanes / 256-bit 输出），随机 16-byte salt，AES-256-GCM，随机 12-byte nonce。版本、算法、KDF 参数、salt、nonce 全部作为 AAD 认证。解析仅接受固定允许的 KDF 参数，避免被修改成超大资源消耗。错误密码、密文篡改会拒绝解密。保存用 0600 临时密文文件与原子替换，临时文件里也没有明文。

密码由用户自定、终端 getpass 隐藏输入，不强制未约定的长度；建议足够长且不重复使用。密文可以被离线猜密码，因此 private 仓库不能替代强密码。程序不会把密码写入参数、环境变量、磁盘、Git 或钥匙串。

运行中解密资料在 Python/mihomo 内存，通过 pipe stdin 或 loopback 控制 API 传递，不生成明文运行配置。`.local` 为 0700、控制 socket 为 0600，控制接口不返回完整配置。不要对本进程抓内存、开启 core dump 或把系统诊断包上传；同用户/管理员或系统 swap 仍可能接触进程数据，Python 不承诺密码内存可验证地清零。

首次创建 vault（维护者操作）：

```sh
./gateway seal --source /已有私密目录
# 目录内包含旧 muon.json 与 mihomo.json，仅在内存导入最小字段。
```

也支持 `seal --input /已有的部署资料.json`，该输入格式如下。这里只是 TEST-NET 示例，不能直接部署：

```json
{
  "schema": 1,
  "day8": {"server": "192.0.2.10", "port": 443, "cipher": "aes-128-gcm", "server_password": "replace-me", "user_password": "replace-me", "max_padding": 1024},
  "residual": {"server": "198.51.100.20", "port": 1080, "username": "replace-me", "password": "replace-me"},
  "expected_exit_ip": "198.51.100.20"
}
```

不要为模型演示创建包含真实内容的明文样例文件。已部署者只需仓库内 vault 和本地密码，不需要旧目录。

修改密码：`./gateway rekey`，先输入旧密码，再输入新密码两次。Git 历史仍可能保留旧密文；若旧密码泄露，应轮换远端真实凭据，而不只修改 vault 密码。
