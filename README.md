# 小宇宙收听历史与文稿读取

通过小宇宙 App 扫码确认登录，在本地验证收听历史、最近播放时间和单集文稿的读取。使用非官方客户端接口，平台改版可能导致失效。

## 运行

需要 Python 3.9+；当前在macOS验证。二维码由 [qrcode](https://github.com/lincolnloop/python-qrcode) 在本地生成 PNG。

在项目根目录安装依赖：

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

仅在用户明确同意本次登录后运行：

```sh
python src/xyz_qr_probe.py --approved-one-shot
```

打开脚本输出的临时二维码图片，用小宇宙 App 扫码并确认。最多等候约四分钟，超时后不自动重建二维码。登录会建立服务端会话；`--approved-one-shot`需要调用者已经获得用户授权。

结果保存在`reports/<北京时间日期>/`。`read_paths_verified`表示本次各读取分支通过验证；`partial_reads`或错误退出2。查看报告中的`verification`及各分支状态。

离线测试（不联网、不登录）：

```sh
PYTHONPATH=src python -B -m unittest discover -s tests -v
```

## 范围与数据

- 最多读取两页历史，文稿目标目前固定在`src/xyz_probe.py`的`TARGET`；尚未实现全量分页、按天清单和任意单集文稿导出。
- `playedAt`是最近播放时间的候选字段，不是完整逐次播放日志；进度0不能判定未听过。文稿仅验证文本读取和时间范围，未校对准确性或完整性。
- access token仅在内存中使用，结束丢弃；不保留refresh token，不续期，不下载或播放音频。旧短信入口停用。
- 二维码图片也是临时认证数据，不要分享或贴到issue。确认、超时及正常取消后会清理；强制杀进程等异常退出可能留下临时目录，需要本地删除。
- 报告可能包含个人收听历史，即使没有token也不适合公开。报告目录0700、文件0600；报告、导出、凭据、日志、原始审查证据和`tmp/`均被Git忽略。
- 发布或分享前检查文件列表与暂存diff；`.gitignore`不阻止其他工具读取，也不会自动清除已经跟踪的敏感文件。

长期复用需要另行实现凭据保存与轮换续期。公开客户端描述access约2小时，refresh固定有效天数未知；本项目尚未验证实际期限，不能保证永久免扫码。参见[期限调查](docs/research/credential-lifetime-20261013.md)。

## 目录

```text
src/                 Python读取与扫码入口
tests/               离线安全及读取测试
docs/                研究与审查记录
reports/             本地报告（Git忽略）
tmp/                 临时文件（Git忽略）
requirements.txt     二维码生成依赖
LICENSE              MIT许可证
```

## 参考项目

下列项目提供了接口、字段和处理流程的参考；未将上游源码库打包为本项目的一部分。续期相关参考目前仅用于后续设计。

| 项目 | 参考范围 | 上游MIT版权声明 |
| --- | --- | --- |
| [ultrazg/xyz](https://github.com/ultrazg/xyz) | 历史分页、App请求格式、早期短信路径调查 | Copyright (c) 2024 2bit |
| [r266-tech/xiaoyuzhou](https://github.com/r266-tech/xiaoyuzhou) | 单集/文稿流程、native media标识、续期设计 | Copyright (c) 2026 r266-tech |
| [jerryqx/dsh-xiaoyuzhou](https://github.com/jerryqx/dsh-xiaoyuzhou) | 扫码创建/轮询、历史与进度关联 | Copyright (c) 2026 dsh-xiaoyuzhou contributors |
| [HuangYincan/VideoNote-MCP](https://github.com/HuangYincan/VideoNote-MCP) | 扫码、文稿metadata/CDN、Android UA、期限资料 | Copyright (c) 2026 Yincan Huang |
| [hesorchen/xiaoyuzhou-juicer](https://github.com/hesorchen/xiaoyuzhou-juicer) | 认证期限和refresh轮换调查 | Copyright (c) 2026 xiaoyuzhou-juicer contributors |

项目采用[MIT许可证](LICENSE)，上述MIT参考项目的版权声明集中保留在本页。二维码依赖的许可由其安装包提供。官方账号页公开前端用于核对接口行为，不代表官方支持第三方集成。

变更与验证记录见[CHANGELOG](CHANGELOG.md)及`docs/`。
