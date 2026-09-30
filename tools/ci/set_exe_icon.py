"""原地替换 Windows PE（exe/dll）的文件图标。

为什么不用 rcedit：rcedit 会重写整个 PE。对 self-contained 单文件 exe（图标是与
代码一起打包的 68MB 附加数据）会直接把它砍没，exe 无法启动；对框架依赖的
MFAAvalonia.exe 虽然体积上只是资源段变小，但既然有官方 API 可用，就没必要引入
一个会整文件重写的第三方工具。

这里改用 Windows 自带的资源更新 API（BeginUpdateResource / UpdateResource /
EndUpdateResource），只改 .rsrc 里的 RT_ICON / RT_GROUP_ICON，不做整文件重写，
并在写完后就地校验：

  1. 除了 .rsrc 之外的每个节区、以及所有 PE 头，逐字节未变；
  2. 回读 RT_GROUP_ICON，内容与目标 ICO 完全一致。

注：MFAAvalonia.exe 本身没有"末尾附加数据"（overlay）。它的 .rsrc 原始 rawsize 是
116,736 字节（MFA 自带图标较大），换成小图标后 .rsrc 缩到约 36KB，文件总大小随之
277,504 -> 197,632。这个体积变化是资源段变小的正常结果，不是数据被丢弃。

用法：
    python tools/ci/set_exe_icon.py <exe 路径> <ico 路径>
"""

from __future__ import annotations

import ctypes
import struct
import sys
from ctypes import wintypes
from pathlib import Path

RT_ICON = 3
RT_GROUP_ICON = 14
LOAD_LIBRARY_AS_DATAFILE = 0x00000002

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
_kernel32.BeginUpdateResourceW.argtypes = (wintypes.LPCWSTR, wintypes.BOOL)

_kernel32.UpdateResourceW.restype = wintypes.BOOL
# lpType / lpName 用 c_void_p 接收，这样既能传整数 ID（MAKEINTRESOURCE）也能传字符串
_kernel32.UpdateResourceW.argtypes = (
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.WORD,
    ctypes.c_void_p,
    wintypes.DWORD,
)

_kernel32.EndUpdateResourceW.restype = wintypes.BOOL
_kernel32.EndUpdateResourceW.argtypes = (wintypes.HANDLE, wintypes.BOOL)

_kernel32.LoadLibraryExW.restype = wintypes.HMODULE
_kernel32.LoadLibraryExW.argtypes = (wintypes.LPCWSTR, wintypes.HANDLE, wintypes.DWORD)

_kernel32.FreeLibrary.restype = wintypes.BOOL
_kernel32.FreeLibrary.argtypes = (wintypes.HMODULE,)

_RESNAMEPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)
_RESLANGPROC = ctypes.WINFUNCTYPE(
    wintypes.BOOL, wintypes.HMODULE, ctypes.c_void_p, ctypes.c_void_p, wintypes.WORD, ctypes.c_void_p
)

_kernel32.EnumResourceNamesW.restype = wintypes.BOOL
_kernel32.EnumResourceNamesW.argtypes = (
    wintypes.HMODULE,
    ctypes.c_void_p,
    _RESNAMEPROC,
    ctypes.c_void_p,
)
_kernel32.EnumResourceLanguagesW.restype = wintypes.BOOL
_kernel32.EnumResourceLanguagesW.argtypes = (
    wintypes.HMODULE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    _RESLANGPROC,
    ctypes.c_void_p,
)

_kernel32.FindResourceExW.restype = wintypes.HANDLE
_kernel32.FindResourceExW.argtypes = (
    wintypes.HMODULE,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.WORD,
)
_kernel32.SizeofResource.restype = wintypes.DWORD
_kernel32.SizeofResource.argtypes = (wintypes.HMODULE, wintypes.HANDLE)
_kernel32.LoadResource.restype = wintypes.HANDLE
_kernel32.LoadResource.argtypes = (wintypes.HMODULE, wintypes.HANDLE)
_kernel32.LockResource.restype = ctypes.c_void_p
_kernel32.LockResource.argtypes = (wintypes.HANDLE,)


def _make_int_resource(res_id: int) -> ctypes.c_void_p:
    return ctypes.c_void_p(res_id)


def _check(ok, what: str) -> None:
    if not ok:
        raise OSError(ctypes.get_last_error(), what)


def parse_ico(data: bytes) -> list[dict]:
    """拆出 ICO 里的每个尺寸：属性 + 图像数据。"""
    reserved, ico_type, count = struct.unpack_from("<HHH", data, 0)
    if reserved != 0 or ico_type != 1:
        raise ValueError("不是合法的 ICO 文件（ICONDIR 头校验失败）")
    images = []
    offset = 6
    for _ in range(count):
        width, height, colors, _res, planes, bit_count, size, img_offset = struct.unpack_from(
            "<BBBBHHII", data, offset
        )
        offset += 16
        images.append(
            {
                "width": width,
                "height": height,
                "colors": colors,
                "planes": planes,
                "bit_count": bit_count,
                "data": data[img_offset : img_offset + size],
            }
        )
    if not images:
        raise ValueError("ICO 里没有任何图像")
    return images


