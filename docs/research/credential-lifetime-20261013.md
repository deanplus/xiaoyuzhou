# 登录凭据复用与期限调查（2026-10-13）

结论：已验证保存refresh token后，跨进程复用和一次真实续期均可避免再次扫码，续期后的收听历史与文稿读取也通过。测试token没有exp字段，不能给出本账户可持续使用的固定天数；自然过期后的401恢复尚未实测。

## 初次调查的公开证据

1. [VideoNote 文稿客户端](https://github.com/HuangYincan/VideoNote-MCP/blob/main/app/downloaders/xiaoyuzhou_subtitle.py)描述access约2小时，配套refresh可续期；遇401执行一次刷新并更新保存的凭据。
2. [xiaoyuzhou-juicer认证说明](https://github.com/hesorchen/xiaoyuzhou-juicer#如何拿到-token)同样描述access约2小时、refresh长效；实现轮换写回与并发锁。但没有提供固定refresh有效天数，不能把“长效”换算成30/90/365天。
3. [r266客户端](https://github.com/r266-tech/xiaoyuzhou/blob/main/xiaoyuzhou/client.py)实现刷新一次后重试，区分认证拒绝与网络故障，原子保存轮换结果。
4. 本轮重新检查官方accounts公开bundle：存在`/app_auth_tokens.refresh`，使用web-api和cookie会话；未找到可确认期限的`expiresIn/expires_in/7200`常量。前端存在刷新机制，不证明每枚refresh无限期有效。

以上公开代码为实现依据；私人接口没有被这里认定为官方第三方API承诺。初次调查未发起新登录、读取既有浏览器/应用凭据或调用刷新接口；后续授权实测见下文。

## 期限与保存是不同问题

- 本地可以长期保存凭据；有效期与服务端撤销决定它能否继续用。
- access约2小时是第三方实现描述，当前账户的实际期限尚未确认。
- refresh可能被续期接口轮换，必须保存返回的新值；具体到期、长期未用失效和服务端撤销规则没有官方可核验的固定保证。
- JWT的`exp`若存在可作为期限元数据，但不是服务端必然接受到该时刻的保证；仅解码不能验证签名。

## 初次调查时的建议（历史记录）

使用macOS钥匙串存储access、refresh和稳定设备标识，项目只保留非敏感期限/状态。使用同一设备身份进行续期；更新返回的凭据，避免并发刷新互相覆盖。401最多刷新并重试一次；网络失败或429/5xx不能直接等同于refresh失效。服务端明确拒绝续期时提示重新扫码。

这是建议的本地保存方式，参考[Apple钥匙串说明](https://support.apple.com/guide/keychain-access/what-is-keychain-access-kyca1083/mac)；尚未实现任何钥匙串读写。

初次调查时，之前的临时凭据已丢弃，初始化需要另一次扫码；当时尚未实现保存和续期。后续已按维护者授权实现并实测下述文件方案，没有创建自动化。

## 当前实现补充

维护者指定跨平台文件保存，替代初次建议中的macOS钥匙串。当前入口提供login/reuse-session/logout，两个token与稳定device ID一起写入Git忽略的tmp/auth/session.json。POSIX使用0700/0600和flock，Windows使用用户/SYSTEM受限DACL及msvcrt锁。CLI核心导入不再强制需要resource或SIGHUP。

续期固定为POST https://api.xiaoyuzhoufm.com/app_auth_tokens.refresh，请求头携带refresh和device ID，不发送access到该接口。来源见上面的r266和VideoNote客户端；本项目不采用r266将所有4xx都清除凭据的策略。

最多续期一次再重试一次；续期前原子写入pending，避免崩溃后重用可能已轮换的旧refresh。需完整新token对；网络/5xx/缺token/落盘失败保留凭据和未知状态，429恢复可重试状态，401/403明确拒绝才标记重新登录。不会自动扫码或删除凭据。本地退出不撤销服务端会话。

JWT期限只提取元数据，不验证签名、不承诺固定寿命。原生Windows/Linux及实际平台ACL还需要对应环境验证。

## 授权实测结果

维护者明确授权重新扫码、保存、跨进程复用和真实续期。扫码所得两个token已保存；新进程复用成功，没有二维码请求或refresh请求。随后主动发起一次真实refresh，服务端返回的access与refresh均发生变化，稳定设备标识保持一致；新token对写入后重新加载，与响应一致。续期后及再次启动的新进程都成功读取历史、播放时间和固定单集文稿。

本地会话状态为ready，macOS目录0700、文件0600，临时二维码已清理。仅核对iat/exp字段是否存在，没有公开原始值；当前access有iat、没有exp。会话和账户报告均在Git忽略目录中，未发给reviewer。

这次主动续期证明接口及轮换可用，没有等待自然过期，也没有证明长期未用、服务端撤销或refresh寿命。自然401路径已有离线测试，但真实过期恢复仍需后续验证。
