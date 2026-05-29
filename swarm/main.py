#!/usr/bin/env python3
"""
swarm/main.py -- R&D Lab
------------------------
Receives tasks from cortex via event bus.
Dispatches sub-agents: research, backtest, code-gen, safety-review.
Each sub-agent is an isolated Python process.
Results feed back to cortex for strategy deployment decisions.
"""
import json, os, time, sys, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "nervous"))
from bus import publish, subscribe  # noqa

TASK_QUEUE = ROOT / "swarm" / "research_queue.json"
RESULTS_DIR = ROOT / "swarm" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

SUBAGENT_SCRIPTS = {
    "backtest": ROOT / "swarm" / "backtest_runner.py",
    "code_gen": ROOT / "swarm" / "code_gen.py",
    "safety_review": ROOT / "swarm" / "safety_review.py",
}


def queue_task(task):
    tasks = []
    if TASK_QUEUE.exists():
        try:
            tasks = json.loads(TASK_QUEUE.read_text())
        except json.JSONDecodeError:
            tasks = []
    tasks.append({"ts": time.time(), **task})
    TASK_QUEUE.write_text(json.dumps(tasks[-50:], indent=2))
    publish("swarm.task.queued", task)


def pop_task():
    if not TASK_QUEUE.exists():
        return None
    try:
        tasks = json.loads(TASK_QUEUE.read_text())
    except json.JSONDecodeError:
        return None
    if not tasks:
        return None
    task = tasks.pop(0)
    TASK_QUEUE.write_text(json.dumps(tasks, indent=2))
    return task


def run_subagent(agent_type, task):
    script = SUBAGENT_SCRIPTS.get(agent_type)
    if not script or not script.exists():
        publish("swarm.error", {"error": f"missing_script:{agent_type}", "task": task})
        return None

    result_file = RESULTS_DIR / f"result_{agent_type}_{int(time.time())}.json"
    env = os.environ.copy()
    env["SWARM_TASK"] = json.dumps(task)
    env["SWARM_OUT"] = str(result_file)

    try:
        proc = subprocess.run(
            [sys.executable, str(script)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            cwd=str(ROOT),
        )
        if proc.returncode != 0:
            publish("swarm.error", {
                "error": proc.stderr,
                "agent": agent_type,
                "task": task,
            })
            return None

        if result_file.exists():
            return json.loads(result_file.read_text())
        return {"status": "ok", "stdout": proc.stdout}
    except subprocess.TimeoutExpired:
        publish("swarm.error", {"error": "timeout", "agent": agent_type, "task": task})
        return None


def run():
    last_seq = 0
    while True:
        # Listen for incoming tasks from cortex
        events = subscribe("swarm.task", since_seq=last_seq)
        for ev in events:
            seq = ev.get("seq", 0)
            if seq > last_seq:
                last_seq = seq
            task = ev.get("payload", {})
            queue_task(task)

        # Process one task at a time
        task = pop_task()
        if task:
            ttype = task.get("type", "")
            if ttype == "backtest_request":
                result = run_subagent("backtest", task)
                if result:
                    publish("swarm.backtest.complete", result)
            elif ttype == "strategy_code_gen":
                result = run_subagent("code_gen", task)
                if result:
                    publish("swarm.code_gen.complete", result)
                # Auto-trigger safety review after code gen
                time.sleep(1)
                review = run_subagent("safety_review", {"code": result.get("code", "")})
                publish("swarm.safety_review.complete", review or {"status": "skipped"})
            elif ttype == "research_request":
                env = os.environ.copy()
                env["SWARM_TASK"] = json.dumps(task)
                result_file = RESULTS_DIR / f"result_research_{int(time.time())}.json"
                env["SWARM_OUT"] = str(result_file)
                script = ROOT / "swarm" / "research_agent.py"
                if script.exists():
                    proc = subprocess.run(
                        [sys.executable, str(script)],
                        env=env,
                        capture_output=True,
                        text=True,
                        timeout=120,
                        cwd=str(ROOT),
                    )
                    if proc.returncode == 0 and result_file.exists():
                        result = json.loads(result_file.read_text())
                        publish("swarm.research.complete", result)
                        for hyp in (result.get("hypotheses") or [])[:2]:
                            bt = {"type": "backtest_request", "strategy_id": hyp.get("id"), "hypothesis": hyp}
                            queue_task(bt)
                    else:
                        publish("swarm.error", {"error": proc.stderr or "research_failed", "task": task})
                else:
                    publish("swarm.task.unknown", {"type": ttype, "reason": "missing_research_agent"})
            else:
                publish("swarm.task.unknown", {"type": ttype})

        time.sleep(5)


if __name__ == "__main__":
    run()
