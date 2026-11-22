# 示例三：真人模拟佩戴

## 用户

> 刚才第一款不错，帮我做成戴在手腕上的效果图。

## MuseLab 的处理

### 1. 读档案（不从记忆里重写）

```
$ python3 scripts/muselab_state.py design-show --dir muselab-projects/beach --id jw_beach_A
```

```json
{
  "version": 1, "status": "approved", "frozen": true,
  "spec": {
    "design_name": "潮汐整串",
    "structure": {"archetype": "full_strand", "length_mm": 160,
                  "sequence": ["珍珠5mm", "青金石×5", "珍珠", "青金石×6", "珍珠", "青金石×5", "珍珠", "青金石×4"]},
    "dimensions": {"length_mm": 160, "bead_diameter_mm": 6, "weight_g": 16},
    "colors": {"primary": "#0F2B46", "secondary": ["#F4EDE4"], "accent": "#D8C3A5"}
  }
}
```

`frozen: true` → 只能引用，不得改设计。

### 2. 判断工具

- 有参考图编辑能力 → Mode A，把定稿商品图作为 `image1`，`input_fidelity` 高。
- 只有文生图 → 属于**生成模拟**，必须提前说清"结构可能变化"。

### 3. 三种方案（用户没指定就都给）

| 方案 | 提示词要点 |
|---|---|
| 日常自然光 | 窗边侧光、浅景深、手腕轻搭在亚麻袖口上、咖啡杯虚化、f/2.8 |
| 近景细节 | 100mm 微距、f/4、可见皮肤纹理与珠面高光、主体占 80% |
| 商业广告 | 硬光 + 反光板、纯净浅灰背景、半身构图、成片感、f/8 |

锁定段（放在提示词最前面）：

```
手链本体不可改变：24 颗珠子（青金石 6mm 与淡水珍珠 5mm 间隔），
珠径 6mm，内周长 16cm，共 4 段珍珠分隔，无金属件，弹力线穿制。
佩戴在右手腕，紧贴皮肤，下缘有轻微压痕。
```

### 4. 出图后逐项核对

| 项 | 档案 | 图 | 结论 |
|---|---|---|---|
| 珠数 | 24 | 23 | ✗ 少 1 颗 |
| 珠径比例 | 6mm / 16cm 手围 | 视觉偏大 ~8mm | ✗ |
| 间隔结构 | 4 段珍珠 | 3 段 | ✗ |
| 金属件 | 无 | 无 | ✓ |
| 手指数量 | 5 | 5 | ✓ |
| 皮肤质感 | 自然 | 略磨皮 | △ |

→ 必须如实写：

> 这张图（`images/tryon_A_1.png`）珠子少 1 颗、珠径偏大、珍珠分隔段数不对，**不能作为商品验收图**，只能作氛围参考。建议：① 提高 `input_fidelity` 并用商品图做 `image1` 重出；② 或改用实拍。

不允许写："已生成佩戴效果图，与原设计一致。"

### 5. 登记

```
$ python3 scripts/muselab_state.py task-add --dir muselab-projects/beach --design-id jw_beach_A --file tryon_task.json
$ python3 scripts/muselab_state.py task-done --dir muselab-projects/beach --task-id img_tryon1 \
    --path images/tryon_A_1.png --tool ImageGen \
    --fidelity-notes "珠数 23 vs 24；珠径偏大；珍珠分隔 3 段 vs 4 段；仅作氛围参考"
```

定稿状态不变：`approved` 的 spec 仍是 `approved`，出图不改变它。

### 6. 商业使用提醒

> 如果这张图要上商品页，建议标注"AI 模拟效果，非实物佩戴实拍"；尺寸与珠数以商品页参数为准。

## 用户提供自己的手腕照

> [上传 wrist.jpg] 用我的手。

- 该图仅用于本次合成，不写入项目档案的公开字段、不外传。
- 作为 `image2` 与商品图一起给工具；提示词补"保持原图的光线与肤色，不改变手部结构"。
- 生成后仍需做人体真实性检查（五指、关节、皮肤纹理、首饰贴合）。

## 无工具时（Mode B）

交付三套提示词 + 比例说明 + 复查清单，并明确：

> 当前环境没有可用的图像编辑工具，以上提示词可直接粘贴使用，佩戴图**尚未生成**。
