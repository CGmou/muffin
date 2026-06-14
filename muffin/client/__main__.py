"""Command-line client for submitting and inspecting jobs.

Examples:
  # Submit a Blender job, 100 frames, 5 frames per task
  python -m muffin.client submit --name shotA --dcc blender --renderer CYCLES \
      --scene //server/proj/shotA.blend --output //server/proj/out/sh_ \
      --start 1 --end 100 --chunk 5 --priority 60

  # Submit a fake job to test the farm end-to-end (no DCC needed)
  python -m muffin.client submit --name smoke --dcc mock --scene none \
      --output ./mockout --start 1 --end 20

  python -m muffin.client jobs
  python -m muffin.client job <job_id>
  python -m muffin.client workers
  python -m muffin.client dccs
"""

import argparse
import json
import sys

import requests

from .. import config


def _url(path: str) -> str:
    return f"{config.MANAGER_URL.rstrip('/')}{path}"


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_submit(a: argparse.Namespace) -> None:
    import getpass
    import socket
    try:
        submitter = f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:
        submitter = socket.gethostname()
    payload = {
        "name": a.name,
        "dcc": a.dcc,
        "renderer": a.renderer,
        "scene_path": a.scene,
        "output_path": a.output,
        "frame_start": a.start,
        "frame_end": a.end,
        "frame_step": a.step,
        "chunk_size": a.chunk,
        "priority": a.priority,
        "extra": json.loads(a.extra) if a.extra else {},
        "submitter": submitter,
    }
    r = requests.post(_url("/api/jobs"), json=payload, timeout=15)
    r.raise_for_status()
    job = r.json()
    print(f"submitted job {job['id']} ({job['name']}) "
          f"frames {job['frame_start']}-{job['frame_end']}, status={job['status']}")


def cmd_jobs(a: argparse.Namespace) -> None:
    _print(requests.get(_url("/api/jobs"), timeout=15).json())


def cmd_job(a: argparse.Namespace) -> None:
    _print(requests.get(_url(f"/api/jobs/{a.job_id}"), timeout=15).json())


def cmd_workers(a: argparse.Namespace) -> None:
    _print(requests.get(_url("/api/workers"), timeout=15).json())


def cmd_dccs(a: argparse.Namespace) -> None:
    _print(requests.get(_url("/api/dccs"), timeout=15).json())


def cmd_action(a: argparse.Namespace) -> None:
    r = requests.post(_url(f"/api/jobs/{a.job_id}/{a.action}"), timeout=15)
    r.raise_for_status()
    print(f"{a.action} -> {r.json()}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="muffin.client", description="Muffin farm client")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("submit", help="submit a render job")
    s.add_argument("--name", required=True)
    s.add_argument("--dcc", required=True, help="blender|maya|houdini|nuke|mock")
    s.add_argument("--renderer", default="")
    s.add_argument("--scene", required=True)
    s.add_argument("--output", default="")
    s.add_argument("--start", type=int, default=1)
    s.add_argument("--end", type=int, default=1)
    s.add_argument("--step", type=int, default=1)
    s.add_argument("--chunk", type=int, default=config.DEFAULT_CHUNK_SIZE)
    s.add_argument("--priority", type=int, default=50)
    s.add_argument("--extra", default="", help="JSON dict of renderer-specific options")
    s.set_defaults(func=cmd_submit)

    sub.add_parser("jobs", help="list jobs").set_defaults(func=cmd_jobs)

    sj = sub.add_parser("job", help="show one job + its tasks")
    sj.add_argument("job_id")
    sj.set_defaults(func=cmd_job)

    sub.add_parser("workers", help="list workers").set_defaults(func=cmd_workers)
    sub.add_parser("dccs", help="list available DCCs").set_defaults(func=cmd_dccs)

    for action in ("pause", "resume", "cancel", "retry"):
        sp = sub.add_parser(action, help=f"{action} a job")
        sp.add_argument("job_id")
        sp.set_defaults(func=cmd_action, action=action)

    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        args.func(args)
    except requests.RequestException as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
