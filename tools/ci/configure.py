from pathlib import Path

import shutil

assets_dir = Path(__file__).parent.parent.parent / "assets"


def configure_ocr_model():
    # 资源根是 assets/resource（见 assets/interface.json 的 {PROJECT_DIR}/resource），
    # MaaFramework 只会在 <资源根>/model/ocr 下找 OCR 模型。
    # 之前这里拷到 resource/base/model/ocr，包内加载不到模型，OCR 识别全部失败
    # （日志表现为 OCRResMgr "Failed to load det or rec" / "recer_ is null"），
    # 大量依赖 OCR 的任务因此无法完成。模型版本也要与 configure.py 一致。
    shutil.copytree(
        assets_dir / "MaaCommonAssets" / "OCR" / "ppocr_v5" / "zh_cn",
        assets_dir / "resource" / "model" / "ocr",
        dirs_exist_ok=True,
    )


if __name__ == "__main__":
    configure_ocr_model()
