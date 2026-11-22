# 脚本 API 参考

所有脚本都是独立 CLI，可直接在 shell 里跑，也可被 Agent 调用。输出为 UTF-8 JSON（`indent=2`），便于程序解析。路径参数接受绝对路径或相对当前目录的路径。

## 退出码约定

| 码 | 含义 | 调用方应做什么 |
|---|---|---|
| 0 | 成功 | 读 stdout JSON |
| 2 | 输入/数据错误（文件不存在、缺字段、非法参数） | 修正参数，不要重试同样的调用 |
| 3 | 能力缺失（Pillow/numpy/Vision 不可用） | 降级 Mode B，并说明原因 |
| 4 | 未找到主体 | 询问用户保留哪个主体，或降级 |
| 5 | 不支持的组合（如 JPEG + 透明） | 换输出格式 |
| 6 | 状态规则冲突（冻结、非法跃迁、缺用户许可），或 `pixel_integrity.py --strict` 检出像素被改 | 按提示处理，不要绕过 |
| 7 | `fidelity_check.py --strict` / `makeup_check.py --strict` / `makeup_spec_audit.py --strict` 检出硬门禁失败 | 读 `machine_failures` / `errors`，改图、改提示词或改档案，不要忽略 |

## check_env.py

```
python3 scripts/check_env.py [--imagegen yes|no|unknown] [--websearch yes|no|unknown] [--json out.json]
```

输出：`capabilities`（python/pillow/numpy/jsonschema/vision_foreground_mask/ffmpeg）、`mode_matrix`（每项能力 A/B/unknown + 替代方案）、`overall`。

## cutout.py

```
python3 scripts/cutout.py <image> --out <rgba.png>
    [--min-alpha 0.02] [--feather 0.0] [--despill] [--instance N] [--json report.json]
```

- 真实分割（macOS Vision `VNGenerateForegroundInstanceMaskRequest`），**只加 alpha，不改 RGB**。
- `--despill` 用 alpha 反解边缘前景色，消除白边/背景污染。**注意**：它按"白色背景"反解，灰/米色背景下会把边缘推亮，且 `mask_audit` 的边缘污染指标用**原图**像素计算、不会因此变好——去污效果必须自己比 RGB 验证。
- `--instance N` 多主体时只保留第 N 个（0 起）。
- 该模型输出的 alpha **上限通常是 254**（不是 255），所以"完全不透明"只是近似说法。
- JSON 关键字段：`method`、`is_generative`、`instance_count`、`subject_bbox`、`subject_area_ratio`、`alpha_stats`、`color_preserved`。

## mask_audit.py

```
python3 scripts/mask_audit.py --image <原图> --cutout <rgba.png>
    [--json audit.json] [--overlay qa_mask.png]
```

- 三项检查：`background_residue`（背景被吞进蒙版）、`translucent_core`（主体该实心却半透明）、`edge_contamination`（边缘像素更像背景）。
- `verdict.overall`：`pass` / `review` / `needs_repair`。`review` 必须人工确认，不等于通过。
- 条目分 `auto`（可自动修）与 `human`（不许自动改，需人确认）。
- **已知边界**：`edge_contamination` 用原图像素与背景聚类比较，衡量的是"这块边缘有没有被背景污染的风险"，**不复核 `--despill` 之后的结果**；主体铺满画面时背景模型退化为全局估计。

## compose.py

```
python3 scripts/compose.py <rgba.png> --out <out.png|jpg|webp>
    [--bg white|transparent|#RRGGBB|<图片路径>] [--size 1600] [--aspect 1:1]
    [--margin 0.08] [--keep-frame] [--format png|jpg|webp] [--quality 92]
    [--shadow] [--shadow-strength 0.28] [--json report.json]
```

