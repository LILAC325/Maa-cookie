# -*- coding: utf-8 -*-
"""「揭开秘社的秘密」（地底 N 层・挖宝）自动挖掘动作。

感知层：整屏截图 → 标定棋盘网格 → 逐格判定未挖方块 / 已挖空地 / 角色 / 地上立绘
      （道具·宝箱·梯子）/ 拾取弹窗
决策层：以角色所在格为源在可通行格上 BFS，先捡散落道具，再处理宝箱·梯子，最后挖前沿
执行层：点道具槽（出现红叉）→ 点目标方块 → 点绿色对勾确认 → 关拾取弹窗 → 走进新格

盘面几何（1280×720 基准，实测）
--------------------------------
* 棋盘外框约 [363.5, 21.5] ~ [918, 577]，8 列 × 8 行，格边长 ≈ 69.35。
  不同层数棋盘尺寸可能不同，故按「面板底色调 ROI + 边缘尖峰反推格数」自动标定
  （见 _count_cells；旧版按周期逐点采样会把 8×8 读成 5×5），
  标定失败才退回 DEFAULTS 里的硬编码值。
* 格子状态判定：
  - 未挖方块：整格与 image/secret_dig/undug_*.png 做归一化模板匹配，得分 ≥ block_corr
    即判为未挖。三种耐久（1/2/3）各是一套美术，故给三个模板取最大分。
    换用模板匹配是因为**游戏的方块美术整体亮度/对比度会随截图大幅变化**：
    同一块未挖方块在两张截图上内区灰度 std 分别是 40 和 10，任何绝对阈值都会翻车
    （23 层整盘曾被判成地上立绘，脚本于是反复去点一块挖不动的方块）。
    TM_CCOEFF_NORMED 对亮度/对比度线性变化不敏感，跨截图稳定。
    实测 23 层 63 块得分 0.88~1.00、角色格 0.11；22 层未挖 0.58~0.85、已挖 0.29 以下。
  - 角色所在格：橙红占比 ≈ 22~36%（普通格 0%），且不含金色，优先判定
  - 地上立绘（散落道具 / 宝箱 / 梯子）：已挖格上另有立绘，内区 std/mean > item_cv，
    当作可通行处理，走上去游戏会自动拾取
  - 移动方向箭头是画在已挖格上的亮绿三角，按亮绿占比排除
  - 未挖方块模板缺失时退回旧的 std / p99-p50 双阈值（undug_std / undug_glyph）
* 施法范围内的可挖格会被游戏自己描一圈亮绿（实测 RGB≈(86,250,4)，格外圈占比 42%，
  非范围格 0%）。因此「先选道具再截图」就能直接拿到合法目标集合，
  未挖方块的判定只用于寻路，不必绝对精确。
* 点完目标格后会在该格上浮出一个绿色圆盘对勾。定位方式是：绿色掩码 → 腐蚀掉细描边
  → 取最大连通域质心。这样既不会被格子描边带偏，也不会误点角色朝向的绿色三角。
* 未挖方块内容完全不可见，故本动作做的是「从前沿逐格开挖」：开挖顺序由
  「离角色步数 → 离已知目标距离 → 能开出多少新相邻方块」排序，直到挖出宝箱 / 梯子。
* 行动优先级：① 走得到的地上散落道具先捡干净 → ② 已知宝箱 / 梯子直接走过去
  → ③ 宝箱被未挖方块盖住就先开挖 → ④ 挖前沿推进。四类目标都在可通行格上 BFS，
  地上立绘格按可通行处理，走上去游戏会自动拾取。走了第二次仍被当成散落道具的格子
  会拉黑（那是拾不起来的固定立绘）：不拉黑的话「去捡它」和「走到施法位」会互相顶掉，
  角色就在两格之间来回横跳、永不开挖。
* 兜底：整屏画面连续 max_stuck_rounds 轮完全不变即判定卡死并收尾。标定出错时
  整盘会被误判成地上立绘，脚本就会反复去点同一块挖不动的方块：这种「点了没反应」
  的情况盘面签 sig 不变、也走不到无进展分支，必须靠画面签单独兜底。
* 剩余耐久是读出来的而非试出来的：三种耐久各有一套美术，整格模板匹配取 argmax
  的同时就得到了「未挖」与「剩余耐久 1/2/3」（对应关系见 undug_dura），
  于是选道具时能直接算出「这一下有几点伤害打在几点耐久上」。
* 选道具以「道具规格表」tool_specs 为准：每个槽给出伤害、影响范围、施法范围，
  施法范围一律自角色所在格起算。决策时枚举「槽 × 目标格」的全部组合，
  按「本次能挖开的格数 − 溢出伤害 − 空放格数 − 道具档位 − 走位步数」打分取最优：
  耐久 1 的格子只会落到最低档身上（伤害 3 的道具只打到 1 格要额外扣分），
  伤害 3 的道具则留给能一次覆盖多格的落点。伤害可叠加，耐久 3 的格子用伤害 1
  打三次同样能挖开，故「打不满」的格子也按部分推进计分。
  「空放格数」是影响范围里压在已挖空地 / 地上立绘上的格子数，按「少挖一格」计价。
  另外，标称范围 ≥ wide_min_cells 格的槽（槽 2/4 的十字 5 格、槽 5/6 的直线 8 格）
  算「大范围道具」，数量稀少，只在施法范围被未挖方块完全吃满（空放 0 格）时才出手，
  否则让位给范围更小的道具；实在没有满足的候选才退回全体（尽量，不硬禁）。

参数格式（custom_action_param，全部可省略，缺省见 DEFAULTS）：
{
    "auto_grid": true,                  # true=自动标定网格；false=用下方 origin/step/rows/cols
    "board_roi": [363, 21, 555, 557],   # 棋盘所在区域
    "origin": [398.2, 56.2],            # 第 0 行第 0 列格子中心（标定失败时兜底）
    "step": [69.35, 69.35],             # 列 / 行方向格边长
    "rows": 8,
    "cols": 8,
    "period_range": [45, 110],          # 自动标定时搜索的格边长范围
    "grid_score_keep": 0.85,            # 格数打分保留系数，见 _count_cells
    "inner_ratio": 0.72,                # 内区采样边长占整格比例
    "undug_templates": ["undug_a.png", "undug_b.png", "undug_c.png"],  # 未挖方块三种耐久美术
    "undug_tpl_size": 69,               # 上面模板原本的格边长（当前格边长不同会缩放）
    "block_corr": 0.45,                 # 整格归一化模板匹配得分超过即判为未挖方块
    "block_search": 2,                  # 模板匹配时允许的格心取整漂移（像素）
    "item_cv": 0.28,                    # 内区 std/mean 超过即判为地上立绘
    "undug_std": 8.5,                   # 内区灰度 std 超过即判为未挖方块（模板缺失时兜底）
    "undug_glyph": 20.0,                # 内区灰度 p99-p50 超过即判为未挖方块（模板缺失时兜底）
    "orange_ratio": 0.12,               # 格内「非金色暖色」占比超过即判为角色所在格
    "gold_split": 0.72,                 # 暖色像素中 g/r 超过它算金色（宝箱一类的金物）
    "char_gold_max": 0.15,              # 金色占比超过此值就不算角色格
    "item_std": 20.0,                   # 内区 std 超过它且非未挖方块 → 地上有立绘（道具/宝箱/梯子）
    "item_glyph": 40.0,                 # 同上，p99-p50 版阈值
    "item_green_max": 0.06,             # 内区亮绿占比超过它 → 是移动方向箭头，不算可拾取物
    "ring_ratio": 0.15,                 # 格外圈亮绿占比超过即判为「施法范围内」
    "green_min": 150,                   # 亮绿掩码：G 分量下限
    "green_dom": 45,                    # 亮绿掩码：G 比 R/B 至少高这么多
    "red_mask": [185, 105, 80],         # 选中红叉掩码：R 下限 / G 上限 / B 上限
    "select_x_erode": 5,                # 红叉掩码腐蚀核边长（滤掉道具图标自带的细碎红色）
    "select_x_pixels": 40,              # 槽内红叉像素超过即判为已选中
    "target_templates": ["chest.png", "ladder.png"],  # 宝箱 / 梯子模板，缺图只告警不报错
    "target_dir": "image/secret_dig",   # 模板所在子目录
    "match_threshold": 0.72,            # 模板匹配阈值
    "slot_centers": [[351.5, 619.0], [447.5, 619.0], [543.5, 619.0],
                     [639.5, 619.0], [735.5, 619.0], [831.5, 619.0], [927.5, 619.0]],
    "slot_box": 88,                     # 槽位方形边长
    "count_roi_rel": [0.08, 0.62, 0.84, 0.34],  # 槽内数量文字的相对区域
    "max_slot": 6,                      # 可用槽位上限（第 7 槽默认不使用）
    "undug_dura": [3, 1, 2],            # 与 undug_templates 一一对应的剩余耐久
    "tool_specs": [                     # 道具规格表，按槽号顺序（槽号 1 起，第 7 槽不用）
        # dmg=伤害；area=影响范围；reach=施法范围（自角色所在格起算）
        # area: single=单格 / cross=十字5格 / line=直线若干格（朝角色→目标方向）
        #       row=横向直线 / col=竖向直线
        # reach: ring4=上下左右各1格 / ring8=含斜角的8邻格 / any=全盘无限制
        {"dmg": 1, "area": "single", "reach": "ring4"},   # 1 单格 周围四格
        {"dmg": 2, "area": "cross", "reach": "ring8"},    # 2 十字 周围八格
        {"dmg": 3, "area": "line", "reach": "ring4"},     # 3 直线3格 周围四格
        {"dmg": 3, "area": "cross", "reach": "any"},      # 4 十字5格 全盘
        {"dmg": 3, "area": "row", "reach": "any"},        # 5 横向直线8格 全盘
        {"dmg": 3, "area": "col", "reach": "any"}         # 6 竖向直线8格 全盘
    ],
    "tool_line_len": 8,                 # area=row/col 的直线长度（格）
    "tool_line_seg": 3,                 # area=line 的直线长度（格）
    "wide_min_cells": 5,                # 施法范围 ≥ 此格数算「大范围道具」，见 _best_dig
    "check_inner": 0.70,                # 对勾搜索区占整格比例（用于裁掉外圈绿色高亮描边）
    "check_erode": 3,                   # 腐蚀核边长，用于滤掉残余噪点
    "check_min_area": 60,               # 绿勾最小连通域面积
    "popup_roi": [300, 150, 700, 330],  # 拾取提示框所在区域
    "popup_dark": 0.25,                 # 该区域暗像素占比超过即判为弹窗出现
    "popup_gray": 45,                   # 弹窗暗像素灰度上限
    "max_popup_rounds": 8,              # 连续判成弹窗却点不掉的轮数上限，超过即收尾
    "floor_roi": [88, 100, 130, 44],    # 左侧「地底N层」文字区域
    "ocr_threshold": 0.3,               # OCR 置信度阈值
    "click_delay": 0.35,                # 单次点击后的等待
    "settle_delay": 0.8,                # 挖掘动画等待
    "move_delay": 1.4,                  # 移动寻路等待
    "poll_interval": 0.6,               # 无操作时的轮询间隔
    "round_interval": 0.2,              # 每轮行动之间的固定间隔
    "max_rounds": 400,
    "max_floors": 30,                   # 层数安全上限
    "max_no_progress": 12,              # 连续无进展轮数上限，超过即收尾
    "max_stuck_rounds": 8,              # 整屏画面完全不变的连续轮数上限，超过即收尾
    "floor_wait_timeout": 60.0,         # 采集到目标后等待进入下一层的超时
    "next_floor_delay": 4.0,            # 站上宝箱/梯子后等「物品获得」提示框弹全的时长
    "bottom_click_node": "bottom_click",# 站上宝箱/梯子后调用一次的公共节点（点掉拾取提示框）
    "next_floor_roi": [640, 445, 230, 72],      # 「探险结束」弹窗里「到下一层」按钮区域
    "next_floor_roi_wide": [320, 430, 640, 100],# 上面的 ROI 没命中时的放宽搜索区
    "next_floor_expected": ["到下一层"],        # 推进下一层的按钮文字
    "nf_erode": 5,                              # OCR 失败时找实心绿按钮的腐蚀核
    "nf_min_area": 2000                         # 腐蚀后剩下的像素数超过即认为是按钮
}
"""
import json
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from maa.agent.agent_server import AgentServer
from maa.context import Context
from maa.custom_action import CustomAction
from maa.pipeline import JOCR, JRecognitionType

