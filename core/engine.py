"""应用编排：回复流程 + 主循环（把各模块串起来）。

这里的职责是把识别(wechat_ui)、配置(config)、持久化(chat_history/memory)、
AI(ai_client)、平台操作(platform) 组装成完整业务，不包含具体算法实现。
"""

import random
import re
import sys
import time

import easyocr
from pynput import mouse

from . import ai_client
from . import chat_history
from . import config
from . import memory
from . import platform
from . import stickers
from . import vision
from . import wechat_ui
from .utils import clamp_region, compare_images, to_screen_point

MAX_SEND_ATTEMPTS = 3  # 单条回复的最大发送尝试次数（每次发送后 OCR 核验是否真的发出）

# 括号内的状态/动作描述（如「（微笑）」「(想了想)」），发送前整体删除
_PAREN_STATUS_RE = re.compile(r"[（(][^（()）]*[)）]")

# 分句发送的句间随机延时范围（秒）：模拟真人逐句打字发送的节奏
SENTENCE_DELAY_RANGE = (1.0, 3.0)


def strip_status_descriptions(text):
    """删除回复文本里括号中的状态/动作描述（如「（微笑）」「(开心)」）。"""
    return _PAREN_STATUS_RE.sub("", text or "")


def split_reply_sentences(text):
    """按句号把回复拆成多句：句末句号不保留、去首尾空白、丢弃空句。"""
    sentences = [p.strip() for p in (text or "").split("。")]
    return [s for s in sentences if s]


class RegionSelector:
    """监听鼠标点击，采集框选区域的对角点坐标。"""

    def __init__(self, max_clicks=4):
        self.clicks = []
        self.max_clicks = max_clicks

    def on_click(self, x, y, button, pressed):
        if pressed:
            x, y = int(x), int(y)
            self.clicks.append((x, y))
            print(f"已捕获坐标：({x}, {y})")
            if len(self.clicks) >= self.max_clicks:
                return False


def build_reply_system_prompt(contact):
    """回复用的 system prompt：人设 +（可选）长期记忆 +（可选）表情包使用说明。"""
    persona = config.get_system_prompt(contact)
    suffixes = [stickers.sticker_prompt_suffix()]
    if config.get_settings().memory_inject_reply:
        summary, _updated_at, _analyzed_through = memory.get_contact_memory(contact)
        if summary:
            suffixes.append(
                "你对这位联系人的已有了解（仅供自然参考，相关时自然用到，不要刻意复述）：\n"
                + summary)
    ret = persona + "".join("\n\n" + s for s in suffixes if s)
    # print("发给AI的所有数据：" + ret)

    return ret


def _verify_switched(list_region, expect_y, row_height):
    """点击切换会话后，核验当前高亮行确实变成了目标行。

    重新截取联系人列表区域并检测高亮行：高亮行中心与目标行（角标中心
    expect_y）距离超过 60% 行高，即视为「没切过去 / 切错了行」。
    高亮检测失败时保守返回 False，让上层重试或放弃——
    宁可不回复，也绝不向错误的会话窗口回复。
    """
    try:
        img = platform.screenshot(region=list_region)
        y, _band = wechat_ui.detect_selected_row_y(img)
    except Exception as e:
        print(f"核验截图/检测失败：{e}")
        return False
    if y is None:
        return False
    return abs(y - expect_y) <= row_height * 0.6