- 等比缩放居中；`--bg white` 输出**纯** `#FFFFFF`。
- **`--keep-frame`**：商品模式。保留抠图自身画布、1:1 像素坐标，不裁 bbox、不缩放、不居中；`--size/--aspect/--margin` 被忽略。原图的出画关系（例如手链被画面边缘切断）因此不会被"补全"或重排。**保真合成必须加这个开关。**
- `--shadow` 仅在图片背景时生效（合成阴影，非真实光照测量）。
- 透明 + JPEG 会被拒绝（退出码 5）。
- JSON 关键字段：`subject_placement`（x/y/w/h/scale）、`frame_preserved`、`has_alpha`、`white_is_pure`、`shadow`。

## color_extract.py

```
python3 scripts/color_extract.py <image> [--mode color|mood] [--max-colors 7] [--min-colors 3]
    [--mask mask.png] [--crop x,y,w,h] [--no-pixels]
    [--swatch palette.png] [--json report.json]
```

- 实测：CIELAB 空间 k-means（k-means++ 初始化、固定随机种子），聚类合并阈值 ΔE < 6。
- 色名由 L/C/h 启发式生成（`name_zh`），是描述性标签，不是权威色名。
- `--mask` 只在掩膜内取色（配合抠图结果取"商品本色"）。
- `--no-pixels` 输出空骨架，`extraction_method = visual_estimate`，用于强制标注"近似"。
- JSON 关键字段：`color_palette[]`（hex/rgb/lab/area_ratio/role/design_use）、`relationships.highest_contrast_pairs[]`（ΔE + WCAG）、`atmosphere`（明度/彩度/冷暖/对比/情绪词）。

## fidelity_check.py

```
python3 scripts/fidelity_check.py --config gate.json
    [--image shot.png] [--json result.json] [--overlay qa.png] [--strict]
python3 scripts/fidelity_check.py --list-checks
```

出图后的**可测项**门禁。只判能量化的东西，其余分栏交视觉模型/人工。

```json
{
  "image": "shot.png",
  "expected_background": "#F2EFE9",
  "background": {"max_delta_e": 6.0, "max_patch_std": 3.0, "max_patch_spread_de": 3.0},
  "materials": [
    {"name": "深青玉髓", "expected": "#128D97", "kind": "gemstone", "sample": [430, 505, 22]},
    {"name": "925 银",   "expected": "#C8CDD2", "kind": "metal",    "sample": [560, 470, 10]}
  ],
  "accessory_rois": [
    {"name": "龙虾扣", "roi": [150, 215, 230, 145], "required": true}
  ]
}
```

- `kind`：`flat_fill` | `gemstone` | `pearl` | `metal`，决定判色口径。
- `sample`：`[x, y, r]` 采样圆，需人工/视觉定位（脚本不会自己找珠子）。
- 相对路径先按 config 所在目录解析，再按 cwd。
- 色彩判定：以背景为光照参考估 `cast`，扣掉 a\*/b\* 与曝光后再判色相/饱和度。
- 关键字段：`verdict`（`pass_code_checks` / `pass_needs_review` / `fail`）、`machine_failures[]`、`correctable_in_post[]`、`needs_human_or_vision[]`、`background.status`、`materials[].status`、`accessories[].status`。
- `--strict`：有硬门禁失败时退出码 7（默认恒 0）。
- **`verdict=pass` 不等于商品图合格**——配件身份、珠数、排列仍需视觉/人工。

## pixel_integrity.py

```
python3 scripts/pixel_integrity.py --original <原图> --cutout <rgba.png> --composite <合成图>
    [--placement compose.json] [--scale S] [--offset x,y] [--bg '#D6D6D6']
    [--tolerance 0] [--json report.json] [--overlay qa_diff.png] [--strict]
```

商品合成路线（A）的门禁：**只回答一个问题——商品自己的像素有没有被改。**

分两步问，不能合并：

| 字段 | 问的是什么 | 失败意味着 |
|---|---|---|
| `cutout_vs_original` | 抠图有没有重绘商品 | 分割步骤改了像素 |
| `composite_vs_ideal_blend` | 合成图是否等于 `a*抠图RGB + (1-a)*背景RGB` | 合成步骤偷偷调色/打光，或被二次压缩 |
| `coverage` | 原图里像商品、但蒙版没收进来的区域 | 蒙版可能整块漏掉了商品（`status=review`） |