from utils import logger

__all__ = ["SecretDig"]

# 格子状态
EMPTY = 0  # 已挖空地（可通行）
UNDUG = 1  # 未挖方块（可挖，不可通行）
ITEM = 2  # 地上放着立绘（道具 / 宝箱 / 梯子）：可通行，优先走上去拾取

_DIRS4 = ((-1, 0), (1, 0), (0, -1), (0, 1))
_DIRS8 = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)

# 「槽 × 目标格」组合的评分权重，见 _dig_candidates
_W_KILL = 10  # 本次就能挖开的格
_W_PART = 2  # 本次只打掉部分耐久的格（也是有效推进，但不如挖开）
# 每点溢出伤害（伤害超出该格剩余耐久）。权重特意高过 _W_KILL：
# 溢出 1 点伤害的代价比多挖开 1 格还大，于是「伤害打不满」的落点一律不被选，
# 高级道具只会用在「伤害恰好被吃满」的地方（耐久 ≥ 伤害），
# 耐久 1 的格子归最低档，高级道具留给能一次覆盖多格的高耐久落点。
_W_WASTE = 12
# 每格「空放」：影响范围里压在已挖空地 / 地上立绘上的格子（伤害打在没东西的地方）。
# 大范围道具本来就稀少，权重按「少挖一格」算，于是槽 5/6 这类 8 格直线只有在
# 几乎能完全覆盖其施法范围时才会压过更小范围的道具出手。
_W_EMPTY = 10
_W_SLOT = 2  # 每高一个道具档位（越靠后的槽越稀缺）
_W_MOVE = 2  # 每多走一步
_W_HI_SINGLE = 8  # 伤害 3 的道具只打到 1 格时的额外惩罚（杀鸡用牛刀）

