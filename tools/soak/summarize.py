#!/usr/bin/env python3
"""Turn soak result JSON files into a Markdown table.

  python3 summarize.py results/*.json
"""
import json
import sys


def f(v, nd=1):
    return "–" if v is None else f"{v:.{nd}f}"


def main(paths):
    rows = []
    for p in paths:
        r = json.load(open(p))
        a, s = r["params"], r["summary"]
        rows.append((a["detector"], a["resolution"], a["cameras"], r, s))
    rows.sort(key=lambda x: (x[0], x[1] != "640x360", x[2]))
    print("| Detector | Res | N | vision-worker CPU % mean (max) | worker RSS MB | ffmpeg pubs CPU % | MediaMTX CPU % "
          "| Host CPU % mean (baseline) | Decoded fps/cam (min) | Protected analyzed fps mean / min cam / worst 2 s "
          "| Normal analyzed fps mean / min cam | Dropped | Decode err | Reconnects |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for det, res, n, r, s in rows:
        w, pub, mtx = s["vision-worker"], s["ffmpeg publishers"], s["mediamtx"]
        pr, no = s["protected"], s["normal"]
        print(f"| {det} | {res} | {n} | {f(w['cpu_pct']['mean'])} ({f(w['cpu_pct']['max'])}) | {f(w['rss_mb_max'], 0)} "
              f"| {f(pub['cpu_pct']['mean'])} | {f(mtx['cpu_pct']['mean'])} "
              f"| {f(s['host_cpu_pct']['mean'])} ({f(r.get('baseline_host_cpu_pct'))}) "
              f"| {f(s['decoded_fps']['mean'])} ({f(s['decoded_fps']['min_camera'])}) "
              f"| {f(pr['analyzed_fps']['mean'], 2)} / {f(pr['analyzed_fps']['min_camera'], 2)} / {f(pr['worst_interval_analyzed_fps'], 1)} "
              f"| {f(no['analyzed_fps']['mean'], 2)} / {f(no['analyzed_fps']['min_camera'], 2)} "
              f"| {s['window_dropped']} | {s['window_decode_errors']} | {s['window_reconnects']} |")
        if r.get("fault"):
            print(f"|  | fault on {r['fault']['camera']}: RECONNECTING after {r['fault'].get('to_reconnecting_s')} s, "
                  f"STREAMING {r['fault'].get('recovery_after_restart_s')} s after restart |||||||||||||")


if __name__ == "__main__":
    main(sys.argv[1:])
