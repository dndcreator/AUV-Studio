# 前端使用路径与场景展示协议

更新：2026-11-01，北京时间。

## 使用路径

首页以需求输入和最近项目为主。用户生成场景后进入工作区：中央是场景，右侧确认模式、查看参与者或事件，底部与导演交流。草案不会立即覆盖当前工作流，也不会自动启动付费运行。

确认并开始：应用草案，保存工作流，调用原运行 API。应用并编辑：只应用，不启动，进入原 React Flow 编辑器。调整模式或角色定义会重新编译并再次要求确认，避免只改显示字段而保留旧身份提示词。研究模式继续使用证据上传确认，运行期间的人工决策直接在右栏处理。

项目菜单保留保存、导入导出、模板、手动搭建和高级入口。完整节点参数、模型连接、环境与背景、Memory、Dynamic State、监控、队列、Trace、Audit、回滚、报告与媒体生成继续使用原功能与 API。没有迁移或删除运行数据。

## 模块边界

- `components/journey/`：首页、导航、导演输入、角色/事件侧栏。
- `scene/presentation.ts`：只读展示协议校验，以及 Workflow/Run Events 到 SceneFrame 的投影。
- `scene/SceneViewport.tsx`：渲染器入口、关系网络和空间分区的内置实现。
- `styles/journey.css`：这条使用路径的布局与主题消费，不负责运行语义。
- 原编辑器和服务端调用仍由 App 集成；本轮未宣称已拆完 App 中所有历史高级面板。

## 展示协议 v1

配置保存在既有 Workflow JSON 的 `environment.simulation.presentation`，随项目、模板、JSON 导入导出保存，不增加另一份世界状态。

```json
{
  "version": 1,
  "renderer": "spatial",
  "theme": {
    "background": "#111827",
    "surface": "#243044",
    "ink": "#F3F4F6",
    "accent": "#EAB308"
  },
  "regions": [
    {"id": "bridge", "label": "舰桥", "x": 20, "y": 20, "width": 550, "height": 400}
  ],
  "placements": [
    {"node_id": "agent_1", "region_id": "bridge", "x": 30, "y": 70}
  ],
  "assets": []
}
```

分区内的 placements 使用相对坐标；没有 region_id 则使用场景坐标。无位置的实体放到独立的备用区域，不猜测它实际在哪。布局默认 network；空间分区为空或协议版本未知时安全回退。颜色只接受六位十六进制值，不执行 HTML、CSS 字符串、脚本或着色器。坐标、数组长度有界，未知实体/分区引用被忽略。

`registerSceneRenderer(id, component)` 供开发者注册额外渲染器；组件接收只读 SceneFrame、selectedId 和 onSelect。未注册的 renderer 回退 network，不从模型文本加载 JS。

## 事件与事实

角色卡的行动来自既有 succeeded 事件的公开 action，不把 intent、observation 或 provider raw 当成公开台词。不通过匹配“死亡”“攻击”等关键词擅自确认结果，也不把画布连线当成已经发生的通信。

分区、图形和颜色是表现元数据，不证明人物位置、死亡或因果结果。实际事件仍以 run_events 为来源，领域事实仍归 Dynamic State。未来的运动、对话、战斗演出应由已提交事件的引用驱动，不能让表现层替模型创造剧情。

## 可选图片层

已有媒体生成依然由用户显式触发，不是创建场景的前置条件。完成的图片可以选择“用作场景背景”，将受控本地资源地址接入 assets。支持背景及角色肖像引用：

```json
{"kind":"background","url":"/api/media/generations/GENERATION_ID/assets/ASSET_ID/content"}
```

肖像另带 `kind=portrait` 和 `node_id`。拒绝模型任意提供的外站跟踪地址、脚本 URL、SVG/data URI 和本地文件路径。

## 当前边界

现在可在场景中显式点击“生成视觉场景”，独立调用文本模型生成颜色、分区及SVG装饰图元。它不是生成草案后的隐式收费步骤。**尚未实现完整飞船美术、每次行动的语义动画编排或自动文生图。** Planning 仍不强制生成展示 Schema，以免重新扩大规划输出。

### Visual Director 技术验证（2026-11-01）

`POST /api/presentation/generate` 接收 `{workflow, run_id?}`，返回严格校验的 v1 展示计划。最多12个分区、80个矩形/圆/线图元、32个绑定；模型输出上限2400 Token，接口总期限90秒，沿用Provider已有的有限传输重试，不因JSON错误无限重生成。未完成、截断、无效引用、脚本颜色等均返回错误，前端保留旧场景。暂不保证精确美元费用。

视觉请求只提供公开场景、实体名称/类型及公共Dynamic State；不提供角色私密定义、API Key、完整日志或私有状态。带run_id时服务端验证它属于该项目，读取最近已提交状态，过滤private概念。没有run_id时可生成静态布局，但不能凭空绑定不存在的状态。

图元字段：`{id,shape:rect|circle|line,x,y,width,height,color,label}`。不接收任意SVG、HTML或可执行样式。石墙、火光、管线和警报灯由模型用相同通用图元组合，不按“地牢/飞船”关键词硬编码主题。

绑定字段：`{kind:location|color,concept_id,target_id,values:{实际值:区域ID或颜色}}`。位置绑定的概念subject_id必须等于角色ID；颜色绑定只影响已声明图元。前端从可见run_events取最新已提交公共状态，仅接受evidence_kind=action且source_refs对应此前已接纳事件的值。私人状态、claim、未提交或无来源状态不驱动画面。回放只看当前轮次已可见事件，因此可恢复历史展示；不再调用LLM。

绑定只消费实际已存在的概念，不创建新领域事实。初始生成时概念可能尚不存在，需要运行产生公共状态后再次显式生成视觉计划才能建立相应绑定；自动增量绑定尚未实现。状态记录的真实性仍依赖现有Action Check/State Coder，视觉层不会把已有系统变成绝对真值判定器。

离线正反例验证了暖色圆形灯与冷色管线两种输出的协议处理，以及移动/断电变色、隐私拒绝、未提交拒绝和回放。它们是固定模型响应，不是实际LLM的主题理解或美术质量证明。真实“地牢求生/太空船故障”双主题对比仍需用户授权模型调用后验收。

## 验证

普通前端测试覆盖首页到草案、确认启动、导演干预、停止、应用进编辑器、角色修订重新编译、Key 配置、研究证据、人工决策和规划异常。SceneFrame 测试覆盖私有认知过滤、输入不变性、版本回退、引用与资源校验。React Flow 在流程测试中替换为轻量画布，因此这些测试不认证真实拖拽、布局或文学质量；仍需浏览器检查与另行授权的真实模型验收。