def handle_badges(reader, list_img, list_region):
    """处理联系人列表的未读角标：白名单内的联系人则点击切换到该会话。

    返回被点击切换到的联系人名称；没有切换则返回 None。

    点击可靠性（曾出现「识别对了却激活相邻会话」的问题）：
      - 角标在头像右上角、位置贴近行的顶部边缘，直接以角标中心作为点击 y，
        在框选区域有轻微偏移/行高有误差时会点到紧邻的行。因此点击
        **行的垂直中部**（离上下行边界最远，容错最大）。
      - 点击后重新截图核验高亮行确实变成了目标行；未切换成功则重试一次，
        仍失败则放弃本轮（返回 None），绝不向错误的窗口回复。
    """
    badges = wechat_ui.detect_red_badges(list_img)
    if not badges:
        return None
    print(f"\n检测到 {len(badges)} 个未读角标，识别发信人...")
    selected_y, selected_band = wechat_ui.detect_selected_row_y(list_img)
    row_height = wechat_ui.estimate_row_height(list_img, list_region, selected_band)
    for bx, by, bw, bh in badges:
        # 角标所在行如果就是当前高亮的会话，无需切换
        if selected_y is not None and abs(by - selected_y) <= row_height * 0.6:
            print("角标所在行就是当前会话，跳过点击")
            continue
        # 角标中心 ≈ 名字行中心（都在行的上半部），直接作为名字行中心传入
        matched, texts = wechat_ui.identify_contact_at(reader, list_img, by, row_height)
        if not matched:
            print(f"未读消息来自「{''.join(texts) or '未知'}」，不在白名单内，不回复")
            continue
        print(f"白名单联系人「{matched}」发来新消息，点击切换会话...")
        # 点击该行的垂直中部（角标中心 + 1/4 行高 = 行中心），
        # 避开最左侧头像防止点开资料页；行中部离行边界最远，坐标容错最大
        row_center_y = by + row_height * 0.25
        px, py = to_screen_point(list_region, list_img, list_img.width * 0.5,
                                 row_center_y)
        print(f"点击屏幕坐标 ({px:.0f}, {py:.0f})")
        for attempt in (1, 2):
            platform.click(px, py)
            time.sleep(1.0)
            if _verify_switched(list_region, by, row_height):
                return matched
            print(f"第 {attempt} 次点击后高亮行不在目标行"
                  f"（目标行 y≈{by}），重试...")
        print(f"「{matched}」所在会话未能确认激活，本轮跳过回复"
              f"（避免向错误窗口发送）")
        return None
    return None


