# 工作流：妆容设计

入口：用户说"根据这张图设计一套妆容 / 这个配色适合什么妆 / 帮我设计三套妆容 / 只改眼妆 / 这个妆我能不能画出来"。

约束层在 `references/makeup-design-rules.md`（九个层次、视觉重点、色彩三层、可操作性、安全）。本文件只管**怎么走**。

复用而不重写：吸色用 `scripts/color_extract.py`，档案与版本用 `scripts/muselab_state.py`，出图路线用 `workflows/generation-modes.md`，出图后验收用 `scripts/makeup_check.py`。

## 第 0 步：先判场景（决定后面能不能说"实测色"）

| 用户给了什么 | 场景 | 色彩来源 | 出图路线 | 能不能说"实测" |
|---|---|---|---|---|
| 参考图（风景/花卉/油画）+ 要妆容 | A 照片转妆容 | `color_extract.py` 真跑 | 通常是 `creative_generation` | **能**，写 `pixel_kmeans_lab` |
| 本人面部照 +（可选）灵感图 | B 定制妆容 | 有灵感图则测灵感图；面部照只测"可见肤色/现有妆" | `reference_based_generation` | 能测，但**不能**据此断言肤质 |
| 一张图要三套 | C 多套设计 | 同一份实测板，三套不同转译 | 每套各自一条路线 | 能 |
| 只有文字 | D 纯文字 | 无 | `creative_generation` | **不能**，必须写 `extraction_method: none` |

一张面部照 + 一张灵感图时，**分开处理**：灵感图给配色与质感，面部照给"必须保留的特征"与可见肤色。不要把两者混成一个色板。

## 第 1 步：采集信息（一次问清，不要多轮来回）

必问（缺了就按默认值走并写明假设）：

1. 场合（通勤/约会/婚礼/拍摄/舞台）
2. 想要的强度（"像没画" / "看得出但日常" / "明显妆感"）
3. 有没有不能接受的颜色或质地（很多人抗拒珠光、亮片、正红）
4. 肤质与已知敏感（**只信用户自述，不从照片推断**）

默认值（用户不答就用，并在档案 `notes` 里写明）：中性偏混干、日常场合、中等强度、不做假睫毛。

面部照场景额外确认一句："这张照片是在什么光下拍的？"——光源决定了肤色判断的误差范围，写进 `inspiration_source.limitations`。

## 第 2 步：读图（有图才做，没图跳过）

```bash
python3 scripts/color_extract.py <参考图> --mode mood --max-colors 5 --swatch palette.png --json palette.json
python3 scripts/color_extract.py <参考图> --mode color --max-colors 7 --json palette_color.json
```

- `--mode mood` 拿整体气氛（均 L、彩度、冷暖、对比）；`--mode color` 拿可用的具体色。
- 八维分析按 `workflows/visual-analysis.md` 走，一条不许只写形容词。
- 面部照**不要**做 palette：皮肤的"主色"没有设计意义，只会把方案带偏。

## 第 3 步：转译（每个设计决定都要能指回某个可观察特征）

| 参考图维度 | 妆容落点 | 必须写出的因果 |
|---|---|---|
| 主色 | 整体色彩方向（暖/冷/中性）、底妆与修容的色温 | "主色 #E3E1DF 灰白占 37% → 底妆走中性偏冷，修容不用暖棕" |
| 辅助色 | 眼影过渡、腮红或唇 | "辅助色 #69B7BB 浅青 → 眼影过渡色，降饱和后用" |
| 点缀色（最高饱和/最小面积） | 眼线、眼尾 1/3、内眼角、局部金属 | "点缀色饱和最高但面积 <5% → 只放在眼尾 1/3，不做满眼窝" |
| 线条与轮廓 | 眼线走向、眼影边界硬度、眉形 | "浪花是短促不规则曲线、峰顶尖锐 → 眼尾线短而上挑、边界偏硬；不做长拉线" |
| 材质纹理 | 哑光/珠光/金属/湿润 | "泡沫半透乳白 → 细珠光压眼窝中央（不是满眼闪）" |
| 明度关系 | 面部明暗层次、修容深度 | "全图高明度、对比低 → 修容减淡一级，高光用小面积" |
| 氛围 | 风格命名与强度 | "均 L 68、彩度低、冷占比高 → 冷调清透，中等强度" |
| 构图重心 | 视觉重点落在哪个区 | "重心偏下 1/3 → 视觉重点放唇；眼妆降级为陪衬"（见下方反例） |

