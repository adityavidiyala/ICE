from safety.inspection import inspect_command, CommandTier
from tools import ICE_TOOLS
from ui.prompt import run_repl

cases = [
    ('git status',              CommandTier.SAFE),
    ('pip list',                CommandTier.SAFE),
    ('python --version',        CommandTier.SAFE),
    ('Get-ChildItem C:\\Users', CommandTier.SAFE),
    ('git status && pip list',  CommandTier.SAFE),
    ('pip install requests',    CommandTier.PROMPT),
    ('git commit -m test',      CommandTier.PROMPT),
    ('python main.py',          CommandTier.PROMPT),
]

all_pass = True
for cmd, expected in cases:
    r = inspect_command(cmd)
    ok = r.tier == expected
    if not ok:
        all_pass = False
    mark = "PASS" if ok else "FAIL"
    print(f"  {mark}  [{r.tier.value:8}]  {cmd!r}")

print()
print("Tools:", [t.__name__ for t in ICE_TOOLS])
print("All tests passed!" if all_pass else "SOME TESTS FAILED")
