---
name: muselab
description: "Visual inspiration engine: analyse an image, extract real colours, design makeable jewellery/accessories or a full makeup look, plan a collection, simulate on-model wear, or turn this week's news into products and posts. Use when the user supplies a photo, colour theme or creative brief and wants 抠图/透明背景/纯白商品图, 吸色/配色/色板, 视觉元素分析, 首饰设计（手串/手链/项链/耳饰/戒指/包挂/手机挂链）, 妆容设计（底妆/眉/眼影/眼线/腮红/唇/修容，含上妆步骤与出图提示词）, 系列设计, 真人试戴效果图, or 新闻灵感→首饰+小红书文案 — e.g. '提取这张照片的颜色设计三款手串' / '根据这张图设计一套妆容' / '帮我抠图' / '把这个设计放到手腕上'. Handles missing image tools by delivering analysis plus copy-ready prompts, never a fake image. Do not use for plain photo editing unrelated to design (retouch, face swap), pure text writing with no visual input, or e-commerce operations."
description_en: "Visual inspiration engine: analyse images, measure real colours, design makeable jewellery and accessories or complete makeup looks with application steps and prompts, plan collections, simulate on-model wear, and convert recent news into product ideas with social copy. Falls back to analysis plus copy-ready prompts when no image tool exists. Not for unrelated retouching or plain copywriting."
version: 1.3.1
---

# MuseLab — 灵感实验室

Turn one image, colour or theme into a real design decision: what it looks like, how it is built, whether it can actually be made, and what the picture should be.

MuseLab is a **general visual engine with jewellery as its first specialist module**. Colour, analysis, cutout, prompt-building and state handling are shared by every module. Never bury a general capability inside the jewellery module.

## 三条铁律

1. **不伪实现。** 没有工具就说没有。生成式重绘不得称为"精确抠图"；视觉猜测的色值不得称为"取色结果"；没生成的图片不得说"已生成"。
2. **不虚构。** 新闻标题、来源、日期、链接、图片路径、测试结果——要么真的，要么明确标注缺失。
3. **定稿不可偷改。** `approved` 的设计只能被引用。任何改动都要用户明确要求，并产出新版本，旧版本永远保留。

## 第 0 步：能力探测（每次会话一次）

```bash
python3 scripts/check_env.py --imagegen <yes|no> --websearch <yes|no> --json .muselab_caps.json
```

`--imagegen` 指当前是否真的能调用图像生成/编辑工具，`--websearch` 指是否能联网搜索。拿不准就填 `unknown`，并先做一次最小调用验证（例如真的生成 1 张图）再决定。

据此得到每个能力的 **Mode A（真实执行）** 或 **Mode B（分析＋提示词）**：

| 能力 | Mode A 方式 | Mode B 交付 |
|---|---|---|
| 抠图 | `scripts/cutout.py`（macOS Vision，真实 alpha） | 说明无法真实抠图；不得用文生图冒充 |
| 蒙版质检 | `scripts/mask_audit.py`（背景残留／异常透明／边缘污染） | 给出人工核对清单，不得声称"蒙版干净" |
| 吸色 | `scripts/color_extract.py`（CIELAB k-means 实测） | 视觉估计，色值必须标注"近似" |
| 背景合成 | `scripts/compose.py`（alpha 合成；`--keep-frame` 保原画布与像素坐标） | 给出修图/合成指令 |
| 商品像素完整性 | `scripts/pixel_integrity.py`（抠图是否重绘／合成是否改色／是否被裁切） | 说明无法逐像素校验，只能人工比对 |
| 图像生成 | 图像生成工具 + 参考图 + 提示词 | 完整提示词、负面词、比例、复查清单 |
| 出图验收（可测项） | `scripts/fidelity_check.py`（背景纯度／去光照色差／ROI 锐度与前景） | 给出人工核对清单，不得声称"已校验通过" |
| 妆容出图验收（可测项） | `scripts/makeup_check.py`（眼影色相／**眼睑覆盖率与爬升高度**／**左右按覆盖+爬升+颜色三项比对**／**下睑色素与亮带**／皮肤纹理／唇缘溢色） | 给出视觉+人工核对清单；ROI 必须人工/视觉给，环境无关键点库；身份保真永远只能人看 |
| 妆容档案自洽性 | `scripts/makeup_spec_audit.py`（色板↔层↔步骤↔`generation.prompt`↔顶层 `concept`/`image_prompt` 是否互相矛盾；`--since/--authorized` 查改动越界） | 人工逐项对照，不得说"档案没问题" |
| 新闻检索 | 联网搜索 | 请用户提供链接或正文 |
| 设计档案 | `scripts/muselab_state.py` | 直接把 JSON 贴在回复里 |

