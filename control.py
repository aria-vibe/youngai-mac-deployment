"""Single-instance YoungAi launcher with an independent macOS memory guard."""
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

from assets import ROOT, STEM, required_assets, verified


STATE = ROOT / "run/service.json"
GIB = 1024 ** 3

def write_json(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)

def sample(pid=0):
    result = subprocess.run([str(ROOT / "memory_probe"), str(pid)], capture_output=True, text=True, timeout=5)
    if result.returncode:
        raise RuntimeError(f"memory probe failed ({result.returncode}); refusing to run unmonitored")
    reading = json.loads(result.stdout)
    for key in ["rss", "footprint", "wired", "swap_used", "swapouts", "compressed", "total"]:
        if not isinstance(reading.get(key), (int, float)) or not math.isfinite(reading[key]) or reading[key] < 0:
            raise RuntimeError("invalid memory probe field: " + key)
    if reading.get("pressure") not in (1, 2, 4) or (pid and not reading.get("process_ok")):
        raise RuntimeError("invalid pressure or process sample")
    return reading

def identity(pid):
    result = subprocess.run(["/bin/ps", "-ww", "-p", str(pid), "-o", "lstart=,command="], capture_output=True, text=True, timeout=5)
    return result.stdout.strip() if result.returncode == 0 else ""

def alive_state():
    if not STATE.exists():
        return None
    state = json.loads(STATE.read_text())
    if state.get("running") and identity(state["supervisor_pid"]) == state.get("supervisor_identity"):
        return state
    return None

def stop_child(child, immediate=False):
    if child.poll() is None:
        if immediate:
            child.kill()
            child.wait(timeout=5)
            return
        child.terminate()
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)

def guard_reasons(reading, baseline, config, warning_seconds):
    problems = []
    if reading["wired"] > config["system_wired_limit_gib"] * GIB:
        problems.append(f"system wired memory exceeded {config['system_wired_limit_gib']} GiB")
    for key in ["rss", "footprint"]:
        if reading[key] > config[key + "_limit_gib"] * GIB:
            problems.append(f"{key} exceeded {config[key + '_limit_gib']} GiB")
    if reading["pressure"] >= 4:
        problems.append("critical memory pressure")
    if reading["pressure"] >= 2 and warning_seconds >= config["pressure_warning_seconds"]:
        problems.append("sustained warning memory pressure")
    swap_limit = config["swap_growth_limit_mib"] * 1024 ** 2
    if max(reading["swap_used"] - baseline["swap_used"], reading["swapouts"] - baseline["swapouts"]) > swap_limit:
        problems.append("swap grew beyond configured limit")
    if reading["compressed"] - baseline["compressed"] > config["compressor_growth_limit_gib"] * GIB:
        problems.append("compressor grew beyond configured limit")
    return problems

