import re,sys,subprocess,tempfile,shutil,os
if not shutil.which("node"): print("SKIP node --check: node not installed"); sys.exit(0)
bad=0
for f in sys.argv[1:]:
    if not os.path.exists(f): continue
    for i,m in enumerate(re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", open(f).read(), re.S)):
        with tempfile.NamedTemporaryFile("w",suffix=".js",delete=False) as t: t.write(m.group(1))
        r=subprocess.run(["node","--check",t.name],capture_output=True,text=True); os.unlink(t.name)
        if r.returncode: bad=1; print("SYNTAX ERROR", f, "script", i, r.stderr[:300])
print("JS syntax", "FAILED" if bad else "ok"); sys.exit(bad)
