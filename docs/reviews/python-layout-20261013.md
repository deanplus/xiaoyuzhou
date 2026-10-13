# MIT、目录与二维码调整核验

## 当前改动

按维护者要求添加MIT LICENSE。版权署名采用仓库已有git作者名。README删除具体账户测试日期、计数和手机对照记录，只保留运行、范围、数据规则和参考项目。第三方来源及版权行集中到README，移除单独声明、许可目录和PRIVACY文档。

Python入口移至src，测试移至tests；BASE调整到项目根目录，reports输出位置保持一致。二维码改为qrcode 8.2的PyPNGImage本地生成，依赖由requirements声明，不再调用Swift、子进程或外部二维码服务。临时目录清理与认证/读取约束保持原有逻辑；PNG创建使用O_EXCL和0600，错误只输出固定代码。私人的TODO和.venv均被忽略。

## 本地实测

- 新虚拟环境安装requirements成功；pip check无依赖冲突。
- 22项离线测试通过，包括真实PNG输出、灰度与白边、0600、不覆盖已有文件、过大数据固定错误和既有扫码清理路径。
- 实际检查BASE等于项目根目录；临时目录内save_report仍写入reports/日期，0700/0600并拒绝覆盖。
- 公开Markdown本地链接无缺失；私人目录与虚拟环境被忽略；限定个人路径和收听样本模式无命中。
- 源码、测试及requirements在两次审查之间的SHA-256保持一致；本轮没有网络登录、refresh或真实账户读取。

尚未验证手机解码新后端生成的真实登录二维码、Linux或Python3.9环境。不能把PNG文件检查等同于新的账户登录实测。

## 独立复审

Reviewer为Claude Code 2.1.292；事件元数据确认模型claude-sonnet-5-5，命令请求high effort，reviewer未能自行确认实际effort。首轮及同一会话续接均退出0，并得到非空最终报告。

状态为limited：reviewer通过Read/Grep/Glob完成静态核查；Bash被拒，未独立运行测试、渲染、哈希或权限检查。续接读取了coder提供的离线实测证据，但没有将其声称为reviewer独立执行。没有高、中优先级findings，也没有续接新增有效findings。

| 编号 | 原始意见 | Coder处理与复核 |
| --- | --- | --- |
| R1 Low | CHANGELOG旧条目与当前文件状态可能混淆 | 成立，旧小节标注此前记录；reviewer确认解决 |
| R2 Low | README删除的汇总数字仍在历史审计中 | 范围解释：用户点名README，保留脱敏历史审计，不含个人节目或精确收听时间；reviewer接受关闭 |
| R3 Low | LICENSE使用泛指contributors | 改为仓库git作者名，无邮箱；reviewer确认文本变化，未独立查git配置 |
| R4 Low | 将来复制上游实质代码需要适用许可通知 | 当前没有打包上游库，README保留来源/版权，LICENSE保留MIT正文；reviewer接受当前范围，未来复制新代码仍需核对通知；上游版权行本轮未被reviewer独立验证 |
| R5 Low | 依赖未完全锁定、跨平台声明未验证 | README仅声称macOS已验证；Python最低版本依据已安装库元数据；pypng未固定/hash锁文件及Linux CI不在本轮范围，reviewer接受该边界 |
| R6 Low | QR隐私提示在合并文档后遗漏 | 成立，README补充二维码不可分享及强制终止可能残留；reviewer确认解决 |

原始Prompt、scope.diff、变更前源码快照、两轮完整原始报告、事件流、退出码及验证日志存于本地忽略的docs/reviews/evidence/python-layout-20261013，目录0700、文件0600，不随源码公开。此处是脱敏审计摘要，原始报告没有被覆盖或改写。

没有stage、commit、push或发布。

## 0.1.0首次提交检查

维护者授权检查后创建首个版本。CHANGELOG已压缩为当天的0.1.0项目初始化与主要功能记录。

- 22项离线测试通过；依赖无冲突，CLI帮助入口可正常运行，Python源码语法有效。
- 源码、测试和requirements的SHA-256与上次复审对象一致，本轮仅改文档，不为相同代码重复启动review。
- 待收录文件共15份，本地Markdown链接有效；14个私人路径忽略探针全部通过。
- 限定JWT、私钥、API key、个人路径和收听样本模式未匹配真实敏感信息；唯一邮箱匹配是固定恶意URL测试fixture。模式扫描不保证覆盖所有秘密。
- 真实报告已不存在；个人TODO、原始审查证据和虚拟环境排除在提交范围外。
- 本次仅授权本地首次提交及v0.1.0版本标记，不包含远端推送、平台发布或新登录。当前二维码后端仍未重新做真实手机扫码验证。
