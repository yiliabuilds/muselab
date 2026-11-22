# 示例二：商品抠图与白底 / 换背景

## 用户

> 帮我把这张手链图抠出来，做成白底商品图。

## MuseLab 的处理

### 1. 探测

```
$ python3 scripts/check_env.py --imagegen yes --websearch no
vision_foreground_mask: true（macOS 15.7 / Vision revision 1）
```

### 2. 真实分割

```
$ python3 scripts/cutout.py bracelet.jpg --out images/bracelet_cutout.png --despill \
    --json bracelet_cutout.json
```

返回：

```json
{
  "method": "vision_foreground_instance_mask",
  "is_generative": false,
  "instance_count": 1,
  "subject_bbox": [314, 185, 885, 714],
  "subject_area_ratio": 0.095,
  "alpha_stats": {"transparent_ratio": 0.895, "opaque_ratio": 0.085, "semi_transparent_ratio": 0.019},
  "color_preserved": true
}
```

`semi_transparent_ratio = 0.019` 说明边缘是真实软边（约 1.9% 像素处于半透明），不是剪刀剪出来的硬边。

### 3. 白底合成

```
$ python3 scripts/compose.py images/bracelet_cutout.png --out exports/bracelet_white.png \
    --bg white --size 2000 --aspect 1:1 --margin 0.08
```

```
subject_placement: {"x":160,"y":160,"w":1680,"h":1680,"scale":1.744}
white_is_pure: true
```

### 4. 质量门禁

- 主体完整：✓（细链末端未断）
- 颜色不变：✓（`color_preserved: true`，抽检最大通道偏差 0）
- 无新增配件：✓
- 边缘自然：✓（半透明过渡 1.9%，无白边——`--despill` 已处理）
- 四角纯白：✓ `(255,255,255)`
- 比例未拉伸：✓ 等比缩放

### 5. 换背景（追加需求）

> 再放到大理石桌面上看看。

```
$ python3 scripts/compose.py images/bracelet_cutout.png --out exports/bracelet_marble.jpg \
    --bg assets/marble.jpg --shadow --format jpg --size 2000 --aspect 4:5
```

回复里必须写：**"阴影为合成示意（高斯模糊+20% 下移），不是真实光照测量；商品本体像素未改动。"**

## 反例（禁止的做法）

用户说"抠图"，环境没有分割工具，却用文生图生成一张"白底手链图"然后说"抠好了"。

后果：珠数会变、链条会断、金属色会漂——等于换了一件商品。正确说法：

> 当前环境没有主体分割能力，无法做真实的像素级抠图。两个选择：① 我给你 PS 的"选择主体 → 调整边缘 → 输出蒙版"具体步骤；② 用生成模型重绘一张近似白底图，但那是重绘，结构可能与实物不同，不能当商品图用。

## 多主体情况

```
instance_count: 3
```

→ 不要猜。回复："图里有 3 个主体（一条手链、一枚戒指、一个吊坠），要保留哪个？还是分别导出三张？"

## 交付

- 透明 PNG：`exports/bracelet_cutout.png`
- 白底 PNG：`exports/bracelet_white.png`（2000×2000，纯白）
- 场景 JPG：`exports/bracelet_marble.jpg`（含合成阴影说明）
- 报告：`bracelet_cutout.json`（可复现的参数与统计）
