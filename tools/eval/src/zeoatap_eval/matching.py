"""Event-level matching (SAFETY_VALIDATION §4).

An incident is *eligible* for a ground-truth event when
  * it has the same event_type,
  * it is in scope for the event's session (shares a camera with it), and
  * their time intervals overlap, or the incident start falls within
    [GT.start - pre_tolerance, GT.end + latency_budget].

Each GT event matches at most one incident and vice versa. The assignment
is solved with the Hungarian algorithm (scipy linear_sum_assignment) on
temporal IoU. Weights are `BIG + IoU` for eligible pairs, so the assignment
first maximises the number of matched events and then, among those, the
total IoU. (Pure IoU weights could leave an event unmatched in favour of a
higher-IoU pair, which would understate recall.)

Eligible but unassigned incidents are *duplicates* of the GT event they
overlap most (not true positives, not false alerts). Incidents eligible for
no GT event are false positives.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from .model import GTEvent, Incident, Session


def temporal_iou(a0: float, a1: float, b0: float, b1: float) -> float:
    inter = max(0.0, min(a1, b1) - max(a0, b0))
    union = max(a1, b1) - min(a0, b0)
    if union <= 0:
        # Two instants at the same time are a perfect match.
        return 1.0 if a0 == b0 else 0.0
    return inter / union


def eligible(gt: GTEvent, inc: Incident, latency_budget: float, pre_tolerance: float = 1.0) -> bool:
    if gt.event_type != inc.event_type:
        return False
    overlap = inc.start <= gt.end and inc.end >= gt.start
    in_window = gt.start - pre_tolerance <= inc.start <= gt.end + latency_budget
    return overlap or in_window


@dataclass
class MatchResult:
    pairs: list[tuple[GTEvent, Incident, float]] = field(default_factory=list)  # (gt, inc, iou)
    duplicates: list[tuple[Incident, GTEvent]] = field(default_factory=list)
    unmatched_gt: list[GTEvent] = field(default_factory=list)
    false_positives: list[Incident] = field(default_factory=list)


def assign(gts: list[GTEvent], incs: list[Incident], budget_for, pre_tolerance: float = 1.0) -> MatchResult:
    """One-to-one matching of one group (normally one session). budget_for
    maps an event type to its latency budget in seconds."""
    res = MatchResult()
    if not gts:
        res.false_positives = list(incs)
        return res
    if not incs:
        res.unmatched_gt = list(gts)
        return res
    big = float(len(gts) + len(incs) + 1)
    w = np.zeros((len(gts), len(incs)))
    ok = np.zeros_like(w, dtype=bool)
    for i, g in enumerate(gts):
        for j, inc in enumerate(incs):
            if eligible(g, inc, budget_for(g.event_type), pre_tolerance):
                ok[i, j] = True
                w[i, j] = big + temporal_iou(g.start, g.end, inc.start, inc.end)
    rows, cols = linear_sum_assignment(w, maximize=True)
    used_gt, used_inc = set(), set()
    for i, j in zip(rows, cols):
        if ok[i, j]:
            res.pairs.append((gts[i], incs[j], w[i, j] - big))
            used_gt.add(i)
            used_inc.add(j)
    res.unmatched_gt = [g for i, g in enumerate(gts) if i not in used_gt]
    for j, inc in enumerate(incs):
        if j in used_inc:
            continue
        cand = [i for i in range(len(gts)) if ok[i, j]]
        if cand:
            # Attribute to the eligible GT event with the highest IoU.
            best = max(cand, key=lambda i: (w[i, j], -i))
            res.duplicates.append((inc, gts[best]))
        else:
            res.false_positives.append(inc)
    res.pairs.sort(key=lambda p: (p[0].start, p[0].id))
    return res


def in_scope(sess: Session, inc: Incident) -> bool:
    """An incident belongs to a session if it shares a camera with it and
    starts within the session's operating span."""
    if inc.cameras and not set(inc.cameras) & set(sess.camera_ids):
        return False
    if not inc.cameras and sess.room_id and inc.room_id and inc.room_id != sess.room_id:
        return False
    return sess.span_start <= inc.start <= sess.span_end


def match_all(sessions: list[Session], incidents: list[Incident], budget_for,
              pre_tolerance: float = 1.0) -> tuple[MatchResult, list[Incident]]:
    """Match per session. Returns the combined result and the incidents that
    fell in no session (out of scope; reported, never scored)."""
    total = MatchResult()
    claimed: set[str] = set()
    for sess in sessions:
        incs = [i for i in incidents if i.id not in claimed and in_scope(sess, i)]
        claimed.update(i.id for i in incs)
        r = assign(sess.events, incs, budget_for, pre_tolerance)
        total.pairs += r.pairs
        total.duplicates += r.duplicates
        total.unmatched_gt += r.unmatched_gt
        total.false_positives += r.false_positives
    out_of_scope = [i for i in incidents if i.id not in claimed]
    return total, out_of_scope
