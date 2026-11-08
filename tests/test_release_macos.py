"""scripts/release_macos.py with a recording runner: no keychain, codesign or Apple service is touched."""

import importlib.util
import json
import plistlib
import shutil
import struct
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("release_macos", ROOT / "scripts" / "release_macos.py")
release_macos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release_macos)

COMPANY = "Developer ID Application: Example Inc. (BBBBBBBBBB)"
PERSONAL = "Developer ID Application: Jane Doe (BBBBBBBBBB)"


def macho(path: Path, filetype: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\xcf\xfa\xed\xfe" + struct.pack("<iII", 0x0100000C, 0, filetype) + b"\0" * 32)


def fake_app(tmp_path: Path) -> Path:
    app = tmp_path / "in" / "Email Town.app"
    (app / "Contents").mkdir(parents=True)
    (app / "Contents/Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleExecutable": "Email Town",
                "CFBundleName": "Email Town",
                "CFBundleShortVersionString": "1.2.3",
            }
        )
    )
    macho(app / "Contents/MacOS/Email Town", 2)
    macho(app / "Contents/Frameworks/lib.dylib", 6)
    macho(app / "Contents/Frameworks/objc/_objc.so", 8)
    (app / "Contents/Resources").mkdir(parents=True)
    (app / "Contents/Resources/lib.dylib").symlink_to("../Frameworks/lib.dylib")
    (app / "Contents/Resources/base_library.zip").write_bytes(b"PK not code")
    return app


class Recorder:
    def __init__(
        self,
        identities=(COMPANY,),
        display="flags=0x10000(runtime)\nTimestamp=Nov 8, 2026",
        notary="Accepted",
    ):
        self.calls, self.identities, self.display, self.notary = [], identities, display, notary

    def __call__(self, command, **kwargs):
        self.calls.append(command)
        out, err = "", ""
        if command[:2] == ["security", "find-identity"]:
            out = "".join(f'  {i}) {"A" * 40} "{name}"\n' for i, name in enumerate(self.identities, 1))
        elif command[0] == "cp":
            shutil.copytree(command[-2], command[-1], symlinks=True)
        elif command[0] == "hdiutil":
            Path(command[-1]).write_bytes(b"image")
        elif command[0] == "ditto":
            Path(command[-1]).write_bytes(b"zip")
        elif command[:2] == ["codesign", "--display"]:
            err = self.display
        elif command[:3] == ["xcrun", "notarytool", "submit"]:
            out = json.dumps({"id": "sub-1", "status": self.notary})
        return subprocess.CompletedProcess(command, 0, out, err)


def args(app: Path, output: Path, *, notarize=False, identity=COMPANY, profile="PROFILE"):
    return release_macos.argparse.Namespace(
        app=app, output=output, identity=identity, notary_profile=profile, no_notarize=not notarize
    )


def test_identity_is_never_guessed_between_a_personal_and_a_company_team():
    assert release_macos.resolve_identity(COMPANY, Recorder(())) == COMPANY
    assert release_macos.resolve_identity("", Recorder((COMPANY,))) == COMPANY
    with pytest.raises(ValueError, match="Several") as error:
        release_macos.resolve_identity("", Recorder((PERSONAL, COMPANY)))
    assert COMPANY in str(error.value) and PERSONAL in str(error.value)
    with pytest.raises(ValueError, match="No 'Developer ID"):
        release_macos.resolve_identity("", Recorder(()))


def test_inputs_refuse_development_identity_reused_output_and_missing_notary_profile(tmp_path):
    app = fake_app(tmp_path)
    with pytest.raises(ValueError, match="Developer ID Application"):
        release_macos.check_inputs(app, tmp_path / "out", "Apple Development: Jane (X)", "P", True)
    with pytest.raises(ValueError, match="already exists"):
        release_macos.check_inputs(app, tmp_path, COMPANY, "P", True)
    with pytest.raises(ValueError, match="notary"):
        release_macos.check_inputs(app, tmp_path / "out", COMPANY, None, True)


def test_signs_deepest_first_without_deep_and_entitles_only_executables(tmp_path):
    app = fake_app(tmp_path)
    runner = Recorder()
    counts = release_macos.sign_app(app, COMPANY, runner)
    signs = [c for c in runner.calls if c[:2] == ["codesign", "--force"]]
    targets = [Path(c[-1]).relative_to(app.parent).as_posix() for c in signs]
    assert targets == [
        "Email Town.app/Contents/Frameworks/objc/_objc.so",
        "Email Town.app/Contents/Frameworks/lib.dylib",
        "Email Town.app",
    ]  # the symlinked copy and the zip are not signed; the app is sealed last
    assert all("--deep" not in c and "runtime" in c and "--timestamp" in c for c in signs)
    assert ["--entitlements" in c for c in signs] == [False, False, True]
    assert counts == {"nested_executables": 0, "nested_libraries": 2, "nested_bundles": []}


def test_missing_hardened_runtime_or_timestamp_is_refused(tmp_path):
    app = fake_app(tmp_path)
    with pytest.raises(RuntimeError, match="hardened runtime"):
        release_macos.check_signed(app, Recorder(display="flags=0x0(none)\nTimestamp=x"))
    with pytest.raises(RuntimeError, match="timestamp"):
        release_macos.check_signed(app, Recorder(display="flags=0x10000(runtime)"))


def test_release_without_notarization_writes_an_honest_receipt_and_leaves_the_input_alone(tmp_path):
    app = fake_app(tmp_path)
    before = sorted(p.relative_to(app).as_posix() for p in app.rglob("*"))
    dmg = release_macos.release(args(app, tmp_path / "out"), Recorder())
    assert dmg.name == "Email-Town-1.2.3-macos-arm64.dmg"
    receipt = json.loads((tmp_path / "out" / "release-receipt.json").read_text())
    assert receipt["notarized"] is False and receipt["identity"] == COMPANY
    assert receipt["validation"].startswith("signed, not notarized")
    assert receipt["dmg"]["sha256"] == release_macos.digest(dmg)
    assert sorted(p.relative_to(app).as_posix() for p in app.rglob("*")) == before


def test_notarized_release_staples_app_and_image_and_a_rejection_stops_it(tmp_path):
    app = fake_app(tmp_path)
    runner = Recorder()
    release_macos.release(args(app, tmp_path / "ok", notarize=True), runner)
    submitted = [Path(c[3]).suffix for c in runner.calls if c[:3] == ["xcrun", "notarytool", "submit"]]
    stapled = [Path(c[-1]).suffix for c in runner.calls if c[:3] == ["xcrun", "stapler", "staple"]]
    assert submitted == [".zip", ".dmg"] and stapled == [".app", ".dmg"]
    assert json.loads((tmp_path / "ok" / "release-receipt.json").read_text())["notarized"] is True
    rejected = Recorder(notary="Invalid")
    with pytest.raises(RuntimeError, match="did not accept"):
        release_macos.release(args(app, tmp_path / "bad", notarize=True), rejected)
    assert any(c[:3] == ["xcrun", "notarytool", "log"] for c in rejected.calls)  # Apple's reasons are kept
