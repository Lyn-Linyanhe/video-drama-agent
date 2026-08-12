# 道具提取与按需参考图设计规格

## 目标

将道具从“LLM 罗列物品 + 六次独立生图 + 2×3 拼图”改为：

1. LLM 在结构化解析阶段直接判断哪些物品是关键道具；
2. 取消固定最多 5 件，但保留严格的关键道具硬校验；
3. 每件道具先生成一张稳定外观母版；
4. 只有镜头确实需要其他角度时，才基于母版补充独立视图；
5. 开封、破损、沾血、递交等剧情状态不生成单独资产，留在视频生成阶段；
6. 所有生成入口共用一套提示词、供应商、版本和 seed 规则。

## 关键道具定义

LLM 只有在以下条件全部满足时才能输出关键道具：

- 是可以被镜头看见的实体物品；
- 至少绑定两个不同分镜；
- 每个绑定镜头都能说明该物品的可见位置、外观或操作状态；
- 至少满足一种关键类型：plot（剧情线索/证据）、identity（身份标记）、handoff（关键交接物）、continuity（跨镜头连续性物品）；
- 必须填写 critical_evidence，明确写出镜头编号和它对后续理解的影响。

以下物品必须排除：

- 只出现一个镜头的物品；
- 只被提及、没有出镜的物品；
- 普通家具、固定陈设、电脑、灯具、货架等场景组成；
- 水渍、血迹、烟雾、破损、光斑等环境或状态；
- 只被角色顺手拿起、使用一次、之后不再出现的普通物品；
- 没有可辨识外观、无法作为参考图使用的泛称。

取消最多 5 件的硬上限。后端不按数量删除合格道具，只在道具数量大于 max(8, ceil(scene_count / 2)) 时给出“道具密度偏高，请复核”的非阻塞警告。

## LLM 输出契约与后端校验

结构化剧本中的 PropAsset 必须包含：

- prop_id、name、description；
- critical_reason，只能是 plot、identity、handoff、continuity；
- critical_evidence；
- shot_ids、is_handheld、aliases。

description 只写可见颜色、材质、形态、比例、磨损和标志细节，不能写用途或剧情解释。

shot_bindings.prop_ids 是镜头实际可见道具的来源真相。解析完成后依次执行：

1. 删除不存在的 prop_id；
2. 将 shot_ids 重建为绑定中实际出现的镜头；
3. 删除少于两个绑定镜头的道具；
4. 删除缺少 critical_reason 或 critical_evidence 的道具；
5. 删除描述为空、过短或只包含用途的道具；
6. 删除背景陈设、环境状态和一次性普通物品；
7. 清理所有镜头中指向已删除道具的 prop_ids；
8. 记录 removed_props 和每个删除原因；
9. 关系完整时继续解析，不因 LLM 原始 shot_ids 不一致而直接失败。

后端不再根据关键词猜测“这是关键道具”。关键词只用于发现明显的背景陈设和环境状态；最终是否保留由 LLM 的结构化理由和硬条件决定。

## 道具生成流程

### 母版

每个关键道具首先只生成一张标准外观母版：

- 横向 4:3 产品参考构图；
- 单个物品、纯净背景、居中、无遮挡；
- 固定材质、颜色、比例和结构；
- 不生成开封、破损、沾血等剧情状态，除非它们是道具的稳定外观。

生成成功后状态为待确认，不自动生成其他视图。

### 视图需求

根据绑定镜头的摄影描述和动作描述推导：

- 默认只需要 front；
- 出现背面、从背后拍摄时加入 back；
- 出现侧面或绕到侧面时加入 left/right；
- 出现俯拍或查看顶部时加入 top；
- 出现底部结构时加入 bottom；
- 没有明确机位需求时不生成多余视图。

每个视图保存为独立 PNG。可以额外生成联系页作为资产库预览，但联系页不能传给视频模型。

### 组图与单图

- 只需要 front：调用母版单图生成；
- 需要两个及以上视图：使用万相参考组图，输入确认母版，返回所需数量的独立图片；
- 返回数量不足、下载失败或顺序无法确认时，不激活任何派生视图；
- 不支持参考组图的模型只能生成母版或单图；
- 用户重生成某一视图时，以母版为第一参考图，可选当前视图为第二参考图。

### 视频阶段状态

道具资产不建立开封、破损、沾血等状态图片。分镜提示词的 prop_state 描述变化，视频生成时读取道具身份、选择最匹配角度并把状态写入有效视频提示词。

## 数据模型

新增项目状态字段：

~~~python
prop_view_requirements: dict[str, PropViewRequirement] = field(default_factory=dict)
~~~

