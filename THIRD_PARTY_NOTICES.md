# 第三方依赖许可证声明

本项目依赖的下述第三方库，其权利归各自作者所有。许可证信息以各依赖官方仓库为准；
正式发布前建议用 `pip-licenses` 复核一遍（`pip install pip-licenses && pip-licenses --format=markdown`）。

| 依赖 | 许可证 | 说明 |
|---|---|---|
| `pynput` | **LGPL-3.0-only** | 鼠标/键盘监听。可闭源分发，但须显著声明使用、随附 GPL+LGPL 文本，**且不得限制使用者修改或替换该库**（因此防篡改/反调试措施不得作用于它） |
| `easyocr` | Apache-2.0 | OCR 识别 |
| `opencv-python` | MIT（内含 Apache-2.0 的 OpenCV 二进制） | 图像处理 |
| `pyautogui` | BSD-3-Clause | 屏幕输入模拟 |
| `pyperclip` | BSD-3-Clause | 剪贴板 |
| `Pillow` | HPND | 截图处理 |
| `numpy` | BSD-3-Clause | 数值计算 |
| `openai` | Apache-2.0 | LLM 客户端（可接任意 OpenAI 兼容接口） |
| `python-dotenv` | BSD-3-Clause | 读取 `.env` |
| `pygetwindow`（仅 Windows） | MIT | 窗口激活 |

其中 LGPL-3.0 的全文可参见：<https://www.gnu.org/licenses/lgpl-3.0.html>

> 本项目自身代码在 Apache-2.0 下授权，与上述依赖的许可证兼容。