def supervise():
    lock = (ROOT / "run/supervisor.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    config = json.loads((ROOT / "service.json").read_text())
    baseline = sample()
    if baseline["pressure"] != 1:
        raise RuntimeError("memory pressure is not normal; model was not loaded")
    if baseline["total"] < 128 * GIB:
        raise RuntimeError("this profile requires at least 128 GiB unified memory")
    for key in ["engine_budget_mib", "max_output_tokens", "rss_limit_gib", "footprint_limit_gib",
                "system_wired_limit_gib", "swap_growth_limit_mib", "compressor_growth_limit_gib",
                "pressure_warning_seconds", "sample_seconds"]:
        value = config[key]
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise RuntimeError("invalid positive setting: " + key)
    if guard_reasons(baseline, baseline, config, 0):
        raise RuntimeError("system already exceeds configured memory limits")
    for job in required_assets(config):
        if not verified(job):
            raise RuntimeError("missing/stale asset verification: " + job["name"] + "; run python3 download.py")
    model = ROOT / "models/model" / (STEM + ".gguf")
    vision_path = str(model.parent / "DeepSeek-V4.1-Flash-1017.vision.gguf") if config["vision"] else "off"
    port = None
    for candidate in range(config["port_start"], config["port_start"] + 100):
        with socket.socket() as sock:
            try:
                sock.bind((config["host"], candidate))
                port = candidate
                break
            except OSError:
                continue
    if port is None:
        raise RuntimeError("no available port")
    command = [str(ROOT / "source/ds4-server"), "--metal", "-m", str(model),
               "--zchain", str(model.parent / (STEM + "-grrb-code_fit_n15360-engine")),
               "--engram-dir", str(ROOT / "models/deepseek-engram"),
               "--mem-budget-mb", str(config["engine_budget_mib"]),
               "--host", config["host"], "--port", str(port),
               "--max-output-tokens", str(config["max_output_tokens"]),
               "--batch", "0", "--vision-gguf", vision_path]
    if not config.get("dspark", False):
        command.append("--no-dspark")
    if config["posttrain"]:
        command.extend(["--posttrain", str(model.parent / "posttrain-code-20261022")])
    stamp = time.strftime("%Y%m%d-%H%M%S")
    log_path = ROOT / f"logs/server-{stamp}.log"
    memory_path = ROOT / f"logs/memory-{stamp}.jsonl"
    reason = "server exited"
    requested_stop = False
    def on_signal(signum, frame):
        nonlocal requested_stop
        requested_stop = True
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    with log_path.open("w") as log, memory_path.open("w", buffering=1) as memory:
        child = subprocess.Popen(command, cwd=ROOT / "source", stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
        awake = None
        state = {"running": True, "supervisor_pid": os.getpid(), "supervisor_identity": None,
                 "server_pid": child.pid, "server_identity": None,
                 "base_url": f"http://{config['host']}:{port}/v1", "command": command,
                 "log": str(log_path), "memory_log": str(memory_path), "baseline": baseline,
                 "config": config, "started_at": time.time()}
        warning_since = None
        peak = {key: 0 for key in ["rss", "footprint", "wired", "compressed", "swap_used", "swapouts", "pressure"]}
        try:
            state["supervisor_identity"] = identity(os.getpid())
            state["server_identity"] = identity(child.pid)
            awake = subprocess.Popen(["/usr/bin/caffeinate", "-i", "-w", str(child.pid)])
            write_json(STATE, state)
            print(f"Started PID {child.pid}; {state['base_url']}", flush=True)
            while child.poll() is None:
                if requested_stop:
                    reason = "stop requested"
                    break
                reading = sample(child.pid)
                if not reading["process_ok"]:
                    if child.poll() is not None:
                        break
                    raise RuntimeError("cannot read child memory")
                memory.write(json.dumps(reading) + "\n")
                for key in peak:
                    peak[key] = max(peak[key], reading[key])
                now = time.monotonic()
                warning_since = (warning_since or now) if reading["pressure"] >= 2 else None
                problems = guard_reasons(reading, baseline, config,
                    now - warning_since if warning_since is not None else 0)
                if problems:
                    reason = "MEMORY_GUARD: " + "; ".join(problems)
                    break
                time.sleep(config["sample_seconds"])
        except BaseException as error:
            reason = f"GUARD_ERROR: {error}"
        finally:
            stop_child(child, immediate=reason.startswith(("MEMORY_GUARD:", "GUARD_ERROR:")))
            if awake is not None and awake.poll() is None:
                awake.terminate()
            state.update(running=False, stopped_at=time.time(), stop_reason=reason, exit_code=child.returncode, peak=peak)
            write_json(STATE, state)
            write_json(ROOT / f"verification/run-{stamp}.json", state)
            print(reason, flush=True)

def main():
    for folder in ["run", "logs", "verification"]:
        (ROOT / folder).mkdir(exist_ok=True)
    command = sys.argv[1] if len(sys.argv) > 1 else "status"
    if command == "supervise":
        supervise()
    elif command == "start":
        state = alive_state()
        if state:
            print("Already running: " + state["base_url"])
            return
        with (ROOT / "logs/supervisor.log").open("a") as log:
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "supervise"],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        for _ in range(30):
            state = alive_state()
            if state:
                print(json.dumps(state, indent=2))
                return
            if process.poll() is not None:
                raise RuntimeError("startup refused; see logs/supervisor.log")
            time.sleep(0.1)
        raise RuntimeError("supervisor did not report startup")
    elif command == "stop":
        state = alive_state()
        if not state:
            print("Already stopped")
            return
        os.kill(state["supervisor_pid"], signal.SIGTERM)
        print("Stop requested for the verified YoungAi supervisor")
    elif command == "status":
        state = json.loads(STATE.read_text()) if STATE.exists() else {"running": False}
        state["running"] = bool(alive_state())
        if state["running"]:
            state["current_memory"] = sample(state["server_pid"])
        print(json.dumps(state, indent=2))
    elif command == "probe":
        print(json.dumps(sample(), indent=2))
    else:
        raise SystemExit("Usage: control.py start|stop|status|probe")

if __name__ == "__main__":
    main()
