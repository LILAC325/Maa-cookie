"""「设定勘查道具」页面自动装填。

需求三步：
1. 先一遍获取道具数量 —— 滚动扫完左侧列表，OCR 每行的道具名称与持有数量；
2. 计算最佳装填逻辑 —— 必装项优先，其余按「效果价值 / 占用格数」性价比
   从高到低贪心，直到勘查包（8x4=32 格）装不下为止；
3. 从上至下依次点击目标道具 —— 回到列表顶部，按行从上到下点「配置」按钮，
   计划装几个就点几次。

依赖前提：本页「配置」按钮点一下即把道具自动摆进勘查包，无需拖拽定位。

坐标基于 1280x720。行位置不写死：每次截图都靠「蓝色配置按钮」的颜色检测
动态求出各行中心（列表滚动后行的 y 会整体平移）。
"""
import json
import os
import re
import time
import traceback
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JOCR, JRecognitionType

from utils import logger

__all__ = ["MineSurveyLoad"]

# 「配置」按钮的蓝色（BGR）
_BTN_B = (170, 255)   # B 下限/上限
_BTN_G = (90, 200)    # G 下限/上限
_BTN_R = (0, 130)     # R 下限/上限
_BTN_DB = 60          # B 至少比 R 高这么多

DEFAULTS = {
    # 道具规格表：key 为名称里的辨识关键词，cells 为在勘查包里占的格数，
    # value 为效果价值（数值越大越值得装），cap 为单次配置上限。
    # 默认 value 取效果描述里的数值，于是「性价比」= 数值 / 格数：
    # 机油 4.44 > 夜视镜 3.33 > 便当 2.78 > 点心 2.5 > 头盔 1.67 > 手杖 1.25 > 收纳箱 0.31
    "items": [
        {"key": "便当", "name": "丰盛的勘查便当", "cells": 9, "value": 25, "cap": 99},
        {"key": "点心", "name": "简便的勘查点心", "cells": 4, "value": 10, "cap": 99},
        {"key": "收纳箱", "name": "奖励收纳箱", "cells": 16, "value": 5, "cap": 99},
        {"key": "头盔", "name": "安全第一勘查头盔", "cells": 6, "value": 10, "cap": 99},
        {"key": "手杖", "name": "坚固的手杖", "cells": 4, "value": 5, "cap": 99},
        {"key": "夜视镜", "name": "圆滚滚夜视镜", "cells": 3, "value": 10, "cap": 99},
        {"key": "机油", "name": "高速机油", "cells": 9, "value": 40, "cap": 1},
    ],
    # 无论性价比都先各装 1 个的道具（机油：总勘查时间-40%）
    "must": ["机油"],
    "pack_cells": 32,             # 勘查包总格数（8 列 x 4 行）
    # 几何（1280x720）：配置按钮只在这一列里找，避开右上角关闭按钮
    "btn_roi": [555, 80, 135, 590],
    "btn_min_area": 1500,         # 蓝色连通域面积门槛
    # 名称/数量都在「配置」按钮左侧（实测量得，按真实像素裁剪验证过）
    "name_roi_rel": [-402, -32, 332, 54],  # 行内道具名称+说明，相对按钮中心
    "count_roi_rel": [-548, 14, 42, 30],   # 行内持有数量（图标右下角白字）
    "list_swipe": [350, 470, 350, 190],    # 列表翻页滑动轨迹
    "swipe_duration": 500,
    "max_scrolls": 4,             # 扫描列表最多往后翻几次
    "max_passes": 4,              # 点击阶段最多翻几次列表
    "reset_swipes": 3,            # 点击前先往回翻几次以回到顶部
    "ocr_threshold": 0.3,
    "click_delay": 0.5,           # 同一行连点「配置」的间隔
    "step_delay": 1.0,            # 点完一行后的稳定时间（等勘查包刷新）
    "debug_dir": "debug/mine_survey",   # 扫描失败时把画面存这里，方便对着像素校准 ROI
}


