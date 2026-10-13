# 小宇宙读取入口核查（2026-10-13）

目标：通过用户本人的一次临时登录，验证收听历史、实际播放时间与文稿读取。不是部署长期自动化，也不声称私有接口受官方第三方支持。

## 本轮公开来源与匿名实测

- [官方统一账号页](https://accounts.xiaoyuzhoufm.com/login)及其公开 `index-CqItVqUi.js`：`/login` 默认 `clientId=xyz-web`；支持二维码创建和轮询登录。此前“没有会话桥接依据”的结论需要更新：扫码路径值得实测，而不是继续尝试旧短信入口。
- [DSH 客户端源码](https://github.com/jerryqx/dsh-xiaoyuzhou/blob/main/lib/xyzfm.js)：扫码确认响应携带 access token；用该 token 调用 App 历史与播放进度接口。该仓库声称历史一页 20 条，游标按原样传递。公开实现不替代本账户的实测。
- [VideoNote 登录实现](https://github.com/HuangYincan/VideoNote-MCP/blob/main/videonote_mcp/cli.py)和[文稿读取实现](https://github.com/HuangYincan/VideoNote-MCP/blob/main/app/downloaders/xiaoyuzhou_subtitle.py)：同样使用扫码；文稿需要 `transcriptMediaId`，返回签名 CDN URL 后以 Android UA 获取 JSON。
- 本轮目标[单集公开网页](https://www.xiaoyuzhoufm.com/episode/6ac75a2ae742e36efcbfc909) HTTP 200，`__NEXT_DATA__` 含标题、4688 秒时长和 `transcriptMediaId`。只有文稿标识，不是已取得文稿。
- 不带凭据的 `POST /v1/episode-played/list-history {}` 返回 HTTP 401。
- 不带凭据的 `POST /v1/episode-transcript/get {eid, mediaId}` 返回 HTTP 401。
- 未创建二维码、未登录时的以上检查均为公开读取；账号验证另在获得用户明确同意后执行。

公开来源缓存和原始审查证据仅留本地，未随源码发布公共页面全文或账号响应。

## 待实测链路

1. `web-api.xiaoyuzhoufm.com/v1/auth/qrcode/create` 创建二维码，用户使用小宇宙 App 扫码并确认。
2. 同域 `/v1/auth/qrcode/login` 轮询；仅从这次新响应获取 access token，不读取浏览器/应用已有登录态。新响应的 refresh token 不保留、不使用。
3. `api.xiaoyuzhoufm.com/v1/episode-played/list-history` 最多两页，原样传递游标。
4. 同域 `/v1/playback-progress/list {eids}` 补充 `playedAt` 与进度；单集发布日期不能作为收听日期。`playedAt` 尚需与 App 对照，可能只表示最近一次播放，不能当完整逐次播放日志。
5. 同域 `/v1/episode/get` 获取目标单集的 native 文稿标识，再调用 `/v1/episode-transcript/get`。
6. 签名 URL 只留内存；已允许的官方 CDN 只做无凭据 GET，统计文本段数、时间范围和相对时长，不保存全文或音频。

## 用户批准的本轮边界

用户已明确回复“同意一次扫码测试”。access token 只在本地进程内存，结束丢弃；不落盘、不续期，不播放/下载音频，不写收听进度或历史，不改变订阅。最多 120 次二维码轮询（约四分钟）、10 次内容读取。临时二维码 PNG 在流程结束时删除。丢弃本地 token 不会撤销服务端会话。

初版入口为 `xyz_qr_probe.py --approved-one-shot`，使用macOS Core Image。后续源码已移至`src/`，二维码改用Python qrcode本地生成PNG，不再需要Swift；当前运行方式见README。

历史结果仅用于有界可行性验证：两页不等于完整历史；成功获取文稿也不证明当天听过该单集。

## 验证汇总与数据清理

真实扫码验证已成功：新签发的web-api access token被App API接受；两页40条历史，40条playedAt可解析；目标文稿409段、27,551字，CDN请求成功。只保存过验证统计，没有导出全文或音频。一次较早的扫码尝试超时，未发出内容读取请求。

两个手机界面样本的顺序和isFinished状态与API一致：当前播放为false，已完成样本为true。进度0不能当作未收听判据。playedAt只有最近播放时间的候选语义，不是完整逐次播放日志；其精确时间和所有样本未全面验证。

文稿时间戳接近节目末尾且单调，只证明读取与时间范围，不证明逐字准确或完全无遗漏。长期持久化、续期、全量历史分页和文稿导出尚未实现。

开源准备时已删除真实JSON报告，并移除本文的个人节目名称、精确播放时间和手机对话细节。上面的指标是不可用于还原原始历史的汇总结论；原始账户数据不随仓库发布。

详见[扫码验证审计汇总](../reviews/qr-probe-20261013.md)和[README中的数据规则](../../README.md#范围与数据)。