def build_group_icon(images: list[dict], first_icon_id: int) -> bytes:
    """按 GRPICONDIR 结构组装图标组资源，指向 RT_ICON 的 ID。"""
    parts = [struct.pack("<HHH", 0, 1, len(images))]
    for index, image in enumerate(images):
        parts.append(
            struct.pack(
                "<BBBBHHIH",
                image["width"],
                image["height"],
                image["colors"],
                0,
                image["planes"],
                image["bit_count"],
                len(image["data"]),
                first_icon_id + index,
            )
        )
    return b"".join(parts)


def pe_layout(data: bytes) -> tuple[int, int, list[tuple[str, int, int]]]:
    """返回 (e_lfanew, section_table_end, [(节区名, 文件偏移, raw_size), ...])。

    按节区在文件中的存储顺序（rawptr 升序）列出；.rsrc 通常是最后一个节区，
    排在它后面的任何节区都必须与"未变更"比较一并视为可疑。
    """
    if data[:2] != b"MZ":
        raise ValueError("不是合法的 PE 文件（缺少 MZ 头）")
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        raise ValueError("不是合法的 PE 文件（缺少 PE 签名）")
    num_sections = struct.unpack_from("<H", data, e_lfanew + 6)[0]
    optional_size = struct.unpack_from("<H", data, e_lfanew + 20)[0]
    section_start = e_lfanew + 24 + optional_size
    sections = []
    for index in range(num_sections):
        base = section_start + index * 40
        name = data[base : base + 8].rstrip(b"\0").decode("ascii", "replace")
        raw_size = struct.unpack_from("<I", data, base + 16)[0]
        raw_ptr = struct.unpack_from("<I", data, base + 20)[0]
        sections.append((name, raw_ptr, raw_size))
    sections.sort(key=lambda s: s[1])
    return e_lfanew, section_start + num_sections * 40, sections


def verify_only_rsrc_changed(before: bytes, after: bytes) -> None:
    """确认 after 相对 before 只改了资源段。

    .rsrc 变小会连带改动这几个头部字段，属于正常结果，其余字节一律不允许变化：
      - 可选头 SizeOfInitializedData（.rsrc 属于已初始化数据）
      - 可选头 SizeOfImage
      - 数据目录项 IMAGE_DIRECTORY_ENTRY_RESOURCE（下标 2）
      - .rsrc 节区表条目里的 VirtualSize / SizeOfRawData
    其它节区内容、机器码、以及文件末尾附加数据（overlay）都必须逐字节一致。
    """
    e_lfanew_b, table_end_b, sections_b = pe_layout(before)
    e_lfanew_a, table_end_a, sections_a = pe_layout(after)
    if e_lfanew_a != e_lfanew_b or [s[0] for s in sections_b] != [s[0] for s in sections_a]:
        raise RuntimeError("节区表结构发生变化")

    allowed: list[tuple[int, int]] = []
    if table_end_b > e_lfanew_b:
        opt_b = e_lfanew_b + 24
        allowed += [
            (opt_b + 8, opt_b + 12),  # SizeOfInitializedData
            (opt_b + 56, opt_b + 60),  # SizeOfImage
            (opt_b + 128, opt_b + 136),  # 数据目录：资源表
        ]
        opt_size = struct.unpack_from("<H", before, e_lfanew_b + 20)[0]
        table_start = e_lfanew_b + 24 + opt_size
        for index, (name, _ptr, _size) in enumerate(sections_b):
            if name == ".rsrc":
                entry = table_start + index * 40
                allowed += [(entry + 8, entry + 12), (entry + 16, entry + 20)]

    def is_allowed(offset: int) -> bool:
        return any(start <= offset < end for start, end in allowed)

    for offset in range(table_end_b):
        if not is_allowed(offset) and before[offset] != after[offset]:
            raise RuntimeError(f"PE 头部 {offset} 处被意外改动: {before[offset]:02x} -> {after[offset]:02x}")

    for (name, ptr, size), (_, ptr_a, size_a) in zip(sections_b, sections_a):
        if name == ".rsrc":
            continue
        if size != size_a or before[ptr : ptr + size] != after[ptr_a : ptr_a + size_a]:
            raise RuntimeError(f"非资源节区 {name} 被改动")
    end_b = max(ptr + size for _, ptr, size in sections_b)
    end_a = max(ptr + size for _, ptr, size in sections_a)
    if before[end_b:] != after[end_a:]:
        raise RuntimeError("文件末尾附加数据被改动")


