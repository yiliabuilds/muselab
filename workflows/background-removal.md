# 工作流：智能抠图与背景处理

入口：用户说"抠图 / 去背景 / 白底图 / 换个背景 / 商品图"。

## 判断

1. 先跑能力探测（`scripts/check_env.py`）。`vision_foreground_mask` 不可用 → **不得声称完成抠图**，跳到「降级」。
2. 图里有几个主体？Vision 返回 `instance_count`：
   - `1` → 直接做。
   - `>1` 且用户没说保留哪个 → 问一句"要保留哪一个主体？"，不要猜。
3. 用户没指定背景模式 → 默认「透明 PNG」；如果用户说"商品图/主图" → 默认「纯白 `#FFFFFF`」。

## 执行

### 第 1 步：真实分割（产生真 alpha）

```bash
python3 scripts/cutout.py <输入图> --out <workspace>/images/<name>_cutout.png --despill --json <name>_cutout.json
```

- 输出 RGB **原样保留**，只加 alpha。
- `--despill` 处理 1px 边缘污染，但它按**白色背景**反解：灰/米色背景下会把边缘推亮。且 `mask_audit` 的边缘污染指标用原图像素计算，**不会因为 despill 变好**——实测改了 10 万像素、最大差 207，指标一动不动。要判断去污有没有用，只能自己比 RGB。
- Vision 的 alpha **上限是 254**，不是 255。所以"完全不透明"只是近似，合成后核心区仍会混入约 1/255 新背景。
- 多主体时加 `--instance N`（0 起）。
- 记录 `method = vision_foreground_instance_mask`，这叫真实抠图。

### 第 2 步：按模式出图

| 模式 | 命令 |
|---|---|
| 透明 PNG | `compose.py <cutout> --out out.png --bg transparent --size 2000` |
| 纯白商品图 | `compose.py <cutout> --out out.png --bg white --size 2000 --aspect 1:1 --margin 0.08` |
| 指定色背景 | `compose.py <cutout> --out out.png --bg "#F2EDE6"` |
| 图片背景 | `compose.py <cutout> --out out.jpg --bg bg.jpg --shadow --format jpg` |
| **商品保真合成** | `compose.py <cutout> --out out.png --bg "#D6D6D6" --keep-frame` |

**`--keep-frame` 什么时候必须加**：不能改变商品在画面里的位置、比例、出画关系时。它不裁 bbox、不缩放、不居中，相当于"只换背景板"。原图被画面边缘切断的商品（例如手链出画），加了这个开关才不会被重新构图。

### 第 3 步：质量门禁（必做，两份报告）

抠图和合成是两个可以各自出错的动作，所以要问两个问题：

```bash
# 问 1：蒙版干净吗？（背景残留 / 异常透明 / 边缘污染）
python3 scripts/mask_audit.py --image 原图 --cutout cut.png --json audit.json --overlay qa_mask.png

# 问 2：商品像素被改了吗？（抠图重绘 / 合成改色 / 被裁切）—— 仅保真合成需要
python3 scripts/pixel_integrity.py --original 原图 --cutout cut.png \
    --composite out.png --placement place.json --bg '#D6D6D6' --json integrity.json
```

人工逐条自查：

- [ ] 主体无缺失（对比原图，尤其细链、细耳针、透明石）
- [ ] 商品颜色未变（`pixel_integrity` 的 `cutout_vs_original` 为 0；肉眼再核一次）
- [ ] 没有新增珠子/吊坠/配件
- [ ] 金属高光、珍珠、透明石材边缘自然，无白边/黑边/残影
- [ ] 半透明区域（`alpha_stats.semi_transparent_ratio`）存在，说明边缘是软边而非剪贴；但**半透明占比过高说明边缘虚**（实测一张真实手链照片达 23.2%）
- [ ] 白底图四角为 `(255,255,255)`；纯色图四角等于目标色（实测 #D6D6D6 全部偏差 0）
- [ ] 比例未被拉伸（`subject_placement.scale` 为等比；`--keep-frame` 时应为 1.0）
- [ ] `structure.clipped_pixels == 0`（没有商品被画布切掉）

**两条结论不能互相顶替**：

- `pixel_integrity` 通过 = 商品没被重画。**它不保证图能用**：蒙版有残影、边缘脏、半透明异常，它一句都不会说。
- `mask_audit` 通过 = 蒙版风险低。**它不保证合成没被调色**：有人在合成后加了一层曲线，它看不出来。

任一份不过 → 不要交付，报告问题并给替代方案。

### 第 4 步：登记

```bash
python3 scripts/muselab_state.py task-add  --dir <proj> --design-id <id> --file task.json
python3 scripts/muselab_state.py task-done --dir <proj> --task-id img_x --path <输出图> --tool vision_cutout
```

`task.json` 里 `image_type` 用 `transparent_cutout` 或 `white_background`，`tool_used` 与 `is_generative: false`。

## 降级

| 情况 | 说法 |
|---|---|
| 无 Vision | "当前环境没有主体分割能力，无法做像素级精确抠图。可以：① 我给出修图指令（PS 选择主体/蒙版步骤）；② 用生成模型重绘一张近似图——但那是重绘，不是抠图，结构可能变化。" |
| 主体太小/纹理图 | "没有检测到明确前景主体，可能需要手动框选。" |
| 多主体无法判断 | 问用户保留哪个。 |

**绝对禁止**：用文生图生成一张"看起来是白底"的图，然后说抠图完成。生成式结果会改珠数、改金属件、改结构，等于换了一件商品。

## 换背景时的额外要求

- 背景是丝绸/大理石/木桌等实景 → `--shadow` 加接触阴影，但要在回复里说明"阴影为合成示意，非真实光照测量"。
- 背景色温与商品不匹配时，不要用调色去"融合"主体（会改商品色），改为换一张背景图。
- 场景合成后仍必须保持：主体轮廓 ≤ 2% 形变、色差 ΔE ≤ 2。