- 关键：蒙版常到不了 alpha=255（Vision 上限 254），"不透明"像素仍混入 1/255 背景，直接拿合成图比原图会得到 delta≤4 的**假失败**。脚本比较的是理想混合，不是原始像素。
- `coverage` 正是"像素相等"检验的盲区补丁：等式检查只能看抠图里**存在**的像素，蒙版悄悄丢掉一颗珠子，那些像素在检查之前就没了。它用背景聚类 + 亮度跨度 + 局部梯度区分"商品"与"投影"，但**只提示不判决**——`looks_like_product=true` 仍需人看一眼。理由：灰珍珠和灰纸上的投影在颜色上本来就分不开。
- `structure.clipped_pixels > 0` → 商品被画布切掉（`--keep-frame` 没加，或画布小于原图）。
- `silhouette_components` 与抠图差异明显 → 有整块结构丢失。
- `compositing_changes` 单独记录重采样、有损容器、阴影——与"像素被改"分开写。
- `--no-coverage` 关闭覆盖检查（大图可省 2–3 秒）。
- `--strict`：`verdict=fail` 时退出码 6。
- **`product_pixels_preserved.overall=true` 不代表图能用**：它只证明没被重画。蒙版有没有残影、半透明是否异常、边缘是否污染，全部看 `mask_audit.py`。反过来也一样：蒙版干净也不代表合成没被改动。

## makeup_check.py

```
python3 scripts/makeup_check.py --image shot.png --config gate.json
    [--json result.json] [--overlay qa.png] [--strict]
python3 scripts/makeup_check.py --list-checks
python3 scripts/makeup_check.py --capabilities
```

妆容出图后的**可测项**验收。它只做**你圈定矩形内**的色彩算术，不找人脸、不定位五官。

```json
{
  "image": "shot.png",
  "reference_image": "face.jpg",
  "skin_baseline": [400, 120, 24],
  "lash_edge": "bottom",
  "roi_provenance": "人工按刻度网格声明：左眼上睑带 x212-296 / y274-294（眉下缘→上睫根）",
  "eyes": {
    "left": {
      "roi": [190, 230, 120, 80],
      "lid": [212, 274, 84, 20],
      "under": [225, 340, 70, 16],
      "expected_hex": "#9FB8B6"
    },
    "right": {"roi": [490, 230, 120, 80], "expected_hex": "#9FB8B6"}
  },
  "lips": {"roi": [345, 420, 110, 50], "expected_hex": "#8A4B55", "surround": [345, 470, 110, 24]},
  "skin_texture": {"roi": [150, 330, 100, 70]}
}
```

`roi` 是"用来测颜色的眼影区"；`lid` 是**上睑带**，必须从眉毛一侧横跨到睫毛之外，`lash_edge` 声明睫毛在带的哪一边（`bottom` / `top`）；`under` 是下睫毛下方的一条带。