class WechatBot:
    """自动回复机器人：维护会话状态并驱动主循环。"""

    def __init__(self, reader):
        self.reader = reader
        self.base_by_contact = {}    # 联系人 -> 该会话基线截图（用于像素变化门控）
        self.current_contact = None  # 当前打开的会话联系人
        self.last_selected_y = None  # 上一轮识别到的高亮行 y，用于判断是否需重新 OCR
        # 联系人 -> (朋友消息, 已生成但未确认发出的回复文本)。
        # 回复是否发出以聊天窗口 OCR 为准，未确认发出的保留在此，下一轮重发。
        self.pending_replies = {}  # 联系人 -> (朋友消息, 已生成回复文本, 表情情绪或 None)

    def auto_reply(self, contact, new_msg, chat_region):
        """带上下文的自动回复：读记录 -> 调 AI -> 发送 -> OCR 核验确实发出。

        「是否已回复」以聊天窗口实际显示为准（最后一条是否变成我方气泡），
        而不是本地聊天记录——粘贴发送可能因输入焦点不在输入框而静默失败，
        此时聊天窗口最后一条仍是对方消息，需重试。
        返回 (回复文本, 是否确认发送成功)。
        """
        history = chat_history.load_history(contact)
        context_messages, n_ctx = ai_client.build_context_messages(new_msg, history)
        persona_key = config.get_persona_key(contact)
        print(f"[{contact}] 人设「{persona_key}」，携带 {n_ctx} 条历史上下文"
              f"（记录共 {len(history)} 条）")

        if config.get_settings().dry_run:
            response_msg = f"[测试模式] 收到：{new_msg}"
            response_msg, tag = stickers.parse_emotion_tag(response_msg)
            response_msg = strip_status_descriptions(response_msg)
            print(f"[{contact}] AI回答：{response_msg}")
            print(f"[{contact}]（测试模式）将按 {len(split_reply_sentences(response_msg))} 句分时发送")
            chat_history.append_history(contact, "friend", new_msg)
            chat_history.append_history(contact, "self", response_msg)
            if tag:
                print(f"[{contact}]（测试模式）将发送表情包：{tag}")
            return response_msg, True

        # 上次生成的回复未确认发出、且还是同一条朋友消息 -> 直接重发，不重复调 AI
        pending = self.pending_replies.get(contact)
        if pending and pending[0] == new_msg:
            response_msg, sticker_emotion = pending[1], pending[2]
            print(f"[{contact}] 上次回复未确认发出，重发同一回复")
        else:
            response_msg = ai_client.chat(
                build_reply_system_prompt(contact), context_messages)
            # AI 可能按提示词在末尾附 [表情:情绪] 标记：解析后文本、表情分开发送
            # print("AI回答原始内容：" + response_msg)
            
            response_msg, sticker_emotion = stickers.parse_emotion_tag(response_msg)
            # 删除括号里的状态/动作描述，只保留真正要发出去的话
            response_msg = strip_status_descriptions(response_msg)
            if sticker_emotion:
                print(f"[{contact}] AI回答：{response_msg} ＋表情包[{sticker_emotion}]")
            else:
                print(f"[{contact}] AI回答：{response_msg}")

        # AI 只回了表情没回文字：直接发表情包，不发送空文本
        if not response_msg and sticker_emotion:
            chat_history.append_history(contact, "friend", new_msg)
            stickers.send_sticker(sticker_emotion)
            return f"[表情包:{sticker_emotion}]", True

        # 先记朋友的消息；我方回复等核验确实发出后再记录
        chat_history.append_history(contact, "friend", new_msg)

        def _after_text_sent():
            """文本确认发出后：记录我方消息并补发表情包（若有）。"""
            chat_history.append_history(contact, "self", response_msg)
            self.pending_replies.pop(contact, None)
            if sticker_emotion:
                stickers.send_sticker(sticker_emotion)

        # 按句号拆成多句，逐句分时发送（句间随机延时 1~3 秒，句末句号不保留）。
        # 拆分/发送/句间等待全程在本方法内同步完成——期间主循环不会去检查
        # 其它联系人的新消息、也不会切换激活别的聊天窗口，全部发完才返回。
        sentences = split_reply_sentences(response_msg) or [response_msg]
        if len(sentences) > 1:
            print(f"[{contact}] 回复拆分为 {len(sentences)} 句，逐句分时发送")

        for attempt in range(1, MAX_SEND_ATTEMPTS + 1):
            for i, sentence in enumerate(sentences, 1):
                platform.send_message(sentence)
                if len(sentences) > 1:
                    print(f"[{contact}] 已发送第 {i}/{len(sentences)} 句：{sentence}")
                if i < len(sentences):
                    delay = random.uniform(*SENTENCE_DELAY_RANGE)
                    print(f"[{contact}] 等待 {delay:.1f} 秒后发送下一句...")
                    time.sleep(delay)
            print(f"等待界面稳定后核验发送结果（第 {attempt}/{MAX_SEND_ATTEMPTS} 次）...")
            time.sleep(1.5)
            verify_img = platform.screenshot(region=chat_region)
            last_msg, side = wechat_ui.get_last_message_with_side(
                wechat_ui.read_results(self.reader, verify_img), verify_img)
            if side == "self":
                _after_text_sent()
                return response_msg, True
            if side == "friend" and last_msg != new_msg:
                # 对方在我方发送后又发来新消息：回复大概率已发出，
                # 新消息交由下一轮主循环正常回复，避免重发造成重复
                print(f"[{contact}] 核验时发现对方又发了新消息，视为回复已发出")
                _after_text_sent()
                return response_msg, True
            print(f"[{contact}] 聊天窗口最后一条仍是对方消息，本次发送可能未成功"
                  f"（输入焦点可能不在聊天输入框）")
        self.pending_replies[contact] = (new_msg, response_msg, sticker_emotion)
        return response_msg, False

    def run(self, list_region, chat_region, stop_event=None):
        """主循环：每轮截图 -> 处理未读角标 -> 识别当前会话 -> 监控聊天区并回复。

        stop_event（可选）传入 threading.Event，外部 set() 后本轮处理完即退出，
        供 GUI 的「停止」按钮使用；命令行不传则一直循环。
        """
        while not (stop_event is not None and stop_event.is_set()):
            try:
                # 每轮检测前清空剪贴板，避免上一次复制发送的内容残留在系统剪贴板
                platform.clear_clipboard()
                # 每轮重新读配置：改了 personas.json 无需重启脚本
                whitelist = config.get_whitelist()
                list_screenshot = platform.screenshot(region=list_region)

                # 1) 联系人列表：检测红点角标，白名单内则点击切换到该会话
                switched_to = handle_badges(self.reader, list_screenshot, list_region)

                # 点击后聊天窗口已切换，此时再截聊天区域
                chat_screenshot = platform.screenshot(region=chat_region)

                # 2) 确定当前会话联系人（切换过就用切换到的名字，否则按高亮行识别）
                if switched_to:
                    self.current_contact = switched_to
                    self.last_selected_y = None
                else:
                    selected_y, selected_band = wechat_ui.detect_selected_row_y(list_screenshot)
                    # 只有高亮行位置发生明显变化（或还没识别出联系人）时才做列表 OCR
                    need_ocr = (self.current_contact is None
                                or (selected_y is not None
                                    and (self.last_selected_y is None
                                         or abs(selected_y - self.last_selected_y) > 8)))
                    if need_ocr:
                        row_height = wechat_ui.estimate_row_height(
                            list_screenshot, list_region, selected_band)
                        self.current_contact = wechat_ui.identify_contact_at_y(
                            self.reader, list_screenshot, selected_y, row_height,
                            fallback=self.current_contact)
                    if selected_y is not None:
                        self.last_selected_y = selected_y

                if self.current_contact is None:
                    print("无法识别当前会话联系人（高亮行检测失败），跳过本轮")
                    time.sleep(2)
                    continue

                if config.whitelist_active() and self.current_contact not in whitelist:
                    print(f"当前联系人「{self.current_contact}」不在白名单内，不回复")
                    time.sleep(2)
                    continue

                base_screenshot = self.base_by_contact.get(self.current_contact)

                # 3) 只有聊天区像素真的变了才做 OCR（easyocr 很慢，截图比对很便宜）
                if base_screenshot is not None and not compare_images(base_screenshot, chat_screenshot):
                    print(f"\n[{self.current_contact}] 聊天区无变化，跳过 OCR")
                    time.sleep(2)
                    continue

                # 4) 识别最后一条消息及其发送方（靠左=朋友，靠右/绿气泡=我方）
                ocr_results = wechat_ui.read_results(self.reader, chat_screenshot)
                last_msg, side = wechat_ui.get_last_message_with_side(
                    ocr_results, chat_screenshot)

                # 4.5) 表情包检测：只找「最后一条文字下方、靠左」的内容块，
                #      即朋友发的表情包（我方内容靠右、文字气泡已被 OCR 读到，
                #      都不会命中靠左判定）。命中后走视觉分析理解含义再回复。
                sticker_bbox = wechat_ui.detect_sticker_region(
                    chat_screenshot, ocr_results)
                if sticker_bbox is not None:
                    sx, sy, sw, sh = sticker_bbox
                    sticker_img = chat_screenshot.crop(
                        (sx, sy, sx + sw, sy + sh))
                    desc, _emotion = vision.analyze_sticker(sticker_img)
                    last_msg = "[表情包]" + (f"（{desc}）" if desc else "")
                    side = "friend"
                    print(f"\n[{self.current_contact}] 朋友发来表情包"
                          f"{('：' + desc) if desc else '（未配置视觉模型，按普通表情处理）'}")

                if side == "friend" and last_msg:
                    # 回复与否只看聊天窗口实际显示：最后一条是对方消息就是还没回复
                    # （不依赖本地记录判断，发送失败时窗口不会变化，自然会重试）
                    print(f"\n[{self.current_contact}] 朋友发来新消息：{last_msg}")
                    _response, sent = self.auto_reply(
                        self.current_contact, last_msg, chat_region)
                    if sent:
                        # 回复后顺手更新该联系人的长期记忆（有新增记录时才调 AI）
                        memory.update_contact_memory(self.current_contact)
                        print("等待界面稳定...")
                        time.sleep(1.5)
                        self.base_by_contact[self.current_contact] = platform.screenshot(
                            region=chat_region)
                    else:
                        # 发送未确认成功：不更新基线，下一轮强制 OCR 复核并自动重试
                        self.base_by_contact[self.current_contact] = None
                        print(f"[{self.current_contact}] 回复未确认发出，下一轮将自动重试")
                else:
                    if side == "self" and last_msg:
                        # 记录我方手动发送的消息（AI 回复已在 auto_reply 里记录，去重由 append 处理）
                        chat_history.append_history(self.current_contact, "self", last_msg)
                    side_desc = {"self": "我方消息", "unknown": "非消息文本（时间/系统提示）",
                                 "friend": "（无文本）"}.get(side, side)
                    print(f"\n[{self.current_contact}] 最后一条为{side_desc}，无需回复")
                    self.base_by_contact[self.current_contact] = chat_screenshot
                time.sleep(2)

            except Exception as e:
                err_str = str(e)
                if "screencapture" in err_str or "non-zero exit" in err_str:
                    print("截图失败：很可能是「屏幕录制」权限被关闭/过期，"
                          "请到 系统设置 > 隐私与安全性 > 屏幕录制 重新授权并重启终端。")
                    if not platform.check_screen_permission():
                        sys.exit(1)
                else:
                    print(f"处理过程中发生错误: {e}")
                time.sleep(2)


