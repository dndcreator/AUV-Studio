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

本轮完成的是使用路径、基础渲染器和可消费的展示协议。**尚未实现自然语言自动产出整艘飞船几何、自动为每次行动生成动画指令或自动调用文生图。** Planning 暂不强制生成展示 Schema，以免重新扩大规划输出、增加耗时与费用。后续可在已有确认草案中接入独立、可选的 presentation producer；其输出必须通过此协议校验，且不具备世界状态写入权。

## 验证

普通前端测试覆盖首页到草案、确认启动、导演干预、停止、应用进编辑器、角色修订重新编译、Key 配置、研究证据、人工决策和规划异常。SceneFrame 测试覆盖私有认知过滤、输入不变性、版本回退、引用与资源校验。React Flow 在流程测试中替换为轻量画布，因此这些测试不认证真实拖拽、布局或文学质量；仍需浏览器检查与另行授权的真实模型验收。
