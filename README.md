<!-- markdownlint-disable MD033 MD041 -->
<p align="center">
  <img alt="LOGO" src="" width="256" height="256" />
</p>

<div align="center">

# Maa-cookie

基于 [MaaFramework](https://github.com/MaaXYZ/MaaFramework) 的《冲呀！饼干人王国》自动化小助手。  
图像识别 + 模拟控制，解放双手！

[![GitHub release](https://img.shields.io/github/v/release/LILAC325/Maa-cookie)](https://github.com/LILAC325/Maa-cookie/releases)
[![GitHub license](https://img.shields.io/github/license/LILAC325/Maa-cookie)](https://github.com/LILAC325/Maa-cookie/blob/main/LICENSE)

🌟 如果本项目对您有帮助，欢迎在仓库右上角点个 Star！

</div>

---

## 功能一览

| 功能 | 说明 |
|------|------|
| 启动游戏 | 自动启动《冲呀！饼干人王国》 |
| 清除绿点 | 自动点击主界面所有绿点提示 |
| 王国管理所 | 收取王国管理所产出 |
| 王国活动 | 许愿树、相约之所、海上贸易中心、热气球、列车、交易所、研究室、繁星岛、矿山 |
| 冒险 | 世界探险、竞技场、实证战场、今日悬赏、守护之战、热带苏打群岛 |
| 公会 | 公会升级、公会扭蛋、公会奖牌交换所 |
| 页面奖励 | 领取主界面各类奖励 |
| 饼干管理 | 配料分解、脆饼分解、配料强化（含词条筛选） |
| 广场挂机 | 广场地图挂机 |
| 商店购买 | 支持金币、钻石、传奇灵魂石等多种商品条件购买 |
| 独立活动 | 巨型讨伐、年货筹备等限时活动 |

---

## 快速开始

### 下载

1. 前往 [Releases](https://github.com/LILAC325/Maa-cookie/releases) 页面下载最新版本压缩包
2. 解压到本地目录（路径中请勿包含中文或空格）

### 使用

1. 打开《冲呀！饼干人王国》游戏
2. 打开 **MaaPiCli.exe**（或使用 MFAAvalonia GUI）
3. 在界面中勾选需要执行的任务
4. 点击「启动」即可开始

> 首次运行会自动创建 Python 虚拟环境并安装依赖，请保持网络畅通。

### 手动构建

如果你需要从源码构建：

```bash
# 1. 下载 MaaFramework 到 deps 目录
# 2. 安装 Python 依赖
pip install -r requirements.txt

# 3. 执行安装脚本
python install.py

# 4. 产出在 install/ 目录
```

---

## 配置说明

### interface.json

项目使用 MaaFramework v2 接口规范，配置按功能模块拆分：

- `assets/interface.json` — 主配置文件（元数据、控制器、资源路径）
- `assets/resource/tasks/Shop.json` — 商店配置
- `assets/resource/tasks/Activity.json` — 活动配置
- `assets/resource/tasks/Daily.json` — 日常配置
- `assets/resource/tasks/KingdomActivity.json` — 王国活动配置
- `assets/resource/tasks/Adventure.json` — 冒险配置
- `assets/resource/tasks/Guild.json` — 公会配置
- `assets/resource/tasks/Cookie.json` — 饼干管理配置

### 模拟器分辨率

建议使用 **16:9** 分辨率（如 1280x720），否则截图检测可能会失效。

### 自定义动作

项目提供了以下自定义动作（位于 `agent/custom/`）：

- **Screenshot** — 保存截图到指定目录
- **DisableNode** — 动态禁用 pipeline 节点
- **NodeOverride** — 动态覆盖 pipeline 参数
- **ROISequentialClick** — 顺序点击多个 ROI 区域
- **MultiRecognition** — 多算法组合识别（支持 AND/OR/CUSTOM 逻辑 + ROI 运算）
- **Count** — 节点匹配次数计数（达到指定次数后停止）

---

## 项目结构

```
Maa-cookie/
├── agent/                      # Python 代理端
│   ├── main.py                 # 主入口（虚拟环境 + 依赖安装 + AgentServer）
│   ├── custom/
│   │   ├── action/general.py   # 自定义动作
│   │   └── reco/general.py     # 自定义识别
│   └── utils/
│       ├── logger.py           # 日志系统（loguru）
│       └── time.py             # 时间工具
├── assets/
│   ├── interface.json          # Maa 接口主配置
│   ├── MaaCommonAssets/OCR/    # OCR 模型文件
│   └── resource/
│       ├── image/              # 模板匹配图片
│       ├── pipeline/           # 任务流水线定义
│       └── tasks/              # 拆分后的接口配置
├── install.py                  # 构建/安装脚本
├── configure.py                # OCR 模型配置
├── check_resource.py           # 资源校验
└── requirements.txt            # Python 依赖
```

---

## 常见问题

**Q: 运行时提示找不到 Python？**
A: 确保已安装 Python 3.10 或以上版本，并已添加到系统 PATH。

**Q: 部分功能识别不准确？**
A: 检查模拟器分辨率是否为 16:9（推荐 1280x720），并确认游戏界面语言为简体中文。

**Q: 如何查看运行日志？**
A: 日志位于 `debug/custom/` 目录，按日期分文件保存。

---

## 鸣谢

### 核心框架

- [MaaFramework](https://github.com/MaaXYZ/MaaFramework) — 基于图像识别的自动化黑盒测试框架

### UI 支持

- [MFAAvalonia](https://github.com/SweetSmellFox/MFAAvalonia) — 基于 Avalonia UI 构建的 MaaFramework 通用 GUI

### 开发者

感谢以下开发者对本项目作出的贡献：

[![Contributors](https://contrib.rocks/image?repo=LILAC325/Maa-cookie/&max=1000)](https://github.com/LILAC325/Maa-cookie/graphs/contributors)

---

## 联系方式

- Bilibili: [混沌人间体](https://space.bilibili.com/191013445)
- GitHub Issues: [提交反馈](https://github.com/LILAC325/Maa-cookie/issues)

---

## 许可

本项目基于 [MIT License](LICENSE) 开源。