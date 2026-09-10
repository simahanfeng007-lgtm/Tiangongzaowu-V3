# P10 R3-B 续跑恢复与本机验证

日期：2026-09-10。代码检查点，不是生产验收。R3-C 尚待完成，P10 未合并。

## 恢复身份

- 远端恢复基点：`02213f6d01b35a9ba90d90d1186eef05f3c3309a`。
- 原 R3-B 补丁从三个已提交的 base64 分块解码，拼接后按 XZ 校验解压；
  26 个文件的前后 git blob 身份逐一吻合，再用 `git apply --index` 应用。
- 压缩数据：19,788 字节，SHA-256
  `0c412559a2664f8d0a5f7c0d0b069912c95806e47b5c9b05858c7c3841dbaf83`。
- 补丁：91,454 字节，SHA-256
  `92bcd8f6f247d6b02c2f0c5498e969d3a32b535145369e77b8f30a228d81fc8b`。
- 分块与临时导出工作流随恢复提交清理；原内容仍可从上述 Git 历史复核。

原 R3-A CI run `34308608216` 的 head 为
`3ec70b1cc10d17e8e9debfe7491c38c716ac0412`：Ubuntu 1382 passed / 10 skipped；
Windows 1381 passed / 11 skipped，均另有 11 subtests。这些结果只证明 R3-A。
导出 run `34379777936` 不是产品测试。

## 复现与修复

首次本机恢复验证为 177 passed / 2 failed / 18 subtests。两个失败均在实际
Gateway 启动时触发：原 P8 路径观察对普通 C 盘目录也报
`STATUS_REPARSE_POINT_ENCOUNTERED`。本机逐级检查目录没有文件系统 reparse point。
同一目录、相同 `OBJ_DONT_REPARSE`，DOS `\\??\\C:` 名称失败，而直接设备名成功。

Windows 的 DOS 驱动器映射本身位于对象命名空间。修复先用 `QueryDosDeviceW`
读取当前直接设备映射，再以原 `OBJ_DONT_REPARSE`、只读元数据权限打开完整路径。
打开与规范化名称来自同一个 handle，均核对设备/共享根；返回前重查设备映射，
映射变化、路径型映射、查询失败及文件系统 junction 仍 fail closed。没有跟随
链接的 fallback、磁盘根 handle、ACL 修改或权限授权。

参考：[QueryDosDeviceW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-querydosdevicew)、
[OBJECT_ATTRIBUTES](https://learn.microsoft.com/en-us/windows/win32/api/ntdef/ns-ntdef-_object_attributes)。

实际 Windows 普通目录、8.3 名称、叶与祖先 junction 测试通过；UNC 为模拟 API
契约验证，真实共享和 AppContainer 仍须单独验证，不能由本次本机结果推出。

## 验证边界

Python 3.12.14，依赖来自 `requirements-source.lock`。本机未设置
`TIANGONG_CI_ENV`，未因 Windows CI 策略跳过真实平台测试。

- 路径契约及 R3-B 真实 Gateway 启动：48 passed，3 warnings。
- 扩大组：316 passed，0 failed/skipped，23 subtests，5 条既有 Pydantic warnings；
  用时 247.81 秒。包括路径/启动安全、R3-A/B、学习冻结、journal replay、
  Source Authority、生成镜像及完整 `tests/golden/p19_r2/`。
- 测试时 `UPDATE_FREEZE`、`UPDATE_FINGERPRINT`、`UPDATE_GOLDEN` 均移除；
  测试前后所有 tracked 输入哈希一致。
- 原始日志、JUnit、环境与输入身份保存在本机忽略目录
  `output/p10-r3b-closeout-20260910/`；首次失败和补丁恢复证据在
  `output/resume-audit-20260910/`。本地目录不是远端持久 CI artifact。

Plane 1.13 明确声明路径算法变化，继承全部 105 条冻结路径。先验证旧 freeze
能够报 drift，再只用原 freeze/fingerprint 生成器更新；Golden baseline 未修改。
官方 source mirror generator 同步后重新检查。

完整 P10 focused gate 新增路径/启动安全选择，保留此前所有测试。提交后必须取得
同一个 head 的 Windows/Ubuntu CI 原始结果；本机 316 项不能替代该完整范围。

R3-C 仍需观察起止残留与归属快照、真实入口工作负载、在途 P9 source pin 保留证据。
本地代表性验证不等于真实生产观察；`zero_usage_proven=false` 不变，有实际旧调用
就保留相应兼容面。P8/P9 的生产、签审和回退债没有由本次修复结清。