def create_reader(languages=("ch_sim", "en")):
    """创建（较慢的）OCR reader，只在启动时调用一次。"""
    return easyocr.Reader(list(languages))


def select_regions():
    """交互式框选联系人列表区域与聊天窗口区域，返回 (list_region, chat_region)。"""
    print("\n【第 1 步】请框选左侧【联系人列表】区域（点击两个对角点，左上/右下）...")
    selector = RegionSelector(max_clicks=4)
    with mouse.Listener(on_click=selector.on_click) as listener:
        listener.join()

    if len(selector.clicks) < 4:
        print("错误：需要点击 4 个点（列表 2 个 + 聊天窗口 2 个）！")
        sys.exit(1)

    size = platform.screen_size()
    list_region = clamp_region(*selector.clicks[0], *selector.clicks[1], size)
    print("\n【第 2 步】已记录联系人列表区域。")
    chat_region = clamp_region(*selector.clicks[2], *selector.clicks[3], size)
    print(f"联系人列表区域: {list_region}")
    print(f"聊天窗口区域:   {chat_region} （屏幕 {size[0]}x{size[1]}）")
    return list_region, chat_region


def print_whitelist_status():
    """启动时打印白名单与联系人→人设对应关系。"""
    whitelist = config.get_whitelist()
    if whitelist:
        cfg = config.load_persona_config()
        print(f"白名单已启用（{len(whitelist)} 人，配置文件: {config.CONFIG_PATH}）：")
        for name in whitelist:
            pkey = cfg["contacts"].get(name) or cfg["default_persona"]
            pname = (cfg["personas"].get(pkey) or {}).get("name", pkey)
            print(f"  - {name}  ->  人设「{pname}」({pkey})")
    elif config.whitelist_active():
        print("白名单模式已开启但名单为空：不会自动回复任何联系人"
              "（可在 GUI「白名单配置」里勾选联系人）。")
    else:
        print("提示：未启用白名单，将自动回复所有联系人的消息（均使用默认人设）。")
