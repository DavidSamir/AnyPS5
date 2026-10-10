from pathlib import Path
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent / "macos"))
from import_fixture import fixture as import_fixture
from test_guest_symbol_names import guest_module

PACKAGER = Path(__file__).resolve().parents[4] / "tools" / "package_macos_app.py"
LC_LOAD_DYLIB = 0xC
EXECUTABLE_PATH = "@executable_path/"
MODULE = "libguest.prx"


def load_dylibs(image):
    count, = struct.unpack_from("<I", image, 16)
    offset = 32
    names = []
    for _ in range(count):
        command, size = struct.unpack_from("<II", image, offset)
        if command == LC_LOAD_DYLIB:
            name, = struct.unpack_from("<I", image, offset + 8)
            names.append(image[offset + name:offset + size].split(b"\0")[0].decode())
        offset += size
    return names


def relink(relinker, case, modules):
    source = case / "source"
    (source / "sce_sys").mkdir(parents=True)
    (source / "sce_sys" / "param.json").write_text(json.dumps({"titleId": "TEST00000"}))
    (source / "input.elf").write_bytes(bytes(import_fixture()))
    for name, image in modules.items():
        (source / "sce_module").mkdir(exist_ok=True)
        (source / "sce_module" / name).write_bytes(image)
    relinked = case / "relinked"
    relinked.mkdir()
    options = [] if modules else ["--skip-sce-module"]
    result = subprocess.run([str(relinker), "--macos", *options, str(source / "input.elf"), str(relinked / "eboot")],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return source, relinked


def package(case, source, relinked):
    libs = case / "libs"
    libs.mkdir()
    (libs / "libkernel.prx").write_bytes(b"prx")
    vulkan = case / "vulkan"
    (vulkan / "lib").mkdir(parents=True)
    (vulkan / "share" / "vulkan" / "icd.d").mkdir(parents=True)
    for name in ("libvulkan.1.dylib", "libMoltenVK.dylib"):
        (vulkan / "lib" / name).write_bytes(b"dylib")
    (vulkan / "share" / "vulkan" / "icd.d" / "MoltenVK_icd.json").write_text(json.dumps({"ICD": {"library_path": "../../../lib/libMoltenVK.dylib"}}))
    bundle = case / "Title.app"
    result = subprocess.run([sys.executable, str(PACKAGER), "--relinked", str(relinked), "--game", str(source),
                             "--libs", str(libs), "--vulkan", str(vulkan), str(bundle)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, (result.stdout, result.stderr)
    return bundle


def guest_dependencies(bundle):
    names = load_dylibs((bundle / "Contents" / "MacOS" / "eboot").read_bytes())
    return [name[len(EXECUTABLE_PATH):] for name in names if name.startswith(EXECUTABLE_PATH)]


def check_guest_modules(bundle, expected):
    macos = bundle / "Contents" / "MacOS"
    game = bundle / "Contents" / "Resources" / "game"
    dependencies = guest_dependencies(bundle)
    assert dependencies == expected, dependencies
    for dependency in dependencies:
        assert (macos / dependency).is_file(), dependency
        assert (macos / dependency).samefile(game / dependency), dependency
    assert not os.path.isabs(os.readlink(macos / "app0")), os.readlink(macos / "app0")
    assert (macos / "app0").samefile(game / "app0")
    assert (game / "app0" / "sce_sys" / "param.json").is_file()


def main():
    relinker = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory(prefix="anyps5-macos-app-package-") as directory:
        work = Path(directory)

        case = work / "module"
        case.mkdir()
        source, relinked = relink(relinker, case, {MODULE: guest_module([("AAAAAAAAAAA", True)])})
        bundle = package(case, source, relinked)
        expected = [f"app0/sce_module/{MODULE}.guest.prx"]
        check_guest_modules(bundle, expected)
        moved = work / "moved" / "Renamed.app"
        moved.parent.mkdir()
        shutil.move(str(bundle), str(moved))
        check_guest_modules(moved, expected)

        case = work / "plain"
        case.mkdir()
        source, relinked = relink(relinker, case, {})
        check_guest_modules(package(case, source, relinked), [])
    print("macOS application package tests passed")


if __name__ == "__main__":
    main()