def list_icon_resources(exe: Path) -> dict[int, list[int]]:
    """列出 exe 里 RT_ICON / RT_GROUP_ICON 的 {类型: {资源名: [语言...]}}。"""
    hmod = _kernel32.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    _check(hmod, f"LoadLibraryExW 失败: {exe}")
    result: dict[int, dict[int, list[int]]] = {}
    try:
        for res_type in (RT_ICON, RT_GROUP_ICON):
            found: dict[int, list[int]] = {}

            def on_name(_h, _t, name, _p, _found=found, _type=res_type):
                if not name or name > 0xFFFF:
                    return True
                langs: list[int] = []

                def on_lang(_h2, _t2, _n2, lang, _p2):
                    langs.append(lang)
                    return True

                lang_cb = _RESLANGPROC(on_lang)
                _kernel32.EnumResourceLanguagesW(
                    hmod, _make_int_resource(_type), _make_int_resource(name), lang_cb, None
                )
                _found[name] = langs
                return True

            name_cb = _RESNAMEPROC(on_name)
            _kernel32.EnumResourceNamesW(hmod, _make_int_resource(res_type), name_cb, None)
            result[res_type] = found
    finally:
        _kernel32.FreeLibrary(hmod)
    return result


def read_resource(exe: Path, res_type: int, res_name: int, lang: int) -> bytes:
    """把某个资源读回来，用于写后校验。"""
    hmod = _kernel32.LoadLibraryExW(str(exe), None, LOAD_LIBRARY_AS_DATAFILE)
    _check(hmod, f"LoadLibraryExW 失败: {exe}")
    try:
        hrsrc = _kernel32.FindResourceExW(
            hmod, _make_int_resource(res_type), _make_int_resource(res_name), lang
        )
        if not hrsrc:
            raise OSError(ctypes.get_last_error(), f"找不到资源 {res_type}/{res_name}/{lang}")
        size = _kernel32.SizeofResource(hmod, hrsrc)
        ptr = _kernel32.LockResource(_kernel32.LoadResource(hmod, hrsrc))
        if not ptr:
            raise OSError(ctypes.get_last_error(), f"加载资源失败 {res_type}/{res_name}/{lang}")
        return ctypes.string_at(ptr, size)
    finally:
        _kernel32.FreeLibrary(hmod)


def replace_icon(exe: Path, ico: Path) -> None:
    images = parse_ico(ico.read_bytes())
    before = exe.read_bytes()
    _, _, sections_before = pe_layout(before)
    rsrc_before = next((s for s in sections_before if s[0] == ".rsrc"), None)

    existing = list_icon_resources(exe)
    groups = existing.get(RT_GROUP_ICON, {})
    if groups:
        group_id = min(groups)
        lang = (groups[group_id] or [0])[0]
    else:
        group_id, lang = 1, 0

    group_data = build_group_icon(images, 1)

    handle = _kernel32.BeginUpdateResourceW(str(exe), False)
    _check(handle, f"BeginUpdateResourceW 失败: {exe}")
    try:
        # 先清掉原有的图标资源，避免新旧 RT_ICON 条目混在一起
        for res_type, items in existing.items():
            for res_name, langs in items.items():
                for res_lang in langs or [lang]:
                    _check(
                        _kernel32.UpdateResourceW(
                            handle,
                            _make_int_resource(res_type),
                            _make_int_resource(res_name),
                            res_lang,
                            None,
                            0,
                        ),
                        f"删除资源失败 {res_type}/{res_name}/{res_lang}",
                    )

        for index, image in enumerate(images):
            blob = image["data"]
            _check(
                _kernel32.UpdateResourceW(
                    handle,
                    _make_int_resource(RT_ICON),
                    _make_int_resource(1 + index),
                    lang,
                    blob,
                    len(blob),
                ),
                f"写入 RT_ICON {1 + index} 失败",
            )
        _check(
            _kernel32.UpdateResourceW(
                handle,
                _make_int_resource(RT_GROUP_ICON),
                _make_int_resource(group_id),
                lang,
                group_data,
                len(group_data),
            ),
            f"写入 RT_GROUP_ICON {group_id} 失败",
        )
    except Exception:
        _kernel32.EndUpdateResourceW(handle, True)
        raise
    _check(_kernel32.EndUpdateResourceW(handle, False), "EndUpdateResourceW 失败")

    # 写后校验：除 .rsrc 外逐字节未变，图标组能原样读回来
    after = exe.read_bytes()
    verify_only_rsrc_changed(before, after)
    if read_resource(exe, RT_GROUP_ICON, group_id, lang) != group_data:
        raise RuntimeError("RT_GROUP_ICON 回读内容与目标 ICO 不一致")

    _, _, sections_after = pe_layout(after)
    rsrc_after = next((s for s in sections_after if s[0] == ".rsrc"), None)
    size_before = rsrc_before[2] if rsrc_before else 0
    size_after = rsrc_after[2] if rsrc_after else 0

    print(f"icon replaced: {ico.name} -> {exe.name}")
    print(f"  file size   : {len(before)} -> {len(after)} bytes")
    print(f"  .rsrc size  : {size_before} -> {size_after} bytes (其它节区逐字节未变)")
    print(f"  group icon  : id={group_id} lang={lang} images={len(images)}")


def main() -> int:
    # GitHub Actions 的 Windows runner 控制台是 cp1252，直接 print 中文会抛
    # UnicodeEncodeError（异常信息本身也带中文，连 traceback 都打不出来）。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    replace_icon(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())