| 检查 | 怎么算 | 输出状态 |
|---|---|---|
| `eyeshadow_*` | ROI 内 a\*/b\* 距肤色基准 > 8 的像素（**L\* 不参与**，阴影与高光不是妆），取其中值色相与饱和度，比设计色 | `consistent` / `divergent` / `consistent_hue_desaturated` / `inconclusive_*` |
| `eyelid_<side>` | 上睑带内**逐行**统计"有妆"像素占比（阈值 `lid_delta_ab=5.0`，比测色更松，为了抓住晕染的羽化边）；给出 `coverage_share`、`climb_fraction`（自睫根向上爬升的高度 / 带高）、`share_near_brow_half`（靠眉半区的有妆占比）、`profile_lash_to_brow` 逐行曲线、`pigment_colour`、`contrast_vs_skin_delta_e`、`delta_hue_vs_design_deg` | `within_band` / `reaches_upper_lid`（爬升 > 0.62 或靠眉半区 > 0.18）/ `full_lid` / `no_pigment` |
| `eyelid_balance` | 左右两睑的**三个独立信号**：覆盖率差、爬升高度差、色素 ΔE | `balanced` / `minor_difference`（1 项超）/ `asymmetric`（**≥2 项同时超**） |
| `under_lid_<side>` | 下睑带内 a\*/b\* 距肤色 > 10（比测色更严：颧骨本来就比额头暖）的像素占比 `pigment_share`；以及 L\* > 带内中位 + 15 的镜面占比 `specular_share` | `clean` / `specular_below_lid`（只记录）/ `pigment_below_lid` / `pigment_and_specular` / `offskin_pigment_other_hue`（有色但不是眼影同色系，多半是腮红/肤色） |
| `left_right_balance` | 左右眼 ROI 中值色的 ΔE 与 Δhue（**只测颜色，测不出上下分布**，不要单独依赖它） | `balanced` / `asymmetric` |
| `lips` | 唇 ROI 中值色比设计唇色 | `consistent` / `divergent_soft`（**软判，永不硬失败**：唇色被唇部本色与光泽合法改变） |
| `lip_boundary` | 唇缘外侧色沿"肤色→唇色"轴投影的比例（`mix_fraction`），并检查是否落在轴上（`perp_distance`） | `clean_edge` / `possible_bleed`（软） |
| `skin_texture` | 皮肤 ROI 的 Laplacian 能量 / 参考照片同一 ROI | `retained` / `over_smoothed` / `texture_added` |

- 硬失败：**双眼**同时色相背离、左右眼不平衡（旧的颜色项）、`eyelid_balance=asymmetric`、`full_lid`、`reaches_upper_lid`、下睑 `hard_fail`（色素占比 ≥ 0.15 且与眼影同色系）、皮肤过度磨皮。单眼背离只进 `review`（多半是 ROI 放偏）。
- **为什么加睑带**：`left_right_balance` 只比中值色，一个矩形里眼影铺满上睑和只压一条线，中值可以一样接近。真实出图里就是这样漏判的——`left_right_balance` 报 `balanced (ΔE 2.28 / Δhue 4.9°)`，而左睑覆盖率 0.30、右睑 0.67 且爬升到 0.93。中值看不见垂直分布。
- **为什么要 ≥2 个信号**：单个信号可以被一个放歪的框伪造（框偏一点覆盖率就变），三个信号同时偏才判不对称。
- `climb_fraction` 会跳过紧贴睫毛的至多 2 行羽化边（`edge_rows=2`），之后连续 2 行低于阈值才停止爬升——单行低于阈值可能只是一条褶线。
- 下睑的镜面亮带**只记录不判决**：它和天然卧蚕高光、干燥皮肤的反射在像素上无法区分，判成失败会冤枉合法的妆效。
- **ROI 从哪来**：目标环境没有 `cv2` / `mediapipe` / `dlib` / `face_recognition`（`--capabilities` 会如实打印 `available: []`），所以所有框都是人/视觉模型标的，必须写进 `roi_provenance`。**不要把某张脸上的固定坐标当成通用识别算法**——换一张脸就全错。
- `verdict`：`pass_code_checks` / `review` / `fail`。`--strict` 在 `fail` 时退出 7。
- 报告固定带 `not_code_testable`（7 项）与 `checklist`（10 项，标注 code / vision / human），并输出 `roi_provenance` 与 `landmark_capability`。
- **`pass_code_checks` ≠ 妆画对了**：眼线位置、畸形睫毛、多余五官、身份保真都不判。实测反例：把"眼睛"ROI 框到海面上，两份 ROI 仍给出 `consistent`（Δhue 9.7°/10.1°）。

## makeup_spec_audit.py

```
python3 scripts/makeup_spec_audit.py --project P [--design ID] [--json out.json] [--strict]
    [--since N --authorized concept,prompt]
python3 scripts/makeup_spec_audit.py --spec design.json [--strict]
```

出图**之前**的档案自洽性检查：色值与结构比对 + 词典匹配，不看图、不判断审美。解决的问题是 schema 管不到的"同一份档案里几处说法互相矛盾"。

