from types import SimpleNamespace

import soak


def _row(t, fps_p, fps_n, dropped=0):
    cam = lambda f, d: {"state": "STREAMING", "decoded_fps": 25.0, "sampled_fps": f, "analyzed_fps": f - d,  # noqa: E731
                        "dropped": d, "decode_errors": 0, "reconnects": 0}
    return {"t": t, "host_cpu_pct": 50.0, "vision-worker": {"cpu_pct": 20.0, "rss_mb": 100.0},
            "mediamtx": {"cpu_pct": 2.0, "rss_mb": 20.0}, "ffmpeg publishers": {"cpu_pct": 30.0, "rss_mb": 50.0},
            "cameras": {"c1": cam(fps_p, dropped), "c2": cam(fps_n, 0)}}


def test_summary_separates_protected_and_normal_cameras_and_counts_drops():
    cams = [{"id": "c1", "protected": True}, {"id": "c2", "protected": False}]
    final = {"cameras": [{"camera_id": c, "frames_sampled_total": 100, "frames_dropped_total": d,
                          "decode_errors_total": 0, "reconnects_total": 0, "frames_decoded_total": 250}
                         for c, d in (("c1", 3), ("c2", 0))]}
    s = soak._summarize([_row(2, 15.0, 5.0), _row(4, 14.0, 5.0, dropped=2)], cams, final, None,
                        SimpleNamespace(input_fps=25))
    assert s["protected"]["sampled_fps"]["mean"] == 14.5
    assert s["protected"]["worst_interval_analyzed_fps"] == 12.0
    assert s["normal"]["analyzed_fps"]["mean"] == 5.0
    assert s["window_dropped"] == 2 and s["totals"]["drop_rate"] == 0.015
    assert s["vision-worker"]["cpu_pct"]["mean"] == 20.0


def test_patterns_are_varied_and_formatted():
    pats = {p.format(s="640x360", r=25) for p in soak.PATTERNS}
    assert len(pats) == len(soak.PATTERNS) >= 4 and all("640x360" in p for p in pats)
