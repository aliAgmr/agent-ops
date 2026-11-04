# Offline tests for infra/bin/figma-shot.py: Figma link parsing, item ids, multipart body.
# Run: python3 tests/figma-shot.test.py
import importlib.util
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("fs", os.path.join(HERE, "..", "infra", "bin", "figma-shot.py"))
fs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fs)
fail = 0


def ok(cond, label, extra=None):
    global fail
    print(("ok   " if cond else "FAIL ") + label + ("" if cond or extra is None else f"\n     {extra}"))
    fail |= not cond


def raises(f, *a):
    try:
        f(*a)
    except ValueError:
        return True
    return False


u = "https://www.figma.com/design/AbCdEf0123456789ExAmPl/Acme-Billing-Dashboard?node-id=46453-128141&t=x"
ok(fs.parse_figma(u) == ("AbCdEf0123456789ExAmPl", "46453:128141"), "design link → key + node (dash → colon)")
ok(fs.parse_figma("https://figma.com/file/AbCdEfGhIjKl/x?node-id=1%3A2")[1] == "1:2", "file link with encoded colon")
ok(fs.parse_figma("https://www.figma.com/design/MAINKEY12345/branch/BRANCHKEY678/x?node-id=3-4")[0] == "BRANCHKEY678", "branch link → branch key")
ok(raises(fs.parse_figma, "https://www.figma.com/design/AbCdEf0123456789ExAmPl/x"), "no node-id → refused")
ok(raises(fs.parse_figma, "https://evil.example/design/AbCdEf0123456789ExAmPl/x?node-id=1-2"), "non-figma host → refused")
ok(raises(fs.parse_figma, "https://figma.com.evil.example/design/AbCdEf0123456789ExAmPl/x?node-id=1-2"), "look-alike host → refused")
ok(raises(fs.parse_figma, "https://evilfigma.com/design/AbCdEf0123456789ExAmPl/x?node-id=1-2"), "evilfigma.com → refused")
ok(raises(fs.parse_figma, "https://www.figma.com/design/../../x/y?node-id=1-2"), "odd key → refused")
ok(raises(fs.parse_figma, "https://www.figma.com/design/AbCdEf0123456789ExAmPl/x?node-id=1-2;rm"), "odd node id → refused")
ok(fs.item_ref("BIL-12") == "BIL-12" and fs.item_ref("PRC-3") == "PRC-3", "item ids")
ok(raises(fs.item_ref, "bil-12") and raises(fs.item_ref, "BIL-12/../x") and raises(fs.item_ref, "BIL"), "bad item ids refused")
body, ctype = fs.multipart({"key": "k1", "policy": "p"}, "figma-1-2.png", b"\x89PNG", "image/png")
b = ctype.split("boundary=")[1]
ok(body.count(f"--{b}".encode()) == 4 and body.endswith(f"--{b}--\r\n".encode()), "multipart: fields + file + closing boundary")
ok(body.index(b'name="key"') < body.index(b'name="file"'), "multipart: policy fields before the file (S3 requires it)")

raise SystemExit(1 if fail else 0)
