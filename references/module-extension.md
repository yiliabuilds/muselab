# 模块扩展规范

V1.3 已实现 `jewelry`（含系列、试戴）、`trend` 与 `makeup`。`outfit`、`ip` 只有接口约定，**没有实现代码**，不得假装可用。

新增模块照第三、四节做，不要复制一份吸色/版本管理/出图系统——那些是共享能力。

## 现有模块

| 模块 id | 状态 | 入口 |
|---|---|---|
| `jewelry` | ✅ 完整 | `workflows/jewelry-design.md` |
| `collection` | ✅ 完整 | `workflows/collection-design.md` |
| `try_on` | ✅ 完整 | `workflows/virtual-try-on.md` |
| `trend` | ✅ 完整 | `workflows/trend-inspiration.md` |
| `makeup` | ✅ 完整（V1.3） | `workflows/makeup-design.md` |
| `outfit` | ⛔ 未实现（仅接口） | — |
| `ip` | ⛔ 未实现（仅接口） | — |

## 新增模块必须提供的五样东西

1. **工作流**：`workflows/<module>.md`
   - 入口触发词
   - 输入参数与默认值
   - 执行步骤（可调用 `scripts/` 的通用能力）
   - 交付格式
   - 降级路径（无图像工具时交付什么）
2. **专业规范**：`references/<module>-rules.md`
   - 该领域的硬约束（对应首饰的"珠径/五金/负重"）
   - 常见不可行案例
   - 质量检查清单
3. **数据结构**：复用 `design-spec.schema.json`，通过以下字段扩展，不改主干
   - `design_category` 增加枚举值（`makeup` 已加；后续如 `outfit_set` / `ip_figure`）
   - 新增 `module_payload`（object）：放该模块专有字段，并为它写一个 `schemas/<module>-spec.schema.json`
   - 在 `scripts/muselab_state.py` 的 `PAYLOAD_SCHEMA` 里加一行 `{"<category>": "<module>-spec.schema.json"}`，`validate_design()` 会自动校验载荷
   - 若该类目不需要 `structure.archetype`（如妆容），在 `validate_design()` 的条件里排除；**不要**改 `structure` 对首饰的含义
   - 新增前先确认 `STATUSES` 与过渡表仍适用（妆容直接复用，未改）
4. **路由**：在 `SKILL.md` 路由表加一行，并更新 description（否则不会触发）
5. **测试**：在 `tests/run_tests.py` 增加该模块的可验证用例；无法自动验证的标 `NOT-RUN` 并写明原因

## 明确不允许

- 建空壳文件冒充实现（例如只有一个 `TODO` 的 `workflows/makeup.md`）。
- 在路由表里挂未实现模块并把用户引进去。
- 复用首饰模块的术语假装是专业建议（妆容说"珠径"、穿搭说"隔珠"）。

## 用户要求未实现模块时

按这个顺序回应：

1. 复用**通用能力**：可以做视觉分析、实测吸色、给出颜色与风格方向。
2. 明确说明：`X 专业模块在 V1.0 尚未实现，以下是基于通用视觉分析的通用建议，不是专业方案。`
3. 不输出看起来像专业成品文档的内容（不做完整妆容步骤表、不做完整衣橱搭配表）。
4. 若用户需要，给"如何自己继续"的三步建议。

## 已落地案例：妆容（V1.3）

照本规范做出来的样子，可作为模板：

| 第几样 | 妆容模块怎么做的 |
|---|---|
| 工作流 | `workflows/makeup-design.md`：四场景路由 → 吸色/八维 → 转译表 → 档案 → 步骤 → 出图 → 三步验收 → 局部修订 |
| 专业规范 | `references/makeup-design-rules.md`：九层结构、视觉主次、色彩三层、硬性不可行项、面部照伦理 |
| 数据结构 | `design_category="makeup"` + `module_payload`（契约 `makeup-spec.schema.json`）+ `PAYLOAD_SCHEMA` 一行映射 |
| 路由 | `SKILL.md` 路由表一行 + frontmatter description 加关键词（不改 description 就不会被触发） |
| 测试 | `tests/run_tests.py` 用例 11a–11n + 12a–12j：schema 拦截、登记、局部修订不动其它层、五类检查的检出与拒判、睑带位置与左右分布、改动的授权范围、档案自洽审计 |
| 自洽审计 | `scripts/makeup_spec_audit.py`：schema 管不到"同一档案里几处说法矛盾"，此类检查必须单独做 |

顺带修掉的共享层问题：`revise` 原来是**浅合并**（只合并第一层 dict），改妆容 `module_payload.layers.eyeshadow` 会连带影响同层兄弟字段。现已改为深合并（dict 递归、列表整体替换），首饰路径行为不变，全部既有用例通过。深合并之外还补了 `replaced_lists` 告警：列表被整体替换时会明确列出路径。

**教训（三条，全是实测踩出来的）**：

1. **改一个图层必须同步改该层对应的 `application_steps`。** 第一次局部修订只改了 `layers`，步骤仍是旧版描述，档案自相矛盾。已写进工作流第 9 步。
2. **列表替换会静默删数据，而且删的可能不是你在改的那一层。** 重发 `application_steps` 时漏带一条"细闪高光"，这条与眼妆毫无关系的步骤就消失了，直到逐版对比才发现。所以提交列表必须带全量，并看 `replaced_lists` 确认。
3. **`generation.prompt` 是设计的一部分，不是附属品。** 眼妆已改成日常单色，提示词里却还写着"深青 #14747C 压眼尾 1/3"。schema 全绿、验收也能过，但用户一旦授权出图，拿到的是旧妆。这一条促成了 `makeup_spec_audit.py`。
4. **"同一事实"藏在的不只是层/步骤/提示词，还有顶层 `concept` 与 `image_prompt`。** v11 把九层、步骤、提示词全改成了日常单色眼妆，`concept` 还留着"压到眼尾 1/3 与下眼睑后段"，`image_prompt` 更是从 v8 起就一直没同步（它和 `generation.prompt` 各存一份），出图后下睑真的被画了色。这两处因为写得像"整体感觉 / 外壳字段"而最容易漏改，现在都被纳入审计（`concept_*` / `image_prompt_*` 四条）。
5. **一个统计量不够，就再多两个，而且要求它们同时成立。** 最初的 `left_right_balance` 只比左右 ROI 的中值色，真实出图上判了"平衡"（ΔE 2.28），而两侧眼影实际一块铺了 30%、一块铺了 90%——中值看不见垂直分布。改造后是三个独立信号（覆盖率 / 爬升高度 / 色素 ΔE），**≥2 项同时超阈**才判不对称：一个放歪的框能伪造一个信号，伪造不了三个。这条对下一模块的推论是：**别用一个数去回答一个形状问题。**

**给下一个模块的推论**：凡是"同一事实在多处重复表达"的结构（层/步骤/提示词/色板/概念句），都需要一个跨字段的一致性检查，否则改一处漏三处是必然，不是意外。而且这类检查要老实交代自己的边界——`makeup_spec_audit.py` 的报告里固定带 `automation_boundary`，把"词典 + 结构比对能做的"和"必须人或视觉模型看的"分开列。

## 扩展优先级建议

1. `outfit`：需要更多外部知识（版型、季节），建议先做颜色与材质方向，不做版型推荐。
2. `ip`：涉及形象一致性与版权，成本最高，最后做。