降级顺序：**A → 替代 A（换工具/换参数）→ B**。降级时必须说明"哪一步没做成、为什么、还能怎么继续"，并保留已完成的分析结果。

## 路由表

| 用户说 | 工作流 |
|---|---|
| 抠图 / 去背景 / 白底图 / 换背景 | `workflows/background-removal.md` |
| 提取颜色 / 配色 / 色板 / 这个色调 | `workflows/color-extraction.md` |
| 分析这张图 / 什么风格 / 设计元素 | `workflows/visual-analysis.md` |
| 设计手串 / 项链 / 耳饰 / 戒指 / 包挂 | `workflows/jewelry-design.md` |
| 做一整套 / 系列 / 同主题多件 | `workflows/collection-design.md` |
| 戴在手上 / 佩戴效果 / 上身图 | `workflows/virtual-try-on.md` |
| 这周新闻 / 热点变设计 / 小红书 | `workflows/trend-inspiration.md` |
| 出图 / 商品图 / 效果图（已有设计） | `workflows/generation-modes.md` 选路线 → 再进 `workflows/image-generation.md` |
| 换背景 / 白底图 / 只要换底色别动商品 | `workflows/generation-modes.md` 路线 A + `workflows/background-removal.md` |
| 妆容 / 化个妆 / 这套配色适合什么妆 / 三套妆容 | `workflows/makeup-design.md` |
| 穿搭 / IP 衍生品 | 见「未实现模块」 |

"根据这张照片设计手串" = 视觉分析 + 吸色 + 首饰设计，一次做完，不要拆成三轮对话问用户要参数。

"根据这张图片设计一套妆容" 同理：吸色 + 八维分析 + 妆容档案 + 上妆步骤 + 提示词，一次做完；出图前先问授权。

## 出图前先选路线（V1.2）

同一个设计，"画个想法"、"换掉背景"、"让模型重画一遍" 是三件事。**路线选错，产物就不能当商品图。**

| | A. Product Compositing | B. Creative Generation | C. Reference-Based Generation |
|---|---|---|---|
| 何时用 | 有实物照片，只需换背景/底色 | 无实物、未定稿、要氛围图 | 有实物但拍得差，要模型美化 |
| 靠什么 | `cutout.py` + `compose.py --keep-frame`，**不用生成模型** | 文生图（十段式提示词） | 参考图 + 图生图/编辑 |
| 商品像素 | 原样搬运，可逐像素校验 | 无 | 被重绘，无法逐像素校验 |
| 能否承诺"就是这件商品" | **可以**（蒙版合格前提下） | 不能 | **不能**，只能标"AI 模拟展示" |
| 验收 | `pixel_integrity.py` + `mask_audit.py` | 视觉评分 + 可制作性 | `fidelity_check.py` + 视觉复核 |

判据一句话：**要"就是这件商品" → A；要想法 → B；要好看但只能是模拟 → C。**

旧称 "Concept Mode" = B；旧称 "Product Fidelity Mode" 拆成 A 与 C——**参考图生成不是严格保真**，上一版那样命名是错的，实测已证明它会擅自补全画外内容、改珠数、换配件。

A 路线两步报告缺一不可：`pixel_integrity` 证明"商品没被重画"，`mask_audit` 证明"蒙版是否干净"。**像素零差异不代表图能用**，反过来也一样。C 路线出图后跑 `fidelity_check.py`，其 `pass` 只代表可测项通过，配件身份、珠数仍需视觉或人工，报告分三栏写明。

## 状态协议（跨轮次记忆）

对话不保存珠子数量，文件保存。

```bash
python3 scripts/muselab_state.py init --dir muselab-projects/<slug> --title "海洋系列"
python3 scripts/muselab_state.py design-add  --dir ... --file design.json
python3 scripts/muselab_state.py status      --dir ... --id jw_a --set selected
python3 scripts/muselab_state.py status      --dir ... --id jw_a --set approved
python3 scripts/muselab_state.py design-list --dir ...
python3 scripts/muselab_state.py design-show --dir ... --id jw_a [--version 1]
python3 scripts/muselab_state.py revise      --dir ... --id jw_a --file patch.json [--consent]
python3 scripts/muselab_state.py prompt-sync --dir ... --id mk_a [--check] [--consent]
python3 scripts/muselab_state.py task-add | task-done | task-fail ...
python3 scripts/muselab_state.py export      --dir ... --design-id jw_a
python3 scripts/muselab_state.py verify      --dir ...
```

