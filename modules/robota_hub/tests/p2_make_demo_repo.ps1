# Build a throwaway repo that mimics CUTORA's queue layout, for rehearsing agy_queue.py
$ErrorActionPreference = "Stop"
$demo = "D:\0_Robota\workspace\demo_repo"
if (Test-Path $demo) { throw "demo repo already exists: $demo (delete it manually first)" }
New-Item -ItemType Directory -Force -Path "$demo\tools","$demo\docs\plan\queue\_done","$demo\cutora","$demo\tests" | Out-Null
Copy-Item D:\0_Dev\CUTORA\tools\queue_runner.py "$demo\tools\queue_runner.py"

@'
# Rules for every task (demo)
1. When finished and verified, write docs/plan/queue/_done/<TASK>.ok containing HEAD and what you verified.
2. If you cannot finish, write docs/plan/queue/_done/<TASK>.stop with the reason.
3. Before finishing, git commit only your own changes. First line: "<TASK>: summary". Never git add -A.
'@ | Set-Content -Encoding utf8 "$demo\docs\plan\queue\_RULES_FOR_EVERY_TASK.md"

@'
def add(a, b):
    return a - b  # BUG
'@ | Set-Content -Encoding utf8 "$demo\cutora\calc.py"

@'
# D01 · fix add()
`cutora/calc.py` add() subtracts instead of adding. Fix it.
Add `tests/test_calc.py` with a test for add(2, 3) == 5 and run it with `python -m pytest tests -q`
(if pytest is not installed, run the test with plain `python -c` instead and say so in the .ok).
'@ | Set-Content -Encoding utf8 "$demo\docs\plan\queue\D01.md"

@'
# D02 · add mul()
Add `mul(a, b)` to `cutora/calc.py` and a test for it in `tests/test_calc.py`. Verify, write .ok, commit.
'@ | Set-Content -Encoding utf8 "$demo\docs\plan\queue\D02.md"

"docs/plan/queue/D01.md`ndocs/plan/queue/D02.md" | Set-Content -Encoding utf8 "$demo\tools\grok-queue.txt"
"docs/plan/queue/_done/`ndocs/plan/queue/_log/`ndocs/plan/queue/_progress.txt`ntools/grok-queue.txt`ntools/grok-done.txt`n__pycache__/" | Set-Content -Encoding utf8 "$demo\.gitignore"

Push-Location $demo
git init -q
git add -A
git -c user.name=Robota -c user.email=robota@local commit -q -m "demo baseline"
git config user.name "Robota-agent"
git config user.email "agent@local"
Pop-Location
"demo repo ready: $demo"
