# 本地会话保存、续期与平台兼容审查

## 需求与实现

维护者要求实现私人TODO中的token保存与续期，用本地文件替代macOS钥匙串，并明确开发平台不限制运行平台。

当前入口提供login/reuse-session/logout，保留不读取或写入凭据的approved-one-shot。持久化数据仅含两个token、稳定设备ID、版本、会话状态、保存时间与JWT期限元数据，位于Git忽略的tmp/auth/session.json。不会从浏览器、应用或既有第三方token文件读取凭据。

固定refresh入口为api.xiaoyuzhoufm.com/app_auth_tokens.refresh；请求只携带refresh及device ID，参考[r266客户端](https://github.com/r266-tech/xiaoyuzhou/blob/main/xiaoyuzhou/client.py)和[VideoNote客户端](https://github.com/HuangYincan/VideoNote-MCP/blob/main/app/downloaders/xiaoyuzhou_subtitle.py)。登录token对从新响应获取，参考[DSH扫码实现](https://github.com/jerryqx/dsh-xiaoyuzhou/blob/main/lib/xyzfm.js)。代码保留本项目的白名单、禁止重定向、CDN无凭据、请求数量上限与固定错误码规则。

- 两个token原子写入并fsync；整次会话操作持有跨进程锁，避免并发刷新覆盖。POSIX为0700/0600及flock；Windows为受限DACL与msvcrt非阻塞锁。会话路径拒绝符号链接/reparse point。
- 每次运行最多一次401刷新及重试；刷新必须完整保存新token对后再读取。
- 外部refresh发出前保存pending。网络/5xx/响应缺token/保存失败/进程中断后不重用可能已轮换的旧refresh，不删除凭据或自动扫码。429允许以后再试；401/403明确拒绝才标记重新登录。
- Windows不强制导入resource或SIGHUP；二维码、报告和凭据使用共享权限设置。文件仍是明文，忽略规则不是加密，也不处理服务端撤销。
- JWT仅提取iat/exp，不验证签名，不把第三方描述的2小时当成当前账户实测期限。

## 验证与处理

Python3.14及Python3.9各42项离线测试通过，覆盖真实文件权限、跨进程互斥/复用、持久化CLI入口、401轮换/一次重试、二次401、缺token、网络/429/5xx、落盘失败、pending崩溃保护、CDN与一次性隔离、脱敏和Windows分支模拟。

最低版本测试发现HTTPError关闭的异常可覆盖既定错误码。新增共享close_http_error，接入读取、扫码和refresh三个消费者：普通关闭异常被吸收，KeyboardInterrupt仍传播。原有二维码测试在3.9复跑通过；另外实际注入三个消费者的close失败，分别得到预期固定错误码，并验证取消继续传播。

源码语法、差异空白、公开Markdown链接、限定敏感模式与Git忽略路径检查通过。离线测试及独立复审时没有实际session.json；所有认证测试均用固定fixture和隔离临时目录，不访问网络或真实账户。后续用户授权的真实验证单独记录如下。

## 独立复审与边界

基线为0.1.0首次提交1115946。范围包含两个原入口、新xyz_auth、相关测试、README/CHANGELOG和期限调查补充；全部为未暂存修改，新文件单独纳入diff和快照。

第一会话：Claude Code 2.1.292，事件确认claude-sonnet-5-5，high为请求值。因供应商会话额度耗尽退出1，没有非错误最终报告，状态failed，不能记为通过。

替代会话：Codex CLI 0.159.2，新会话独立只读审查；请求gpt-6.1-sol/medium，最终报告未自行确认实际子型号/effort，保留未知。退出0，非空最终报告，范围内8份文件哈希与r2输入一致。

### Reviewer原始结论（脱敏摘录）

“未发现可确认的新增correctness、遗漏或回归findings。旧问题：无确认项。”

Reviewer报告审查范围complete、验证limited，完整读取三个实现及相关入口、测试和文档，核查保存/锁、401、pending、轮换失败、取消、HTTP分类、CDN、脱敏和退出。首次测试受只读沙箱的临时目录限制，获准在fixture范围重跑后实际42项通过；没有修改源码或项目状态，也没有登录、refresh或读取私人数据。

### Coder处理

没有有效findings需要修复。上述Python3.9关闭异常修复已包含在替代审查对象中。Reviewer未独立执行的Python3.9及三个关闭异常消费者已由coder补测，不冒充reviewer亲自执行。

完整原始报告未改写，与Prompt、两轮事件流、退出码、scope diff、新文件快照和哈希保存在本地忽略的docs/reviews/evidence/saved-session-20261013，0700/0600；不随源码公开。这一轮没有额外发起review reviewer。

## 授权实测补充

维护者已明确授权扫码保存、跨进程复用、服务端续期与账户读取；实现源码与已复审对象一致，没有把真实凭据、会话或账户报告发给reviewer。

| 阶段 | 结果 | 二维码请求 | refresh请求 |
| --- | --- | --- | --- |
| 扫码登录并保存两个token | 历史、播放时间、固定文稿读取通过 | 已创建并完成扫码 | 0 |
| 首次新进程复用 | 读取通过 | 0 | 0 |
| 主动真实续期 | 两个token均轮换并保存，读取通过 | 0 | 1 |
| 续期后再次新进程复用 | 读取通过 | 0 | 0 |

四个阶段每次读取请求均为6次，历史40条、播放时间匹配40条，固定单集文稿409段、27,551字符。续期设备标识保持一致，保存后重新加载的token对与响应相符；会话状态ready、目录0700/文件0600，二维码已清理。私有报告及汇总保存在Git忽略的reports目录；本段只记录汇总，不公开账户条目或凭据。

access元数据有iat、没有exp，未公开原始值。这次主动refresh没有等待自然过期，不能代替真实过期401恢复或长期寿命验证。

## 未完成的验证

- 自然过期后的真实401恢复、实际有效期限及长期未用失效规则尚未验证。
- 原生Linux/Windows完整运行、ACL与锁未验证；当前Windows测试是mock，不能证明原生行为。开发机Docker daemon未运行，没有启动系统服务来补测。
- 实现与实测阶段未新增定时后台续期，也没有stage/commit/push；0.1.0标签指向原首次提交。

## 0.2.0 提交检查

维护者授权将本次会话保存、复用、续期及跨平台支持作为0.2.0提交。源码及测试哈希与已复审对象一致，实测补充仅更新文档；无需重复发起独立复审。CHANGELOG已标记0.2.0，凭据、账户报告、私人TODO及原始审查证据继续由Git忽略，不纳入版本提交。
