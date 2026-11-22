# 工作流：系列设计

入口："做一整套 / 系列 / 同主题多件 / 手链+项链+耳饰一起"。

## 原则

系列感 ≠ 长得一样。四件事必须共享，三件事必须不同：

**共享**：主题叙事、主色（同一 HEX）、材质体系（同一批主材）、一个可识别的造型符号（例如都用"破碎边缘"或都用"双线结构"）。
**不同**：单品结构、尺寸尺度、视觉重点位置、佩戴场景。

## 第 1 步：先出系列总案（不急着出单品）

```json
{
  "collection_id": "col_beach_01",
  "theme": "退潮线",
  "narrative": "潮水退去后留在沙上的三道线",
  "shared": {
    "palette": ["#0F2B46", "#2E8B93", "#F4EDE4", "#D8C3A5"],
    "materials": ["海蓝宝 6mm", "淡水珍珠 5mm", "925 银"],
    "signature": "三段式疏密结构：密—疏—密",
    "metal": "925 银，哑光 + 局部抛光"
  },
  "pieces": [
    {"design_id": "col_beach_A", "category": "necklace", "structure": "minimal_single_pendant"},
    {"design_id": "col_beach_B", "category": "beaded_bracelet", "structure": "half_bead_half_chain"},
    {"design_id": "col_beach_C", "category": "earrings", "structure": "dangle"},
    {"design_id": "col_beach_D", "category": "bag_charm", "structure": "partial_strand_chain"}
  ]
}
```

## 第 2 步：单件展开

每件都走 `workflows/jewelry-design.md` 的第 3–6 步，共用系列 `shared` 字段，逐件 `design-add`。

一件项链 + 一件手链 + 一对耳饰 + 一个包挂，是最稳的四件组合：尺度递进、场景覆盖、成本可控。

## 第 3 步：一致性检查表

- [ ] 主色 HEX 完全一致（不是"差不多"）
- [ ] 至少 2 种材质在所有单品中出现
- [ ] 造型符号在每件上都可指认（例如都在偏侧位置有一个金属停顿）
- [ ] 没有两件用了相同结构原型
- [ ] 价格带在同一档（避免项链 1200 而包挂 80）
- [ ] 单件拆出来单独看，仍能看出属于同一系列

## 第 4 步：出图策略（重要）

多张图的**结构漂移**是系列图最大的问题。做法：

1. 先为每件出 1 张"设计定稿图"（`image_type: product_shot`），确认结构无误。
2. 再做"系列合影"（`image_type: collection_sheet`）：把所有已定稿单品作为参考图一起喂给生成工具（ImageGen 支持 `image1/image2/image3`），提示词里明确"保持每件单品的珠子数量、排列与金属件不变"。
3. 出图后逐件比对定稿图，偏差写进 `fidelity_notes`，不要假装一致。

只有 1–3 张参考图位置时，优先保证：主件 + 最贵件 + 最易漂移件（通常是耳饰）。

## 输出

1. 系列总案（主题/叙事/共享元素/价格带）
2. 单品对照表（品类｜结构｜主色｜材质｜尺寸｜场景）
3. 单品详案（每件按首饰模板）
4. 系列合影说明（已生成 / 未生成 + 提示词）