**反例（不许出现）**："蓝色来自海洋，所以眼影用蓝色。"——没有位置、没有饱和、没有面积、没有主次。
**正例**："参考图最高饱和的是浪花脊线的浅青 #69B7BB，面积仅 6%，集中在画面右侧并向上抬升 → 眼尾 1/3 用同色相、降饱和 15% 的青绿，走向随脊线上扬；眼窝其余部分用中性灰棕压住，避免青绿铺满导致显肿。"

构图重心的判断要和首饰区分开：首饰看"视觉重量落在哪个部件"，妆容看"视线最终停在哪里"。人像图的重心常在眼睛，风景图的重心未必能直接搬到脸上——**搬运不成立时明确说"这条不迁移"**。

## 第 4 步：组装档案

外层用 `design-spec.schema.json`，妆容字段全进 `module_payload`（契约：`schemas/makeup-spec.schema.json`）。

```json
{
  "design_id": "mk_beach_tidal",
  "design_category": "makeup",
  "design_name": "潮汐眼",
  "concept": "把浪花脊线的浅青压在眼尾 1/3，其余压到中性",
  "image_prompt": "← 派生字段：由 module_payload.generation.prompt 自动写入，不要手写",
  "negative_prompt": "...",
  "approval_status": "draft",
  "module_payload": {
    "makeup_name_zh": "潮汐眼", "makeup_name_en": "Tidal Eye",
    "makeup_style": "cool_ethereal", "visual_focus": "eyes", "focus_strength": "moderate",
    "restraint_rule": "腮红降到裸杏色 sheen，唇用低饱和豆沙缎面；高光只在眼头一点点",
    "inspiration_source": {"kind": "image", "path": "source_beach.jpg",
                           "extraction_method": "pixel_kmeans_lab",
                           "limitations": ["手机拍摄，海面高光可能被压缩"]},
    "palette": {
      "measured": [{"hex": "#69B7BB", "role": "secondary", "area_ratio": 0.181}],
      "designed": [{"area": "eyeshadow", "hex": "#2E8B97",
                    "reason": "实测浅青降饱和约 15%、明度降 8，落在眼皮上不发灰"}],
      "translation_notes": "主色灰白只用于底妆质感，不进彩色区"
    },
    "layers": {"base": {...}, "brows": {...}, "eyeshadow": {...}, "eyeliner": {...},
               "lashes": {...}, "blush": {...}, "lips": {...}, "finishing": {...}},
    "application_steps": [{"step": 1, "area": "eyeshadow", "product_type": "哑光眼影",
                           "color": "#2E8B97", "placement": "眼尾 1/3 至下眼睑后段",
                           "technique": "压后向外晕，边界用干净刷扫开",
                           "effect": "拉长眼型，青绿不显肿"}],
    "suitable_occasions": ["通勤", "春夏日常"],
    "generation": {"route": "creative_generation", "aspect_ratio": "1:1",
                   "prompt": "← 唯一权威提示词，写在这里"},
    "safety_notes": ["眼周只用眼用化妆品", "上妆前做斑贴测试"]
  }
}
```

**提示词只写一处**：`module_payload.generation.prompt`。顶层 `image_prompt` 由 `design-add` / `revise` 自动从它派生，手写的值是无效的（保留该字段只是为了兼容 `design-spec.schema.json` 与旧调用点）。

登记：

```bash
python3 scripts/muselab_state.py init --dir muselab-projects/<slug> --title "妆容：<主题>"
python3 scripts/muselab_state.py design-add --dir ... --file mk_v1.json
```

只做局部（"只画眼妆"）时，`layers` 只放眼妆相关层，**不要**补齐九层来凑完整。

## 第 5 步：写步骤（这一步决定方案能不能用）

顺序固定：妆前 → 粉底 → 遮瑕 → 局部定妆 → 眉 → 眼影打底 → 眼影 → 眼线 → 睫毛 → 腮红 → 修容高光 → 唇 → 定妆。

每步五要素缺一不可：**产品类型 → 颜色 → 位置 → 手法 → 预期效果**。

写法对比：

