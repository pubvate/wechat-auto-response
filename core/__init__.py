"""微信自动回复机器人核心包。

模块职责划分（高内聚、低耦合）：

- config       : 配置加载（.env 环境变量 + personas.json 人设白名单）
- utils        : 与业务无关的纯函数（图像/坐标/时间/文件名）
- platform     : 双平台兼容层（激活窗口、剪贴板、模拟按键、截图、权限检查）
- chat_history : 聊天记录持久化（按联系人分文件）
- ai_client    : AI 接口封装 + 上下文构建
- memory       : 联系人长期记忆（memory.json）的存取与增量分析
- wechat_ui    : 微信界面识别（OCR/红点/高亮行/消息归属/表情包检测）——纯识别，无副作用
- donate       : 「请喝杯咖啡」提示 + 打开微信/支付宝付款码预览（启动/退出各一次）
- stickers     : 表情包（情绪→面板格子映射、[表情:XX] 标记解析与发送）
- vision       : 视觉模型接入（理解对方发来的表情包/图片，带缓存）
- persona_editor: 人设可视化编辑（PersonaStore 纯数据层 + tkinter 编辑窗口）
- sticker_config: 表情包配置窗口（映射编辑 / 面板标定 / 测试发送）
- gui          : tkinter 主界面（框选区域、启停主循环、日志、人设入口）
- engine       : 应用编排（回复流程 + 主循环）
"""

__version__ = "2.0.0"
