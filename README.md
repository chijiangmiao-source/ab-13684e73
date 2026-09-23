# Pulse Audit — 脉冲中子符合分组服务

把 2–8 个探测器记录的 4–80 个命中划分成物理事件与噪声。事件须满足：

- 至少两个命中，且每个命中至多分属一个事件；
- 同一事件内同一探测器至多一个命中；
- 事件内最晚与最早整数时刻之差（闭区间跨度）不超过符合窗。

优化目标按字典序：**先最大化已分组命中的可信度和，再最少化事件数**。

## 接口

### `GET /health`

返回 `200 {"status":"ok"}`。

### `POST /audit`

请求：

```json
{
  "window": 2,
  "detectors": ["D1", "D2", "D3"],
  "hits": [
    {"id": "a", "detector": "D1", "time": 0, "confidence": 5},
    {"id": "b", "detector": "D2", "time": 1, "confidence": 5},
    {"id": "c", "detector": "D3", "time": 2, "confidence": 5},
    {"id": "d", "detector": "D1", "time": 9, "confidence": 1}
  ]
}
```

- `window`：非负整数符合窗（闭窗）；
- `detectors`：2–8 个唯一 ASCII 标识；
- `hits`：4–80 个命中，`id` 为唯一可打印 ASCII 串，`time` 为整数，
  `confidence` 为正整数；
- 前提：任意长度为 `window` 的闭窗内至多 10 个命中。

成功响应 `200`，字段均在 `result` 内：

```json
{
  "result": {
    "grouped_confidence": 15,
    "event_count": 1,
    "optimal_solution_count": 1,
    "canonical_groups": [["a", "b", "c"]],
    "noise": ["d"],
    "pair_membership": [
      {"pair": ["a", "b"], "status": "always"},
      {"pair": ["a", "c"], "status": "always"},
      {"pair": ["b", "c"], "status": "always"}
    ]
  }
}
```

- `optimal_solution_count`：最优分组方案数，任意精度整数（JSON 数字，
  不加引号、不丢精度）；
- `canonical_groups`：规范解。先把每个事件的成员标识排序，再对事件元组
  序列排序，取字典序最小者；
- `pair_membership`：对每一对**可同组**（不同探测器且时刻差不超过窗）
  的命中给出：
  - `always`：在所有最优方案中都同属一个事件；
  - `sometimes`：存在同组、也存在不同组的最优方案；
  - `never`：没有任何最优方案使二者同组。

失败响应 `400`，只含 `errors`，不夹带任何结果字段：

```json
{"errors": [{"path": "hits[1].time", "message": "must be an integer"}]}
```

## 算法

不枚举完整分组方案。命中按 `(时刻, id)` 排序后做扫掠动态规划：

- 事件只在其最早命中处“发起”，DP 状态仅是未来 10 个位置中已被先前事件
  占用的 10 位掩码（由“任意闭窗至多 10 命中”保证可同组的更晚命中不超过
  9 个）；
- 每个位置枚举该命中作为噪声、或发起单个可行事件（单事件至多 2⁹ 个成员
  子集，且只保留两两兼容的团）；
- 层间聚合同一掩码状态，一次前向扫掠同时得到最优可信度和、最少事件数、
  最优方案数（任意精度计数）以及成对归属的交集位集（必然）与并集位集
  （从不 = 不在并集中）；
- 规范解通过“逐 id 锁定 + 前向/后向价值 DP”裁决：最小 id 的未决命中若
  能进入某个最优方案中的事件，选择其可行成员 id 元组中字典序最小者并
  锁定，重复至全部命中处理完毕。

满规模（80 命中、10 命中滑窗）最坏实测约 1.5 秒。

## 运行与验证

仅依赖 Python 3.11 标准库。

```bash
# 本地运行
python3 -m app.server            # 默认 0.0.0.0:8080，可用 AUDIT_PORT 覆盖

# 本地测试 + 冒烟
AUDIT_PORT=8099 python3 -m app.server &
AUDIT_BASE_URL=http://127.0.0.1:8099 python3 scripts/verify.py

# Docker
docker compose up -d --build     # 宿主端口可配：AUDIT_HOST_PORT=9090
docker compose run --rm verify   # 单次校验：构建检查 + 测试 + HTTP 冒烟，按结果退出
```

`verify` 服务依次执行：字节编译构建检查、完整测试套件（多解计数、规范
裁决、成对归属、窗口闭区间边界、校验路径、满规模性能与大整数方案数）、
对运行中服务的 `/health` 与 `/audit` HTTP 冒烟；全部通过退出码 0，否则
非零。