PropViewRequirement 包含 prop_id、views、reasons、source（auto/manual）、signature。

ShotVisualBinding 新增：

~~~python
prop_view_preferences: dict[str, str] = Field(default_factory=dict)
~~~

PropImage 新增 is_master、needs_confirmation、master_version_id、reference_version_ids、derived_from_version_id、generation_mode、model、resolution、is_stale、requirement_signature。

旧 standard_views 记录仍可读取；迁移后仅作为历史预览，不自动当作已确认母版，也不再作为新视频任务参考。

## 统一提示词与旧路径删除

唯一生成入口是：

~~~python
compose_asset_prompt(
    state,
    asset_type="prop",
    asset_id=prop_id,
    view_type=view_type,
    asset=asset,
    custom_instructions=custom_instructions,
)
~~~

批量生成、卡片生成、弹窗重生成和兼容接口都必须调用该入口。

删除或改为纯转发的内部旧路径：

- src/api/routes/assets.py 的 _legacy_regenerate_image_asset；
- ImageGenerationAgent.generate_props；
- ImageGenerationAgent._generate_prop_image；
- 任何直接调用 image_gen_agent.api_client 的道具生成逻辑。

现有 HTTP 兼容接口保留，但统一转发到 src/services/asset_generation.py。

## 统一 seed

新增唯一函数：

~~~python
def derive_asset_seed(
    project_id: str,
    asset_type: str,
    asset_id: str,
    view_type: str,
    prompt_template_version: int,
) -> int: ...
~~~

规则：

1. 拼接规范化字段；
2. 使用 SHA-256；
3. 转成供应商允许范围内的整数；
4. 首次生成和失败重试保存该 seed；
5. 用户主动重新生成传入 seed=None，后端生成新随机 seed；
6. 任务和图片版本保存最终 seed。

人物、场景、道具和所有旧兼容入口统一调用此函数，移除 sum(ord(...)) 等旧算法。稳定 seed 只保证首次生成可追溯，不限制用户重新生成，也不能替代参考母版。

## API 与前端

新增接口：

- GET /api/projects/{project_id}/props/{prop_id}/view-requirements
- PUT /api/projects/{project_id}/props/{prop_id}/view-requirements
- POST /api/projects/{project_id}/props/{prop_id}/master/generate
- POST /api/projects/{project_id}/props/{prop_id}/master/confirm
- POST /api/projects/{project_id}/props/{prop_id}/views/{view_type}/generate

保留并兼容：

- /api/projects/{project_id}/assets/prop/{prop_id}/prompt
- /api/projects/{project_id}/assets/prop/{prop_id}/generate
- /api/projects/{project_id}/prop-image-prompt
- /api/props

道具卡片显示关键原因、证据摘要、绑定镜头、母版状态、所需视图和过期状态。编辑器显示单个视图和历史版本。联系页只能作为缩略图。

视频参考选择顺序：

1. 当前镜头指定且未过期的独立视图；
2. 当前道具已确认的母版；
3. 旧 standard_views 只作为兼容预览，不作为新视频任务参考。

## 错误与并发

- 母版未确认：409，并提示先生成并确认道具母版；
- 当前模型不支持参考组图：409；
- 组图返回数量不符：502，不能激活部分结果；
- 生成期间母版或视图需求变化：任务标记过期，不能覆盖新版本；
- 项目切换后旧任务结果不能写入当前项目；
- 删除道具时保留历史文件，但从活动索引隐藏。

## 测试验收

后端覆盖：

- 8 件合格关键道具不会被数量上限截断；
- 单镜头道具、普通家具、环境状态和一次性物品会被删除；
- 连续性道具在至少两个镜头清晰出现时保留；
- critical_evidence 缺失时删除并记录原因；
- stale shot_ids 可由绑定确定性修复；
- 母版未确认时组图返回 409；
- 组图使用母版参考，部分失败不激活；
- 单视图重生成只更新目标视图；
- 视频参考不再传 2×3 联系页；
- 人物、场景、道具首次 seed 可复现，主动重生成 seed 改变；
- 旧 API 与新服务的供应商、提示词和版本快照一致。

前端覆盖：

- 卡片不再只显示不可编辑的拼图；
- 用户可以确认母版、补齐所需视图、单视图重生成和切换历史；
- SSE 只更新当前道具卡片，不重建编辑器；
- 旧 standard_views 可以查看但不会被新视频任务选为参考。

## 非目标

- 首期不为开封、破损、沾血等状态生成独立图片；
- 不强制所有道具生成固定六视图；
- 不引入新的图片供应商；
- 不拆分 studio.html 或 api_server.py。