@AgentServer.custom_action("MineSurveyLoad")
class MineSurveyLoad(CustomAction):
    """设定勘查道具页：按性价比自动装填勘查包。"""

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        # 自定义动作跑在 ctypes 回调里，异常会被框架静默吞掉（agent 日志里看不到任何一行），
        # 所以这里统一兜住并写进日志，避免下次又只能靠猜。
        try:
            return self._run(context, argv)
        except Exception:
            logger.error("MineSurveyLoad 未捕获异常:\n" + traceback.format_exc())
            return CustomAction.RunResult(success=False)

    def _run(
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
            logger.error(f"MineSurveyLoad 参数解析失败: {e}")
            return CustomAction.RunResult(success=False)

        # pipeline 没写 custom_action_param 时框架发来的是字符串 "null"，
        # json.loads 得到 None，直接合并会抛 TypeError
        if not isinstance(params, dict):
            params = {}
        cfg = {**DEFAULTS, **params}

        # 1. 先一遍获取道具数量
        found = self._scan_list(context, cfg)
        if not found:
            logger.warning("MineSurveyLoad 未读到任何道具，跳过装填")
            return CustomAction.RunResult(success=False)
        logger.info(
            "MineSurveyLoad 持有道具：" + "、".join(
                f"{k}x{v}" for k, v in found.items()
            )
        )

        # 2. 计算最佳装填逻辑
        plan, room = self._make_plan(found, cfg)
        if not plan:
            logger.warning("MineSurveyLoad 没能装下任何道具，跳过")
            return CustomAction.RunResult(success=False)
        specs = {str(s["key"]): s for s in cfg["items"]}
        used = int(cfg["pack_cells"]) - room
        logger.info(
            "MineSurveyLoad 装填计划：" + "、".join(
                f"{k}x{n}({int(specs[k]['cells']) * n}格)" for k, n in plan.items()
            )
            + f"，共占 {used}/{int(cfg['pack_cells'])} 格，余 {room} 格"
        )

        # 3. 从上至下依次点击目标道具
        left = self._apply(context, cfg, plan)
        if left:
            logger.warning(
                "MineSurveyLoad 下列道具没点到或数量不足：" + "、".join(
                    f"{k}x{n}" for k, n in left.items()
                )
            )
        return CustomAction.RunResult(success=True)

    # ---------- 感知：列表扫描 ----------

    def _scan_list(self, context: Context, cfg: Dict[str, object]) -> Dict[str, int]:
        """把列表从头翻到尾，收集 {关键词: 持有数量}。

        列表一次只显示 5 行（7 种道具要翻页），行的屏幕 y 会随滚动变化，
        所以每轮都重新检测「配置」按钮求行心；翻到连续一轮没有新道具即停。
        """
        found: Dict[str, int] = {}
        prev_keys: set = set()
        shots: List[np.ndarray] = []
        diag: List[str] = []
        for i in range(int(cfg["max_scrolls"]) + 1):
            img = self._screencap(context)
            if img is None:
                break
            shots.append(img)
            keys = set()
            seen: List[str] = []
            for cx, cy in self._button_rows(img, cfg):
                text, key = self._read_name(context, img, cx, cy, cfg)
                seen.append(f"y={cy}「{text}」")
                if key is None:
                    continue
                keys.add(key)
                n = self._read_count(context, img, cx, cy, cfg)
                if n is not None and n > found.get(key, 0):
                    found[key] = n
                elif key not in found:
                    found[key] = 0
            diag.append(f"第{i + 1}屏 行数={len(seen)} " + " ".join(seen))
            # 整屏一个关键词都没读到就别再翻了（继续翻也不会有收获），
            # 但第一屏全空时仍往后翻，好把各屏画面都存下来定位 ROI
            if keys and not keys - prev_keys:
                break
            prev_keys |= keys
            if i < int(cfg["max_scrolls"]):
                self._scroll(context, cfg, forward=True)
                time.sleep(float(cfg["step_delay"]))
        if not found:
            for line in diag:
                logger.warning("MineSurveyLoad 扫描明细 " + line)
            self._dump(cfg, shots)
        return found

    def _button_rows(
        self, img: np.ndarray, cfg: Dict[str, object]
    ) -> List[Tuple[int, int]]:
        """找出所有「配置」按钮的中心坐标，按上下顺序返回。"""
        x, y, w, h = [int(v) for v in cfg["btn_roi"]]
        x = max(0, x)
        y = max(0, y)
        w = min(w, img.shape[1] - x)
        h = min(h, img.shape[0] - y)
        if w <= 0 or h <= 0:
            return []
        sub = img[y:y + h, x:x + w]
        b = sub[:, :, 0].astype(int)
        g = sub[:, :, 1].astype(int)
        r = sub[:, :, 2].astype(int)
        mask = (
            (b >= _BTN_B[0]) & (b <= _BTN_B[1])
            & (g >= _BTN_G[0]) & (g <= _BTN_G[1])
            & (r >= _BTN_R[0]) & (r <= _BTN_R[1])
            & (b > r + _BTN_DB)
        ).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
        n, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        rows: List[Tuple[int, int]] = []
        for i in range(1, n):
            bx, by, bw, bh, area = stats[i]
            if area < int(cfg["btn_min_area"]):
                continue
            if not (40 <= bw <= 120 and 35 <= bh <= 100):
                continue
            rows.append((x + bx + bw // 2, y + by + bh // 2))
        rows.sort(key=lambda p: p[1])
        return rows

    def _read_name(
        self,
        context: Context,
        img: np.ndarray,
        cx: int,
        cy: int,
        cfg: Dict[str, object],
    ) -> Tuple[str, Optional[str]]:
        """OCR 该行的道具名称，返回 (识别到的原文, 命中的规格表关键词)。"""
        dx, dy, w, h = [int(v) for v in cfg["name_roi_rel"]]
        roi = [cx + dx, cy + dy, w, h]
        text = "".join(self._ocr_texts(context, img, roi, cfg))
        for spec in cfg["items"]:
            if str(spec["key"]) in text:
                return text, str(spec["key"])
        return text, None

    def _read_count(
        self,
        context: Context,
        img: np.ndarray,
        cx: int,
        cy: int,
        cfg: Dict[str, object],
    ) -> Optional[int]:
        """OCR 图标右下角的持有数量；读不到返回 None。"""
        dx, dy, w, h = [int(v) for v in cfg["count_roi_rel"]]
        roi = [cx + dx, cy + dy, w, h]
        for text in self._ocr_texts(context, img, roi, cfg):
            m = re.search(r"\d+", text)
            if m:
                return int(m.group())
        return None

    def _ocr_texts(
        self,
        context: Context,
        img: np.ndarray,
        roi: List[int],
        cfg: Dict[str, object],
    ) -> List[str]:
        """OCR 指定 ROI，返回去重后的文本片段。"""
        roi = [int(v) for v in roi]
        if roi[2] <= 0 or roi[3] <= 0:
            return []
        try:
            detail = context.run_recognition_direct(
                JRecognitionType.OCR,
                JOCR(
                    roi=(roi[0], roi[1], roi[2], roi[3]),
                    threshold=float(cfg["ocr_threshold"]),
                ),
                img,
            )
        except Exception as e:
            logger.error(f"MineSurveyLoad OCR 调用异常: {e}")
            return []
        if detail is None:
            return []
        texts: List[str] = []
        for group in (detail.filtered_results, detail.all_results):
            for item in group or []:
                text = str(getattr(item, "text", "") or "")
                if text and text not in texts:
                    texts.append(text)
        if not texts and detail.best_result is not None:
            text = str(getattr(detail.best_result, "text", "") or "")
            if text:
                texts.append(text)
        return texts

    # ---------- 决策：装填计划 ----------

    def _make_plan(
        self, found: Dict[str, int], cfg: Dict[str, object]
    ) -> Tuple[Dict[str, int], int]:
        """必装项优先，其余按「效果价值 / 占用格数」性价比从高到低贪心填满。

        返回 (计划, 剩余格数)。
        """
        specs: Dict[str, Dict[str, object]] = {
            str(s["key"]): s for s in cfg["items"]
        }
        room = int(cfg["pack_cells"])
        plan: Dict[str, int] = {}

        def take(key: str, want: int) -> int:
            nonlocal room
            spec = specs[key]
            cells = max(1, int(spec["cells"]))
            # 还能装几个 = 持有量、配置上限、剩余格数三者取小（都按已装数量扣减）
            n = min(
                want,
                int(found.get(key, 0)) - plan.get(key, 0),
                int(spec.get("cap", 99)) - plan.get(key, 0),
                room // cells,
            )
            if n <= 0:
                return 0
            plan[key] = plan.get(key, 0) + n
            room -= cells * n
            return n

        for key in cfg.get("must", []):
            key = str(key)
            if key in specs:
                take(key, 1)

        order = sorted(
            (k for k in specs if found.get(k, 0) > 0),
            key=lambda k: -(
                float(specs[k]["value"]) / max(1, int(specs[k]["cells"]))
            ),
        )
        for key in order:
            cells = max(1, int(specs[key]["cells"]))
            while room >= cells and take(key, 1) > 0:
                pass
        return plan, room

    # ---------- 执行：点「配置」 ----------

    def _apply(
        self, context: Context, cfg: Dict[str, object], plan: Dict[str, int]
    ) -> Dict[str, int]:
        """回到列表顶部后从上至下点「配置」，返回没点完的部分。"""
        left = dict(plan)
        for _ in range(int(cfg["reset_swipes"])):
            self._scroll(context, cfg, forward=False)
            time.sleep(float(cfg["step_delay"]))
        for i in range(int(cfg["max_passes"])):
            img = self._screencap(context)
            if img is None:
                break
            for cx, cy in self._button_rows(img, cfg):
                _, key = self._read_name(context, img, cx, cy, cfg)
                if key is None or left.get(key, 0) <= 0:
                    continue
                for _ in range(left[key]):
                    self._click(context, cx, cy)
                    time.sleep(float(cfg["click_delay"]))
                logger.info(
                    f"MineSurveyLoad 已配置 {key} x{left[key]}"
                    f"（点「配置」{left[key]} 次）"
                )
                del left[key]
                time.sleep(float(cfg["step_delay"]))
            if not left:
                break
            if i < int(cfg["max_passes"]) - 1:
                self._scroll(context, cfg, forward=True)
                time.sleep(float(cfg["step_delay"]))
        return left

    # ---------- 基础操作 ----------

    def _dump(self, cfg: Dict[str, object], shots: List[np.ndarray]) -> None:
        """扫描失败时把画面存盘，方便对着真实像素校准 ROI。"""
        out_dir = str(cfg.get("debug_dir") or "")
        if not out_dir or not shots:
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            for i, img in enumerate(shots):
                path = os.path.join(out_dir, f"fail_{stamp}_{i + 1}.png")
                # cv2.imwrite 走不了中文路径，得先编码成内存再写文件
                cv2.imencode(".png", img)[1].tofile(path)
            logger.warning(f"MineSurveyLoad 扫描画面已存到 {out_dir}（共 {len(shots)} 张）")
        except Exception as e:
            logger.error(f"MineSurveyLoad 存图失败: {e}")

    def _screencap(self, context: Context) -> Optional[np.ndarray]:
        try:
            return context.tasker.controller.post_screencap().wait().get()
        except Exception as e:
            logger.error(f"MineSurveyLoad 截图异常: {e}")
            return None

    def _click(self, context: Context, x: float, y: float) -> None:
        try:
            context.tasker.controller.post_click(int(x), int(y)).wait()
        except Exception as e:
            logger.error(f"MineSurveyLoad 点击({x},{y})异常: {e}")

    def _scroll(self, context: Context, cfg: Dict[str, object], forward: bool) -> None:
        """forward=True 往后翻（看更靠后的道具），False 翻回列表顶部。"""
        x1, y1, x2, y2 = [int(v) for v in cfg["list_swipe"]]
        if not forward:
            x1, y1, x2, y2 = x2, y2, x1, y1
        try:
            context.tasker.controller.post_swipe(
                x1, y1, x2, y2, int(cfg["swipe_duration"])
            ).wait()
        except Exception as e:
            logger.error(f"MineSurveyLoad 滑动异常: {e}")