| 不合格 | 合格 |
|---|---|
| "用青绿色眼影画眼尾" | "哑光青绿 #2E8B97，小号扁刷压在**眼尾 1/3** 与下眼睑后段，先压后向外晕，边界用干净晕染刷扫 1 次；**不越过眼窝中线**" |
| "刷睫毛" | "睫毛夹分三段夹翘，纤长型睫毛膏 Z 字刷根部，下睫毛只刷中段，避免结块" |
| "打腮红" | "裸杏色膏状腮红，笑肌最高点向外上方拍开，范围不超过瞳孔外缘垂直线，浓度淡到一眼看不出边界" |

几何锁定句对妆容同样有效："只画在眼尾 1/3，不得蔓延到眼窝""唇线最多外扩 1 mm，不得改变唇形轮廓"。

## 第 6 步：多套设计（C 场景）

**硬规则：三套之间的差异至少 3 项来自结构维度，且不能包含"颜色不同"。**

可选结构维度：`eye_structure`（哑光层叠 / 单色平涂 / 图形切割）、`eye_placement`（眼窝 vs 眼尾 vs 下眼睑 vs 全包）、`texture`（全哑光 / 珠光 / 金属 / 湿感）、`lip_finish`（哑光 / 缎面 / 水光 / 染唇）、`lip_shape`（原唇 / 微外扩 / 咬唇）、`focus_area`、`liner_type`、`base_finish`、`brow_shape`。

三套的典型分工（不是模板，是差异示范）：

| | 日常轻透 | 创意杂志 | 晚宴精致 |
|---|---|---|---|
| 眼结构 | 单色平涂 + 内眼线 | 图形切割 / 下眼睑全包 | 三段式晕染 + 上扬眼线 |
| 质地 | 全哑光 | 金属或湿感局部 | 缎面 + 细闪 |
| 唇 | 染唇，边缘模糊 | 哑光高饱和，边缘锐利 | 缎面，唇线清晰 |
| 重点 | 皮肤质感 | 眼 | 唇 |
| 底妆 | 极薄，保留纹理 | 干净哑光（为图形让位） | 缎光，轻微修容 |

写进 `differentiation.differs_in`，测试用例会检查差异项数量与是否只换了颜色。

## 第 7 步：出图（三条路线的妆容适配）

| 情况 | 路线 | `generation.route` |
|---|---|---|
| 无面部照，要"这套妆大概什么感觉" | 文生图虚拟人物 | `creative_generation` |
| 有本人面部照，要"我上这个妆什么样" | 参考图编辑 | `reference_based_generation` |
| 想像素级换妆 | **没有这条路** | `not_generated` + 说明 |

**Product Compositing 不适用于妆容。** 现有合成能力针对"可抠出的商品 + 纯色背景"，没有经过验证的面部局部像素编辑，不得宣称可用、不得用抠图冒充上妆。

### 妆容提示词六段式

1. **主体与构图**：一位 20–30 岁亚洲女性正面/微侧 3/4，面部占画面 60%，肩部以上，干净背景
2. **五官与皮肤**：真实皮肤纹理、可见毛孔与细纹，**不得磨皮**，保留自然肤色不均与淡淡黑眼圈边缘
3. **妆容分区**（逐区写"位置 + 色 + 质感 + 边界"）：底妆/眉/眼影/眼线/睫毛/腮红/修容高光/唇
4. **光与镜头**：85 mm，f/2.8，柔光箱 45° 主光 + 反光板，眼窝有柔和过渡阴影，无闪光灯直打
5. **风格与用途**：clean beauty 美妆大片 / 日常自拍 / 杂志内页（决定整体质感，不要写"高级感"）
6. **负面词**：见下

**范围要用相对量，不要用绝对距离。** "眉骨下方 5 mm""眼影宽度 8 mm"这类写法在生成模型那里等于没写：脸型、拍摄比例、镜头压缩都会让它落到完全不同的位置。改成相对可见解剖的比例——

- ✅ "从上睫毛根算起，覆盖不超过**睁眼时可见上睑的一半**；靠眉毛的那一半必须是干净皮肤"
- ✅ "只压在外眼角**外 1/3**，不得向眼窝蔓延"
- ❌ "眉骨下方 5 mm""眼影铺 1 cm"

