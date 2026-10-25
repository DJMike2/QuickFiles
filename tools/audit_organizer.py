"""Inspect organizer evidence and independently reviewed placements without applying moves."""
import argparse
from collections import defaultdict
from dataclasses import asdict
import fnmatch
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
from app.core.preparation import Preparation
from app.core.scanner import Scanner
from app.core.validator import Validator


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--preview", action="store_true", help="Also group subjects, match files and independently review placements")
    parser.add_argument("--cache", type=Path, help="Optional local summary cache; omitted means no cache")
    parser.add_argument("--testing-model", action="store_true")
    parser.add_argument("--limit", type=int, help="Evaluate at most this many loose files (diagnostic sample)")
    parser.add_argument("--sample", type=int, help="Deterministic sample spread across file extensions")
    parser.add_argument("--include", action="append", default=[], help="Evaluate matching relative-path globs only")
    parser.add_argument("--trace", type=Path, help="Write private local model inputs/responses as JSONL")
    args = parser.parse_args()
    if args.trace and args.trace.resolve().is_relative_to(args.directory.expanduser().resolve()):
        parser.error("Trace must be outside the folder being evaluated")
    config = AppConfig()
    manager = ModelManager(config.models_dir)
    if args.testing_model:
        manager.model_info, manager.model_path = manager.testing_model_info, manager.testing_model_path
    runtime = AIRuntime(BinaryManager(config.app_data_dir, probe()), manager)
    if args.trace:
        generate = runtime._generate
        def traced(system, user, *positional, **kwargs):
            started_call = perf_counter()
            raw = generate(system, user, *positional, **kwargs)
            with args.trace.open("a", encoding="utf-8") as output:
                output.write(json.dumps({"input": user, "response": raw,
                                         "seconds": round(perf_counter()-started_call, 3)},
                                        ensure_ascii=False) + "\n")
            return raw
        runtime._generate = traced
    if not runtime.available:
        parser.error("Install the local AI engine and model first")
    started = perf_counter()
    status = lambda message: print(message, file=sys.stderr, flush=True)
    try:
        inventory = Scanner(args.directory, exclude_patterns=config.get("exclude_patterns")).scan()
        scanned_files = inventory.total_files
        before = {entry.rel_path: (Path(entry.abs_path).stat().st_size,
                                  Path(entry.abs_path).stat().st_mtime_ns)
                  for entry in inventory.entries}
        if args.include:
            inventory.entries = [entry for entry in inventory.entries if any(
                fnmatch.fnmatchcase(entry.rel_path, pattern) for pattern in args.include)]
        if args.limit is not None and args.sample is not None:
            parser.error("Choose either --limit or --sample")
        sample_size = args.limit if args.limit is not None else args.sample
        if sample_size is not None:
            if sample_size < 1:
                parser.error("Sample size must be positive")
            loose = [entry for entry in inventory.entries if entry.depth == 0]
            if args.sample is not None:
                groups = defaultdict(list)
                for entry in loose:
                    groups[entry.extension].append(entry)
                loose = []
                while any(groups.values()):
                    for extension in sorted(groups):
                        if groups[extension]:
                            loose.append(groups[extension].pop(0))
            inventory.entries = sorted(loose[:sample_size], key=lambda entry: entry.rel_path)
        moves = []
        rejected = []
        catalog = {}
        destinations = {}
        decisions = {}
        course_aliases = {}
        review = {}
        if args.preview:
            planner = Planner(runtime, status, summary_cache_path=args.cache, allow_suffix_on_collision=False)
            plan = planner.plan(inventory)
            prepared = planner.prepared
            catalog = planner.catalog
            destinations = {path: {'profile': asdict(value['profile']), 'existing': value['existing'],
                'members': sorted(value['members']), 'families': sorted(value['families'])}
                for path, value in planner.destinations.items()}
            decisions = planner.decisions
            course_aliases = planner.course_aliases
            review = plan.review
            report = Validator(Path(inventory.root), allow_suffix_on_collision=False).validate_plan(plan)
            moves = [asdict(action) for action in report.valid_actions]
            rejected = [{"source": action.src_rel, "reason": reason}
                        for action, reason in report.rejected_actions]
        else:
            prepared = Preparation(runtime, status, cache_path=args.cache).prepare(inventory)
        after_inventory = Scanner(args.directory, exclude_patterns=config.get("exclude_patterns")).scan()
        after = {entry.rel_path: (Path(entry.abs_path).stat().st_size,
                                 Path(entry.abs_path).stat().st_mtime_ns)
                 for entry in after_inventory.entries}
        print(json.dumps({
            "root": inventory.root, "model": manager.model_info["label"],
            "backend": runtime._server.backend if runtime._server else "cache",
            "seconds": round(perf_counter() - started, 3), "applied": False,
            "inventory_unchanged": before == after and inventory.directories == after_inventory.directories,
            "scanned_files": scanned_files, "evaluated_files": inventory.total_files,
            "sample_limit": sample_size, "sample_method": ("extension spread" if args.sample else
                "first files" if args.limit else "all matching files" if args.include else "all files"),
            "include_patterns": args.include,
            "eligible_files": len(prepared.files),
            "cache_hits": prepared.cache_hits, "protected_folders": inventory.protected_projects,
            "held": prepared.held, "summaries": [asdict(item) for item in prepared.summaries.values()],
            "preview_moves": moves, "rejected_moves": rejected,
            "catalog": catalog, "destinations": destinations, "decisions": decisions,
            "course_aliases": course_aliases,
            "review": review,
        }, ensure_ascii=False, indent=2))
    finally:
        runtime.shutdown()


if __name__ == "__main__":
    main()
