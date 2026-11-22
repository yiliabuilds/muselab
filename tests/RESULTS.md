# MuseLab 测试结果

- 运行时间：20261122-020724
- 平台：darwin
- 汇总：PASS 83 / FAIL 0 / NOT-RUN 7 / ENV-UNSUPPORTED 0

| 用例 | 结果 | 说明 |
|---|---|---|
| env: capability probe | PASS | vision=True pillow=True numpy=True jsonschema=False |
| 01 单主体抠图（真实 alpha） | PASS | RGBA (600, 600), 中心不透明/背景全透明, method=vision_foreground_instance_mask |
| 01b 抠图不改变商品颜色 | PASS | 完全不透明像素 92158 个，最大通道偏差 0 |
| 01c 主体定位 | PASS | bbox=[314, 185, 885, 714] area=0.095 instances=1 |
| 01d 缺失输入的处理 | PASS | exit=2 |
| 02 海滩照片吸色（测量精度） | PASS | 命中 5/5，多出 无，method=pixel_kmeans_lab |
| 02b 面积占比准确 | PASS | 每块 0.2 等分 |
| 02c 氛围分析（明度/冷暖/对比） | PASS | {'mean_lightness': 63.2, 'lightness_range': [16.9, 94.1], 'mean_chroma': 17.9, 'max_chroma': 26.9, 'warm_ratio': 0.25, 'cool_ratio': 0.5, 'neutral_ratio': 0.2, 'temperature': 'cool', 'contrast_spread': 77.2, 'mood_words': ['中调', '柔和', '对比强烈', '偏冷']} |
| 02d 色板图输出 | PASS | swatch.png |
| 02e 区分测量与推测 | PASS | method=visual_estimate |
| 03 纯白背景导出 | PASS | corner=(255, 255, 255) |
| 03b 透明输出保留真实通道 | PASS | mode=RGBA |
| 03c 拒绝 JPEG+透明 组合 | PASS | exit=5 |
| 03d 缩放后边缘仍为软边 | PASS | semi-transparent px=13838 |
| 03e --keep-frame 保画布与像素坐标 | PASS | size=[1200, 900] scale=1.0 offset=(0,0) frame_preserved=True |
| 03f 像素完整性：正常合成无改动 | PASS | verdict=pass_exact core=0 blend_err_max=0.5 |
| 03g 检出合成后调色（且不误判抠图） | PASS | exit=6 preserved={'cutout_step': True, 'compositing_step': False, 'overall': False} |
| 03h 检出非预期裁切 | PASS | exit=6 clipped_px=47440 |
| 03i 覆盖检查抓出蒙版漏掉的商品 | PASS | hole@(833,449) status=review uncovered_px=1305 product_like=1 (像素完整性本次仍判 pass_exact) |
| 03j 蒙版质检输出结构 | PASS | overall=review checks=['background_residue', 'edge_contamination', 'translucent_core'] |
| 03k 蒙版修补可运行（不覆盖输入） | PASS | exit=0 ok=True removed=- |
| 04 项目初始化 | PASS |  |
| 04b 视觉档案入库 | PASS |  |
| 05 设计档案入库 | PASS |  |
| 05b 拒绝覆盖已有设计 | PASS | exit=2 |
| 05c 拒绝 draft→approved 跳步 | PASS | exit=6 |
| 05d 定稿（selected→approved） | PASS |  |
| 05e 定稿后标记 frozen | PASS | True |
| 05f 拒绝改动已定稿设计 | PASS | exit=6 |
| 06 未获许可不得修改定稿 | PASS | exit=6 |
| 06b 授权修改生成新版本 v2 | PASS | {"ok": true, "design_id": "jw_test_A", "new_version": 2, "previous_version_kept": 1, "status": "draft", "changed_fields": ["colors", "materials"], "change_reque |
| 06c 原版本仍可引用且未被改写 | PASS | v1 accent=#D8C3A5 status=approved superseded_by=2 |
| 06d 新版本只改指定字段 | PASS | v2 accent=#C9A227 parent=1 |
| 07 图像任务登记 | PASS |  |
| 07b 拒绝虚构输出路径 | PASS | exit=2 |
| 07c 工具不可用时如实降级 | PASS | {"ok": true, "task_id": "img_t1", "execution_status": "skipped_no_tool", "reason": "当前环境无图像生成工具", "output_path": null, "reminder": "Tell the user the image was  |
| 07d 记录真实产出与保真说明 | PASS | {"ok": true, "task_id": "img_t1", "output_path": "<scratch>/fake_out.png", "t |
| 07e 项目完整性校验 | PASS | {"ok": true, "problems": [], "designs": 1, "image_tasks": 1, "prompt_drift": []} |
| 07f 设计清单可引用 | PASS | [{"design_id": "jw_test_A", "version": 2, "status": "generated", "frozen": false, "name": "潮汐三段", "category": "beaded_bracelet", "structure": "half_bead_half_chain", "image_tasks": 1}] |
| 08 Schema 校验拦截缺字段 | PASS | exit=2 |
| 10 门禁：合规图判 pass（且不越权宣布合格） | PASS | verdict=pass_code_checks bg=pass_seamless mat=pass_within_lighting |
| 10b 明度偏但色相对 → 归为可后期修正 | PASS | status=tone_shift dL_res=-10.7 hue=0.1 硬失败=0 |
| 10c 色相错 → 判材质不符 | PASS | status=material_mismatch hue_drift=144.7° |
| 10d 光照补偿：raw ΔE 大但材质本身对 | PASS | dE_raw=11.87 → dE_decast=4.28 bg=off_spec |
| 10e 打光布景背景 → 判不合格 | PASS | status=off_spec spread=8.0 |
| 10f 配件虚化 → 判「无法验收」而非通过 | PASS | 上=ready_for_vision_review 下=unverifiable_blur ratio=0.016 |
| 10g 声明位置的配件缺失 → 硬失败 | PASS | status=absent_at_roi fg=0.0 |
| 10h --strict 硬门禁失败时退出 7 | PASS | exit=7 |
| 10i 采样点落在背景 → 拒判，不硬给结论 | PASS | status=inconclusive_low_separation reason=100% of the disc is within ΔE 10 of the backdrop and the disc is flat  |
| 10j 三条路线进入任务 schema（且兼容旧值） | PASS | product_compositing exit=0 旧值 exit=0 非法 exit=2  |
| 10k 声明哪些检查是代码/视觉/人工 | PASS | 10 条，代码 5 |
| 11a 妆容档案缺 module_payload 被拒 | PASS | exit=2 {   "ok": false,   "error": "invalid_design_spec",   "errors": [     "module_payload: requ |
| 11b 妆容档案走同一状态机登记 | PASS | exit=0 category=makeup  |
| 11c 只改眼妆：唇/底妆不动且旧版保留 | PASS | revise exit=0 底妆保留=True 眼妆改变=True v1存在=True |
| 11d 非法妆容风格被 payload schema 拦下 | PASS | exit=2 {   "ok": false,   "error": "invalid_design_spec",   "errors": [     "module_payload/makeu |
| 11e 妆容检查声明代码/视觉/人工三条边界 | PASS | 21 条，by=['code', 'code(soft)', 'human', 'vision', 'vision+human'] |
| 11f 色相一致时判 consistent 并给出数值 | PASS | verdict=pass_code_checks dHue=9.1° chromaRatio=0.75 |
| 11g 双眼色相背离判 fail（strict 退出 7） | PASS | verdict=fail strict=7 dHue=174.0° |
| 11h 左右眼不平衡被单独检出 | PASS | ΔE=27.46 Δhue=153.2° verdict=fail |
| 11i 过度磨皮按参考图纹理能量检出 | PASS | ratio=0.002 (参考 275.3) |
| 11j 唇缘溢色只作软提示不硬判 | PASS | status=possible_bleed mix=0.402 verdict=review |
| 11k 报告明确声明身份/五官不可自动判定 | PASS | 7 项非代码可测 |
| 11l 未申报色值被审计点名（schema 查不出） | PASS | exit=0 ok=False 未申报=['#8A4B55'] |
| 11m 提示词跑色 / 谎称取色 → error 且 strict 退出 7 | PASS | exit=7 errors=['image_prompt_not_synced', 'measured_without_source', 'palette_undeclared', 'prompt_colour_drift'] |
| 11n 自洽档案审计通过（strict 退出 0，零 warn） | PASS | exit=0 ok=True E=0 W=0 |
| 12a 对称且只涂下半个上睑 → 通过（不再是'中位色接近就算平衡'） | PASS | verdict=pass_code_checks cov=0.2945 |
| 12b 爬升高度按带高归一化，读到画进去的 0.45 | PASS | climb=0.425（夹具绘制 0.45，容差 0.12） |
| 12c 涂满整条上睑 → full_lid 硬失败 | PASS | climb=0.95 |
| 12d 左 0.30 / 右 0.90 → 覆盖+爬升两项同时超阈值才判不对称 | PASS | signals=['coverage', 'climb_height'] |
| 12e 下睫毛下方亮带 → 记为需人判的观察项，不当硬失败 | PASS | spec=0.0958 |
| 12f 明确声明 ROI 来源：无关键点库时不得冒充识别算法 | PASS | available=[] source=human_or_vision_declared |
| 12g concept 写'眼尾加深/下眼睑带色'而层里写省略 → 两项 error | PASS | exit=7 rules=['deepen', 'lower_lid'] |
| 12h 否定式（不加深眼尾）不被误判为冲突 | PASS | exit=0 errors=[] |
| 12i --since 比对：只授权眼影却改了唇 → 点名 unauthorized | PASS | unauthorized=['layer.lips'] |
| 12j 真实出图回归（需要 MUSELAB_REAL_RENDER 指向出一张真实图） | NOT-RUN | 本轮人工执行过，结果见项目内 qa_render_tidal_v11_real_01.md；套件不代跑额度任务 |
| 12k 顶层 image_prompt 残留旧眼妆 → 冲突与未申报色两项 error | PASS | exit=7 errors=['image_prompt_contradicts_layer', 'image_prompt_undeclared_colour'] |
| 12l '不叠加深色'这类夹字否定不被误判（按子句判否定） | PASS | exit=0 errors=[] |
| 13a design-add 时 image_prompt 由 generation.prompt 派生（手写值被覆盖） | PASS | exit=0 derived=True |
| 13b 只改 generation.prompt → 镜像自动跟随；授权 prompt 时镜像算派生不算越界 | PASS | mirror==P2=True derived=['image_prompt'] unauth=[] |
| 13c 绕过状态机手改 image_prompt → 审计报 image_prompt_not_synced（error） | PASS | exit=7 errors=['image_prompt_not_synced', 'image_prompt_undeclared_colour'] |
| 13d prompt-sync --check 当前版本漂移时退出 6 并点名设计 | PASS | exit=6 drift=['mk_prompt_sync'] |
| 13e prompt-sync 生成同步版本：当前版本对齐、旧版本原样保留、复查退出 0 | PASS | v2->v3 recheck_exit=0 prev_kept=True |
| 13f 只改 image_prompt 而 prompt 未变 → 不享受派生豁免，仍判越界 | PASS | derived=[] unauth=['image_prompt'] |
| 13g 首饰设计（无载荷提示词）不被派生改写，prompt-sync 报 derived=false | PASS | image_prompt 原样=True derived=False |
| 09 照片转三款手串（含真实出图） | NOT-RUN | 需要 agent 侧图像生成工具 + 1 次出图额度；见 examples/beach-to-jewelry.md |
| 09b 系列产品设计（多图一致） | NOT-RUN | 需要图像生成工具与 4 次出图额度 |
| 09c 真人模拟佩戴 | NOT-RUN | 需要参考图编辑能力；商品一致性无法用脚本验证 |
| 09d 新闻灵感设计（联网） | NOT-RUN | 需要联网搜索工具；脚本不联网，不编造新闻 |
| 12 妆容真实 ImageGen 出图（套件不代跑额度） | NOT-RUN | 本轮已在项目内人工执行 1 次 v11 出图并出验收报告；套件不消耗额度，故不在自动流程内 |
| 12m 妆容参考图路线（面部照编辑） | NOT-RUN | 需要真实面部照片 + 参考图编辑能力；本轮用户未提供面部照 |
