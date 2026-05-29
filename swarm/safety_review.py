#!/usr/bin/env python3
"""
swarm/safety_review.py — Code Safety Auditor
---------------------------------------------
Reads generated code and checks for:
  - Fatal risk: No stop-loss
  - Fatal risk: Position size > 5% of equity
  - High risk: Hardcoded credentials or paths
  - High risk: Recursive/infinite loops
  - Medium risk: Missing error handling
  - Medium risk: Unbounded memory growth
Outputs PASS or FAIL with reasons.
"""
import json, os, re, time
from pathlib import Path

TASK = json.loads(os.environ.get("SWARM_TASK", "{}"))
OUT = os.environ.get("SWARM_OUT", "/tmp/swarm_safety_review.json")
CODE = TASK.get("code", "")

def check_no_sl(code):
    """Strategy code should have stop-loss logic."""
    has_sl = bool(re.search(r'\bsl\b|\bstop[_-]?loss\b|\bstoploss\b', code, re.I))
    return has_sl, "No stop-loss mechanism found" if not has_sl else None

def check_position_size(code):
    """Look for position sizing and ensure it's bounded."""
    has_size = bool(re.search(r'lot[s]?|position.*size|NormalizeDouble', code, re.I))
    has_cap = bool(re.search(r'0\.0[0-5]|MathMin.*lot|Min.*lot', code, re.I))
    if not has_size:
        return False, "No position sizing logic found"
    if not has_cap:
        return False, "Position sizing found but no explicit cap (risk > 5% possible)"
    return True, None

def check_hardcoded_credentials(code):
    """No API keys, passwords, or account numbers embedded."""
    patterns = [
        r'password\s*=\s*["\'][^"\']+["\']',
        r'api[_-]?key\s*=\s*["\'][^"\']+["\']',
        r'account\s*\d{5,}',
        r'secret\s*=\s*["\'][^"\']+["\']',
    ]
    for p in patterns:
        if re.search(p, code, re.I):
            return False, f"Hardcoded credential pattern matched: {p[:40]}"
    return True, None

def check_infinite_loops(code):
    """Detect simple infinite loop patterns (while true without break)."""
    loop_pattern = r'while\s*True\s*:\s*\n(\s+(?!if|break|return|sys\.exit).*\n)*'
    loops = re.findall(r'while\s*True\s*:', code)
    if loops:
        # Check if break/return is in same scope
        for m in re.finditer(r'while\s*True\s*:', code):
            after = code[m.end():m.end()+500]
            if 'break' not in after and 'return' not in after:
                return False, "Potential infinite loop (while True without break/return)"
    return True, None

def check_memory(code):
    """Unbounded collections without limits."""
    unbounded = re.findall(r'(list|deque|dict)\(\).*append|\.extend\(', code)
    bounded = bool(re.search(r'maxlen|limit|truncate', code, re.I))
    if unbounded and not bounded:
        return False, "Unbounded collection growth detected"
    return True, None

def check_error_handling(code):
    """Look for try/except blocks."""
    has_try = bool(re.search(r'try\s*:', code))
    has_except = bool(re.search(r'except\s*', code))
    if not has_try or not has_except:
        return False, "Missing error handling (try/except)"
    return True, None

def run():
    checks = [
        ("stop_loss", check_no_sl),
        ("position_size", check_position_size),
        ("credentials", check_hardcoded_credentials),
        ("infinite_loops", check_infinite_loops),
        ("memory_bounds", check_memory),
        ("error_handling", check_error_handling),
    ]
    
    passed = True
    check_results = []
    for name, checker in checks:
        ok, reason = checker(CODE)
        severity = "critical" if name in ("stop_loss", "position_size") else "warning"
        if not ok:
            passed = False
        check_results.append({
            "name": name,
            "passed": ok,
            "severity": severity,
            "reason": reason,
        })
    
    result = {
        "agent": "safety_review",
        "ts": time.time(),
        "overall": "PASS" if passed else "FAIL",
        "checks": check_results,
        "code_length": len(CODE),
    }
    with open(OUT, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Safety review: {result['overall']}. Wrote {OUT}")

if __name__ == "__main__":
    run()
