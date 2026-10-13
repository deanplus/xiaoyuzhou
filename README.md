# 小宇宙收听历史与文稿读取

通过小宇宙 App 扫码确认登录，在本地验证收听历史、最近播放时间和单集文稿的读取。使用非官方客户端接口，平台改版可能导致失效。

## 运行

面向macOS、Linux和Windows，需要Python 3.9+；开发与当前原生测试在macOS完成。二维码由 [qrcode](https://github.com/lincolnloop/python-qrcode) 在本地生成 PNG。

在项目根目录安装依赖（macOS/Linux）：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Windows PowerShell安装方式：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

首次扫码并保存凭据（运行前明确同意登录、保存和后续自动续期）：

```sh
python src/xyz_qr_probe.py --login
```

以后复用本地会话，API返回401时自动续期并重试一次，无需重新扫码：

```sh
python src/xyz_qr_probe.py --reuse-session
```

仅测试、不保存凭据时用`--approved-one-shot`；删除本地凭据用`--logout`。这些模式互斥。复用失败不会自动创建新二维码；本地删除也不会撤销服务端会话。

打开脚本输出的临时二维码图片，用小宇宙 App 扫码并确认。最多等候约四分钟，超时后不自动重建二维码。登录会建立服务端会话，需要调用者已取得用户授权。

结果保存在`reports/<北京时间日期>/`。`read_paths_verified`表示本次各读取分支通过验证；`partial_reads`或错误退出2。查看报告中的`verification`及各分支状态。

离线测试（不联网、不登录）：

```sh
PYTHONPATH=src python -B -m unittest discover -s tests -v
```

Windows PowerShell执行测试时先设置`$env:PYTHONPATH="src"`，再运行`python -B -m unittest discover -s tests -v`。Linux/Windows原生环境尚未在本次开发机上实测。

## 范围与数据

- 最多读取两页历史，文稿目标目前固定在`src/xyz_probe.py`的`TARGET`；尚未实现全量分页、按天清单和任意单集文稿导出。
- `playedAt`是最近播放时间的候选字段，不是完整逐次播放日志；进度0不能判定未听过。文稿仅验证文本读取和时间范围，未校对准确性或完整性。
- `--login`把access/refresh token及稳定设备标识保存在`tmp/auth/session.json`，两个token一起原子更新；该目录被Git忽略。文件是明文，适合放在自己控制的本地磁盘，不使用macOS钥匙串。`--approved-one-shot`只在内存中使用access token。
- 同一会话只允许一个进程使用；每次运行最多发起一次refresh。续期成功必须同时取得并保存两个token后再重试读取。网络错误、429和5xx不会清除凭据或自动扫码。
- 续期发出前保存待确认标记。请求结果不确定、进程中断、响应缺token或轮换落盘失败时，阻止再次使用可能失效的旧refresh；状态是“续期结果未知”，不等同于认证拒绝。429允许下次运行重试；明确认证拒绝才标记需要重新登录。恢复未知状态可在排查后显式重新`--login`，不能手工把状态改为ready或复用旧refresh。
- 不下载或播放音频；旧短信入口停用。
- 二维码图片也是临时认证数据，不要分享或贴到issue。确认、超时及正常取消后会清理；强制杀进程等异常退出可能留下临时目录，需要本地删除。
- 报告可能包含个人收听历史，即使没有token也不适合公开。macOS/Linux的会话及报告目录0700、文件0600；Windows会话、二维码和报告使用仅当前用户与SYSTEM可访问的ACL；报告、导出、凭据、日志、原始审查证据和`tmp/`均被Git忽略。
- 发布或分享前检查文件列表与暂存diff；`.gitignore`不阻止其他工具读取，也不会自动清除已经跟踪的敏感文件。

公开客户端描述access约2小时，refresh固定有效天数未知；本项目已验证保存、跨进程复用、一次真实续期及续期后的读取。测试token没有exp字段，实际签发期限和自然过期后的401恢复仍未实测；JWT解码不等于签名验证或服务端有效期保证，不能保证永久免扫码。参见[期限调查](docs/research/credential-lifetime-20261013.md)。

## 目录

```text
src/                 Python读取与扫码入口
tests/               离线安全及读取测试
docs/                研究与审查记录
reports/             本地报告（Git忽略）
tmp/                 会话凭据与临时文件（Git忽略）
requirements.txt     二维码生成依赖
LICENSE              MIT许可证
```

## 参考项目

下列项目提供了接口、字段和处理流程的参考；未将上游源码库打包为本项目的一部分。续期的接口及字段也参考这些项目，错误处理与本地保存按本项目规则实现。

| 项目 | 参考范围 | 上游MIT版权声明 |
| --- | --- | --- |
| [ultrazg/xyz](https://github.com/ultrazg/xyz) | 历史分页、App请求格式、早期短信路径调查 | Copyright (c) 2024 2bit |
| [r266-tech/xiaoyuzhou](https://github.com/r266-tech/xiaoyuzhou) | 单集/文稿流程、native media标识、续期接口 | Copyright (c) 2026 r266-tech |
| [jerryqx/dsh-xiaoyuzhou](https://github.com/jerryqx/dsh-xiaoyuzhou) | 扫码创建/轮询、历史与进度关联 | Copyright (c) 2026 dsh-xiaoyuzhou contributors |
| [HuangYincan/VideoNote-MCP](https://github.com/HuangYincan/VideoNote-MCP) | 扫码、文稿metadata/CDN、Android UA、期限资料 | Copyright (c) 2026 Yincan Huang |
| [hesorchen/xiaoyuzhou-juicer](https://github.com/hesorchen/xiaoyuzhou-juicer) | 认证期限和refresh轮换 | Copyright (c) 2026 xiaoyuzhou-juicer contributors |

项目采用[MIT许可证](LICENSE)，上述MIT参考项目的版权声明集中保留在本页。二维码依赖的许可由其安装包提供。官方账号页公开前端用于核对接口行为，不代表官方支持第三方集成。

变更与验证记录见[CHANGELOG](CHANGELOG.md)及`docs/`。
