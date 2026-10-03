"""Private source-reader process; importing this module never starts the UI."""
import json
import os
import pickle
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path


def run_worker(directory: str) -> int:
    """Read sources once and publish only completed IPC files in a private folder."""
    destination = Path(directory)
    try:
        settings = json.loads((destination / "settings.json").read_text(encoding="utf-8"))
        os.environ["FLOW_READ_WORKER_SETTINGS"] = str(destination / "settings.json")
        import config
        config.ECOMM_FILE = settings["ecomm_file"]
        config.PALLETS_FILE = settings["pallets_file"]
        from data_reader import load_all_flow_data
        started_at = datetime.now()
        last_progress = 0.0

        def progress(percent, stage, detail=""):
            nonlocal last_progress
            now = time.monotonic()
            if now - last_progress < 0.1 and int(percent) not in (0, 1, 100):
                return
            temporary = destination / "progress.tmp"
            temporary.write_text(json.dumps([percent, stage, detail], ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, destination / "progress.json")
            last_progress = now

        result = load_all_flow_data(progress_callback=progress)
        temporary = destination / "result.tmp"
        with temporary.open("wb") as stream:
            pickle.dump({"result": result, "started_at": started_at}, stream, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, destination / "result.pkl")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(run_worker(sys.argv[1]))