| 检查 | 级别 | 说明 |
|---|---|---|
| `palette_undeclared` | error | layers / steps / prompt 里出现的色值不在 `palette.designed` 里 |
| `prompt_colour_drift` | error | `generation.prompt` 引用了未申报色值——提示词与设计对不上，出图必跑偏 |
| `concept_undeclared_colour` | error | 顶层 `concept` 里写了色值，但这个色既不在 `designed` 也不在 `measured` 里（`concept` 最常见被漏改） |
| `concept_contradicts_layer` | error | `concept` 描述了某层明确声明"不做"的动作：`lower_lid`（下眼睑带色）/ `deepen`（眼尾压深）/ `liner_tail`（眼线外拉） |
| `image_prompt_undeclared_colour` | error | 同 `concept_undeclared_colour`，但查**顶层 `image_prompt`**——未同步时它是第二个"自称生成提示词"的字段 |
| `image_prompt_contradicts_layer` | error | 同 `concept_contradicts_layer`，但查顶层 `image_prompt`。`mk_beach_tidal` 从 v8 起 `image_prompt` 一直是旧版"深青压眼尾 1/3 与下眼睑后段"，正是这条抓出来的 |
| `prompt_contradicts_layer` | error | 两条提示词**已同步**时，同一规则改查权威字段 `generation.prompt`（不再重复报 `image_prompt_*`，避免同一段文字报两遍） |
| `image_prompt_not_synced` | error | 顶层 `image_prompt` 与 `generation.prompt` 不逐字相同。它已改为派生字段，不同步意味着有人绕过 `revise` 手改了 `project.json`；用 `prompt-sync` 修，不要手改 |
| `measured_without_source` | error | `extraction_method=none` 却带着非空 `palette.measured`（谎称取过色） |
| `step_not_in_prompt` | warn | 步骤写了某个部位，`generation.prompt` 完全没提 |
| `prompt_claims_without_step` | warn | prompt 声称画了某个部位，`application_steps` 里没有对应步骤 |
| `palette_unused` | warn | 色板列了颜色但没有任何层或步骤引用 |
| `step_unknown_area` | warn | 步骤的 `area` 不在九层结构内 |
| `layer_without_step` | warn | 某层写了做法却没有对应步骤（用户照着做会漏） |
| `base_not_first` / `finishing_not_last` | warn | 步骤顺序明显不合理 |
| `unauthorized_change` | warn | 配合 `--since`，出现了 `--authorized` 之外的单元改动 |

- `--strict` 在存在 **error** 时退出 7；warn 不影响退出码。
- `{"type": "none"}` / `{"position": "不使用"}` / `"省略：…"` 前缀 / `0` / `null` 都视为**明确不做**，不报 `layer_without_step`——否则假警告会淹掉真问题。
- 词典匹配带**否定识别**（按子句判，`"不叠加深色"` 这类夹字写法也算否定；`"眼尾不加宽，可略微加深"` 仍会报），且每个规则有各自的动词类：`压到眼尾 1/3` 是**定位**不是**加深**，混用会把正确写法判成错。
- `--since N` 先把版本 diff 成**授权单元**（`concept` / `prompt` / `negative_prompt` / `notes` / `layer.<name>` / `steps` / `palette` / `meta` / `image_prompt`），再和 `--authorized` 求差。输出 `version_diff.{changed_units, changed_leaf_count, authorized, derived_units, unauthorized_changes}`。这是"我要改眼睛，结果顺手动了唇"的机器防线。
- `image_prompt` 是 `prompt` 的**派生单元**：`prompt` 也变了时自动算已授权（出现在 `derived_units`）；**只改了 `image_prompt` 而 `prompt` 没动**时不享受豁免，照常报越界。
- 报告固定带 `automation_boundary`：`machine_checked`（能机器做的 6 件事）/ `needs_human_or_vision`（做不了的 3 件事）。**不要把这套东西说成语义审计。**
- 它**不**判断：`concept` / `generation.prompt` 的文字与 layers 是否**语义等价**（只有色值和词典能机器比对）、词典没覆盖的旧版措辞、这套妆好不好看、颜色能否真上脸。
- **不改写档案**：发现不一致只报告。同步由 `muselab_state.py prompt-sync` 做，因为改档案必须产生新版本、必须留下 `change_request`。
- 真实来历：这套检查是在 V1.3 自测里被自己的档案打出来的——`mk_beach_tidal` 改了眼妆却漏改 `generation.prompt`（仍写着旧版的深青加深），色板也只申报了 6 色而实际用了 9 色。第二次来历是 v11 的真实出图：`layers`/`steps`/`prompt` 全改成了日常单色，唯独顶层 `concept` 还留着"把这一条线压到眼尾 1/3 与下眼睑后段"，于是 `concept_contradicts_layer` 把它抓了出来。第三次是修完 `concept` 之后再跑一遍：顶层 `image_prompt` 从 v8 起也是旧文，同一个坑换了个字段——先加 4b/5b 两条，最后干脆把 `image_prompt` 改成派生字段，从根上断掉"两份真相"。

