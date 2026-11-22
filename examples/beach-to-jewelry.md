# 示例一：海滩照片 → 三款手串

## 用户

> [上传 beach.jpg] 提取这张照片里的颜色，帮我设计三款不同风格的手串。

## MuseLab 的处理

### 0. 探测

```
$ python3 scripts/check_env.py --imagegen yes --websearch yes
vision_foreground_mask: true   → 抠图可用
image_generation: A            → 可出图
```

### 1. 实测色板

```
$ python3 scripts/color_extract.py beach.jpg --mode mood --max-colors 5 \
    --swatch exports/beach_palette.png --json beach_colors.json
```

返回（节选）：

```
#0F2B46 深蓝   32%  primary    深海蓝
#2E8B93 青     24%  secondary  浅海青
#F4EDE4 象牙白 21%  secondary  贝壳白
#D8C3A5 沙米   15%  accent     沙滩米
#9CB89A 灰绿    8%  accent     海沫绿
atmosphere: 中调 / 柔和 / 对比强烈 / 偏冷（均 L 63.2，均 C 17.9，ΔE 跨度 77）
method: pixel_kmeans_lab（实测，非估计）
```

### 2. 结构与配色决策

- 主色 #0F2B46 面积最大 → 主珠材质：青金石 6mm（哑光带金星，对应"深海"的沉）。
- 浅海青 #2E8B93 → 海蓝宝 6mm（半透，对应"水"的透）。
- 贝壳白 #F4EDE4 → 淡水珍珠 5mm（柔光晕彩）。
- 沙滩米 #D8C3A5 → 贝母片（半透层理）。
- 海沫绿 #9CB89A 与 #2E8B93 ΔE 12.1 → 相邻会糊，中间插银隔珠隔开。

### 3. 三款（结构差异是重点）

| | A 潮汐整串 | B 半珠半链 | C 异形主石 |
|---|---|---|---|
| 结构 | `full_strand` | `half_bead_half_chain` | `irregular_centerpiece` |
| 主材 | 青金石 6mm×20 + 珍珠 5mm×4 | 海蓝宝 6mm×12 + 1.2mm O 字链 4cm | 单颗 14mm 巴洛克珍珠 |
| 五金 | 弹力线，无金属 | 925 银隔珠×3 + 龙虾扣 + 5cm 延长链 | 锤纹银包边 + 挂环 + 细链 |
| 重量 | ≈16g | ≈12g | ≈9g |
| 场景 | 日常通勤 | 度假/叠戴 | 正式/送礼 |

A 串珠序列（写清珠数）：
`珍珠 5mm → 青金石 ×5 → 珍珠 → 青金石 ×6 → 珍珠 → 青金石 ×5 → 珍珠 → 青金石 ×4`（共 24 颗，手围 16cm，弹力线双股）

B 串珠序列：
`龙虾扣 → 延长链 5cm → 银隔珠 3mm → 海蓝宝 ×4 → 银隔珠 → 海蓝宝 ×4 → 银隔珠 → 海蓝宝 ×4 → O 字链 4cm → 龙虾扣`

C 结构：
`细链 1.1mm 40cm → 挂环 → 锤纹银包边（厚 0.8mm）→ 14mm 巴洛克珍珠（横穿孔 1.0mm，九字针贯穿）`

### 4. 出图

三款各 1 张商品图：`ImageGen`，`1024x1024`，`image1` 不用（原创结构），提示词按十段式：

```
商业珠宝产品摄影。一条 6mm 青金石与 5mm 淡水珍珠间隔的整串手链，共 24 颗，
青金石深蓝带金色星点，珍珠柔白带晕彩，弹力线穿制无金属件。米白亚麻布背景，
正俯 90° 平铺，主体占画面 70%。柔光箱 45° 主光 + 右侧反光板，柔和投影。
100mm 微距，f/8，中心构图。真实材质反射，可见石面细微金星与珍珠层理，无塑料感。
```

登记：

```
$ python3 scripts/muselab_state.py task-done --dir muselab-projects/beach --task-id img_A1 \
    --path images/jw_beach_A_1.png --tool ImageGen --fidelity-notes "青金石星点比实物少，珠数正确"
```

### 5. 回复结构

先给对照表（上面那张），再逐款展开成 12 项（名称/灵感/理念/主辅色/结构+序列/材料清单/尺寸/工艺/可行性/场景/提示词/负面词），最后一句：

> 三款结构分别是整串、半珠半链、单颗异形主石，差异在结构而非配色。B 款需要焊接延长链，属手工进阶；A 款 10 分钟可完成。要不要把 A 款做成项链？

## 关键点

- 色值是**实测**的，可以说"测得"。
- 三款结构真的不同，不是三个配色方案。
- 珠数写出来了，能对上 16cm 手围。
- 出图后如实记录偏差。
