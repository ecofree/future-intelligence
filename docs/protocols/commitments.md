# FI-JSON-v1 / FI-MERKLE-v1

本模块只证明内容完整性。缺少可信外部锚定时，不能证明内容产生于某个历史时间。

## 规范化输入
采用本项目显式版本化 JSON 子集：null、bool、整数、字符串、列表及字符串键字典。
**拒绝全部浮点、tuple、bytes 及非字符串键。** 概率用整数百万分位 `probability_ppm` 表示，费用后续用整数最小单位。
JSON 编码：UTF-8、`ensure_ascii=False`、`sort_keys=True`、分隔符 `,` 和 `:`，不做 Unicode 归一化；不同 Unicode 序列视为不同字节。此协议不声称兼容 RFC8785。

提交正文包含所有待承诺的规范字段和独立随机 nonce；nonce 至少 32 字节，用64位十六进制保存。正式 nonce 只能由安全随机生成；测试固定 nonce 只能在 MOCK 区使用。

record_digest = SHA256(`FI:record:v1\0` || canonical_json)
leaf = SHA256(0x00 || record_digest_bytes)
node = SHA256(0x01 || left || right)

奇数层复制最末节点；空树拒绝。证明包含 leaf_index、leaf_count 和逐层兄弟节点/方向。验证器检查拓扑、证明长度及奇数复制节点，避免把错误索引当成合格包含证明。

最终批次根须与预登记清单哈希一起锚定。Merkle 证明本身不保证清单完整、不保证数据库追加、也不保证时间。

## API 安全边界
提交时间由服务器记录并与独立外部证明比对；不能相信模型生成的 observed_at。记录被外部锚定之后，不允许以替换 JSON 的方式修复错误，必须创建新记录。
