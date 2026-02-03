import os
import json
from datetime import datetime

from PIL import Image
from maa.agent.agent_server import AgentServer
from maa.custom_action import CustomAction
from maa.context import Context

from utils import logger


@AgentServer.custom_action("Screenshot")
class Screenshot(CustomAction):
    """
    自定义截图动作，保存当前屏幕截图到指定目录。

    参数格式:
    {
        "save_dir": "保存截图的目录路径"
    }
    """

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:

        # image array(BGR)
        screen_array = context.tasker.controller.cached_image

        # Check resolution aspect ratio
        height, width = screen_array.shape[:2]
        aspect_ratio = width / height
        target_ratio = 16 / 9
        # Allow small deviation (within 1%)
        if abs(aspect_ratio - target_ratio) / target_ratio > 0.01:
            logger.error(f"当前模拟器分辨率不是16:9! 当前分辨率: {width}x{height}")

        # BGR2RGB
        if len(screen_array.shape) == 3 and screen_array.shape[2] == 3:
            rgb_array = screen_array[:, :, ::-1]
        else:
            rgb_array = screen_array
            logger.warning("当前截图并非三通道")

        img = Image.fromarray(rgb_array)

        save_dir = json.loads(argv.custom_action_param)["save_dir"]
        os.makedirs(save_dir, exist_ok=True)
        now = datetime.now()
        img.save(f"{save_dir}/{self._get_format_timestamp(now)}.png")
        logger.info(f"截图保存至 {save_dir}/{self._get_format_timestamp(now)}.png")

        task_detail = context.tasker.get_task_detail(argv.task_detail.task_id)
        logger.debug(
            f"task_id: {task_detail.task_id}, task_entry: {task_detail.entry}, status: {task_detail.status._status}"
        )

        return CustomAction.RunResult(success=True)

    def _get_format_timestamp(self, now):

        date = now.strftime("%Y.%m.%d")
        time = now.strftime("%H.%M.%S")
        milliseconds = f"{now.microsecond // 1000:03d}"

        return f"{date}-{time}.{milliseconds}"


@AgentServer.custom_action("DisableNode")
class DisableNode(CustomAction):
    """
    将特定 node 设置为 disable 状态 。

    参数格式:
    {
        "node_name": "结点名称"
    }
    """

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:

        node_name = json.loads(argv.custom_action_param)["node_name"]

        context.override_pipeline({f"{node_name}": {"enabled": False}})

        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("NodeOverride")
class NodeOverride(CustomAction):
    """
    在 node 中执行 pipeline_override 。

    参数格式:
    {
        "node_name": {"被覆盖参数": "覆盖值",...},
        "node_name1": {"被覆盖参数": "覆盖值",...}
    }
    """

    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:

        ppover = json.loads(argv.custom_action_param)

        if not ppover:
            logger.warning("No ppover")
            return CustomAction.RunResult(success=True)

        logger.debug(f"NodeOverride: {ppover}")
        context.override_pipeline(ppover)

        return CustomAction.RunResult(success=True)


@AgentServer.custom_action("ROISequentialClick")
class ROISequentialClick(CustomAction):
    """
    顺序点击18个预设ROI位置的自定义动作
    
    参数格式:
    {
        "roi_list": [
            [x1, y1, width1, height1],
            [x2, y2, width2, height2],
            ...
        ],
        "click_center": true  # 是否点击ROI中心，默认为true
    }
    """
    
    # 类变量，用于记录当前点击的索引
    current_index = 0
    
    def run(
        self,
        context: Context,
        argv: CustomAction.RunArg,
    ) -> CustomAction.RunResult:
        
        # 解析参数
        param = json.loads(argv.custom_action_param)
        roi_list = param.get("roi_list", [])
        click_center = param.get("click_center", True)
        
        if not roi_list:
            logger.error("ROI列表为空")
            return CustomAction.RunResult(success=False)
        
        # 获取当前要点击的ROI
        roi = roi_list[self.current_index]
        
        # 计算点击位置
        if click_center:
            # 点击ROI中心
            x = roi[0] + roi[2] // 2
            y = roi[1] + roi[3] // 2
            click_box = [x, y, 1, 1]
        else:
            # 点击ROI左上角
            click_box = [roi[0], roi[1], 1, 1]
        
        # 执行点击操作
        try:
            action_result = context.run_action(
                "Click",
                box=click_box,
                pipeline_override={
                    "Click": {
                        "action": "Click",
                        "target": click_box
                    }
                },
            )
            
            if action_result and action_result.success:
                logger.info(f"成功点击第{self.current_index + 1}个ROI位置: {roi}")
                
                # 更新索引，循环使用
                self.current_index = (self.current_index + 1) % len(roi_list)
                
                return CustomAction.RunResult(success=True)
            else:
                logger.error(f"点击第{self.current_index + 1}个ROI位置失败")
                return CustomAction.RunResult(success=False)
                
        except Exception as e:
            logger.error(f"执行点击操作时发生错误: {e}")
            return CustomAction.RunResult(success=False)



