# -*- coding: utf-8 -*-
"""连连看（中秋配对游戏）自动解题动作。

感知层：整屏截图 → 自动标定槽位网格 → 颜色掩码判定空格/障碍/问号 → 同类图标聚类
决策层：≤2 拐角连通性判定（棋盘外扩一圈，支持绕行棋盘外侧）
执行层：controller.post_click 点击成对格子；无解时翻开问号块或使用洗牌/提示道具

设计要点
--------
* 每关棋盘的行数/纵向起点都不同（关卡1 为 5 行、关卡3 为 6 行），因此网格由
  「面板定 ROI + 内容投影」自动标定，不依赖硬编码坐标。网格必须覆盖所有方块：
  一旦算歪半格，就会把相隔的空格当成可通行，从而凑出根本不通的假配对。
* 图标种类随关卡从大池中随机抽取，无法预先穷举模板，因此不做模板匹配，改为
  对当前盘面的方块两两做外观相似度聚类（同类图标相似度 >0.9，不同类 <0.6，
  分离间隙极清晰），只求「哪些格子是同一类」。
* 问号块被点击后只是短暂亮起：一旦配对失败就会变回问号。因此翻开后必须立刻
  把亮起的格子和它的同伴消掉，期间不点任何其它格子，否则这次翻开就白费了。
* 金黄底钟表卡（特殊牌）同样按「两张同款配对消除」，但它的底色正好落在普通牌
  与问号牌之间：若不单独判定，会被当成问号块反复空点，最终触发死局判定而提前
  收尾。故在读盘阶段先按底色占比把金卡挑出来，直接视作普通牌参与聚类配对。
* 关卡进度只信游戏右上角自带的两个字段：「关卡N」与「剩余牌数: N」（OCR 读取）。
  牌面读空 + 等固定时长这种本地推断在结算大字、过渡动画期间极不可靠：剩余牌数
  归零即本关清空，关卡号变大即已进入下一关，切换逻辑完全由这两个权威字段驱动。
  「剩余牌数」读不到有两种情形，靠「本轮标定是否成功 + 盘面是否连续稳定」区分：
  仍是正常游戏中（只是烟花/结算特效把读数闪掉）就照常按盘面操作，干等只会浪费
  时间；盘面也不可信（标定为碎片）才是过渡画面，此时只观察不操作，既不能拿去
  配对（会点到空处），也不能拿去判死局（会提前收尾）。而「关卡N」与「剩余牌数」
  双双消失超过 hud_lost_timeout 秒，说明画面已离开棋盘页面，本局结束。
* 进入新关后不能立刻点击：方块生成/掉落动画期间的点击不生效，会让本地盘面模型
  与真实盘面错位，后续配对整体错位。因此以「盘面签名连续两轮完全一致」作为落位
  判据，落位前只观察不点击，比死等固定时长可靠。

参数格式（custom_action_param，全部可省略，缺省见 DEFAULTS）：
{
    "auto_grid": true,             # true=自动标定网格；false=使用下方 origin/rows/cols
    "origin": [175.1, 148.65],     # 第 0 行第 0 列格子中心（auto_grid=false / 标定失败时兜底）
    "step": [71.9, 85.3],          # 列 / 行方向步长
    "rows": 6,
    "cols": 14,
    "search_radius": 27,           # 单格搜索窗半径（抗平移）
    "core_size": 40,               # 聚类核心区边长
    "cluster_threshold": 0.85,     # 同类判定阈值
    "empty_threshold": 0.5,        # 棋盘底色占比 > 该值为空格
    "block_threshold": 0.55,       # 金属挡板占比 > 该值为障碍格
    "special_threshold": 0.28,     # 金黄钟表卡底色占比 > 该值按普通牌参与配对
    "question_template": "question.png",
    "click_delay": 0.15,           # 一对内两次点击的间隔
    "settle_delay": 0.5,           # 消除动画等待
    "rescan_pairs": 5,             # 每消 N 对重新截图校正一次
    "max_rounds": 200,
    "max_prop_clicks": 5,          # 洗牌/提示道具最多点击次数
    "shuffle_button": null,        # [x, y] 洗牌道具按钮，无则留 null
    "hint_button": null,           # [x, y] 提示道具按钮，无则留 null
    "max_levels": 3,               # 关卡总数
    "level_roi": [965, 8, 100, 46],     # 右上角「关卡N」文字区域
    "remain_roi": [1030, 40, 180, 46], # 右上角「剩余牌数: N」文字区域
    "ocr_threshold": 0.3,          # OCR 置信度阈值
    "level_transition_delay": 5.0, # 清空一关后先停留的间隙，避免在过渡动画里误点
    "board_ready_timeout": 30.0,   # 等待新关盘面落位（连续两轮签名一致）的超时
    "hud_lost_timeout": 3.0,       # 关卡 HUD（关卡N + 剩余牌数）双双消失超过此时长 = 本局结束
    "hud_lost_trust_rounds": 2,    # 剩余牌数读不到时，盘面连续稳定的轮数达到该值即视为仍在游戏中
    "wait_level_timeout": 90.0,    # 等待下一关关卡号变化的超时
    "level_poll_interval": 0.5,    # 轮询下一关的间隔
    "clear_confirm_rounds": 3,     # 「本关已清空」需连续确认的轮数，防过渡画面误判
    "max_stall": 6,                # 盘面连续无变化的最大轮数，超过判定死局
    "max_no_progress": 12,         # 连续无解且无可翻问号的轮数上限，超过则收尾重试
    "reveal_reset_interval": 4     # 每 N 轮无进展重置翻开计数，允许重翻问号
}
"""
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JOCR, JRecognitionType