**颜色要用"是什么 + 不是什么"两句话锁死。** 低饱和色最容易被模型自由发挥成同家族里更艳的那一支：只说"灰青"可能得到冰蓝，只说"低饱和"可能得到发白的银灰。真实出图踩过这个坑，所以要写成"灰调为主、青色为辅、带轻微绿色倾向——**不是冰蓝、不是钢蓝、不是银灰蓝**"，并把这三个词同时放进负面词。

**左右一致要单独写一句。** 只写"双眼对称"往往不够，模型会把两侧的晕染范围算成两个独立决定。要写清"两侧颜色、覆盖范围和视觉浓度相近，允许自然光照差异"，并在负面词里加"左右眼妆不一致、一侧眼影更浓"。

参考图路线额外加一段**保真指令**（写在提示词开头，独立成段）：

> 以提供的人物照片为准：保留同一张脸——脸型、五官比例、眼距、鼻形、唇形、肤色基调与发型不变；只改变妆容。不得瘦脸、不得放大眼睛、不得改变年龄感、不得换人。

### 负面词库

```
额外眼睛，多余睫毛，畸形眉毛，双眼不对称，眼线出界，唇膏溢出唇线，
过度磨皮，塑料皮肤，蜡像感，磨皮滤镜，美颜相机，改变脸型，改变眼型，
换人，年龄变化，夸张高光，四区同时高饱和，水印，文字，logo，
不自然的腮红位置，修容脏，睫毛结块
```

按本套追加：全哑光方案加"油光、湿感高光"；图形眼线加"眼线糊开、边界模糊"。

### 出图前必须做的事

- **先跑档案自洽审计**（改过任何一版之后都要重跑）：

  ```bash
  python3 scripts/makeup_spec_audit.py --project <项目目录> --design <设计ID> --strict
  ```

  它查的是 schema 查不到的矛盾：`generation.prompt` 里还写着上一版的色值、**顶层 `concept` 还留着旧版的妆效描述**（v11 真实出图前就漏了这一处）、色板只申报了一部分实际在用的颜色、某一层写了做法却没有步骤、声称"凭文字设计"却带着实测色板。**有 error 就不要出图**——带着矛盾的提示词去生成，钱花了图也是错的。

- **改过某一版之后，加 `--since` 证明你只动了被授权的东西**：

  ```bash
  python3 scripts/makeup_spec_audit.py --project <项目目录> --design <设计ID> \
      --since <上一版号> --authorized concept,image_prompt,prompt,negative_prompt --strict
  ```

  输出里的 `version_diff.unauthorized_changes` 必须为空。它把改动归成授权单元（`concept` / `prompt` / `negative_prompt` / `notes` / `layer.<名>` / `steps` / `palette` / `meta`），是"我要改眼睛，结果顺手动了唇"的机器防线。注意它只能证明"改动落在范围内"，证明不了"改得对不对"。

- 报价：每张约 5–10 点额度，先问"是否继续"；一次授权只覆盖一次调用。
- 登记 `task-add`，`image_type` 用 `makeup_look`（概念）或 `makeup_try_on`（参考图）。
- 生成后 `task-done --path <真实文件>`；失败 `task-fail`，`output_path` 恒 `null`。

## 第 8 步：验收（三层，不许合并成一句"已通过"）

### 8.1 代码可测（`scripts/makeup_check.py`）

```bash
python3 scripts/makeup_check.py --config makeup_gate.json --json gate.json --overlay qa.png
```

ROI 必须由视觉模型或人给（脚本不找人脸，先跑 `--capabilities` 确认环境有没有关键点库；没有就把坐标来源写进 `roi_provenance`）。最小配置：`skin_baseline`（额头/颊侧裸肌）、两眼 ROI + 设计色、唇 ROI + 设计色（可选 `surround`）、皮肤质感 ROI（可选 `reference_image`）。

**要判"眼影画到哪、两边一不一样"，必须再给睑带**：`eyes.<side>.lid`（从眉侧横跨到睫毛之外的矩形）+ `lash_edge`（睫毛在带的哪一边），以及 `eyes.<side>.under`（下睫毛下方的一条带）。只有 `roi` 时它只知道颜色对不对，不知道铺开了多少。

