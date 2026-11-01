# maic-whiteboard-lint

离线、确定性的 OpenMAIC 白板布局校验器 —— 在课前/CI 里判定白板元素**是否重叠、线段是否穿字、是否挤出画布**，并额外报告上游算法静默忽略的**旋转盲区**。

**为什么现在需要它**：OpenMAIC 的 AI 用 `left/top` 绝对坐标往白板上放 LaTeX/文字/图形，LLM 常把元素放得太近导致重叠（上游 issue #38 已挂七个月并被 `status:icebox`）。上游代码里确实有几何检测，但它把结果拼成一段**给模型看的提示词文本**，既不返回结构化结果、也没有 CLI、更没有退出码 —— 所以你无法在把课件发给学生之前，用一条命令判断"这块白板到底有没有坏"。

Upstream: https://github.com/THU-MAIC/OpenMAIC

## 上游是什么、它缺什么

OpenMAIC（清华 MAIC 团队）是一个多智能体互动课堂，AI 会在白板上生成元素。它缺三件事，全部有真实证据：

| # | 证据（可点开核验） | 说明 |
| --- | --- | --- |
| 1 | [issue #38](https://github.com/THU-MAIC/OpenMAIC/issues/38)（open，`status:icebox`，2026-03-16 起，0 评论） | 原文："elements (LaTeX formulas, text annotations, shapes) **frequently overlap** because the LLM-generated coordinates (`left`/`top`) are too close together." 诉求就是"碰撞检测 + 自动布局"，**从未实现**。 |
| 2 | [issue #74](https://github.com/THU-MAIC/OpenMAIC/issues/74)（closed as **duplicate** of #38） | 第二个独立用户报告"多个讨论的时候白板重叠了"；维护者关闭时指向 #38。 |
| 3 | [`lib/orchestration/summarizers/whiteboard-conflicts.ts`](https://github.com/THU-MAIC/OpenMAIC/blob/main/lib/orchestration/summarizers/whiteboard-conflicts.ts) | 上游有完整 AABB 检测（画布 1000×563，`OVERLAP_THRESHOLD=0.3`），但 `buildWhiteboardConflicts()` 返回的是 **string**（喂给 system prompt），无结构化输出、无 CLI、无退出码。 |
| 4 | [`state-context.ts#L259`](https://github.com/THU-MAIC/OpenMAIC/blob/main/lib/orchestration/summarizers/state-context.ts#L259) | 唯一调用点**只检查最后一块白板**（`stage.whiteboard[last]`），历史白板、导出包全都不查。 |

也就是说：**它能在 prompt 里"提醒"模型别重叠，但没人能在课前离线对任何一份白板 JSON 断言"这里到底有没有冲突"。**

## 这个工具做什么、不做什么

**做**：
- 精确复刻上游 `whiteboard-conflicts.ts` 的三类几何检测（相对重叠 ≥30% / 线段穿 bbox / 超出 1000×563 画布）。
- 默认**检查输入里的每一块白板**（上游只看最后一块），并给出逐块的结构化报告。
- 新增**旋转盲区检测**：上游 `toBBox()` 完全不读 `rotate`，旋转元素的真实 AABB 更大，因此会**漏报**它们之间的重叠。本工具计算真实旋转 AABB，把上游"放行但实际会撞"的元素对列为 `ROTATION_FALSE_NEGATIVE`。
- `--json` 机器可读输出 + 固定退出码，可直接进 CI / pre-commit。

**不做**：
- 不做自动布局 / 自动挪开（那是 issue #38 的第 2 步，本工具只负责**检测与阻断**）。
- 不复现上游的 VLM 视觉评分（`eval/whiteboard-layout/scorer.ts` 需要浏览器 + 模型 + 网络，不可离线）。
- 不替代上游运行时 —— 它只读本地 JSON，不联网、不起服务。

## 本轮创新

**首次把 OpenMAIC 的"提示词里的几何提醒"变成可断言的离线 lint，并首次量化上游算法的旋转盲区。** 与上游内置的 `buildWhiteboardConflicts()` 不同：那个函数只返回一段给模型看的文本、只检查最后一块白板、且完全忽略 `rotate`；本工具输出结构化 findings、默认扫描全部白板，并用真实旋转 AABB 找出上游会静默放行的碰撞对（示例见 `examples/in/rotation-blindspot.json`：上游算 25% 放行，旋转后真实是 32% —— 会撞）。这不是换个热榜项目重做的静态审计，而是对同一份上游算法做了**上游自己没做的失效归因**。

## 安装 / 用法

零依赖，只需 `python3`（3.8+）。

```bash
# 1) 基础检查：有没有冲突（有则退出码 1）
./bin/maic-whiteboard-lint examples/in/stage-two-boards.json

# 2) 机器可读 JSON（可直接喂给 CI）
./bin/maic-whiteboard-lint examples/in/stage-two-boards.json --json

# 3) 只检查最后一块白板（复刻上游行为）
./bin/maic-whiteboard-lint examples/in/stage-two-boards.json --only-last

# 4) 把旋转盲区也当失败（退出码 1）
./bin/maic-whiteboard-lint examples/in/rotation-blindspot.json --strict-rotation
```

### 输入格式

接受以下几种 JSON（自动识别）：

- **stage.json**：`data.stage.whiteboard[]`（每块白板含 `elements[]`）
- **manifest / 导出包**：`data.scenes[].whiteboard[]` 或 `data.scenes[].content.whiteboard[]`
- **裸元素数组**：`[ {..element..}, ... ]`，或 `{"elements": [...]}`

元素字段与上游 `PPTBaseElement` 一致：`{id, type, left, top, width, height, rotate, ...}`；线段为 `{type:"line", left, top, start:[x,y], end:[x,y], points:[...]}`。

### `--json` 输出示例

```json
{
  "ok": false,
  "canvas": {"width": 1000, "height": 563},
  "overlap_threshold": 0.3,
  "upstream_algorithm": "lib/orchestration/summarizers/whiteboard-conflicts.ts",
  "whiteboards_linted": 2,
  "conflicts_total": 4,
  "rotation_blindspots": [],
  "whiteboards": [
    {
      "tag": "whiteboard", "id": "wb-1", "element_count": 4, "conflict_count": 4,
      "conflicts": [
        {"type": "OVERLAP", "level": "error", "a": "t1", "b": "t2",
         "percent": 40, "message": "latex \"\\alpha\"[t1] and text \"a ray\"[t2] share 40% of the smaller one's area"}
      ]
    }
  ]
}
```

（完整样例见 `examples/out/stage-two-boards.json` 与 `examples/out/rotation-blindspot.json`。）

### 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 通过：所有被检查的白板都没有冲突（若加了 `--strict-rotation`，还要求无旋转盲区） |
| `1` | 检出问题：存在重叠 / 穿字 / 出界（或 `--strict-rotation` 下的旋转盲区） |
| `2` | 用法或输入错误：文件不存在、JSON 解析失败、找不到任何白板元素 |

## 局限

- **轴对齐下界**：旋转盲区用的是"旋转后的轴对齐包围盒"，比真实旋转矩形更保守（可能把"其实没撞"判成会撞），但**绝不会漏**——这正是为了覆盖上游忽略 `rotate` 的漏报。
- **只做几何，不懂语义**：元素是否有意义、文字是否可读，不在本工具范围内。
- **不读上游数据库**：`stage.whiteboard` 存在 SQLite 里时，本工具只能读你导出的 JSON，读不到未导出的运行时状态。
- **算法随上游漂移**：常量（1000×563、0.3）来自上游 `whiteboard-conflicts.ts` 当前版本（`main` HEAD `fe2cf7d`）；若上游改阈值，需要同步。

## 测试

```bash
python3 tests/test_lint.py
```

26 个用例，覆盖几何边界（含恰好 30%）、三类冲突、全白板遍历、`--only-last`、旋转盲区、退出码 0/1/2、`--json` 可解析。

## License

MIT — 见 [LICENSE](LICENSE)。