from utils import logger

__all__ = ["OnetConnect"]

# 格子状态
EMPTY = 0  # 空位（可通行）
QUESTION = -1  # 问号块（可点击翻开，占用格子）
BLOCK = -2  # 障碍 / 无法识别（不可点击，不可通行）

# 棋盘底色（浅紫色大圆角矩形）
_BG_LO, _BG_HI = (118, 110, 140), (150, 200, 215)
# 方块米白底 / 问号块橙色
_CREAM_LO, _CREAM_HI = (0, 0, 185), (180, 90, 255)
_ORANGE_LO, _ORANGE_HI = (8, 100, 150), (36, 255, 255)
# 灰褐色金属挡板
_PLATE_LO, _PLATE_HI = (10, 15, 90), (30, 90, 175)
# 金黄钟表卡（特殊牌）底色。注意它的色相与问号牌同为橙黄，靠饱和度区分：
# 金卡实测 S≈132，问号牌 S≈195，取 170 为界最清晰。
_GOLD_LO, _GOLD_HI = (12, 95, 185), (34, 170, 255)

_DIRS = ((-1, 0), (1, 0), (0, -1), (0, 1))

DEFAULTS: Dict[str, object] = {
    "auto_grid": True,
    "origin": [175.1, 148.65],
    "step": [71.9, 85.3],
    "rows": 6,
    "cols": 14,
    "search_radius": 27,
    "core_size": 40,
    "cluster_threshold": 0.85,
    "empty_threshold": 0.5,
    "block_threshold": 0.55,
    "special_threshold": 0.28,
    "question_template": "question.png",
    "click_delay": 0.15,
    "settle_delay": 0.5,
    "rescan_pairs": 5,
    "max_rounds": 200,
    "max_prop_clicks": 5,
    "shuffle_button": None,
    "hint_button": None,
    "max_levels": 3,
    "level_roi": [965, 8, 100, 46],
    "remain_roi": [1030, 40, 180, 46],
    "ocr_threshold": 0.3,
    "level_transition_delay": 5.0,
    "board_ready_timeout": 30.0,
    "hud_lost_timeout": 3.0,
    "hud_lost_trust_rounds": 2,
    "wait_level_timeout": 90.0,
    "level_poll_interval": 0.5,
    "clear_confirm_rounds": 3,
    "max_stall": 6,
    "max_no_progress": 12,
    "reveal_reset_interval": 4,
}