DEFAULTS: Dict[str, object] = {
    "auto_grid": True,
    "board_roi": [363, 21, 555, 557],
    "origin": [398.2, 56.2],
    "step": [69.35, 69.35],
    "rows": 8,
    "cols": 8,
    "period_range": [45, 110],
    "grid_score_keep": 0.85,
    "inner_ratio": 0.72,
    "undug_templates": ["undug_a.png", "undug_b.png", "undug_c.png"],
    "undug_tpl_size": 69,
    "block_corr": 0.45,
    "block_search": 2,
    "item_cv": 0.28,
    "undug_std": 8.5,
    "undug_glyph": 20.0,
    "orange_ratio": 0.12,
    "gold_split": 0.72,
    "char_gold_max": 0.15,
    "item_std": 20.0,
    "item_glyph": 40.0,
    "item_green_max": 0.06,
    "ring_ratio": 0.15,
    "green_min": 150,
    "green_dom": 45,
    "red_mask": [185, 105, 80],
    "select_x_erode": 5,
    "select_x_pixels": 40,
    "target_templates": ["chest.png", "ladder.png"],
    "target_dir": "image/secret_dig",
    "match_threshold": 0.72,
    "slot_centers": [
        [351.5, 619.0],
        [447.5, 619.0],
        [543.5, 619.0],
        [639.5, 619.0],
        [735.5, 619.0],
        [831.5, 619.0],
        [927.5, 619.0],
    ],
    "slot_box": 88,
    "count_roi_rel": [0.08, 0.62, 0.84, 0.34],
    "max_slot": 6,
    "undug_dura": [3, 1, 2],
    "tool_specs": [
        {"dmg": 1, "area": "single", "reach": "ring4"},
        {"dmg": 2, "area": "cross", "reach": "ring8"},
        {"dmg": 3, "area": "line", "reach": "ring4"},
        {"dmg": 3, "area": "cross", "reach": "any"},
        {"dmg": 3, "area": "row", "reach": "any"},
        {"dmg": 3, "area": "col", "reach": "any"},
    ],
    "tool_line_len": 8,
    "tool_line_seg": 3,
    "wide_min_cells": 5,
    "check_inner": 0.70,
    "check_erode": 3,
    "check_min_area": 60,
    "popup_roi": [300, 150, 700, 330],
    "popup_dark": 0.25,
    "popup_gray": 45,
    "max_popup_rounds": 8,
    "floor_roi": [88, 100, 130, 44],
    "ocr_threshold": 0.3,
    "click_delay": 0.35,
    "settle_delay": 0.8,
    "move_delay": 1.4,
    "poll_interval": 0.6,
    "round_interval": 0.2,
    "max_rounds": 400,
    "max_floors": 30,
    "max_no_progress": 12,
    "max_stuck_rounds": 8,
    "floor_wait_timeout": 60.0,
    "next_floor_delay": 4.0,
    "bottom_click_node": "bottom_click",
    "next_floor_roi": [640, 445, 230, 72],
    "next_floor_roi_wide": [320, 430, 640, 100],
    "next_floor_expected": ["到下一层"],
    "nf_erode": 5,
    "nf_min_area": 2000,
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


@AgentServer.custom_action("SecretDig")
class SecretDig(CustomAction):
    """地底挖宝自动挖掘。"""

    _tpl_cache: Dict[str, Optional[np.ndarray]] = {}

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
            logger.error(f"SecretDig 参数解析失败: {e}")
            return CustomAction.RunResult(success=False)

        cfg = {**DEFAULTS, **params}
        tpls = self._load_templates(cfg)
        if not tpls:
            logger.warning(
                "SecretDig 未找到宝箱/梯子模板，将只做盲目开挖，"
                "挖出目标后无法自动识别（请把模板放到 "
                f"{cfg['target_dir']}/ 下）"
            )
        blocks = self._load_blocks(cfg)
        if not blocks:
            logger.warning(
                "SecretDig 未找到未挖方块模板，退回灰度阈值判定"
                "（跨截图不稳，容易把整盘误判成地上立绘）"
            )

        layout: Optional[Dict[str, object]] = None
        floor: Optional[int] = None
        floor_pending: Optional[int] = None
        floor_pending_cnt = 0
        char: Optional[Tuple[int, int]] = None
        last_dig: Optional[Tuple[int, int]] = None
        last_sig: Optional[tuple] = None
        prev_sig: Optional[tuple] = None
        no_progress = 0
        stuck = 0
        stuck_sig: Optional[tuple] = None
        dig_ok = 0
        reached = 0
        sel_slot: Optional[int] = None
        popup_rounds = 0
        # 已经走上去却没被拾取、下一轮又判成地上立绘的格子：不再去捡
        blocked_items: Set[Tuple[int, int]] = set()
        walk_tries: Dict[Tuple[int, int], int] = {}
        visited: Set[Tuple[int, int]] = set()
        prev_counts: Optional[List[Optional[int]]] = None

        for rnd in range(1, int(cfg["max_rounds"]) + 1):
            # 每轮行动之间留一个固定的间隔：让上一手的点击动画 / 弹窗 / 耐久美术
            # 落到稳定状态再截图判盘，避免截到过渡帧把耐久或空地读错
            time.sleep(float(cfg["round_interval"]))
            img = self._screencap(context)
            if img is None:
                logger.error("SecretDig 截图失败，终止")
                break

            # 画面一直不变 = 点不动（例如点到不可通行方块、弹窗点不掉），
            # 这种情况不会体现在盘面 sig 上（早退分支根本走不到 sig），故单独兜底
            cur_stuck = self._screen_sig(img)
            if cur_stuck == stuck_sig:
                stuck += 1
            else:
                stuck, stuck_sig = 0, cur_stuck
            if stuck >= int(cfg["max_stuck_rounds"]):
                logger.warning(
                    f"SecretDig 连续 {stuck} 轮画面没有任何变化"
                    f"（疑似点在不可通行位置或弹窗点不掉），收尾交回上层流程"
                )
                break

            # 拾取弹窗会挡住一切交互，优先点掉；但连着很多轮都点不掉说明是误判
            # （把一块暗色区域当成了弹窗），再点只会空转，直接收尾
            if self._popup_visible(img, cfg):
                # 「探险结束」弹窗也是一整块暗面板，但它只能用绿色「到下一层」推进：
                # 点面板正中（暗像素质心）落在正文上，怎么点都没反应，必须先试按钮
                if self._click_next_floor(context, img, cfg):
                    time.sleep(float(cfg["click_delay"]))
                    continue
                popup_rounds += 1
                if popup_rounds > int(cfg["max_popup_rounds"]):
                    logger.warning(
                        f"SecretDig 连续 {popup_rounds} 轮把画面判成拾取提示框却点不掉，"
                        "疑似误判，收尾交回上层流程"
                    )
                    break
                px, py = self._popup_point(img, cfg)
                logger.info(f"[第{rnd}轮] 发现拾取提示框，点击关闭 ({px},{py})")
                self._click(context, px, py)
                time.sleep(float(cfg["click_delay"]))
                continue
            popup_rounds = 0

            # 左侧「地底N层」是权威进度信号：层号变化即进入新层
            cur_floor = self._read_floor(context, img, cfg)
            if (
                cur_floor is not None
                and cur_floor != floor
                and (floor is None or cur_floor > floor)
            ):
                if cur_floor == floor_pending:
                    floor_pending_cnt += 1
                else:
                    floor_pending = cur_floor
                    floor_pending_cnt = 1
                if floor_pending_cnt >= 2:
                    logger.info(f"SecretDig 层号 {floor} -> {cur_floor}，重置本层状态")
                    floor = cur_floor
                    layout = None
                    char = None
                    last_dig = None
                    prev_sig = None
                    last_sig = None
                    no_progress = 0
                    sel_slot = None
                    blocked_items.clear()
                    walk_tries.clear()
                    visited.clear()
                    prev_counts = None
                    floor_pending, floor_pending_cnt = None, 0
                if floor != cur_floor:
                    time.sleep(float(cfg["poll_interval"]))
                    continue
            else:
                floor_pending, floor_pending_cnt = None, 0

            layout = self._calibrate(img, cfg, layout)
            board, feats, dura = self._read_board(img, layout, cfg, blocks)
            rows, cols = int(layout["rows"]), int(layout["cols"])

            char = self._find_char(feats, rows, cols, char)
            if char is not None:
                visited.add(char)
            targets = self._match_targets(img, layout, tpls, cfg)

            undug = sum(
                1 for r in range(rows) for c in range(cols) if board[r][c] == UNDUG
            )
            free = sum(
                1 for r in range(rows) for c in range(cols) if board[r][c] == EMPTY
            )
            sig = (
                tuple(
                    sorted(
                        (r, c)
                        for r in range(rows)
                        for c in range(cols)
                        if board[r][c] == UNDUG
                    )
                ),
                char,
                tuple(int(v) for v in dura.ravel().tolist()),
            )
            logger.info(
                f"[第{rnd}轮] 地底{floor}层 网格 {rows}×{cols}"
                f"{'' if layout.get('valid') else '(沿用上轮)'} "
                f"原点({layout['ox']:.0f},{layout['oy']:.0f}) 步长{layout['step']:.1f} "
                f"未挖 {undug} / 空地 {free} 角色 {char} 目标 {targets}"
            )

            # 上一轮挖的那格如果连耐久都没动，说明这一下根本没生效（误点/没选中）
            if last_dig is not None and sig == last_sig:
                logger.info(f"SecretDig 上轮开挖 {last_dig} 后画面无变化，疑似未生效")
            last_dig = None
            last_sig = sig

            if prev_sig is None or sig != prev_sig:
                no_progress = 0
            else:
                no_progress += 1
            prev_sig = sig

            if char is None:
                logger.warning("SecretDig 没找到角色所在格，重新读盘")
                no_progress += 1
                if no_progress >= int(cfg["max_no_progress"]):
                    logger.warning("SecretDig 连续找不到角色，收尾交回上层流程")
                    break
                time.sleep(float(cfg["poll_interval"]))
                continue

            counts = self._slot_counts(context, img, cfg)
            # OCR 偶发漏读（同一槽这轮 None、下轮又有值）会让可用槽集合上下跳、
            # 计划跟着抖（选到的施法位一变，角色就开始来回走）。数量读到 0 才是
            # 真用完，故只有在「上一轮读到不止 1 个」时才沿用，避免把用光的槽
            # 当成还有货（上一轮 >1，用掉一个后至少还剩 1，绝不会误点空槽）
            if prev_counts is not None:
                for i, n in enumerate(counts):
                    if (
                        n is None
                        and i < len(prev_counts)
                        and isinstance(prev_counts[i], int)
                        and prev_counts[i] > 1
                    ):
                        counts[i] = prev_counts[i]
            prev_counts = list(counts)
            sel_slot = self._selected_slot(img, cfg)
            usable = self._usable_slots(counts, cfg)
            logger.info(
                f"SecretDig 道具数量 {counts} 选中槽 {sel_slot} 可用 {usable}"
            )

            plan = self._plan(board, dura, char, targets, counts, cfg, blocked_items)
            if plan is None:
                no_progress += 1
                logger.info(
                    f"SecretDig 暂无可开挖的前沿方块（无进展 "
                    f"{no_progress}/{cfg['max_no_progress']}）"
                )
                # 不因为某一轮读不出数量就直接收尾：OCR 会偶发漏读，
                # 重试到无进展上限再判定，是真没道具还是不巧没读到都能覆盖
                if no_progress >= int(cfg["max_no_progress"]):
                    if usable:
                        logger.warning("SecretDig 连续无进展，判定本层无法继续，收尾")
                    else:
                        logger.info("SecretDig 已无可用道具，收尾交回上层流程")
                    break
                time.sleep(float(cfg["poll_interval"]))
                continue

            kind = plan["kind"]
            if kind == "walk":
                cell = plan["cell"]
                if cell == char:
                    logger.info(f"SecretDig 已站上目标 {cell}，等待进入下一层")
                    # 等层期间会点掉拾取弹窗，若道具仍处于选中态，那一下会误消耗道具
                    self._deselect(context, cfg, sel_slot)
                    sel_slot = None
                    # 拾取宝箱/梯子后先弹「物品获得」提示框，等它弹全再由 bottom_click 点掉；
                    # 点掉后才会出现「探险结束」弹窗，由 _wait_floor 里的 _click_next_floor 推进
                    time.sleep(float(cfg["next_floor_delay"]))
                    self._run_node(context, str(cfg["bottom_click_node"]))
                    nxt = self._wait_floor(context, cfg, floor)
                    if nxt is None:
                        logger.warning("SecretDig 等待下一层超时，收尾交回上层流程")
                        break
                    floor = nxt
                    reached += 1
                    layout = None
                    char = None
                    prev_sig = None
                    last_sig = None
                    no_progress = 0
                    sel_slot = None
                    blocked_items.clear()
                    walk_tries.clear()
                    visited.clear()
                    prev_counts = None
                    continue
                # 散落道具走上去会被游戏自动拾取、下一轮就不该再判成地上立绘。
                # 只有「确实站上过这格、这轮却还被要求去捡」才说明那是拾不起来的固定
                # 立绘（多半是没认出来的宝箱/梯子）：不拉黑的话「去捡它」和「走到施法位」
                # 会互相顶掉，角色就在两格之间来回横跳、永不开挖。
                # 没站上过（还在半路）不算，继续往那儿走，免得把远处真道具误杀
                if plan.get("loose"):
                    if walk_tries.get(cell, 0) >= 1 and cell in visited:
                        logger.info(
                            f"SecretDig 地上立绘 {cell} 站上过却没被拾取，"
                            "判定为固定立绘，不再去捡"
                        )
                        blocked_items.add(cell)
                        continue
                    walk_tries[cell] = walk_tries.get(cell, 0) + 1
                logger.info(f"SecretDig 走向目标 {cell}")
                self._deselect(context, cfg, sel_slot)
                sel_slot = None
                self._click_cell(context, layout, *cell)
                time.sleep(float(cfg["move_delay"]))
                continue

            if kind == "move":
                cell = plan["cell"]
                logger.info(f"SecretDig 移动到前沿位置 {cell}")
                self._deselect(context, cfg, sel_slot)
                sel_slot = None
                self._click_cell(context, layout, *cell)
                time.sleep(float(cfg["move_delay"]))
                continue

            # kind == "dig"
            slot = int(plan["slot"])
            cell = plan["cell"]
            logger.info(
                f"SecretDig 用第 {slot} 槽开挖 {cell}（评分 {plan['score']}，"
                f"本次可挖开 {plan['kill']} 格 / 打掉部分耐久 {plan['part']} 格 / "
                f"溢出伤害 {plan['waste']} 点 / 空放 {plan['empty']} 格，"
                f"走位 {plan['cost']} 步）"
            )
            ok = self._dig(context, cfg, layout, slot, cell, sel_slot)
            sel_slot = slot
            if ok:
                dig_ok += 1
            last_dig = cell
            time.sleep(float(cfg["settle_delay"]))

        logger.info(
            f"SecretDig 结束（地底{floor}层），成功开挖 {dig_ok} 次，"
            f"进入下一层 {reached} 次"
        )
        return CustomAction.RunResult(success=dig_ok > 0 or reached > 0)

    # ---------- 感知 ----------

    def _screencap(self, context: Context) -> Optional[np.ndarray]:
        try:
            return context.tasker.controller.post_screencap().wait().get()
        except Exception as e:
            logger.error(f"SecretDig 截图异常: {e}")
            return None

    @staticmethod
    def _screen_sig(img: np.ndarray) -> tuple:
        """整屏缩略图的量化灰度，用来判断画面是否真的变了。

        量化到 8 级灰度，既能忽略噪点/抖动，又能敏感地反映弹窗开合、
        角色位移、方块被挖开这类变化。
        """
        small = cv2.resize(img, (16, 9), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        return tuple((gray // 8).astype(np.uint8).ravel().tolist())

    def _click(self, context: Context, x: float, y: float) -> None:
        try:
            context.tasker.controller.post_click(int(x), int(y)).wait()
        except Exception as e:
            logger.error(f"SecretDig 点击({x},{y})异常: {e}")

    def _find_asset(self, cfg: Dict[str, object], name: str) -> Optional[Path]:
        rel = str(cfg["target_dir"]).replace("\\", "/").strip("/")
        root = Path(__file__).resolve().parents[3]
        candidates = [
            Path.cwd() / "resource" / rel / name,
            root / "assets" / "resource" / rel / name,
            root / "resource" / rel / name,
        ]
        return next((p for p in candidates if p.is_file()), None)

    def _load_templates(
        self, cfg: Dict[str, object]
    ) -> List[Tuple[str, np.ndarray]]:
        out: List[Tuple[str, np.ndarray]] = []
        for name in cfg["target_templates"]:
            target = self._find_asset(cfg, str(name))
            if target is None:
                logger.warning(f"SecretDig 缺少目标模板 {name}（{cfg['target_dir']}/）")
                continue
            key = str(target)
            if key not in self._tpl_cache:
                im = _imread(key)
                self._tpl_cache[key] = (
                    None if im is None else cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
                )
            tpl = self._tpl_cache[key]
            if tpl is None:
                logger.warning(f"SecretDig 目标模板读取失败 {target}")
                continue
            out.append((str(name), tpl))
            logger.info(f"SecretDig 载入目标模板 {name} {tpl.shape[1]}×{tpl.shape[0]}")
        return out

    def _load_blocks(
        self, cfg: Dict[str, object]
    ) -> List[Tuple[np.ndarray, int]]:
        """载入未挖方块模板（每种耐久一张）及其剩余耐久，保留彩色供模板匹配用。"""
        names = [str(n) for n in cfg["undug_templates"]]
        duras = [int(v) for v in cfg["undug_dura"]]
        out: List[Tuple[np.ndarray, int]] = []
        for i, name in enumerate(names):
            target = self._find_asset(cfg, name)
            if target is None:
                logger.warning(f"SecretDig 缺少未挖方块模板 {name}（{cfg['target_dir']}/）")
                continue
            im = _imread(str(target))
            if im is None:
                logger.warning(f"SecretDig 未挖方块模板读取失败 {target}")
                continue
            out.append((im, duras[i] if i < len(duras) else 1))
        if out:
            h, w = out[0][0].shape[:2]
            logger.info(
                f"SecretDig 载入未挖方块模板 {len(out)} 张，{w}×{h}，"
                f"耐久 {[d for _, d in out]}"
            )
        return out

    def _calibrate(
        self,
        img: np.ndarray,
        cfg: Dict[str, object],
        last: Optional[Dict[str, object]],
    ) -> Dict[str, object]:
        """标定棋盘网格；失败时沿用上次结果，再失败退回 DEFAULTS 硬编码值。"""
        fallback = self._default_layout(cfg, valid=False)
        if not cfg["auto_grid"]:
            return self._default_layout(cfg, valid=True)

        x, y, w, h = [int(v) for v in cfg["board_roi"]]
        h = min(h, img.shape[0] - y)
        w = min(w, img.shape[1] - x)
        if w <= 0 or h <= 0:
            return last or fallback
        roi = img[y : y + h, x : x + w].astype(np.int32)
        b, g, r = roi[:, :, 0], roi[:, :, 1], roi[:, :, 2]
        mask = (r > 95) & (b > 95) & (g < b - 15) & (r < b + 40)
        if float(mask.mean()) < 0.15:
            return last or fallback
        ys = np.nonzero(mask.sum(axis=1) > w * 0.35)[0]
        xs = np.nonzero(mask.sum(axis=0) > h * 0.35)[0]
        if len(ys) < 2 or len(xs) < 2:
            return last or fallback
        bx0, by0 = x + int(xs.min()), y + int(ys.min())
        bx1, by1 = x + int(xs.max()) + 1, y + int(ys.max()) + 1
        bw, bh = bx1 - bx0, by1 - by0

        gray = cv2.cvtColor(img[by0:by1, bx0:bx1], cv2.COLOR_BGR2GRAY).astype(
            np.float32
        )
        prof_x = self._axis_profile(gray, 1)
        prof_y = self._axis_profile(gray, 0)
        if prof_x.size < 8 or prof_y.size < 8:
            return last or fallback
        lo, hi = [int(v) for v in cfg["period_range"]]
        keep = float(cfg["grid_score_keep"])
        cols = self._count_cells(prof_x, bw, lo, hi, keep)
        rows = self._count_cells(prof_y, bh, lo, hi, keep)
        if cols is None or rows is None:
            return last or fallback
        step_x, step_y = bw / cols, bh / rows
        if abs(step_x - step_y) > 0.25 * max(step_x, step_y):
            return last or fallback
        return {
            "valid": True,
            "ox": bx0 + step_x / 2.0,
            "oy": by0 + step_y / 2.0,
            "step": (step_x + step_y) / 2.0,
            "step_x": step_x,
            "step_y": step_y,
            "rows": rows,
            "cols": cols,
            "bbox": (bx0, by0, bw, bh),
        }

    @staticmethod
    def _axis_profile(gray: np.ndarray, axis: int) -> np.ndarray:
        """沿指定方向求相邻像素灰度差均值，再去掉低频趋势。

        棋盘格边框会在「格边」位置压出一排尖峰，尖峰间距即格边长；
        减去滑动均值只留下这排尖峰，方便后面按格数打分。
        """
        prof = np.abs(np.diff(gray, axis=axis)).mean(axis=1 - axis)
        win = max(5, min(15, prof.size // 20))
        return prof - np.convolve(prof, np.ones(win) / win, mode="same")

    @staticmethod
    def _count_cells(
        prof: np.ndarray, total: float, lo: int, hi: int, keep: float
    ) -> Optional[int]:
        """由边缘尖峰序列反推格数，返回 None 表示标定失败。

        对每个候选格数 n，取 n-1 条「格边」位置上的尖峰高度均值打分，格边长
        必须落在 period_range 内。n 取到真值的整数分之一时（真值 8 取 4），
        采样位置同样落在真实格边上，分数一样高，所以在「不低于最高分 keep 倍」
        的候选里取最大的 n。

        旧做法是「按周期 p 逐点采样后取均值」，样本数随 p 变化会产生系统性
        偏差：截图上 8×8 棋盘（步长 69.4）会被读成 5×5（步长 111.2），
        于是整盘误判为地上立绘、角色被指挥去点一块挖不动的方块。
        """
        scored: List[Tuple[float, int]] = []
        for n in range(3, 17):
            step = total / float(n)
            if not (lo <= step <= hi):
                continue
            pos = np.round(np.arange(1, n) * step - 0.5).astype(int)
            pos = pos[(pos >= 0) & (pos < prof.size)]
            if pos.size < 2:
                continue
            win = max(1, int(step * 0.06))
            vals = [
                float(prof[max(0, q - win) : q + win + 1].max())
                for q in pos.tolist()
            ]
            scored.append((float(np.mean(vals)), n))
        if not scored:
            return None
        top = max(s for s, _ in scored)
        if top <= 0:
            return None
        return max(n for s, n in scored if s >= top * keep)

    def _default_layout(
        self, cfg: Dict[str, object], valid: bool
    ) -> Dict[str, object]:
        ox, oy = float(cfg["origin"][0]), float(cfg["origin"][1])
        sx, sy = float(cfg["step"][0]), float(cfg["step"][1])
        rows, cols = int(cfg["rows"]), int(cfg["cols"])
        x0, y0 = int(ox - sx / 2), int(oy - sy / 2)
        return {
            "valid": valid,
            "ox": ox,
            "oy": oy,
            "step": (sx + sy) / 2.0,
            "step_x": sx,
            "step_y": sy,
            "rows": rows,
            "cols": cols,
            "bbox": (x0, y0, int(sx * cols), int(sy * rows)),
        }

    def _read_board(
        self,
        img: np.ndarray,
        layout: Dict[str, object],
        cfg: Dict[str, object],
        blocks: List[Tuple[np.ndarray, int]],
    ) -> Tuple[List[List[int]], Dict[str, np.ndarray], np.ndarray]:
        """逐格判定：0=已挖空地 / 1=未挖方块 / 2=地上立绘。

        同时给出各处特征矩阵，以及每个未挖方块的剩余耐久（其余格为 0）。
        """
        rows, cols = int(layout["rows"]), int(layout["cols"])
        ox, oy = float(layout["ox"]), float(layout["oy"])
        sx, sy = float(layout["step_x"]), float(layout["step_y"])
        ratio = float(cfg["inner_ratio"])
        green_min = int(cfg["green_min"])
        green_dom = int(cfg["green_dom"])
        # 未挖方块模板：先缩放到当前格边长；匹配时整格再加上 block_search 像素的
        # 对齐余量，抵消「格心取整」和标定残留偏差带来的亚像素错位。
        side = max(8, int(round(min(sx, sy))))
        grow = max(0, int(cfg["block_search"]))
        corr = float(cfg["block_corr"])
        tpls = [
            cv2.resize(t, (side, side), interpolation=cv2.INTER_AREA)
            for t, _ in blocks
        ]
        duras = [int(d) for _, d in blocks]
        b = img[:, :, 0].astype(np.int32)
        g = img[:, :, 1].astype(np.int32)
        r = img[:, :, 2].astype(np.int32)
        gray = (0.114 * b + 0.587 * g + 0.299 * r).astype(np.int32)
        green = (g > green_min) & ((g - np.maximum(r, b)) > green_dom)
        # 暖色再拆两半：金色（宝箱等金物）与橙红（角色本体）。
        # 不拆的话金色宝箱的暖色占比高达 0.87，会被当成角色所在格。
        warm = (r > 150) & ((r - b) > 60) & (g > 90)
        gold = warm & (g > float(cfg["gold_split"]) * r)
        hot = warm & ~gold

        board: List[List[int]] = []
        std_m = np.zeros((rows, cols), dtype=np.float32)
        glyph_m = np.zeros((rows, cols), dtype=np.float32)
        orange_m = np.zeros((rows, cols), dtype=np.float32)
        ring_m = np.zeros((rows, cols), dtype=np.float32)
        dura_m = np.zeros((rows, cols), dtype=np.int32)
        h_img, w_img = gray.shape
        for rr in range(rows):
            row: List[int] = []
            for cc in range(cols):
                cx = ox + cc * sx
                cy = oy + rr * sy
                half = sy * ratio / 2.0
                y0 = int(max(0, cy - half))
                y1 = int(min(h_img, cy + half))
                x0 = int(max(0, cx - sx * ratio / 2.0))
                x1 = int(min(w_img, cx + sx * ratio / 2.0))
                if y1 - y0 < 6 or x1 - x0 < 6:
                    row.append(EMPTY)
                    continue
                patch = gray[y0:y1, x0:x1]
                std = float(patch.std())
                p50, p99 = np.percentile(patch, 50), np.percentile(patch, 99)
                gold_r = float(gold[y0:y1, x0:x1].mean())
                hot_r = float(hot[y0:y1, x0:x1].mean())
                green_r = float(green[y0:y1, x0:x1].mean())

                # 外圈：判断游戏是否把该格标成「施法范围内」
                ring_box = (
                    int(max(0, cy - sy / 2.0)),
                    int(max(0, cy + sy / 2.0)),
                    int(max(0, cx - sx / 2.0)),
                    int(max(0, cx + sx / 2.0)),
                )
                ry0, ry1, rx0, rx1 = ring_box
                if ry1 - ry0 > 20 and rx1 - rx0 > 20:
                    band = max(6, int(min(sx, sy) * 0.13))
                    ring = np.concatenate(
                        [
                            green[ry0 : ry0 + band, rx0:rx1].ravel(),
                            green[ry1 - band : ry1, rx0:rx1].ravel(),
                            green[ry0:ry1, rx0 : rx0 + band].ravel(),
                            green[ry0:ry1, rx1 - band : rx1].ravel(),
                        ]
                    )
                    rratio = float(ring.mean())
                else:
                    rratio = 0.0

                glyph = float(p99 - p50)
                std_m[rr][cc] = std
                glyph_m[rr][cc] = glyph
                # 角色分额外要求「不偏金」，否则金色宝箱（暖色 0.87、金色 0.55）
                # 会盖过角色（暖色 0.36、金色 0.00）被 _find_char 选中。
                orange_m[rr][cc] = (
                    hot_r if gold_r <= float(cfg["char_gold_max"]) else 0.0
                )
                ring_m[rr][cc] = rratio

                bidx = -1
                if tpls:
                    # 未挖方块：整格归一化模板匹配，取各耐久美术里的最高分；
                    # argmax 命中的那张模板同时给出该格还剩几点耐久
                    fx0 = int(round(cx - side / 2.0)) - grow
                    fy0 = int(round(cy - side / 2.0)) - grow
                    big = img[
                        max(0, fy0) : min(h_img, fy0 + side + 2 * grow),
                        max(0, fx0) : min(w_img, fx0 + side + 2 * grow),
                    ]
                    bscore = -1.0
                    bidx = -1
                    for ti, t in enumerate(tpls):
                        if big.shape[0] < t.shape[0] or big.shape[1] < t.shape[1]:
                            continue
                        sc = float(
                            cv2.matchTemplate(big, t, cv2.TM_CCOEFF_NORMED).max()
                        )
                        if sc > bscore:
                            bscore, bidx = sc, ti
                    is_block = bscore >= corr
                else:
                    is_block = std > float(cfg["undug_std"]) or glyph > float(
                        cfg["undug_glyph"]
                    )

                if hot_r > float(cfg["orange_ratio"]) and gold_r <= float(
                    cfg["char_gold_max"]
                ):
                    row.append(EMPTY)  # 角色所在格：已挖开，可通行
                elif green_r > float(cfg["item_green_max"]):
                    row.append(EMPTY)  # 移动方向箭头，画在已挖开的格子上
                elif is_block:
                    row.append(UNDUG)
                    if 0 <= bidx < len(duras):
                        dura_m[rr][cc] = duras[bidx]
                elif tpls:
                    # 方块已排除，这时还有明显纹理就是地上的立绘
                    if std / (float(patch.mean()) + 1e-6) > float(cfg["item_cv"]):
                        row.append(ITEM)
                    else:
                        row.append(EMPTY)
                elif std > float(cfg["item_std"]) or glyph > float(
                    cfg["item_glyph"]
                ):
                    row.append(ITEM)  # 地上有立绘：可通行，走上去即自动拾取
                else:
                    row.append(EMPTY)
            board.append(row)

        feats = {
            "std": std_m,
            "glyph": glyph_m,
            "orange": orange_m,
            "ring": ring_m,
        }
        return board, feats, dura_m

    def _find_char(
        self,
        feats: Dict[str, np.ndarray],
        rows: int,
        cols: int,
        prev: Optional[Tuple[int, int]],
    ) -> Optional[Tuple[int, int]]:
        """角色所在格：橙色占比最高者；并列时优先离上一轮位置近的。"""
        orange = feats["orange"]
        cands = [
            (float(orange[r][c]), (r, c))
            for r in range(rows)
            for c in range(cols)
            if orange[r][c] > 0.001
        ]
        if not cands:
            return None
        cands.sort(key=lambda x: -x[0])
        if len(cands) == 1 or cands[0][0] >= cands[1][0] * 1.5 or prev is None:
            return cands[0][1]
        return min(
            (c for _, c in cands),
            key=lambda p: abs(p[0] - prev[0]) + abs(p[1] - prev[1]),
        )

    def _match_targets(
        self,
        img: np.ndarray,
        layout: Dict[str, object],
        tpls: List[Tuple[str, np.ndarray]],
        cfg: Dict[str, object],
    ) -> List[Tuple[Tuple[int, int], str]]:
        if not tpls:
            return []
        bx, by, bw, bh = layout["bbox"]
        bx = max(0, int(bx))
        by = max(0, int(by))
        bw = min(int(bw), img.shape[1] - bx)
        bh = min(int(bh), img.shape[0] - by)
        if bw < 10 or bh < 10:
            return []
        gray = cv2.cvtColor(img[by : by + bh, bx : bx + bw], cv2.COLOR_BGR2GRAY)
        best: Dict[Tuple[int, int], Tuple[float, str]] = {}
        for name, tpl in tpls:
            th, tw = tpl.shape[:2]
            if th > gray.shape[0] or tw > gray.shape[1]:
                continue
            res = cv2.matchTemplate(gray, tpl, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= float(cfg["match_threshold"]))
            for yy, xx in zip(ys.tolist(), xs.tolist()):
                cx = bx + xx + tw / 2.0
                cy = by + yy + th / 2.0
                cell = self._cell_at(cx, cy, layout)
                score = float(res[yy, xx])
                if cell not in best or score > best[cell][0]:
                    best[cell] = (score, name)
        return [(cell, name) for cell, (_, name) in sorted(best.items())]

    def _cell_at(
        self, px: float, py: float, layout: Dict[str, object]
    ) -> Tuple[int, int]:
        cc = int(round((px - float(layout["ox"])) / float(layout["step_x"])))
        rr = int(round((py - float(layout["oy"])) / float(layout["step_y"])))
        return (rr, cc)

    def _slot_counts(
        self,
        context: Context,
        img: np.ndarray,
        cfg: Dict[str, object],
    ) -> List[Optional[int]]:
        out: List[Optional[int]] = []
        for x, y in cfg["slot_centers"]:
            rx, ry, rw, rh = self._count_roi(x, y, cfg)
            out.append(self._ocr_number(context, img, [rx, ry, rw, rh], cfg))
        return out

    def _count_roi(
        self, cx: float, cy: float, cfg: Dict[str, object]
    ) -> Tuple[int, int, int, int]:
        box = float(cfg["slot_box"])
        f0, f1, f2, f3 = [float(v) for v in cfg["count_roi_rel"]]
        rx = int(cx - box / 2.0 + box * f0)
        ry = int(cy - box / 2.0 + box * f1)
        return rx, ry, int(box * f2), int(box * f3)

    def _ocr_number(
        self,
        context: Context,
        img: np.ndarray,
        roi: List[int],
        cfg: Dict[str, object],
    ) -> Optional[int]:
        """OCR 指定 ROI 并取出其中第一段数字；读不到返回 None。"""
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
            logger.error(f"SecretDig OCR 调用异常: {e}")
            return None
        if detail is None:
            return None
        # 同一段数字常同时出现在 filtered_results 与 all_results（甚至两个重叠框）里，
        # 直接拼接会把「160」拼成「160160」、「27」拼成「2727」，
        # 于是「这个槽还剩几个」被读错。这里去重后只取第一段可读数字。
        texts: List[str] = []
        for group in (detail.filtered_results, detail.all_results):
            for item in group or []:
                text = getattr(item, "text", None)
                if text and str(text) not in texts:
                    texts.append(str(text))
        if not texts and detail.best_result is not None:
            text = getattr(detail.best_result, "text", None)
            if text:
                texts.append(str(text))
        for text in texts:
            m = re.search(r"\d+", text)
            if m:
                return int(m.group())
        return None

    def _ocr_find(
        self,
        context: Context,
        img: np.ndarray,
        roi: List[int],
        expected: List[str],
        cfg: Dict[str, object],
    ) -> Optional[Tuple[int, int, int, int]]:
        """OCR 指定 ROI，返回第一个命中 expected 的结果框（x,y,w,h）；没命中返回 None。"""
        try:
            detail = context.run_recognition_direct(
                JRecognitionType.OCR,
                JOCR(
                    roi=(int(roi[0]), int(roi[1]), int(roi[2]), int(roi[3])),
                    expected=expected,
                    threshold=float(cfg["ocr_threshold"]),
                ),
                img,
            )
        except Exception as e:
            logger.error(f"SecretDig OCR 调用异常: {e}")
            return None
        if detail is None:
            return None
        for item in list(detail.filtered_results or []) + list(
            detail.all_results or []
        ):
            text = str(getattr(item, "text", "") or "")
            box = getattr(item, "box", None)
            if not box or not text:
                continue
            if any(k in text for k in expected):
                return (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
        return None

    def _read_floor(
        self, context: Context, img: np.ndarray, cfg: Dict[str, object]
    ) -> Optional[int]:
        return self._ocr_number(context, img, list(cfg["floor_roi"]), cfg)

    def _popup_visible(self, img: np.ndarray, cfg: Dict[str, object]) -> bool:
        x, y, w, h = [int(v) for v in cfg["popup_roi"]]
        x = max(0, x)
        y = max(0, y)
        w = min(w, img.shape[1] - x)
        h = min(h, img.shape[0] - y)
        if w <= 0 or h <= 0:
            return False
        patch = img[y : y + h, x : x + w]
        gray = patch.mean(axis=2)
        return float((gray < float(cfg["popup_gray"])).mean()) > float(
            cfg["popup_dark"]
        )

    def _popup_point(self, img: np.ndarray, cfg: Dict[str, object]) -> Tuple[int, int]:
        x, y, w, h = [int(v) for v in cfg["popup_roi"]]
        x = max(0, x)
        y = max(0, y)
        w = min(w, img.shape[1] - x)
        h = min(h, img.shape[0] - y)
        patch = img[y : y + h, x : x + w]
        gray = patch.mean(axis=2)
        ys, xs = np.nonzero(gray < float(cfg["popup_gray"]))
        if len(ys) == 0:
            return (x + w // 2, y + h // 2)
        return (x + int(np.median(xs)), y + int(np.median(ys)))

    def _click_next_floor(
        self, context: Context, img: np.ndarray, cfg: Dict[str, object]
    ) -> bool:
        """推进「探险结束」弹窗（拾取宝箱/梯子后自动弹出），命中并点击返回 True。

        该弹窗里只有「离开」和「到下一层」两个按钮，没有「点击画面继续」这类
        文字，bottom_click 抓不到，必须在这里单独点绿色「到下一层」。
        为避免盲点把角色带走，只在真的 OCR 到按钮文字时才点。
        """
        expected = [str(t) for t in cfg["next_floor_expected"]]
        for roi in (cfg["next_floor_roi"], cfg["next_floor_roi_wide"]):
            box = self._ocr_find(context, img, [int(v) for v in roi], expected, cfg)
            if box is None:
                continue
            x, y, w, h = box
            self._click(context, x + w / 2.0, y + h / 2.0)
            logger.info("SecretDig 点击「到下一层」，退出「探险结束」弹窗")
            return True
        # OCR 认不出按钮文字时退一步：ROI 里那块实心亮绿就是「到下一层」。
        # 实测按钮框 [647,452,204,59]、9227 像素，质心 (749,482)；施法范围描边和
        # 绿色对勾都是细线或小圆盘，5×5 腐蚀后剩下的面积远达不到阈值，不会误点。
        point = self._next_floor_button(img, cfg)
        if point is None:
            return False
        self._click(context, point[0], point[1])
        logger.info(f"SecretDig 按绿色按钮位置点击「到下一层」({point[0]:.0f},{point[1]:.0f})")
        return True

    def _next_floor_button(
        self, img: np.ndarray, cfg: Dict[str, object]
    ) -> Optional[Tuple[float, float]]:
        """ROI 里面积最大的「实心亮绿块」质心；没有返回 None。"""
        x, y, w, h = [int(v) for v in cfg["next_floor_roi"]]
        x = max(0, x)
        y = max(0, y)
        w = min(w, img.shape[1] - x)
        h = min(h, img.shape[0] - y)
        if w <= 0 or h <= 0:
            return None
        patch = img[y : y + h, x : x + w]
        b = patch[:, :, 0].astype(np.int32)
        g = patch[:, :, 1].astype(np.int32)
        r = patch[:, :, 2].astype(np.int32)
        mask = (
            (g > int(cfg["green_min"])) & ((g - np.maximum(r, b)) > int(cfg["green_dom"]))
        ).astype(np.uint8)
        k = max(3, int(cfg["nf_erode"]))
        mask = cv2.erode(mask, np.ones((k, k), np.uint8))
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
        best = -1
        for i in range(1, n):
            if stats[i][4] >= int(cfg["nf_min_area"]) and (
                best < 0 or stats[i][4] > stats[best][4]
            ):
                best = i
        if best < 0:
            return None
        return (x + float(cent[best][0]), y + float(cent[best][1]))

    def _selected_slot(
        self, img: np.ndarray, cfg: Dict[str, object]
    ) -> Optional[int]:
        """槽位是否已选中：槽内红叉像素数超过阈值。返回 1 起的槽号。

        道具图标自带红色（红辣椒、炸弹引线、火把等），实测未选中的槽也能有
        0~284 px 落在掩码里，而红叉只有约 360 px —— 直接数原始像素，选中槽
        （360+）与「红辣椒槽」（284）只差不到 80 px，换个图标就会误判。
        但两者形态差别很大：图标的红是细碎描边，红叉是一块约 26x22 的实心十字。
        故先做 5x5 腐蚀：红叉残留 103 px，其余槽最多 17 px，分离度约 6 倍。
        """
        r_lo, g_hi, b_hi = [int(v) for v in cfg["red_mask"]]
        box = int(cfg["slot_box"]) // 2
        k = int(cfg["select_x_erode"])
        if k > 1:
            k = k if k % 2 == 1 else k + 1
        kernel = np.ones((k, k), np.uint8)
        best, best_n = None, 0
        for i, (x, y) in enumerate(cfg["slot_centers"], start=1):
            x0, y0 = max(0, int(x) - box), max(0, int(y) - box)
            patch = img[y0 : y0 + box * 2, x0 : x0 + box * 2].astype(np.int32)
            if patch.size == 0:
                continue
            b, g, r = patch[:, :, 0], patch[:, :, 1], patch[:, :, 2]
            mask = ((r > r_lo) & (g < g_hi) & (b < b_hi)).astype(np.uint8) * 255
            if k > 1:
                mask = cv2.erode(mask, kernel)
            n = int((mask > 0).sum())
            if n > best_n:
                best, best_n = i, n
        if best is not None and best_n >= int(cfg["select_x_pixels"]):
            return best
        return None

    # ---------- 决策 ----------

    def _bfs(
        self,
        start: Tuple[int, int],
        board: List[List[int]],
        rows: int,
        cols: int,
    ) -> Dict[Tuple[int, int], int]:
        dist = {start: 0}
        queue = [start]
        head = 0
        while head < len(queue):
            r, c = queue[head]
            head += 1
            for dr, dc in _DIRS4:
                nr, nc = r + dr, c + dc
                if (
                    0 <= nr < rows
                    and 0 <= nc < cols
                    and (nr, nc) not in dist
                    and board[nr][nc] != UNDUG
                ):
                    dist[(nr, nc)] = dist[(r, c)] + 1
                    queue.append((nr, nc))
        return dist

    def _plan(
        self,
        board: List[List[int]],
        dura: np.ndarray,
        char: Tuple[int, int],
        targets: List[Tuple[Tuple[int, int], str]],
        counts: List[Optional[int]],
        cfg: Dict[str, object],
        blocked: Optional[Set[Tuple[int, int]]] = None,
    ) -> Optional[Dict[str, object]]:
        rows, cols = len(board), len(board[0])
        dist = self._bfs(char, board, rows, cols)
        target_cells = [c for c, _ in targets]
        blocked = blocked or set()

        def td(cell: Tuple[int, int]) -> int:
            if not target_cells:
                return 0
            return min(
                abs(cell[0] - t[0]) + abs(cell[1] - t[1]) for t in target_cells
            )

        # 先捡地上的散落道具：走得到就先去捡，再去碰宝箱/梯子。
        # blocked 里的格子是「走上去也没被拾取、下一轮又判成地上立绘」的固定立绘
        # （多半是没认出来的宝箱/梯子），不再理会，否则会在两点之间来回横跳
        loose = sorted(
            (dist[cell], cell)
            for cell in dist
            if cell != char
            and board[cell[0]][cell[1]] == ITEM
            and cell not in target_cells
            and cell not in blocked
        )
        if loose:
            return {"kind": "walk", "cell": loose[0][1], "loose": True}

        # 已知目标优先：能走就直接走，被未挖方块盖着就先挖开它
        for cell, _name in targets:
            if not (0 <= cell[0] < rows and 0 <= cell[1] < cols):
                continue
            if board[cell[0]][cell[1]] != UNDUG and cell in dist:
                return {"kind": "walk", "cell": cell}
            if board[cell[0]][cell[1]] != UNDUG:
                continue
            plan = self._best_dig(board, dura, char, counts, cfg, td, dist, only=cell)
            if plan is None:
                continue
            if int(plan["cost"]) == 0:
                return plan
            return {"kind": "move", "cell": plan["stand"]}

        # 通用开挖：在「槽 × 目标格」里挑评分最高的一手；
        # 最优的那手若够不到，就先走到它的施法位置，下一轮再挖
        plan = self._best_dig(board, dura, char, counts, cfg, td, dist)
        if plan is not None:
            if int(plan["cost"]) == 0:
                return plan
            return {"kind": "move", "cell": plan["stand"]}

        # 一个可用的槽都没有时不再空走位：点数量为 0 的槽会弹「以下道具不足」挡住盘面，
        # 这里直接返回，交给上层收尾
        if not self._usable_slots(counts, cfg):
            return None

        # 兜底：所有道具都够不到任何未挖方块时，走到最近的未挖方块旁
        cands = []
        for (pr, pc), d in dist.items():
            for dr, dc in _DIRS4:
                q = (pr + dr, pc + dc)
                if not (0 <= q[0] < rows and 0 <= q[1] < cols):
                    continue
                if board[q[0]][q[1]] != UNDUG:
                    continue
                cands.append((td(q), d, (pr, pc)))
        if cands:
            cands.sort()
            return {"kind": "move", "cell": cands[0][2]}
        return None

    def _best_dig(
        self,
        board: List[List[int]],
        dura: np.ndarray,
        char: Tuple[int, int],
        counts: List[Optional[int]],
        cfg: Dict[str, object],
        td,
        dist: Dict[Tuple[int, int], int],
        only: Optional[Tuple[int, int]] = None,
    ) -> Optional[Dict[str, object]]:
        """枚举「道具槽 × 目标格」的全部组合，返回评分最高的一手。

        score = 本次挖开的格数 × _W_KILL
              + 只打掉部分耐久的格数 × _W_PART
              − 溢出伤害 × _W_WASTE
              − 空放格数 × _W_EMPTY
              − 道具档位 × _W_SLOT
              − 走位步数 × _W_MOVE
        另外：伤害 3 的道具只打到 1 格时再扣 _W_HI_SINGLE（杀鸡用牛刀）。
        溢出伤害是「伤害超过该格剩余耐久」的部分，且权重大于挖开一格，
        于是耐久 1 的格子不会被伤害 2/3 的道具碰，高级道具只在伤害吃满
        （耐久 ≥ 伤害）且能一次覆盖多格时才出手。
        空放格数按「少挖一格」计，大范围道具因此只在施法范围几乎被吃满时才出手。
        """
        rows, cols = len(board), len(board[0])
        if only is not None:
            cells = [only]
        else:
            cells = [
                (r, c)
                for r in range(rows)
                for c in range(cols)
                if board[r][c] == UNDUG
            ]
        out: List[Dict[str, object]] = []
        for cell in cells:
            if not (0 <= cell[0] < rows and 0 <= cell[1] < cols):
                continue
            if board[cell[0]][cell[1]] != UNDUG:
                continue
            for slot in range(1, int(cfg["max_slot"]) + 1):
                if not counts[slot - 1]:  # 0 或没读出来（None）都不算可用
                    continue
                spec = self._tool_spec(slot, cfg)
                if spec is None:
                    continue
                stand, cost = self._stand_for(cell, spec, char, dist, rows, cols)
                if stand is None:
                    continue
                dmg = int(spec.get("dmg", 1))
                area_name = str(spec.get("area", "single"))
                # 该道具的标称施法范围格数（不按棋盘裁边，边缘落点也算它本来的范围）
                if area_name == "cross":
                    span = 5
                elif area_name == "line":
                    span = max(1, int(cfg["tool_line_seg"]))
                elif area_name in ("row", "col"):
                    span = max(1, int(cfg["tool_line_len"]))
                else:
                    span = 1
                kill = part = waste = empty = 0
                for gr, gc in self._tool_area(stand, cell, spec, rows, cols, cfg):
                    if board[gr][gc] != UNDUG:
                        # 影响范围压在已挖空地 / 地上立绘上：这一格伤害白费
                        empty += 1
                        continue
                    d = int(dura[gr][gc])
                    if d <= dmg:
                        kill += 1
                        waste += dmg - d
                    else:
                        part += 1
                score = (
                    kill * _W_KILL
                    + part * _W_PART
                    - waste * _W_WASTE
                    - empty * _W_EMPTY
                    - (slot - 1) * _W_SLOT
                    - cost * _W_MOVE
                )
                if dmg >= 3 and kill + part <= 1:
                    score -= _W_HI_SINGLE
                out.append(
                    {
                        "kind": "dig",
                        "slot": slot,
                        "cell": cell,
                        "score": score,
                        "kill": kill,
                        "part": part,
                        "waste": waste,
                        "empty": empty,
                        "span": span,
                        "stand": stand,
                        "cost": cost,
                    }
                )
        if not out:
            return None
        # 大范围道具数量稀少：标称范围 ≥ wide_min_cells 的槽（十字 5 格、直线 8 格）
        # 只在施法范围被未挖方块完全吃满（empty==0）时才出手。若这一轮没有一个满足，
        # 才退回全体候选（「尽量」而非硬禁，免得只剩它们够得到前沿时卡住不开挖）。
        wide = int(cfg["wide_min_cells"])
        pool = [p for p in out if p["span"] < wide or p["empty"] == 0] or out
        pool.sort(key=lambda p: (-p["score"], td(p["cell"]), p["slot"], p["cell"]))
        return pool[0]

    @staticmethod
    def _tool_spec(slot: int, cfg: Dict[str, object]) -> Optional[Dict[str, object]]:
        """取第 slot 槽（1 起）的道具规格；未配置返回 None。"""
        specs = cfg["tool_specs"]
        if not isinstance(specs, list) or slot < 1 or slot > len(specs):
            return None
        spec = specs[slot - 1]
        return spec if isinstance(spec, dict) else None

    @staticmethod
    def _stand_for(
        cell: Tuple[int, int],
        spec: Dict[str, object],
        char: Tuple[int, int],
        dist: Dict[Tuple[int, int], int],
        rows: int,
        cols: int,
    ) -> Tuple[Optional[Tuple[int, int]], int]:
        """施法该格时角色应当站的位置，以及走过去的步数。

        施法范围自角色所在格起算：any 原地就能放；ring4 / ring8 要求角色站在
        目标格的四邻 / 八邻之一，故在这些位置里取 BFS 步数最少者。
        走不过去时返回 (None, 0)。
        """
        reach = str(spec.get("reach", "any"))
        if reach == "any":
            return char, 0
        offs = _DIRS4 if reach == "ring4" else _DIRS8
        stands = [
            (dist[(cell[0] + dr, cell[1] + dc)], (cell[0] + dr, cell[1] + dc))
            for dr, dc in offs
            if 0 <= cell[0] + dr < rows
            and 0 <= cell[1] + dc < cols
            and (cell[0] + dr, cell[1] + dc) in dist
        ]
        if not stands:
            return None, 0
        d, stand = min(stands)
        return stand, d

    @staticmethod
    def _shift_span(lo: int, hi: int, size: int) -> Tuple[int, int]:
        """把长度固定的区间整体平移进 [0, size-1]，贴边时保住区间长度。"""
        if lo < 0:
            hi -= lo
            lo = 0
        if hi > size - 1:
            lo -= hi - (size - 1)
            hi = size - 1
        return max(0, lo), min(size - 1, hi)

    @staticmethod
    def _tool_area(
        stand: Tuple[int, int],
        cell: Tuple[int, int],
        spec: Dict[str, object],
        rows: int,
        cols: int,
        cfg: Dict[str, object],
    ) -> List[Tuple[int, int]]:
        """该道具以 cell 为目标时的实际影响格集合（已裁到棋盘内）。

        影响范围一律相对「目标格」：single 只打目标格；cross 是目标格 + 四邻；
        line 自目标格沿「角色 → 目标格」方向再延伸；row / col 是以目标格为中心
        的那一段横线 / 竖线（长度 tool_line_len，贴边时整体平移以尽量打满）。
        """
        tr, tc = cell
        area = str(spec.get("area", "single"))
        cells = [(tr, tc)]
        if area == "cross":
            cells += [(tr + dr, tc + dc) for dr, dc in _DIRS4]
        elif area == "line":
            seg = max(1, int(cfg["tool_line_seg"]))
            dr, dc = tr - stand[0], tc - stand[1]
            if abs(dr) >= abs(dc) and dr != 0:
                dr, dc = (1 if dr > 0 else -1), 0
            elif dc != 0:
                dr, dc = 0, (1 if dc > 0 else -1)
            else:
                dr = dc = 0
            cells += [(tr + dr * i, tc + dc * i) for i in range(1, seg)]
        elif area in ("row", "col"):
            total = max(1, int(cfg["tool_line_len"]))
            half = (total - 1) // 2
            if area == "row":
                lo, hi = SecretDig._shift_span(tc - half, tc - half + total - 1, cols)
                cells = [(tr, c) for c in range(lo, hi + 1)]
            else:
                lo, hi = SecretDig._shift_span(tr - half, tr - half + total - 1, rows)
                cells = [(r, tc) for r in range(lo, hi + 1)]
        return [p for p in cells if 0 <= p[0] < rows and 0 <= p[1] < cols]

    def _usable_slots(
        self, counts: List[Optional[int]], cfg: Dict[str, object]
    ) -> List[int]:
        """数量读出来且大于 0 才算可用。

        读不出来（None）一律按不可用处理：点空格子会弹「以下道具不足」挡住盘面，
        比少用一个槽严重得多。
        """
        out = []
        for i, n in enumerate(counts[: int(cfg["max_slot"])], start=1):
            if n is not None and n > 0:
                out.append(i)
        return out

    # ---------- 执行 ----------

    def _deselect(
        self,
        context: Context,
        cfg: Dict[str, object],
        sel: Optional[int],
    ) -> None:
        """移动前先取消道具选中，避免点空地时误触发道具。"""
        if sel is None:
            return
        x, y = cfg["slot_centers"][sel - 1]
        logger.info(f"SecretDig 取消道具选中（第 {sel} 槽）")
        self._click(context, x, y)
        time.sleep(float(cfg["click_delay"]))

    def _dig(
        self,
        context: Context,
        cfg: Dict[str, object],
        layout: Dict[str, object],
        slot: int,
        cell: Tuple[int, int],
        sel: Optional[int],
    ) -> bool:
        """点道具槽 → 点目标方块 → 点绿色对勾确认。返回是否点到对勾。"""
        if sel != slot:
            x, y = cfg["slot_centers"][slot - 1]
            self._click(context, x, y)
            time.sleep(float(cfg["click_delay"]))
        self._click_cell(context, layout, *cell)
        time.sleep(float(cfg["click_delay"]))
        img = self._screencap(context)
        if img is None:
            return False
        pt = self._find_check(img, cfg, layout, cell)
        if pt is None:
            logger.info(f"SecretDig {cell} 未检出绿色对勾，按已执行处理")
            return False
        self._click(context, pt[0], pt[1])
        return True

    def _click_cell(
        self,
        context: Context,
        layout: Dict[str, object],
        r: int,
        c: int,
    ) -> None:
        x = float(layout["ox"]) + c * float(layout["step_x"])
        y = float(layout["oy"]) + r * float(layout["step_y"])
        self._click(context, x, y)

    def _find_check(
        self,
        img: np.ndarray,
        cfg: Dict[str, object],
        layout: Dict[str, object],
        cell: Tuple[int, int],
    ) -> Optional[Tuple[int, int]]:
        """在目标格内找绿色对勾。

        注意：待确认的格子会同时被游戏画上「亮绿色高亮边框」，它与对勾是同一种
        亮绿，且边框周长约 1400px、对勾仅约 280px，直接取最大连通域只会拿到边框，
        而边框又细（约 6px），一腐蚀就没了。故改为只取格子内区（check_inner），
        把外圈边框裁掉，剩下的绿色只有对勾。
        """
        g = img[:, :, 1].astype(np.int32)
        r = img[:, :, 2].astype(np.int32)
        b = img[:, :, 0].astype(np.int32)
        sx, sy = float(layout["step_x"]), float(layout["step_y"])
        cx = float(layout["ox"]) + cell[1] * sx
        cy = float(layout["oy"]) + cell[0] * sy
        inner = float(cfg["check_inner"])
        hx, hy = sx * inner / 2, sy * inner / 2
        x0 = int(max(0, cx - hx))
        y0 = int(max(0, cy - hy))
        x1 = int(min(img.shape[1], cx + hx))
        y1 = int(min(img.shape[0], cy + hy))
        if x1 - x0 < 12 or y1 - y0 < 12:
            return None
        mask = (
            (g[y0:y1, x0:x1] > int(cfg["green_min"]))
            & (
                g[y0:y1, x0:x1]
                - np.maximum(r[y0:y1, x0:x1], b[y0:y1, x0:x1])
                > int(cfg["green_dom"])
            )
        ).astype(np.uint8) * 255
        k = int(cfg["check_erode"])
        if k > 1:
            k = k if k % 2 == 1 else k + 1
            mask = cv2.erode(mask, np.ones((k, k), np.uint8))
        if not mask.any():
            return None
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
        best_area, best_pt = 0, None
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            if area > best_area:
                best_area = area
                best_pt = (x0 + int(round(cents[i][0])), y0 + int(round(cents[i][1])))
        if best_pt is None or best_area < int(cfg["check_min_area"]):
            return None
        return best_pt

    def _run_node(self, context: Context, node: str) -> None:
        """调用一次 pipeline 公共节点；未识别到目标不影响主流程。"""
        if not node:
            return
        try:
            detail = context.run_task(node)
        except Exception as e:
            logger.error(f"SecretDig 执行节点 {node} 异常: {e}")
            return
        if detail is None:
            logger.info(f"SecretDig 节点 {node} 未识别到目标，跳过")
        else:
            logger.info(f"SecretDig 已执行节点 {node}")

    def _wait_floor(
        self,
        context: Context,
        cfg: Dict[str, object],
        cur: Optional[int],
    ) -> Optional[int]:
        """站上宝箱/梯子后轮询左侧层号，直到进入下一层。"""
        interval = float(cfg["poll_interval"])
        deadline = time.time() + float(cfg["floor_wait_timeout"])
        if cur is None:
            # 起始层号就没读到时无法比对，先留出过渡时间再接受首个读数
            time.sleep(min(3.0, float(cfg["floor_wait_timeout"]) * 0.2))
        while time.time() < deadline:
            img = self._screencap(context)
            if img is None:
                time.sleep(interval)
                continue
            # 先试「到下一层」按钮：探险结束弹窗只能靠它推进，点面板正中无效
            if self._click_next_floor(context, img, cfg):
                time.sleep(float(cfg["click_delay"]))
                continue
            if self._popup_visible(img, cfg):
                px, py = self._popup_point(img, cfg)
                self._click(context, px, py)
                time.sleep(float(cfg["click_delay"]))
                continue
            f = self._read_floor(context, img, cfg)
            if f is not None and (cur is None or f != cur):
                logger.info(f"SecretDig 进入地底{f}层")
                return f
            time.sleep(interval)
        return None