| 输出状态 | 含义 |
|---|---|
| `consistent` / `balanced` / `within_band` / `retained` / `clean_edge` | 该 ROI 内没有可测漂移 |
| `divergent`（双眼） | 色相换了家族 → `fail` |
| `asymmetric` | 左右眼不一致 → `fail`（先排除 ROI 放偏） |
| `eyelid_balance=asymmetric` | 三个信号里 ≥2 个同时超阈（覆盖率 / 爬升高度 / 色素 ΔE）→ `fail`；只超 1 个进 `minor_difference` 复核 |
| `full_lid` / `reaches_upper_lid` | 眼影爬到了可见上睑的一半以上，或靠眉半区有妆 → `fail` |
| `pigment_below_lid`（且与眼影同色系） | 下睑多了眼影色 → `fail`；异色系记 `offskin_pigment_other_hue`（多半是腮红/肤色） |
| `over_smoothed` | 纹理能量低于参考 45% → `fail` |
| `divergent_soft` / `possible_bleed` / `specular_below_lid` / `no_pigment` | 软提示或记录项，进 `review`，人工看 |
| `inconclusive_*` | 拒判：没有肤色基准、ROI 里没有妆、设计色本身是中性 —— **不是通过** |

`verdict=pass_code_checks` 只说明"你圈的那几块没测出漂移"。

**别只看状态词**：`eyelid_<side>.profile_lash_to_brow` 是从睫毛到眉的逐行有妆占比，`pigment_colour` 与 `contrast_vs_skin_delta_e` 是实测反差。状态是阈值化的结论，曲线才是证据——「差一点就判失败」和「稳稳在范围内」在状态词上可能一样。

### 8.2 视觉模型（必须做，脚本替不了）

眼线落点是否贴合睑缘、眉毛/睫毛有没有畸形、腮红修容位置是否合理、有没有多余的眼睛或睫毛、唇线是否真的贴合唇形、有没有改脸。逐项写结论与依据，不许写"整体自然"。

### 8.3 人工（必须做）

身份是否还是本人（**脚本永远不能判**）、这套妆能不能用真实产品化出来、是否可对外发布（AI 标注）。

三项合起来的判定词只有三个：`通过（可展示）` / `通过但需修（列出项）` / `不通过（列原因）`。任何一项没做，就只能写"未完成验收"。

## 第 9 步：局部修改与版本（这是最容易出错的地方）

用户说"保留底妆和唇妆，只把眼妆改日常一点"：

1. 读当前版本：`design-show --dir ... --id ...`
2. 写**只含眼妆**的补丁：

```json
{"change_request": "保留底妆和唇妆，只把眼妆改成更日常的版本",
 "module_payload": {"layers": {"eyeshadow": {"main": "浅棕单色平涂，眼尾不加青",
                                             "finish": "matte", "boundary": "soft"}}}}
```

3. `revise --dir ... --id ... --file patch.json`（`approved` 状态需先征得用户同意并加 `--consent`）
4. 回读新旧两版做**逐字段比对**，把"没动的层"列出来给用户看

`revise` 是深合并：只覆盖补丁里出现的键，`layers.base` / `layers.lips` 原样继承。**不要**为了"顺手统一风格"去改用户没点名的层——这正是测试用例 11c 守的行为。

改了哪一层，就在 `palette.designed` 里同步那一层的色值；其余层保持原值。

**必做：连步骤一起改，而且别把别人的步骤弄丢。** `application_steps` 是独立列表，不会因为 `layers` 变了而自动更新；更危险的是**列表是整体替换**——补丁里没重发的步骤会直接消失。两次真实的翻车：

- 只改了 `layers.eyeshadow`，步骤里仍写着"深青压眼尾" → 档案自相矛盾；
- 重发 `application_steps` 时漏带了"细闪高光"那一条 → 这条与眼妆无关的步骤被静默删除，等到对比版本才发现。

所以补丁要**带上完整列表**，且改完先看 `revise` 返回的 `replaced_lists`，确认每个被整体替换的列表都是你有意替换的。

**必做：`generation.prompt` 也是"眼妆的一部分"。** 提示词里写死了色号和画法，改了设计不改提示词，用户授权出图后拿到的仍是旧妆。实测踩过：v1 提示词写着"深青 #14747C 压眼尾 1/3"，而设计早已改成单色日常版，两处对不上。