- 状态：`draft → selected → approved`；`approved` 后只能 `revise --consent` 生成新版本。
- `revise` 是**补丁语义**：只改传入字段，其余继承。用户说"银色改金色"就只传五金相关字段。
- **提示词只有一个权威来源**：`module_payload.generation.prompt`。顶层 `image_prompt` 是派生字段，`design-add` / `revise` 会自动覆盖写入，**不要手写它**；历史档案漂移用 `prompt-sync` 补（`--check` 只报告，当前版本不同步时退 6）。
- 出图必须登记：`task-add` → 成功 `task-done --path <真实存在的文件>` → 失败 `task-fail --no-tool`。失败任务的 `output_path` 恒为 `null`。
- 续接旧设计：`design-show` 取回的 spec 是唯一事实来源；不要把上一轮回复的措辞当规范。

## 交付格式

**首饰**（顺序固定，缺项要说明原因）：
产品名称 · 灵感来源 · 设计理念 · 主/辅色（HEX）· 结构（含序列）· 材料清单（规格+数量）· 关键尺寸 · 连接与工艺 · 可行性 · 场景 · 图像提示词 · 负面词。

**妆容**：
灵感分析（参考来源/实测色/视觉元素/风格情绪/转化逻辑）· 妆容方案（中英文名/风格/视觉重点/底妆/眉/眼/腮红/唇/高光修容/场合）· 实际操作（分步：产品类型→颜色→位置→手法→效果）· 图像生成（提示词/负面词/比例/参考图说明）· 一致性检查。

多方案时先给一张对照表（差异一眼可见），再逐款展开。**多款的差异必须在结构上**：首饰看结构、五金；妆容看眼结构、质地、唇质地、视觉重心——都不能只靠换颜色。

## 专业规范按需读取

| 场景 | 读 |
|---|---|
| 配色、色值角色、色彩关系 | `references/color-theory.md` |
| 元素→设计语言、分析维度 | `references/visual-analysis.md` |
| 珠子尺寸、结构、五金、工艺可行性 | `references/jewelry-design-rules.md` |
| 摄影提示词、镜头、光线、比例 | `references/photography-standards.md` |
| 多图/试戴保持商品一致、颜色三层判定 | `references/product-consistency.md` |
| 妆容层次、显色逻辑、上妆可行性、面部照伦理 | `references/makeup-design-rules.md` |
| 版权、AI 标注、功效宣称、新闻合规 | `references/copyright-and-safety.md` |
| 新增穿搭/IP 模块（接口约定） | `references/module-extension.md` |

只在需要时读，不要一次全读。`SKILL.md` 不装细节，细节在 `workflows/` 与 `references/`。

## 模块状态（V1.3）

| 模块 | 状态 | 入口 |
|---|---|---|
| 首饰 / 系列 / 试戴 / 新闻 | ✅ 已实现 | 见路由表 |
| 妆容 | ✅ 已实现（V1.2 起） | `workflows/makeup-design.md` |
| 穿搭 / IP 衍生品 | ⛔ 只有接口约定 | 明确说"未实现"，只给通用方向，不产出成品级方案 |

妆容模块不提供 Product Compositing：现有合成能力是"可抠出的商品 + 纯色背景"，**没有验证过的面部局部像素编辑**。用户要"像素级换妆"时直说做不到，不要用抠图冒充。

## 输出前自检

- 色值是测的还是猜的？说清楚了吗？
- 图是真生成了，还是只有提示词？
- 走的是 A/B/C 哪条路线？说清楚了吗？有没有把 C 说成"严格保真"？
- 走 A 路线时，`pixel_integrity` 和 `mask_audit` 两份报告都出了吗？像素零差异被误当成"图能用"了吗？
- 走 C 路线时，跑过 `fidelity_check.py` 吗？色差是光照还是材质，分清了吗？画外有没有被补全？
- 已定稿的设计有没有被悄悄改动？（改眼妆时有没有顺手动了唇或底妆？）
- 珠数、尺寸、五金连接是否自洽（没有悬空配件）？
- 妆容：色值分清了"实测 / 设计指定 / 出图呈现"三层吗？四区里有没有同时高饱和？
- 妆容：步骤写全了"产品类型→颜色→位置→手法→效果"吗？有没有把非化妆品写上去？
- 妆容：`makeup_check.py` 跑了吗？只看了左右中位色就宣布平衡了吗（要看覆盖率与爬升高度）？`pass_code_checks` 被当成"妆对了"了吗？"还是同一个人"是人工判的吗？
- 改过任何一版妆之后：`makeup_spec_audit.py --strict` 跑了吗？`--since N --authorized ...` 用来证明没改到授权外的地方，用过吗？
- 妆容：`makeup_spec_audit.py` 跑了吗？`generation.prompt` 改了之后，顶层 `concept` 跟上了吗？（v12 起 `image_prompt` 已自动派生，若审计报 `image_prompt_not_synced`，说明有人手改了 JSON）
- 有没有医疗/财富功效、品牌授权、公益合作的暗示？
- 商业用途的 AI 图，有没有提示"需标注 AI 模拟效果"？
