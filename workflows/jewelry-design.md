# 工作流：首饰与配饰设计

入口：用户给出图片/颜色/文字，要求设计手串、手链、项链、耳饰、戒指、包挂、手机挂链。

## 第 1 步：定参数（不要审问式提问）

| 参数 | 有就说 | 没有就用默认 |
|---|---|---|
| 品类 | "手串" | 手串（串珠手链） |
| 款数 | "三款" | 1 款；≥2 款时必须结构不同 |
| 风格 | "清冷/复古/极简" | 依参考图的实测风格 |
| 材质偏好 | "不要塑料感/要天然石" | 天然石 + 925 银 |
| 预算 | "200 以内" | 200–400 元（银+天然石） |
| 尺寸 | "手围 16cm" | 手围 16cm；项链 45cm；耳饰 25mm |

默认值必须在回复里以"采用的假设"明示，例如：*假设：手围 16cm、925 银配件、预算约 300 元*。

## 第 2 步：三种设计模式

- **Mode 1 灵感转化**（有参考图）：提取视觉语言 → 原创结构。不复制原物。
- **Mode 2 元素保留**（用户指定元素）：只把用户点名的元素（如"那颗贝壳""那个浪花形状"）转成产品元素，其余自由。
- **Mode 3 自由创作**（只有文字）：直接按文字约束设计。

## 第 3 步：结构必须先定，再谈颜色

多款设计的差异**必须落在结构上**。可选原型（`structure.archetype`）：

| 原型 | 结构 | 适合 |
|---|---|---|
| `full_strand` | 整串等径珠 | 经典，最安全 |
| `half_bead_half_chain` | 半串珠 + 半段链，或三段式交替 | 有呼吸感、可调 |
| `knotted_cord` | 编绳（平安结/蛇结/平结）为主线 | 中式、轻量 |
| `asymmetric_pendant` | 偏侧吊坠，左右不等重 | 设计感强 |
| `irregular_centerpiece` | 一颗异形主石/巴洛克珍珠为视觉中心 | 高级感 |
| `double_layer` | 双层叠戴，长短差 3–5cm | 层次 |
| `metal_bead_mix` | 金属珠与石珠交替 | 中性、通勤 |
| `minimal_single_pendant` | 单颗吊坠 + 细链 | 极简 |
| `partial_strand_chain` | 局部珠组（3–5 颗）嵌在链中 | 轻珠宝 |

三款不同风格的最小差异组合（可直接用）：
1. `full_strand`（6mm 等径，主色占 70%）
2. `half_bead_half_chain`（珠段 + 1.2mm O 字链 + 延长链）
3. `irregular_centerpiece` 或 `asymmetric_pendant`（一颗异形主石 + 细链）

## 第 4 步：材质与工艺（硬约束）

读 `references/jewelry-design-rules.md` 取完整规则，这里是要点：

- 圆珠常规 6/7/8mm；≥12mm 只能作为**局部视觉重点**（1 颗），不能成串堆大珠。
- 珠串必须有闭合结构：弹力线（隐形，适合手串）/ 龙虾扣 / OT 扣 / S 钩 + 延长链（3–5cm）。
- 吊坠必须有**孔位与连接方式**（单孔穿线 / 双孔横穿 / 包边镶 + 挂环），不得悬空。
- 金属件与珠材之间要有隔珠或包金珠过渡，避免硬磨。
- 天然石、珍珠、贝母、木、银的质感差异要写出来，别都写成"光泽"。
- 不做无法实现的造型：无支撑的悬臂、看不见连接点的浮空件、比重明显失衡的耳饰。

## 第 5 步：写进设计档案

字段对应 `schemas/design-spec.schema.json`，最小必填：

```json
{
  "design_id": "jw_beach_A",
  "design_category": "beaded_bracelet",
  "design_name": "潮汐三段",
  "concept": "海浪的疏密节奏 → 珠段的疏—密—疏",
  "design_mode": "inspiration_transfer",
  "source_profile_id": "vp_beach_01",
  "structure": {"archetype": "half_bead_half_chain", "length_mm": 165,
                "sequence": ["龙虾扣", "延长链 5cm", "银隔珠", "6mm 海蓝宝 × 8", "1.2mm O 字链 4cm", "6mm 海蓝宝 × 4", "银隔珠"]},
  "materials": [
    {"name": "海蓝宝", "spec": "6mm 圆珠，半透", "quantity": "12 颗", "role": "main"},
    {"name": "925 银隔珠", "spec": "3mm 光面", "quantity": "4 颗", "role": "metal"},
    {"name": "925 银 O 字链", "spec": "1.2mm", "quantity": "4cm", "role": "cord"},
    {"name": "龙虾扣+延长链", "spec": "925 银", "quantity": "1 套", "role": "closure"}
  ],
  "colors": {"primary": "#2E8B93", "secondary": ["#F4EDE4"], "accent": "#D8C3A5"},
  "dimensions": {"length_mm": 165, "bead_diameter_mm": 6, "weight_g": 12},
  "components": ["龙虾扣 10mm", "延长链 5cm", "包金隔珠 3mm"],
  "manufacturing_notes": "珠孔 0.8mm，需 0.6mm 钢丝线穿 3 圈固定；链段焊接点做哑光处理",
  "feasibility": "handmade_simple",
  "style_scene": "通勤/度假，配白衬衫或亚麻",
  "image_prompt": "...",
  "negative_prompt": "...",
  "approval_status": "draft"
}
```

```bash
python3 scripts/muselab_state.py design-add --dir <proj> --file design.json
```

多款就多次 `design-add`，`design_id` 用 `_A/_B/_C` 后缀。

## 第 6 步：出图（Mode A）或给提示词（Mode B）

- Mode A：见 `workflows/image-generation.md`。出图后必须做一致性复查（珠数、排列、金属色、结构），有偏差写进 `fidelity_notes`。
- Mode B：给完整提示词 + 负面词 + 比例，并且**明说这张图没有生成**。

提示词按 `references/photography-standards.md` 的十段式写，不要写"唯美""高级感"这种无法执行词。

## 回复模板（每款）

```
### A｜潮汐三段（半珠半链）
灵感：参考图里浪花的疏密节奏
结构：龙虾扣 → 延长链 → 银隔珠 → 海蓝宝 6mm×8 → O字链 4cm → 海蓝宝 6mm×4 → 银隔珠
主色 #2E8B93 · 辅助 #F4EDE4 · 点缀 #D8C3A5
尺寸：手围 16cm+5cm 延长 · 珠径 6mm · 约 12g
可行性：手工可做（需 0.6mm 钢丝线 + 焊接点）
场景：通勤、度假
出图：已生成 <路径>｜或：未生成，提示词见下
```

## 常见失败

- 三款只换颜色 → 打回重做结构。
- 12mm 以上大珠成串 → 违反佩戴负重与审美，改单颗。
- 吊坠没孔位 → 补连接方式。
- 只写"天然水晶有助运势" → 违规，删掉（见 `copyright-and-safety.md`）。
