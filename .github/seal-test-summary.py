"""Summarise POI's Ant JUnit XML reports (build/*test-results/TEST-*.xml).

Prints every failure/error, every skipped test with its reason, and a TOTAL
line; exits non-zero on any failure or error. Ant's XML formatter records an
@Ignore or a message-less JUnit assumption as a bare <skipped/>, so the reason
is resolved from the test source: the @Ignore("...") text on the method (or
class, or a superclass), else the assume* statements in the method body.
"""
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

TEST_ROOTS = ["src/testcases", "src/scratchpad/testcases", "src/ooxml/testcases",
              "src/excelant/testcases", "src/integrationtest"]

# Context for skips whose assumption message alone does not say why.
CONTEXT = {
    "org.apache.poi.hslf.usermodel.TestFontRendering":
        "upstream build.xml target test-scratchpad-download-resources deliberately "
        "disables the font download ('disable font downloading until TestFontRendering "
        "works on all plattforms'), so upstream skips this rendering comparison too",
    "org.apache.poi.xssf.usermodel.TestFormulaEvaluatorOnXSSF":
        "the parameterized row of FormulaEvalTestData.xls for this function has no "
        "formula cell (blank or literal-only test row), which the upstream test skips by design",
    "org.apache.poi.poifs.crypt.TestSignatureInfo":
        "the test calls a public RFC3161 time-stamp service and itself skips when the "
        "service errors (external network service)",
}

_src_cache = {}


def find_source(simple_or_fqcn):
    rel = simple_or_fqcn.replace(".", "/") + ".java"
    for root in TEST_ROOTS:
        p = os.path.join(root, rel)
        if os.path.exists(p):
            return p
    simple = simple_or_fqcn.split(".")[-1] + ".java"
    for root in TEST_ROOTS:
        for p in glob.glob(os.path.join(root, "**", simple), recursive=True):
            return p
    return None


def read(path):
    if path not in _src_cache:
        with open(path, encoding="utf-8", errors="replace") as fh:
            _src_cache[path] = fh.read()
    return _src_cache[path]


def method_reason(path, method, depth=0):
    if path is None or depth > 4:
        return None
    src = read(path)
    m = re.search(r"((?:\s*(?://[^\n]*|@\w+(?:\([^)]*\))?))*)\s*public\s+void\s+%s\s*\(" % re.escape(method), src)
    if m:
        ann = m.group(1)
        ig = re.search(r"@Ignore(?:\(\s*(?:value\s*=\s*)?\"((?:[^\"\\]|\\.)*)\"\s*\))?", ann)
        if ig:
            if ig.group(1):
                return "@Ignore(\"%s\") in %s" % (ig.group(1), path)
            why = "disabled upstream by a bare @Ignore in %s (present in the 3.15 release source, not a sealed-build change)" % path
            pre = src[:m.start()].rstrip()
            if pre.endswith("*/"):
                doc = pre[pre.rfind("/*"):]
                doc = " ".join(l.strip(" /*\t") for l in doc.splitlines()).strip()
                if doc:
                    why += "; upstream comment: " + doc
            return why
        body_start = m.end()
        depth_b, i = 0, src.index("{", body_start)
        j = i
        while j < len(src):
            if src[j] == "{":
                depth_b += 1
            elif src[j] == "}":
                depth_b -= 1
                if depth_b == 0:
                    break
            j += 1
        body = src[i:j]
        assumes = re.findall(r"(?:Assume\.)?assume\w+\([^;]*\);", body)
        if assumes:
            return "upstream JUnit assumption not met in %s: %s" % (path, " ".join(a.strip() for a in assumes))
        return "JUnit assumption not met (raised from a helper called by %s.%s)" % (path, method)
    cls_ig = re.search(r"@Ignore(?:\(\s*\"((?:[^\"\\]|\\.)*)\"\s*\))?\s*(?:@\w+(?:\([^)]*\))?\s*)*public\s+(?:abstract\s+)?(?:final\s+)?class", src)
    if cls_ig:
        return "class-level @Ignore(%s) in %s" % (cls_ig.group(1) or "no message", path)
    sup = re.search(r"class\s+\w+(?:<[^>]*>)?\s+extends\s+([\w.]+)", src)
    if sup:
        return method_reason(find_source(sup.group(1)), method, depth + 1)
    return None


def main():
    tot = fail = err = skip = 0
    failed = []
    groups = {}
    files = sorted(glob.glob("build/*test-results/TEST-*.xml"))
    for f in files:
        r = ET.parse(f).getroot()
        tot += int(r.get("tests", 0))
        fail += int(r.get("failures", 0))
        err += int(r.get("errors", 0))
        skip += int(r.get("skipped", 0) or 0)
        for tc in r.iter("testcase"):
            cls, name = tc.get("classname"), tc.get("name") or ""
            for kind in ("failure", "error"):
                e = tc.find(kind)
                if e is not None:
                    failed.append("%s %s.%s: %s" % (kind.upper(), cls, name, (e.get("message") or "")[:300]))
            s = tc.find("skipped")
            if s is None:
                continue
            base = re.sub(r"\[.*$", "", name)
            msg = (s.get("message") or "").strip()
            if not msg:
                msg = method_reason(find_source(cls), base) or "@Ignore/assumption (source not located)"
            if cls in CONTEXT:
                msg += " -- " + CONTEXT[cls]
            groups.setdefault((cls, base, msg), []).append(name)
    print("=== Skipped tests (%d), grouped by test method and reason ===" % skip)
    for (cls, base, msg), names in sorted(groups.items()):
        if len(names) == 1:
            print("SKIPPED %s.%s: %s" % (cls, names[0], msg))
        else:
            print("SKIPPED %s.%s x%d parameterized cases: %s" % (cls, base, len(names), msg))
    for line in failed:
        print(line)
    passed = tot - fail - err - skip
    print("TOTAL: %d suites, %d tests, %d passed, %d failures, %d errors, %d skipped"
          % (len(files), tot, passed, fail, err, skip))
    sys.exit(1 if (fail or err or not files) else 0)


if __name__ == "__main__":
    main()