## muselab_state.py

| 子命令 | 作用 |
|---|---|
| `init --dir P --title T [--project-id id] [--force]` | 建项目（已存在则拒绝，除非 `--force`） |
| `profile-add --dir P --file profile.json` | 写入 Visual Profile（按 schema 校验） |
| `design-add --dir P --file design.json` | 新建设计 v1（同 id 已存在则拒绝） |
| `design-list --dir P` | 列出设计（id/版本/状态/冻结/结构/任务数） |
| `design-show --dir P --id X [--version N]` | 读取指定版本 |
| `status --dir P --id X --set S [--note ...]` | 状态跃迁（白名单校验） |
| `revise --dir P --id X --file patch.json [--consent] [--change-request ...]` | 生成新版本（**深合并**补丁语义：dict 递归合并，列表整体替换；被替换的列表会在 `replaced_lists` 里列出来，别忽略这个警告）。若载荷里有 `generation.prompt`，顶层 `image_prompt` 会被自动重写，返回 `image_prompt_derived` |
| `prompt-sync --dir P [--id X] [--check] [--consent]` | 处理提示词重复来源。`--check` 只报告：当前版本不同步时退 6，历史版本列在 `historical_drift`（冻结版本不改写）；不带 `--check` 则为每个漂移的设计生成一个只改 `image_prompt` 的新版本 |
| `task-add --dir P --design-id X --file task.json` | 登记图像任务 |
| `task-done --dir P --task-id T --path F [--tool ...] [--fidelity-notes ...]` | 记录成功（路径必须真实存在） |
| `task-fail --dir P --task-id T --reason R [--no-tool]` | 记录失败/无工具（`output_path` 恒为 null） |
| `link-report --dir P --design-id X --report cutout.json [--image F]` | 把本地处理报告挂到设计版本 |
| `export --dir P [--design-id X]` | 导出完整状态或单设计上下文 |
| `verify --dir P` | 完整性校验（版本连续性、路径存在性、schema） |

状态机：

```
draft ──► selected ──► approved（冻结，改动须 --consent 且产生新版本）
  │           │              │
  ├──► failed └──► generated  └──► revision_requested ◄── 任何阶段
```

## 数据结构位置

Schema 位于 `scripts/` 的兄弟目录 `schemas/`。`muselab_state.py` 自动定位；若整体搬迁，可用环境变量 `MUSELAB_SCHEMA_DIR` 显式指定。

| Schema | 校验对象 |
|---|---|
| `design-spec.schema.json` | 设计外壳（id / 类目 / 名称 / concept / prompt / 版本 / 状态） |
| `makeup-spec.schema.json` | `design_category=makeup` 时，`module_payload` 的载荷（九层、色板三层、步骤、出图配置） |
| `image-task.schema.json` | 图像任务（含 `generation_mode` 三条路线） |
| `visual-profile.schema.json` | 视觉档案 |
| `project-state.schema.json` | 项目状态文件 |

`design_category=makeup` 时不要求 `structure.archetype`（珠串结构对妆容无意义），改为强制 `module_payload` 通过 `makeup-spec.schema.json` 校验。新增模块只需在 `muselab_state.py` 的 `PAYLOAD_SCHEMA` 加一行映射。