def _imread(path: str) -> Optional[np.ndarray]:
    """cv2.imread 对含中文的路径会静默失败，统一走 imdecode。"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
        if buf.size == 0:
            return None
        return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except Exception:
        return None


@AgentServer.custom_action("OnetConnect")
class OnetConnect(CustomAction):
    """连连看自动解题。"""

    _q_tpl_cache: Dict[str, Optional[np.ndarray]] = {}
    _last_layout: Optional[Dict[str, object]] = None

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        try:
            params = (
                json.loads(argv.custom_action_param)
                if argv.custom_action_param
                else {}
            )
        except Exception as e:
            logger.error(f"OnetConnect 参数解析失败: {e}")
            return CustomAction.RunResult(success=False)

        cfg = {**DEFAULTS, **params}
        q_tpl = self._load_question_template(cfg)
        if q_tpl is None:
            logger.warning("OnetConnect 未找到问号模板，将只用颜色规则判定问号块")

        reveal_tries: Dict[Tuple[int, int], int] = {}
        pending: Optional[Tuple[int, int]] = None
        prop_clicks = 0
        total_pairs = 0
        prev_sig: Optional[tuple] = None
        stall = 0
        cleared_rounds = 0
        empty_streak = 0
        no_progress = 0
        level = 1
        hud_pending: Optional[int] = None
        hud_pending_count = 0
        # 盘面落位闸门：为 True 时只观察、不点击（开局与每次换关都要先过这道门）
        await_ready = True
        ready_sig: Optional[tuple] = None
        ready_deadline = time.time() + float(cfg["board_ready_timeout"])
        # 右上角「剩余牌数」连续读不到时的起始时刻，None 表示当前读得到
        hud_lost_since: Optional[float] = None
        # 「剩余牌数」读不到期间盘面是否稳定：签名与连续一致轮数
        hud_lost_sig: Optional[tuple] = None
        hud_lost_stable = 0

        for rnd in range(1, int(cfg["max_rounds"]) + 1):
            img = self._screencap(context)
            if img is None:
                logger.error("OnetConnect 截图失败，终止")
                break

            hud_level, hud_remain = self._read_hud(context, img, cfg)

            # 关卡号是切换的权威信号：变大即已进入新关，立刻重置本地状态。
            # 要求连续两轮读到同一个新关卡号才采纳，防止 OCR 偶尔误读导致跳关。
            if (
                hud_level is not None
                and hud_level != level
                and 1 <= hud_level <= int(cfg["max_levels"])
            ):
                if hud_level == hud_pending:
                    hud_pending_count += 1
                else:
                    hud_pending = hud_level
                    hud_pending_count = 1
                if hud_pending_count >= 2:
                    logger.info(f"OnetConnect 关卡号 {level} -> {hud_level}，重置关卡状态")
                    level = hud_level
                    stall = 0
                    prev_sig = None
                    pending = None
                    cleared_rounds = 0
                    empty_streak = 0
                    no_progress = 0
                    reveal_tries.clear()
                    hud_pending, hud_pending_count = None, 0
                    await_ready = True
                    ready_sig = None
                    ready_deadline = time.time() + float(cfg["board_ready_timeout"])
            else:
                hud_pending, hud_pending_count = None, 0

            if hud_remain is None:
                if hud_lost_since is None:
                    hud_lost_since = time.time()
            else:
                hud_lost_since = None

            board, crops, layout = self._read_board(img, cfg, q_tpl)
            rows, cols = layout["rows"], layout["cols"]
            groups = self._group_tiles(board, crops, rows, cols, cfg)

            remain = sum(
                1 for r in range(rows) for c in range(cols) if board[r][c] > 0
            )
            questions = [
                (r, c)
                for r in range(rows)
                for c in range(cols)
                if board[r][c] == QUESTION
            ]
            logger.info(
                f"[第{rnd}轮] 关卡{level}(HUD {hud_level}) 剩余牌数HUD={hud_remain} "
                f"网格 {rows}×{cols}{'' if layout.get('valid') else '(沿用上轮)'} "
                f"原点({layout['ox']:.0f},{layout['oy']:.0f}) "
                f"图案 {remain} 个 / {len(groups)} 类, 问号 {len(questions)} 个"
            )

            sig = (
                tuple(sorted((r, c) for r in range(rows) for c in range(cols)
                             if board[r][c] > 0)),
                tuple(sorted(questions)),
            )

            # 「剩余牌数」读不到时先分清两种画面：
            #   A. 关卡过渡（结算大字 / 铺新盘面）：盘面读出来的是正在生成的碎片，
            #      标定多半也失败。此时点击会点到空处、判死局又会提前收尾，只观察不操作。
            #   B. 仍在正常游戏中，只是烟花/结算特效把右上角读数闪掉了：盘面标定成功
            #      且连续多轮完全一致。此时必须照常操作，干等只会白白浪费时间。
            # 判据用「本轮标定是否成功（没沿用上轮网格）+ 盘面签名连续稳定」，
            # 而不是「HUD 读不读得到」。
            if hud_remain is None:
                pending = None
                lost = time.time() - (hud_lost_since or time.time())
                grid_ok = bool(layout.get("valid"))
                hud_lost_stable = hud_lost_stable + 1 if sig == hud_lost_sig else 0
                hud_lost_sig = sig
                if (
                    grid_ok
                    and hud_lost_stable >= int(cfg["hud_lost_trust_rounds"])
                    and remain >= 2
                ):
                    logger.info(
                        f"OnetConnect 第 {level} 关剩余牌数读不到，但盘面已连续稳定"
                        f"（图案 {remain} 个），判定仍在游戏中，按盘面继续操作"
                    )
                    stall = 0
                    prev_sig = None
                else:
                    stall = 0
                    prev_sig = None
                    # 「关卡N」与「剩余牌数」双双读不到、盘面也不可信、且已经开打：
                    # 画面早就不在棋盘页面上（本局关卡都走完了），直接收尾。
                    if (
                        not await_ready
                        and hud_level is None
                        and lost >= float(cfg["hud_lost_timeout"])
                    ):
                        logger.info(
                            f"OnetConnect 关卡 HUD 消失已 {lost:.1f}s，判定本局已结束"
                        )
                        break
                    if lost >= float(cfg["board_ready_timeout"]):
                        logger.warning(
                            f"OnetConnect 第 {level} 关盘面一直不可信 {lost:.1f}s，"
                            f"收尾交回上层流程"
                        )
                        break
                    logger.info(
                        f"OnetConnect 第 {level} 关剩余牌数读不到（已 {lost:.1f}s），"
                        f"判定为过渡画面，暂不操作"
                    )
                    time.sleep(float(cfg["level_poll_interval"]))
                    continue
            else:
                hud_lost_sig = None
                hud_lost_stable = 0

            # 盘面落位闸门：换关后（以及开局）先等方块生成/掉落动画结束再动手。
            # 判据是「同一盘面签名连续两轮完全一致」——动画期间签名每轮都在变，
            # 落位后才会稳定；比死等固定时长可靠，也不会白等。
            if await_ready:
                if remain >= 2 and sig == ready_sig:
                    logger.info(
                        f"OnetConnect 第 {level} 关盘面已落位（图案 {remain} 个），开始操作"
                    )
                    await_ready = False
                    ready_sig = None
                    prev_sig = sig
                    time.sleep(float(cfg["settle_delay"]))
                    continue
                ready_sig = sig
                stall = 0
                prev_sig = None
                if time.time() >= ready_deadline:
                    logger.warning("OnetConnect 等待盘面落位超时，直接开始操作")
                    await_ready = False
                    ready_sig = None
                else:
                    logger.info(f"OnetConnect 等待第 {level} 关盘面落位（当前图案 {remain} 个）")
                    time.sleep(float(cfg["level_poll_interval"]))
                    continue

            # 盘面连续多轮读空、HUD 剩余牌数却一直不报 0：判定 HUD 读数不可信，
            # 本关改按盘面判定清空，避免无限空转。
            empty_streak = empty_streak + 1 if (remain == 0 and not questions) else 0
            if empty_streak >= int(cfg["max_stall"]) and hud_remain != 0:
                logger.warning("OnetConnect 盘面连续读空但 HUD 未报 0，改为按盘面判定")
                hud_remain = None

            # 「本关已清空」以右上角剩余牌数为准，OCR 读不到才退回盘面读空
            empty_signal = (
                hud_remain == 0
                if hud_remain is not None
                else (remain == 0 and not questions)
            )
            if empty_signal:
                cleared_rounds += 1
                if cleared_rounds < int(cfg["clear_confirm_rounds"]):
                    # 单轮读到 0 可能只是消除动画 / 关卡结算大字这类瞬时画面，
                    # 必须连续多轮确认，否则三关还没走完就会提前收尾。
                    logger.info(
                        f"OnetConnect 第 {level} 关疑似清空（确认 "
                        f"{cleared_rounds}/{cfg['clear_confirm_rounds']}）"
                    )
                    time.sleep(float(cfg["level_poll_interval"]))
                    continue
                logger.info(f"OnetConnect 第 {level} 关已清空（剩余牌数 {hud_remain}）")
                if level >= int(cfg["max_levels"]):
                    break
                nxt = self._wait_next_level(context, cfg, q_tpl, level)
                if nxt is None:
                    break
                level = nxt
                stall = 0
                prev_sig = None
                pending = None
                cleared_rounds = 0
                empty_streak = 0
                no_progress = 0
                reveal_tries.clear()
                hud_pending, hud_pending_count = None, 0
                await_ready = True
                ready_sig = None
                ready_deadline = time.time() + float(cfg["board_ready_timeout"])
                logger.info(f"OnetConnect 进入第 {level} 关")
                continue
            cleared_rounds = 0

            # 右上角显示还有牌、本地却读成空盘：正处于关卡过渡或新盘面加载，
            # 耐心等盘面出现，绝不能当成死局收尾。
            if remain == 0 and not questions and hud_remain is not None:
                logger.info(
                    f"OnetConnect 第 {level} 关盘面尚未就绪（HUD 剩余 {hud_remain}），等待"
                )
                stall = 0
                prev_sig = None
                time.sleep(float(cfg["level_poll_interval"]))
                continue

            # 盘面签名变化（且上轮有真实签名）说明有消除/翻开等动作发生，无进展计数归零
            if prev_sig is not None and sig != prev_sig:
                no_progress = 0
            stall = stall + 1 if sig == prev_sig else 0
            prev_sig = sig
            if stall >= int(cfg["max_stall"]):
                if hud_remain is not None and hud_remain > 0:
                    # 右上角明确显示还有牌：只是本地读盘没变化，不该收尾
                    logger.info(
                        f"OnetConnect 盘面读数停滞，但右上角仍显示剩余 {hud_remain} 张牌，继续尝试"
                    )
                    stall = 0
                    continue
                logger.warning("OnetConnect 连续多轮盘面无变化，判定为死局/无可用手段")
                break

            pairs = self._solve(board, rows, cols)

            # 问号牌翻开只是临时状态（配对失败就会变回问号），所以翻开后必须
            # 立刻把它和同伴消掉：期间不点任何其它格子，否则本次翻开白费。
            if pending is not None:
                pr, pc = pending
                if 0 <= pr < rows and 0 <= pc < cols and board[pr][pc] != QUESTION:
                    hit = next((p for p in pairs if pending in p), None)
                    if hit is not None:
                        other = hit[1] if hit[0] == pending else hit[0]
                        logger.info(f"OnetConnect 立即消除翻开后的 {pending} 与 {other}")
                        self._click_cell(context, other, layout, cfg)
                        time.sleep(float(cfg["settle_delay"]))
                        total_pairs += 1
                        no_progress = 0
                        pending = None
                        # 盘面已有进展，之前翻不开的问号可以重新尝试
                        reveal_tries.clear()
                        continue
                    logger.info(f"OnetConnect 翻开后的 {pending} 暂时无解，放弃本次翻开")
                pending = None

            if pairs:
                # 盘面无变化时轮换候选对顺序，跳过反复点不动的坏对
                off = stall % len(pairs)
                order = pairs[off:] + pairs[:off]
                for a, b in order[: int(cfg["rescan_pairs"])]:
                    self._click_cell(context, a, layout, cfg)
                    time.sleep(float(cfg["click_delay"]))
                    self._click_cell(context, b, layout, cfg)
                    time.sleep(float(cfg["settle_delay"]))
                    total_pairs += 1
                no_progress = 0
                continue

            target = next((q for q in questions if reveal_tries.get(q, 0) < 2), None)
            if target is not None:
                reveal_tries[target] = reveal_tries.get(target, 0) + 1
                logger.info(f"无解可消，翻开问号 {target}")
                self._click_cell(context, target, layout, cfg)
                pending = target
                time.sleep(float(cfg["settle_delay"]))
                continue

            btn = cfg.get("shuffle_button") or cfg.get("hint_button")
            if btn and prop_clicks < int(cfg["max_prop_clicks"]):
                prop_clicks += 1
                logger.info(
                    f"无解可消，点击道具 {btn} ({prop_clicks}/{cfg['max_prop_clicks']})"
                )
                context.tasker.controller.post_click(int(btn[0]), int(btn[1])).wait()
                time.sleep(1.0)
                continue

            if hud_remain != 0:
                # 右上角没有明确报「剩余牌数 0」：本地无解多半是识别抖动、盘面
                # 尚未稳定，或本关确实没有可消的对。先用 no_progress 计数兜底，
                # 避免之前 stall/prev_sig 每轮清零导致的死循环。
                no_progress += 1
                # 每过若干轮无进展，重置问号翻开计数，允许重新尝试翻问号
                if no_progress % int(cfg["reveal_reset_interval"]) == 0:
                    reveal_tries.clear()
                if no_progress >= int(cfg["max_no_progress"]):
                    logger.warning(
                        f"OnetConnect 连续 {no_progress} 轮无解（HUD 剩余 "
                        f"{hud_remain}，本地 {remain + len(questions)} 张），"
                        f"判定本关无法自动通关，收尾交回上层重试"
                    )
                    break
                # 本地牌数远少于 HUD 上报：多半是识别漏牌或盘面正在过渡，
                # 打个 warning 便于后续调参，但不据此直接判死。
                if (
                    hud_remain is not None
                    and hud_remain > 0
                    and remain + len(questions) < hud_remain * 0.6
                ):
                    logger.warning(
                        f"OnetConnect 本地牌数({remain + len(questions)}) 远少于 "
                        f"HUD({hud_remain})，可能存在识别漏牌或盘面未稳定"
                    )
                logger.info(
                    f"OnetConnect 本关暂时无解（HUD 剩余 {hud_remain}，"
                    f"无进展 {no_progress}/{cfg['max_no_progress']}），重新读盘"
                )
                prev_sig = None
                stall = 0
                time.sleep(float(cfg["level_poll_interval"]))
                continue

            logger.info("无解且无可用手段，结束")
            break

        logger.info(f"OnetConnect 结束（第 {level} 关），共消除 {total_pairs} 对")
        return CustomAction.RunResult(success=total_pairs > 0)

    # ---------- 感知 ----------

    def _load_question_template(
        self, cfg: Dict[str, object]
    ) -> Optional[np.ndarray]:
        name = str(cfg["question_template"])
        root = Path(__file__).resolve().parents[3]
        candidates = [
            Path.cwd() / "resource" / "image" / "onet_connect" / name,
            root / "assets" / "resource" / "image" / "onet_connect" / name,
            root / "resource" / "image" / "onet_connect" / name,
        ]
        target = next((p for p in candidates if p.is_file()), None)
        if target is None:
            return None
        key = str(target)
        if key not in self._q_tpl_cache:
            im = _imread(key)
            cs = int(cfg["core_size"]) // 2
            self._q_tpl_cache[key] = (
                None
                if im is None
                else cv2.cvtColor(
                    im[
                        im.shape[0] // 2 - cs : im.shape[0] // 2 + cs,
                        im.shape[1] // 2 - cs : im.shape[1] // 2 + cs,
                    ],
                    cv2.COLOR_BGR2GRAY,
                ).astype(np.float32)
            )
        return self._q_tpl_cache[key]

    def _screencap(self, context: Context) -> Optional[np.ndarray]:
        try:
            return context.tasker.controller.post_screencap().wait().get()
        except Exception as e:
            logger.error(f"OnetConnect 截图异常: {e}")
            return None

    def _ocr_number(
        self,
        context: Context,
        img: np.ndarray,
        roi: List[int],
        cfg: Dict[str, object],
    ) -> Optional[int]:
        """OCR 指定 ROI 并取出其中第一段数字；读不到返回 None。

        只解析 all_results 里的原文，不依赖 expected 的过滤语义（过滤命中与否
        不影响 all_results 的内容），因此这里不传 expected。
        """
        try:
            detail = context.run_recognition_direct(
                JRecognitionType.OCR,
                JOCR(
                    roi=(int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])),
                    threshold=float(cfg["ocr_threshold"]),
                ),
                img,
            )
        except Exception as e:
            logger.error(f"OnetConnect OCR 调用异常: {e}")
            return None
        if detail is None:
            return None
        texts: List[str] = []
        for item in list(detail.all_results or []) + list(detail.filtered_results or []):
            text = getattr(item, "text", None)
            if text:
                texts.append(str(text))
        if not texts and detail.best_result is not None:
            text = getattr(detail.best_result, "text", None)
            if text:
                texts.append(str(text))
        m = re.search(r"\d+", "".join(texts))
        return int(m.group()) if m else None

    def _read_hud(
        self, context: Context, img: np.ndarray, cfg: Dict[str, object]
    ) -> Tuple[Optional[int], Optional[int]]:
        """读右上角 HUD：「关卡N」与「剩余牌数: N」，任一读不到即返回 None。"""
        level = self._ocr_number(context, img, list(cfg["level_roi"]), cfg)
        remain = self._ocr_number(context, img, list(cfg["remain_roi"]), cfg)
        return level, remain

    def _wait_next_level(
        self,
        context: Context,
        cfg: Dict[str, object],
        q_tpl: Optional[np.ndarray],
        cur_level: int,
    ) -> Optional[int]:
        """清空一关后等待进入下一关，返回 OCR 读到的关卡号。

        先停留一个可配置的间隙，再轮询右上角「关卡N」：一旦关卡号与当前关不同
        即认为已切换（返回新关卡号）。OCR 读不到关卡号时退回「盘面连续两次稳定
        且非空」的兜底判定（返回 cur_level + 1）；超时返回 None。
        """
        delay = float(cfg["level_transition_delay"])
        logger.info(f"OnetConnect 关卡过渡，停留 {delay}s 后等待关卡号变化")
        time.sleep(delay)

        interval = float(cfg["level_poll_interval"])
        deadline = time.time() + float(cfg["wait_level_timeout"])
        prev: Optional[tuple] = None
        while time.time() < deadline:
            img = self._screencap(context)
            if img is None:
                time.sleep(interval)
                continue
            hud_level, _ = self._read_hud(context, img, cfg)
            if hud_level is not None and hud_level != cur_level:
                logger.info(f"OnetConnect 关卡号推进：{cur_level} -> {hud_level}")
                return hud_level
            if hud_level is None:
                # OCR 兜底：盘面连续两次稳定且非空，按下一关处理
                board, _, layout = self._read_board(img, cfg, q_tpl)
                rows, cols = int(layout["rows"]), int(layout["cols"])
                occupied = tuple(
                    sorted(
                        (r, c)
                        for r in range(rows)
                        for c in range(cols)
                        if board[r][c] > 0
                    )
                )
                sig = (rows, cols, occupied)
                if len(occupied) >= 2 and sig == prev:
                    logger.info(
                        f"OnetConnect 关卡号读不到，按盘面就绪判定进入第 "
                        f"{cur_level + 1} 关（网格 {rows}×{cols}，图案 {len(occupied)} 个）"
                    )
                    return cur_level + 1
                prev = sig
            else:
                prev = None
            time.sleep(interval)

        logger.info("OnetConnect 等待下一关超时，视为全部关卡已完成")
        return None

    def _calibrate(self, img: np.ndarray, cfg: Dict[str, object]) -> Dict[str, object]:
        """反推槽位网格（origin / rows / cols）。

        不用「单个方块连通域聚类」：方块彼此相邻时会粘连、被棋盘底色碎片裁剪后
        质心还会漂移，残局（方块稀疏）时更会把网格算歪——网格一旦偏半格，
        就会把不相邻的空格当成可通行，从而凑出根本不通的假配对。

        改为对「方块 ∪ 挡板」做投影：
        1. 用棋盘底色掩码的大碎片外接框圈定棋盘面板（底色被菱形纹理切碎，
           故取若干大碎片的并集，而非最大单一碎块）；
        2. 在面板内统计「整格高度/宽度」以上的列/行，得到内容边界；
        3. 用固定步长把边界换算成 origin / rows / cols。
        """
        rows, cols = int(cfg["rows"]), int(cfg["cols"])
        ox, oy = float(cfg["origin"][0]), float(cfg["origin"][1])
        sx, sy = float(cfg["step"][0]), float(cfg["step"][1])
        fixed: Dict[str, object] = {
            "ox": ox,
            "oy": oy,
            "sx": sx,
            "sy": sy,
            "rows": rows,
            "cols": cols,
            "valid": False,
        }
        if not cfg.get("auto_grid", True):
            fixed["valid"] = True
            return fixed

        cached = OnetConnect._last_layout
        if cached is None:
            fallback = fixed
        else:
            fallback = dict(cached)
        # 沿用上轮网格时本轮并未真正标定成功，标成不可信，供调用方区分
        # 「盘面读得准但 HUD 读不到」与「整块画面都不可信」两种情形。
        fallback["valid"] = False

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

        # 棋盘面板：底色掩码的大碎片并集
        bg = cv2.inRange(hsv, _BG_LO, _BG_HI)
        bg = cv2.morphologyEx(bg, cv2.MORPH_OPEN, np.ones((9, 9), np.uint8))
        n, _, st, _ = cv2.connectedComponentsWithStats(bg, 8)
        if n < 2:
            logger.error("OnetConnect 未找到棋盘底色区域，沿用上次网格")
            return fallback
        biggest = max(st[i][4] for i in range(1, n))
        big = [i for i in range(1, n) if st[i][4] > 0.15 * biggest]
        px0 = min(int(st[i][0]) for i in big)
        py0 = min(int(st[i][1]) for i in big)
        px1 = max(int(st[i][0] + st[i][2]) for i in big)
        py1 = max(int(st[i][1] + st[i][3]) for i in big)

        # 内容 = 方块 ∪ 挡板，限制在面板内缩范围内（避开面板描边）
        cream = cv2.inRange(hsv, _CREAM_LO, _CREAM_HI)
        orange = cv2.inRange(hsv, _ORANGE_LO, _ORANGE_HI)
        plate = cv2.inRange(hsv, _PLATE_LO, _PLATE_HI)
        content = cv2.bitwise_or(cv2.bitwise_or(cream, orange), plate)
        roi = np.zeros_like(content)
        m = 6
        roi[py0 + m : py1 - m, px0 + m : px1 - m] = 255
        content = cv2.morphologyEx(
            cv2.bitwise_and(content, roi), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8)
        )
        if int(content.sum()) // 255 < 0.3 * sx * sy:
            # 面板内几乎没有内容 => 本关已清空，沿用上次网格即可（全部判空）
            return fallback

        # 列/行密度阈值取整格边长的一定比例，借以滤掉 UI 文字与立绘
        xs = np.nonzero((content.sum(axis=0) // 255) >= 0.75 * sy)[0]
        if len(xs) < 2:
            logger.error("OnetConnect 自动标定失败（列），沿用上次网格")
            return fallback
        x0, x1 = int(xs[0]), int(xs[-1])
        ys = np.nonzero((content[:, x0 : x1 + 1].sum(axis=1) // 255) >= 0.75 * sx)[0]
        if len(ys) < 2:
            logger.error("OnetConnect 自动标定失败（行），沿用上次网格")
            return fallback
        y0, y1 = int(ys[0]), int(ys[-1])

        gcols = int(round((x1 - x0 + 1 - sx) / sx)) + 1
        grows = int(round((y1 - y0 + 1 - sy) / sy)) + 1
        if not (2 <= gcols <= 24 and 2 <= grows <= 24):
            logger.error(f"OnetConnect 自动标定结果异常 {grows}×{gcols}，沿用上次网格")
            return fallback

        layout: Dict[str, object] = {
            "ox": x0 + sx / 2,
            "oy": y0 + sy / 2,
            "sx": sx,
            "sy": sy,
            "rows": grows,
            "cols": gcols,
            "valid": True,
        }
        OnetConnect._last_layout = layout
        return layout

    def _read_board(
        self,
        img: np.ndarray,
        cfg: Dict[str, object],
        q_tpl: Optional[np.ndarray],
    ) -> Tuple[List[List[int]], Dict[Tuple[int, int], np.ndarray], Dict[str, object]]:
        layout = self._calibrate(img, cfg)
        rows, cols = int(layout["rows"]), int(layout["cols"])
        ox, oy = layout["ox"], layout["oy"]
        sx, sy = layout["sx"], layout["sy"]
        sr = int(cfg["search_radius"])
        cs = int(cfg["core_size"]) // 2
        eth, bth = float(cfg["empty_threshold"]), float(cfg["block_threshold"])
        ih, iw = img.shape[:2]

        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        bg = cv2.inRange(hsv, _BG_LO, _BG_HI)
        cream = cv2.inRange(hsv, _CREAM_LO, _CREAM_HI)
        orange = cv2.inRange(hsv, _ORANGE_LO, _ORANGE_HI)
        plate = cv2.inRange(hsv, _PLATE_LO, _PLATE_HI)
        gold = cv2.inRange(hsv, _GOLD_LO, _GOLD_HI)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)

        board: List[List[int]] = [[EMPTY] * cols for _ in range(rows)]
        crops: Dict[Tuple[int, int], np.ndarray] = {}
        for r in range(rows):
            y = int(round(oy + r * sy))
            for c in range(cols):
                x = int(round(ox + c * sx))
                y0, y1, x0, x1 = y - sr, y + sr, x - sr, x + sr
                if y0 < 0 or x0 < 0 or y1 > ih or x1 > iw:
                    board[r][c] = BLOCK
                    continue
                if (bg[y0:y1, x0:x1] > 0).mean() > eth:
                    continue
                if (plate[y0:y1, x0:x1] > 0).mean() > bth:
                    board[r][c] = BLOCK
                    continue
                win = gray[y0:y1, x0:x1]
                # 金黄钟表卡（特殊牌）：底色饱和度介于普通牌与问号牌之间，
                # 必须优先挑出来按普通牌参与配对，否则会被误判成问号块反复空点。
                gf = (gold[y0:y1, x0:x1] > 0).mean()
                if gf > float(cfg["special_threshold"]):
                    board[r][c] = 1
                    crops[(r, c)] = win
                    continue
                if q_tpl is not None:
                    score = self._match(win, q_tpl)
                    if score >= float(cfg["cluster_threshold"]):
                        board[r][c] = QUESTION
                        continue
                of = (orange[y0:y1, x0:x1] > 0).mean()
                cf = (cream[y0:y1, x0:x1] > 0).mean()
                if of > 0.45 and cf < 0.25:
                    board[r][c] = QUESTION
                    continue
                # 先占位，类型留给聚类阶段填写
                board[r][c] = 1
                crops[(r, c)] = win

        return board, crops, layout

    def _group_tiles(
        self,
        board: List[List[int]],
        crops: Dict[Tuple[int, int], np.ndarray],
        rows: int,
        cols: int,
        cfg: Dict[str, object],
    ) -> List[int]:
        """对盘面方块两两做外观相似度聚类，把同类写成同一个正整数类型。"""
        cs = int(cfg["core_size"]) // 2
        sr = int(cfg["search_radius"])
        th = float(cfg["cluster_threshold"])
        items = sorted(crops.keys())
        cores = [crops[k][sr - cs : sr + cs, sr - cs : sr + cs] for k in items]

        parent = list(range(len(items)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                s = max(
                    self._match(crops[items[j]], cores[i]),
                    self._match(crops[items[i]], cores[j]),
                )
                if s >= th:
                    a, b = find(i), find(j)
                    if a != b:
                        parent[a] = b

        groups: Dict[int, List[Tuple[int, int]]] = {}
        for i, key in enumerate(items):
            groups.setdefault(find(i), []).append(key)
        sizes: Dict[int, int] = {}
        for gi, (_, members) in enumerate(
            sorted(groups.items(), key=lambda kv: kv[1][0]), start=1
        ):
            for key in members:
                board[key[0]][key[1]] = gi
            sizes[gi] = len(members)
        counts = sorted(sizes.values(), reverse=True)
        if any(n % 2 for n in counts):
            logger.warning(f"OnetConnect 存在奇数个同类方块（可能有同伴被问号块遮挡）: {counts}")
        return counts

    @staticmethod
    def _match(win: np.ndarray, tpl: np.ndarray) -> float:
        res = cv2.matchTemplate(win, tpl, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(res).max())

    # ---------- 决策 ----------

    def _solve(
        self, board: List[List[int]], rows: int, cols: int
    ) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
        """返回可消除的格子对（格子外坐标），按拐角数升序。"""
        height, width = rows + 2, cols + 2
        grid = [[0] * width for _ in range(height)]
        same: Dict[int, List[Tuple[int, int]]] = {}
        for r in range(rows):
            for c in range(cols):
                v = board[r][c]
                if v == EMPTY:
                    continue
                grid[r + 1][c + 1] = 1
                if v > 0:
                    same.setdefault(v, []).append((r + 1, c + 1))

        used = set()
        found: List[Tuple[Tuple[int, int], Tuple[int, int], int, int]] = []
        for v in sorted(same):
            cells = same[v]
            for i in range(len(cells)):
                if cells[i] in used:
                    continue
                for j in range(i + 1, len(cells)):
                    if cells[j] in used:
                        continue
                    path = self._find_path(grid, cells[i], cells[j])
                    if path:
                        used.add(cells[i])
                        used.add(cells[j])
                        # 优先选择全程不越出棋盘内部（含边界行/列）的路线
                        outside = any(
                            not (1 <= p[0] <= rows and 1 <= p[1] <= cols)
                            for p in path
                        )
                        found.append(
                            (
                                (cells[i][0] - 1, cells[i][1] - 1),
                                (cells[j][0] - 1, cells[j][1] - 1),
                                int(outside),
                                len(path) - 2,
                            )
                        )
                        break
        found.sort(key=lambda item: (item[2], item[3]))
        return [(a, b) for a, b, _, _ in found]

    def _find_path(
        self, grid: List[List[int]], a: Tuple[int, int], b: Tuple[int, int]
    ) -> Optional[List[Tuple[int, int]]]:
        """≤2 拐角连通性，返回折线路径（含端点）。"""
        path = self._link1(grid, a, b)
        if path:
            return path
        for dr, dc in _DIRS:
            rr, cc = a[0] + dr, a[1] + dc
            while (
                0 <= rr < len(grid)
                and 0 <= cc < len(grid[0])
                and grid[rr][cc] == 0
            ):
                sub = self._link1(grid, (rr, cc), b)
                if sub:
                    return [a] + sub
                rr += dr
                cc += dc
        return None

    def _link1(
        self, grid: List[List[int]], a: Tuple[int, int], b: Tuple[int, int]
    ) -> Optional[List[Tuple[int, int]]]:
        if self._straight(grid, a, b):
            return [a, b]
        for corner in ((a[0], b[1]), (b[0], a[1])):
            if grid[corner[0]][corner[1]] != 0:
                continue
            if self._straight(grid, a, corner) and self._straight(grid, corner, b):
                return [a, corner, b]
        return None

    @staticmethod
    def _straight(
        grid: List[List[int]], a: Tuple[int, int], b: Tuple[int, int]
    ) -> bool:
        """同一直线上且中间格全空（不含端点）。"""
        if a == b:
            return True
        if a[0] == b[0]:
            lo, hi = sorted((a[1], b[1]))
            return all(grid[a[0]][c] == 0 for c in range(lo + 1, hi))
        if a[1] == b[1]:
            lo, hi = sorted((a[0], b[0]))
            return all(grid[r][a[1]] == 0 for r in range(lo + 1, hi))
        return False

    # ---------- 执行 ----------

    def _click_cell(
        self,
        context: Context,
        cell: Tuple[int, int],
        layout: Dict[str, object],
        cfg: Dict[str, object],
    ) -> None:
        r, c = cell
        x = int(round(layout["ox"] + c * layout["sx"]))
        y = int(round(layout["oy"] + r * layout["sy"]))
        logger.debug(f"OnetConnect 点击 (r{r},c{c}) -> ({x},{y})")
        context.tasker.controller.post_click(x, y).wait()