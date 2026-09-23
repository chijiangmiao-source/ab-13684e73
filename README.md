# 脉冲中子命中分组审计服务

将 2–8 个探测器、4–80 个命中（唯一 ASCII 标识、所属探测器、整数时刻、
正整数可信度）在给定非负符合窗（闭区间）下做最优事件分组。

## 优化目标（按优先级）

1. **最大化已分组命中的可信度之和**；
2. 在其前提下 **最少化事件数**；
3. 多解时以「成员标识排序后的事件序列」做 **词典序规范裁决**。

约束：一个事件至少两个命中、同一探测器至多一个、最晚与最早时刻差 ≤ W；
每个命中只能属于一个事件或作为噪声；任意长度 W 的闭窗内至多 10 个命中。

## 接口

- `GET /health` → `200 {"status":"ok"}`
- `POST /audit`
  ```json
  {
    "window": 2,
    "detectors": ["A", "B", "C"],
    "hits": [
      {"id": "h1", "detector": "A", "time": 0, "confidence": 5},
      {"id": "h2", "detector": "B", "time": 1, "confidence": 7}
    ]
  }
  ```
  成功 `200`：
  ```json
  {
    "optimal_confidence": 12,
    "event_count": 1,
    "solution_count": "1",
    "canonical_groups": [["h1", "h2"]],
    "pair_relations": [{"a": "h1", "b": "h2", "relation": "always"}]
  }
  ```
  失败 `400`（仅含按路径的字段错误，不夹带任何结果）：
  ```json
  {"errors": [{"path": "hits[1].time", "message": "..."}]}
  ```

成对归属 `relation`：

- `always`（必然）：该可同组命中对出现在 **所有** 最优解中；
- `optional`（可选）：出现在部分而非全部最优解中；
- `never`（从不）：可同组（探测器不同、时刻差 ≤ W）但不在任何最优解中同组。

`solution_count` 为任意精度整数的十进制字符串。

## 算法（不枚举完整方案）

按 `(时刻, 标识)` 排序后做扫描线动态规划。处理命中 i 时，所有“已被早先
事件占用、尚未关窗”的命中都落在 `[t_i, t_i+W]` 闭窗内，至多 10 个，
因此每步状态是 ≤10 位的位掩码（≤1024 态）。事件转移枚举空闲位的子掩码
（三态 3^(k-1)，k≤10），由前向/后向 DP 得到：

- 最大可信度和、最少事件数（词典型比较）；
- 任意精度最优方案计数（Python 大整数）；
- 词典序最小的规范解（按状态记忆化的后缀裁决）；
- 每个候选事件在最优解中的出现次数 = 前向方案数 × 后向方案数，
  进而得到每对命中的必然 / 可选 / 从不归属。

## 运行

```bash
# 宿主机端口可配置（默认 8080）
HOST_PORT=9000 docker compose up --build app

# verify 单次服务：测试 + 构建检查 + 算法核对 + HTTP 冒烟，按结果退出
docker compose --profile verify run --rm verify
```

本地开发：

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests -q
.venv/bin/gunicorn -b 127.0.0.1:8080 app.web:app
BASE_URL=http://127.0.0.1:8080 .venv/bin/python verify.py
```
