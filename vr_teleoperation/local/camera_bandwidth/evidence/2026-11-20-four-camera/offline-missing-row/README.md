# MOCK_ONLY 中间缺样本记录

本目录不是实机示范。来自离线运行 `MOCK_DURABLE_FINAL`，其运行返回 0，
旧版样本完整性检查却发现：

- 左臂 action/obs 各 30 行；
- 右臂 action/obs 各 29 行，均缺已知合成 tick=1，不是尾部 tick=29；
- 旧 metadata 曾标 success，不能把该标记当成完整性通过。

保留四个 Parquet、metadata 和原始 Dora 日志；合成 JPEG 没有必要长期保留。
当前修订只给 Recorder 订阅增加有界队列，不对机械臂控制源施加反压。
mock 的结束消息新增明确的四路发送计数，接收数不足时不能成功完成，
验证器还核对完整的 0..29 合成数组，而不只检查行数。

后续 `MOCK_DURABLE_BUFFERED` 全部 30 行并通过验证，但没有包级证据证明
这次缺行必然由默认队列导致，也不能据此保证真实设备长期无丢包。
