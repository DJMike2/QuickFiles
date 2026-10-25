"""Time an AI organization preview. Never apply moves or modify the scanned folder."""
import argparse
import json
from pathlib import Path
import sys
from time import perf_counter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.binary_manager import BinaryManager
from app.ai.hardware_probe import probe
from app.ai.model_manager import ModelManager
from app.ai.runtime import AIRuntime
from app.config import AppConfig
from app.core.planner import Planner
from app.core.scanner import Scanner
from app.core.validator import Validator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--testing-model', action='store_true', help='Use the retained 9B model instead of 4B')
    parser.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    config = AppConfig()
    manager = ModelManager(config.models_dir)
    if args.testing_model:
        manager.model_info = manager.testing_model_info
        manager.model_path = manager.testing_model_path
    manager.runtime_settings['seed'] = args.seed
    runtime = AIRuntime(BinaryManager(config.app_data_dir, probe()), manager)
    if not runtime.available:
        parser.error(f"AI engine or {manager.model_path} is missing; install it first")
    started = perf_counter()
    try:
        inventory = Scanner(args.directory, exclude_patterns=config.get('exclude_patterns')).scan()
        plan = Planner(runtime, progress_cb=lambda message: print(message, file=sys.stderr)).plan(inventory)
        report = Validator(Path(inventory.root), allow_suffix_on_collision=False).validate_plan(plan)
        print(json.dumps({
            'model': manager.model_info['label'], 'seed': args.seed,
            'backend': runtime._server.backend if runtime._server else 'not loaded',
            'seconds_including_scan_and_model_loading': round(perf_counter()-started, 3),
            'scanned_files': inventory.total_files, 'proposed_moves': len(report.valid_actions),
            'protected_projects': inventory.protected_projects,
            'moves': [{'before': action.src_rel, 'after': action.dst_rel} for action in report.valid_actions],
            'rejected_moves': len(report.rejected_actions), 'applied': False,
        }, indent=2))
    finally:
        runtime.shutdown()


if __name__ == '__main__':
    main()