**必做：顶层 `concept` 是最容易被漏改的一处。** 它不在九层里，写起来像"整体感觉"，于是 `layers` / `application_steps` / `prompt` 全改成日常单色了，`concept` 还留着"把这一条线压到眼尾 1/3 与下眼睑后段"。v11 就是这么带着矛盾的档案出的图——结果是下眼睑被画了色。现在 `makeup_spec_audit.py` 会把 `concept` 纳入检查（`concept_undeclared_colour` / `concept_contradicts_layer`），但它是**词典匹配**：能抓"说反了"，抓不到"换了套说法"。改完自己读一遍 `concept`。

**必做：顶层 `image_prompt` 不用你写，它由程序派生。** 设计外壳上有一个必填的 `image_prompt`，与 `module_payload.generation.prompt` 各存一份；v8 改日常眼妆时只同步了后者，`image_prompt` 一直留着 v1 的"浅青晕至眼窝 2/3、深青压眼尾 1/3 与下眼睑后段"，直到 v13 才被发现。自 1.3.1 起：`generation.prompt` 是**唯一权威来源**，`design-add` / `revise` 会用它覆盖 `image_prompt`，手写的值不生效。历史档案的漂移用 `prompt-sync` 补，它只会生成新版本：

```bash
python3 scripts/muselab_state.py prompt-sync --dir <项目目录> --id <设计ID> --check   # 只报告
python3 scripts/muselab_state.py prompt-sync --dir <项目目录> --id <设计ID>           # 生成同步版本
```

审计若报 `image_prompt_not_synced`，说明有人绕过状态机手改了 `project.json`——不要手动修，跑 `prompt-sync` 让它留下版本记录。

改完自查四句：**改的层对了吗？对应的步骤跟上了吗？`generation.prompt` 改了吗？`concept` 还是同一个妆吗？没点名的层和步骤有没有被动过？**

**收尾必跑两条**，把"以为改全了"变成可验证的事实：

```bash
# 1. 档案内部是否自洽（含提示词单一来源）
python3 scripts/makeup_spec_audit.py --project <项目目录> --design <设计ID> --strict

# 2. 改动是否都落在授权范围内（unauthorized_changes 必须为空）
python3 scripts/makeup_spec_audit.py --project <项目目录> --design <设计ID> \
    --since <上一版号> --authorized concept,prompt --strict
```

第二条的授权单元写法：`concept` / `prompt` / `negative_prompt` / `notes` / `layer.eyeshadow`（可简写 `eyeshadow`）/ `steps` / `palette` / `meta`。`image_prompt` 是 `prompt` 的镜像，**来源也变了**时自动算作已授权（出现在 `version_diff.derived_units` 里）；只改镜像而不改来源仍会被点名。用户说"只改眼妆"，就只授权眼妆那一单元；顺手改的唇会被点名。

## 第 10 步：交付格式

每套妆容按这个顺序输出（缺项要说明原因）：

1. **灵感分析**：参考来源、主要颜色（标"实测"或"近似"）、视觉元素、风格与情绪、核心转化逻辑
2. **妆容方案**：中英文名、风格、视觉重点（+ 其余区域如何克制）、底妆、眉、眼、腮红、唇、高光修容、适用场合
3. **实际操作**：分步表格（产品类型/颜色/位置/手法/效果）
4. **图像生成**：中文完整提示词、负面词、比例、参考图使用说明、AI 标注文案
5. **一致性检查**：出图后填。写清"与档案一致的部分 / 偏差 / 无法验证"

多套时先给对照表（结构差异一眼可见），再逐套展开。

## 降级与反例

**没有图像生成工具**：交付完整方案 + 提示词 + 验收清单，明确写"未生成图"。不得用"示意图""参考图"话术暗示已出图。

**没有真实吸色**（纯文字）：`extraction_method: none`，色值标"设计指定"，不得写"提取自"。

**反例清单**（出现即在自查里拦下）：

- 把参考图 HEX 直接写进眼影（"眼影 #69B7BB"）而不做降饱和与明度调整
- 四区同时高饱和
- 三套妆容只换了眼影颜色
- 用"高级感""氛围感"代替具体位置与手法
- 声称"已用脚本验证身份未变"
- 把抠图合成说成"像素级上妆"
- 建议非化妆品材料上脸
