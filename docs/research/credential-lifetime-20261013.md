# 登录凭据复用与期限调查（2026-10-13）

结论：保存refresh token并实现续期，有可能避免每次扫码。当前仅完成一次性读取验证，没有保留refresh、检查真实token期限或测试续期；不能给出本账户可持续使用的固定天数。

## 证据

1. [VideoNote 文稿客户端](https://github.com/HuangYincan/VideoNote-MCP/blob/main/app/downloaders/xiaoyuzhou_subtitle.py)描述access约2小时，配套refresh可续期；遇401执行一次刷新并更新保存的凭据。
2. [xiaoyuzhou-juicer认证说明](https://github.com/hesorchen/xiaoyuzhou-juicer#如何拿到-token)同样描述access约2小时、refresh长效；实现轮换写回与并发锁。但没有提供固定refresh有效天数，不能把“长效”换算成30/90/365天。
3. [r266客户端](https://github.com/r266-tech/xiaoyuzhou/blob/main/xiaoyuzhou/client.py)实现刷新一次后重试，区分认证拒绝与网络故障，原子保存轮换结果。
4. 本轮重新检查官方accounts公开bundle：存在`/app_auth_tokens.refresh`，使用web-api和cookie会话；未找到可确认期限的`expiresIn/expires_in/7200`常量。前端存在刷新机制，不证明每枚refresh无限期有效。

以上公开代码为实现依据；私人接口没有被这里认定为官方第三方API承诺。没有发起新登录、读取既有浏览器/应用凭据或调用刷新接口。

## 期限与保存是不同问题

- 本地可以长期保存凭据；有效期与服务端撤销决定它能否继续用。
- access约2小时是第三方实现描述，当前账户的实际期限尚未确认。
- refresh可能被续期接口轮换，必须保存返回的新值；具体到期、长期未用失效和服务端撤销规则没有官方可核验的固定保证。
- JWT的`exp`若存在可作为期限元数据，但不是服务端必然接受到该时刻的保证；仅解码不能验证签名。

## 建议的下一阶段

使用macOS钥匙串存储access、refresh和稳定设备标识，项目只保留非敏感期限/状态。使用同一设备身份进行续期；更新返回的凭据，避免并发刷新互相覆盖。401最多刷新并重试一次；网络失败或429/5xx不能直接等同于refresh失效。服务端明确拒绝续期时提示重新扫码。

这是建议的本地保存方式，参考[Apple钥匙串说明](https://support.apple.com/guide/keychain-access/what-is-keychain-access-kyca1083/mac)；尚未实现任何钥匙串读写。

初始化至少需要另一次扫码，因为之前的临时凭据已丢弃。启用前应获得包含长期本地保存和服务端刷新请求的明确授权，完成最小实现、异常路径检查与独立复审，再用用户账户验证实际续期。当前没有实现上述方案，没有更改凭据授权边界，也没有创建自动化。
