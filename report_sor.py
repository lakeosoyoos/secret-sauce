"""
report_sor.py — SOR-file variant of the clean report.

Takes a folder of .sor files, runs the same classification logic (single
wavelength), and produces the clean HTML + PDF output with likelihood column.
"""
import os, sys, glob, base64, subprocess, argparse
from datetime import datetime
from itertools import combinations
from io import BytesIO
import numpy as np
from scipy.stats import norm

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from sor_reader324802a import parse_sor_full

from report import (  # reuse helpers — all neutral
    _BASE_CSS, _embed_logo, _find_chrome, _outlier_probability,
    html_to_pdf_bytes, _fmt_time_gap, _detrend, _shape_color,
    _COLOR_HIGH, _COLOR_MID, _COLOR_LOW,
)

_IOR = 1.4682
_LAUNCH_SKIP_M = 500
_END_BUFFER_M  = 200


def load_sor_file(path):
    r = parse_sor_full(path, trim=False)
    if r is None:
        raise ValueError(f'unparseable: {path}')
    trace = r['trace']
    sp = r.get('exfo_sampling_period')
    if not sp or sp <= 0:
        raise ValueError(f'bad sampling period: {path}')
    dz_m = 2.998e8 * sp / (2.0 * _IOR)
    pos = np.arange(len(trace)) * dz_m
    length_m = r.get('exfo_spans_length') or (pos[-1] if len(pos) else 0.0)
    events = r.get('events') or []
    # Max splice loss from event table (firmware-reported, interior events only)
    splice_vals = [e.get('splice_loss') for e in events
                   if e.get('splice_loss') is not None
                   and not e.get('is_end')
                   and (e.get('dist_km') or 0) > 0.01]
    max_splice = max((abs(v) for v in splice_vals), default=None) if splice_vals else None
    # Pull OTDR serial number from GenParams/SupParams so we can flag pairs
    # acquired by different OTDRs in the confirmed-duplicate detail table.
    from sor_reader324802a import parse_gen_params
    gp = parse_gen_params(path) or {}
    serial = (gp.get('serial_number') or '').strip() or None
    return {
        'name':     os.path.splitext(os.path.basename(path))[0],
        'filepath': path,
        'trace':    trace.astype(np.float32),
        'pos':      pos,
        'length':   float(length_m),
        'loss':     r.get('exfo_spans_loss'),
        'max_splice_dB': max_splice,
        'timestamp': r.get('date_time'),
        'wavelength': r.get('exfo_wavelength_nm') or r.get('wavelength'),
        'serial_number': serial,
        'events':   events,
    }


def _pair_score(a, b, interior_start, interior_end):
    pa, pb = a['pos'], b['pos']
    ta, tb = a['trace'], b['trace']
    n = min(len(ta), len(tb))
    mask = (pa[:n] > interior_start) & (pa[:n] < interior_end)
    if mask.sum() < 50:
        return None
    return float(np.std(ta[:n][mask] - tb[:n][mask]))


def _event_match_quality(a_events, b_events, pos_tol_m=100.0):
    """Greedy match interior splice/event detections by closest position.
    Skips end-of-fiber and very-near-launch (< 10 m) events.

    Returns (n_matched, n_max_events, n_min_events, mean_dloss_db). When
    n_min_events < 3 the metric isn't meaningful — caller should treat as
    'agree' by default.
    """
    def _interior(events):
        out = []
        for e in events or []:
            if e.get('is_end'):
                continue
            d = e.get('dist_km') or 0
            if d < 0.01:
                continue
            out.append((d * 1000.0, e.get('splice_loss') or 0.0))
        return out

    a = _interior(a_events)
    b = _interior(b_events)
    if not a or not b:
        return 0, 0, 0.0
    used_b = [False] * len(b)
    matched_dloss = []
    for pa, la in a:
        best_j = -1
        best_d = pos_tol_m + 1.0
        for j, (pb, _) in enumerate(b):
            if used_b[j]:
                continue
            d = abs(pa - pb)
            if d < best_d:
                best_d = d
                best_j = j
        if best_j >= 0 and best_d <= pos_tol_m:
            matched_dloss.append(abs(la - b[best_j][1]))
            used_b[best_j] = True
    n_match = len(matched_dloss)
    n_max = max(len(a), len(b))
    n_min = min(len(a), len(b))
    mean_dloss_db = float(np.mean(matched_dloss)) if matched_dloss else 0.0
    return n_match, n_max, n_min, mean_dloss_db


def _events_agree(n_match, n_max, n_min, mean_dloss_db,
                  min_count=3, frac_thresh=0.85, loss_thresh_db=0.010):
    """Return True iff the pair's events look like the same physical fiber.

    Calibrated against measured-truth datasets:
      - True same-fiber re-shoots: 100% match rate, mean |Δloss| ~1 mdB,
        equal event counts.
      - Different fibers in the same cable (DURSAN-style): 25-90% match
        rate, mean |Δloss| 10-40 mdB, asymmetric event counts.

    Default thresholds:
      - at least 3 matched events
      - ≥ 85% of the LONGER event list matched (penalizes asymmetric counts;
        a real duplicate detects the same splices in both shots)
      - mean loss difference ≤ 10 mdB (true dups are <2 mdB; this is
        generously above noise but catches splice-aligned non-duplicates)
    """
    if n_min < min_count or n_max == 0:
        return True  # too few events to evaluate — don't penalize
    return (n_match >= min_count
            and n_match / n_max >= frac_thresh
            and mean_dloss_db <= loss_thresh_db)


def _compute_pair_metrics_batch(files, interior_start, interior_end, min_samples=50):
    """Vectorized pair-metric computation. For N files this scales as O(N²·S)
    via two matmuls instead of O(N²) Python loops, so 864-file runs go from
    hours to seconds.

    Returns (sigma_matrix, r_matrix, valid_file_indices) where the matrices
    are indexed by position within `valid_file_indices` (NOT the original
    `files` list). σ is computed on raw traces; r on detrended traces.
    """
    interior = []
    valid_idx = []
    for i, f in enumerate(files):
        ta, pa = f['trace'], f['pos']
        n = len(ta)
        mask = (pa[:n] > interior_start) & (pa[:n] < interior_end)
        if mask.sum() < min_samples:
            continue
        interior.append((ta[mask].astype(np.float32),
                         pa[mask].astype(np.float32)))
        valid_idx.append(i)
    if len(interior) < 2:
        return None

    N = min(len(d[0]) for d in interior)
    K = len(interior)
    M_raw = np.empty((K, N), dtype=np.float32)
    M_det = np.empty((K, N), dtype=np.float32)
    for k, (ts, ps) in enumerate(interior):
        ts = ts[:N]; ps = ps[:N]
        M_raw[k] = ts
        # Detrend per-row: subtract best-fit linear (slope·pos + intercept).
        # Closed-form: slope = cov(p, t) / var(p), intercept = mean(t) - slope·mean(p).
        pm = ps.mean(); tm = ts.mean()
        denom = ((ps - pm) ** 2).sum()
        slope = float(((ps - pm) * (ts - tm)).sum() / denom) if denom > 0 else 0.0
        intercept = float(tm - slope * pm)
        M_det[k] = ts - (slope * ps + intercept)

    # σ(M[i] - M[j]) for all pairs via the variance-decomposition identity:
    #     var(A - B) = mean(A²) + mean(B²) - 2·E[A·B] - (E[A] - E[B])²
    m1 = M_raw.mean(axis=1)
    m2 = (M_raw.astype(np.float64) ** 2).mean(axis=1)
    C = (M_raw.astype(np.float64) @ M_raw.astype(np.float64).T) / float(N)
    var_ij = (m2[:, None] + m2[None, :] - 2.0 * C
              - (m1[:, None] - m1[None, :]) ** 2)
    sigma_matrix = np.sqrt(np.maximum(var_ij, 0.0))

    # Pearson r on detrended traces. Detrended rows are mean-≈-0, but subtract
    # to be exact. Then r_ij = (Mc @ Mc.T) / (N · std_i · std_j).
    Mc = (M_det.astype(np.float64) - M_det.astype(np.float64).mean(axis=1, keepdims=True))
    std = np.sqrt((Mc ** 2).mean(axis=1))
    std_outer = np.outer(std, std)
    np.maximum(std_outer, 1e-12, out=std_outer)
    r_matrix = (Mc @ Mc.T) / (float(N) * std_outer)
    np.clip(r_matrix, -1.0, 1.0, out=r_matrix)
    return sigma_matrix, r_matrix, valid_idx


def _pair_shape_r(a, b, interior_start, interior_end):
    """Detrended Pearson r in the interior window. r ≈ 1 → same fiber."""
    pa = a['pos']
    ta, tb = a['trace'], b['trace']
    n = min(len(ta), len(tb))
    mask = (pa[:n] > interior_start) & (pa[:n] < interior_end)
    if mask.sum() < 50:
        return None
    pp = pa[:n][mask].astype(np.float64)
    da = _detrend(ta[:n][mask].astype(np.float64), pp)
    db = _detrend(tb[:n][mask].astype(np.float64), pp)
    sa, sb = np.std(da), np.std(db)
    if sa == 0 or sb == 0:
        return None
    return float(np.dot(da - da.mean(), db - db.mean()) / (sa * sb * len(da)))


def _distribution_chart(scores, p_dup, stats, shape_rs=None):
    """2x2 grid of panels (4-mode) or stacked 2 (2-mode):
        top-left:    level-of-disagreement distribution (histogram + cluster fit)
        top-right:   similarity score distribution (histogram + same-fiber tiers)
        bottom-left: per-pair likelihood vs level of disagreement
        bottom-right: per-pair likelihood vs similarity score
    When `shape_rs` is None, reverts to a 2-panel column (top-left + bottom-left)."""
    if shape_rs is not None:
        # 13x6 keeps the chart compact enough that section 1 banner + the 2x2
        # grid fit on the same landscape page as the title/cards header.
        fig, axes = plt.subplots(2, 2, figsize=(13, 6))
        ax1, axR  = axes[0, 0], axes[0, 1]
        ax2, axRS = axes[1, 0], axes[1, 1]
    else:
        fig, axes = plt.subplots(2, 1, figsize=(13, 5.5))
        ax1, ax2 = axes
        axR = axRS = None
    legend_kw = dict(loc='upper center', bbox_to_anchor=(0.5, -0.30),
                     ncol=2, fontsize=7.5, frameon=False)

    log_s = np.log10(np.maximum(scores, 1e-9))
    counts, bin_edges, _ = ax1.hist(log_s, bins=50, color='#4A90D9',
                                    alpha=0.75, edgecolor='white')
    bin_width = bin_edges[1] - bin_edges[0]
    # Scale the Gaussian PDF to raw-count units so it overlays the histogram.
    x = np.linspace(log_s.min() - 0.2, log_s.max() + 0.2, 400)
    ax1.plot(x, norm.pdf(x, stats['center_log'], stats['spread_log']) * len(log_s) * bin_width,
             color='#b97000', linewidth=2, label='cluster fit')
    ax1.axvline(stats['center_log'], linestyle='--', color='#b97000', alpha=0.7)
    for z_line in (-3, -5, -10):
        ax1.axvline(stats['center_log'] + z_line * stats['spread_log'],
                    linestyle=':', color='#888', alpha=0.5)
    ax1.set_xticklabels([])
    ax1.set_xlabel('level of disagreement (log scale)')
    ax1.set_ylabel('Number of pairs')
    ax1.set_title('Pair level-of-disagreement distribution with cluster fit', fontweight='bold')
    ax1.legend(**legend_kw)
    ax1.grid(alpha=0.3)

    if axR is not None:
        rs = np.asarray([r if r is not None else np.nan for r in shape_rs],
                        dtype=np.float64)
        rs_valid = rs[~np.isnan(rs)]
        # Always show out to similarity = 1.0 with the 0.95/0.99 thresholds
        # visible, so the reference lines anchor the reader's eye.
        lo = min(0.4, float(rs_valid.min()) - 0.02) if rs_valid.size else 0.4
        hi = 1.005
        if rs_valid.size:
            bins = np.linspace(lo, hi, 60)
            axR.hist(rs_valid, bins=bins, color='#4A90D9', alpha=0.75,
                     edgecolor='white')
            # Tier markers: green ≥ 0.99, orange 0.95–0.99, grey < 0.95.
            axR.axvspan(0.99, hi, color=_COLOR_HIGH, alpha=0.10)
            axR.axvspan(0.95, 0.99, color=_COLOR_MID, alpha=0.10)
            axR.axvline(0.99, linestyle='--', color=_COLOR_HIGH, linewidth=1.3,
                        label='≥ 0.99 (same fiber)')
            axR.axvline(0.95, linestyle=':', color=_COLOR_MID, linewidth=1.2,
                        label='= 0.95 (borderline floor)')
        axR.set_xlim(lo, hi)
        axR.set_xlabel('similarity score per pair')
        axR.set_ylabel('Number of pairs')
        ttl = ('Similarity score distribution — duplicates concentrate near 1.0'
               if rs_valid.size else 'Similarity score unavailable')
        axR.set_title(ttl, fontweight='bold')
        axR.legend(**legend_kw)
        axR.grid(axis='y', alpha=0.3)

    # Tier masks: high ≥ 0.9, mid 0.5–0.9, low ≤ 0.5. Colors match the tables.
    p = np.asarray(p_dup)
    m_hi = p > 0.9
    m_md = (p > 0.5) & (~m_hi)
    m_lo = ~(m_hi | m_md)
    if m_lo.any():
        ax2.scatter(log_s[m_lo], p[m_lo], s=45, alpha=0.6, color=_COLOR_LOW,
                    edgecolor='white', linewidth=0.5,
                    label=f'Non-duplicate (n={int(m_lo.sum())})')
    if m_md.any():
        ax2.scatter(log_s[m_md], p[m_md], s=120, alpha=0.95,
                    color=_COLOR_MID, edgecolor='black', linewidth=1, zorder=4,
                    label=f'Borderline 50–90% (n={int(m_md.sum())})')
    if m_hi.any():
        ax2.scatter(log_s[m_hi], p[m_hi], s=140, alpha=0.95,
                    color=_COLOR_HIGH, edgecolor='black', linewidth=1, zorder=5,
                    label=f'Duplicate ≥90% (n={int(m_hi.sum())})')
    ax2.axhline(0.9, color=_COLOR_HIGH, linestyle=':', alpha=0.4, linewidth=1)
    ax2.axhline(0.5, color=_COLOR_MID, linestyle='--', alpha=0.5, linewidth=1)
    ax2.set_xticklabels([])
    ax2.set_xlabel('level of disagreement (log scale)')
    ax2.set_ylabel('duplicate likelihood')
    ax2.set_title('Per-pair likelihood vs level of disagreement', fontweight='bold')
    ax2.legend(**legend_kw)
    ax2.grid(alpha=0.3)

    if axRS is not None:
        # Per-pair likelihood vs similarity score (Pearson r). Same tier-color
        # masks as the disagreement scatter, so high/mid/low pairs render
        # consistently between panels.
        rs_full = np.asarray([r if r is not None else np.nan for r in shape_rs],
                             dtype=np.float64)
        valid = ~np.isnan(rs_full)
        m_hi_v = m_hi & valid
        m_md_v = m_md & valid
        m_lo_v = m_lo & valid
        if m_lo_v.any():
            axRS.scatter(rs_full[m_lo_v], p[m_lo_v], s=45, alpha=0.6,
                         color=_COLOR_LOW, edgecolor='white', linewidth=0.5,
                         label=f'Non-duplicate (n={int(m_lo_v.sum())})')
        if m_md_v.any():
            axRS.scatter(rs_full[m_md_v], p[m_md_v], s=120, alpha=0.95,
                         color=_COLOR_MID, edgecolor='black', linewidth=1, zorder=4,
                         label=f'Borderline 50–90% (n={int(m_md_v.sum())})')
        if m_hi_v.any():
            axRS.scatter(rs_full[m_hi_v], p[m_hi_v], s=140, alpha=0.95,
                         color=_COLOR_HIGH, edgecolor='black', linewidth=1, zorder=5,
                         label=f'Duplicate ≥90% (n={int(m_hi_v.sum())})')
        axRS.axhline(0.9, color=_COLOR_HIGH, linestyle=':', alpha=0.4, linewidth=1)
        axRS.axhline(0.5, color=_COLOR_MID, linestyle='--', alpha=0.5, linewidth=1)
        axRS.axvline(0.99, color=_COLOR_HIGH, linestyle=':', alpha=0.4, linewidth=1)
        axRS.axvline(0.95, color=_COLOR_MID, linestyle='--', alpha=0.5, linewidth=1)
        # Lock x-axis so the 0.95 / 0.99 reference lines always show.
        rs_valid_pts = rs_full[valid]
        rs_lo = min(0.4, float(rs_valid_pts.min()) - 0.02) if rs_valid_pts.size else 0.4
        axRS.set_xlim(rs_lo, 1.005)
        axRS.set_xlabel('similarity score per pair')
        axRS.set_ylabel('duplicate likelihood')
        axRS.set_title('Per-pair likelihood vs similarity score', fontweight='bold')
        axRS.legend(**legend_kw)
        axRS.grid(alpha=0.3)

    plt.tight_layout()
    buf = BytesIO()
    plt.savefig(buf, format='png', dpi=150, bbox_inches='tight')
    plt.close()
    buf.seek(0)
    return base64.b64encode(buf.read()).decode('ascii')


def build_report_sor(folder, title, out_pdf):
    paths = sorted(glob.glob(os.path.join(folder, '*.sor')))
    files = []
    for p in paths:
        try:
            files.append(load_sor_file(p))
        except Exception as e:
            print(f'  skip {os.path.basename(p)}: {e}')
    if len(files) < 2:
        raise RuntimeError(f'Not enough usable .sor files in {folder}')
    print(f'Loaded {len(files)} .sor files from {folder}')

    min_L = min(f['length'] for f in files if f['length'] > 0)
    interior_start = _LAUNCH_SKIP_M
    interior_end = min_L - _END_BUFFER_M
    if interior_end - interior_start < 100:
        # Short-fiber fallback: scale the window to the span itself.
        # 2 m floor keeps the launch buffer sane for coils under 50 m; for
        # anything longer, 5% of the span is plenty.
        interior_start = max(2.0, min_L * 0.05)
        interior_end = max(interior_start + 2.0, min_L * 0.95)
    print(f'Interior window: {interior_start:.0f}–{interior_end:.0f} m  '
          f'(common span {min_L:.0f} m)')

    print(f'Computing pair metrics for {len(files)} files '
          f'({len(files) * (len(files) - 1) // 2} pairs)...')
    batch = _compute_pair_metrics_batch(files, interior_start, interior_end)
    if batch is None:
        raise RuntimeError('No comparable pairs after interior masking')
    sigma_matrix, r_matrix, valid_idx = batch
    pairs = []
    K = len(valid_idx)
    for ki in range(K):
        i = valid_idx[ki]
        name_i = files[i]['name']
        len_i = files[i].get('length')
        for kj in range(ki + 1, K):
            j = valid_idx[kj]
            len_j = files[j].get('length')
            len_delta = (abs(len_i - len_j) if (len_i and len_j) else None)
            pairs.append({
                'a': name_i,
                'b': files[j]['name'],
                'score': float(sigma_matrix[ki, kj]),
                'shape_r': float(r_matrix[ki, kj]),
                'length_delta_m': len_delta,
            })
    if not pairs:
        raise RuntimeError('No comparable pairs after interior masking')
    print(f'Pair metrics ready: {len(pairs)} pairs')

    scores = np.array([p['score'] for p in pairs], dtype=np.float64)
    p_dup_sigma, stats = _outlier_probability(scores)

    # Pearson-shape contribution: r ≥ 0.99 → 1.0, r ≤ 0.95 → 0, linear in between.
    # Same ramp as JSON/TRC mode, so the verdict reads the combined likelihood.
    def _r_to_p(r):
        if r is None:
            return 0.0
        if r >= 0.99:
            return 1.0
        if r <= 0.95:
            return 0.0
        return float((r - 0.95) / 0.04)

    p_dup_r = np.array([_r_to_p(p.get('shape_r')) for p in pairs],
                       dtype=np.float64)
    # Combined likelihood = max of σ-outlier and shape-correlation tiers.
    p_dup_raw = np.maximum(p_dup_sigma, p_dup_r)

    # Physical-reality filter: same fiber must produce the same end-of-fiber
    # length to within launch-connector + IOR + sample-resolution variation.
    # Tolerance scales with fiber length but is bounded:
    #   - floor 0.5 m  (launch-mating + OTDR sample resolution dominate at small spans)
    #   - 0.01 % of length above 5 km
    #   - cap 2 m      (avoid being too permissive on 100 km+ spans)
    # When a pair's length delta exceeds tol, cap likelihood at 0.5 (borderline) —
    # different physical fibers can't be the same fiber regardless of how similar
    # their splice profiles look. Pairs with no length info pass through.
    LEN_CAP = 0.5
    def _len_tol_m(length_m):
        if length_m is None or length_m <= 0:
            return 0.5
        return max(0.5, min(2.0, length_m * 1e-4))
    length_deltas = np.array([(p.get('length_delta_m') or 0.0) for p in pairs], dtype=np.float64)
    has_lengths = np.array([p.get('length_delta_m') is not None for p in pairs])
    # Use the LONGER of the two fibers in the pair to set tolerance.
    name_to_length = {f['name']: (f.get('length') or 0) for f in files}
    pair_max_len = np.array([
        max(name_to_length.get(p['a'], 0), name_to_length.get(p['b'], 0))
        for p in pairs
    ], dtype=np.float64)
    tols = np.array([_len_tol_m(L) for L in pair_max_len], dtype=np.float64)
    length_violation = has_lengths & (length_deltas > tols)

    # Event-table consistency gate: same physical fiber → splice events match
    # in count, position, and loss. Different fibers can share σ/r and even
    # length (paths diverge then reconverge) but their event tables disagree.
    # Only evaluate pairs that survived the σ/r screen, since pairs already
    # at p_dup_raw < 0.1 won't be flagged regardless.
    file_events = {f['name']: f.get('events') for f in files}
    events_violation = np.zeros(len(pairs), dtype=bool)
    EVENT_CHECK_THRESHOLD = 0.10
    for i, p in enumerate(pairs):
        if p_dup_raw[i] < EVENT_CHECK_THRESHOLD:
            continue
        n_match, n_max, n_min, mean_dloss = _event_match_quality(
            file_events.get(p['a']), file_events.get(p['b']))
        p['events_n_match'] = int(n_match)
        p['events_n_max']   = int(n_max)
        p['events_n_min']   = int(n_min)
        p['events_mean_dloss_db'] = float(mean_dloss)
        if not _events_agree(n_match, n_max, n_min, mean_dloss):
            events_violation[i] = True

    physical_violation = length_violation | events_violation
    p_dup = np.where(physical_violation, np.minimum(p_dup_raw, LEN_CAP), p_dup_raw)

    for i, p in enumerate(pairs):
        p['p_dup_sigma']   = float(p_dup_sigma[i])
        p['p_dup_r']       = float(p_dup_r[i])
        p['p_dup_raw']     = float(p_dup_raw[i])
        p['p_dup']         = float(p_dup[i])
        p['length_capped'] = bool(length_violation[i])
        p['events_capped'] = bool(events_violation[i])
        p['z']             = float(stats['z'][i])

    order = np.argsort(scores)
    n99 = int((p_dup > 0.99).sum())
    n50 = int((p_dup > 0.5).sum())
    n10 = int((p_dup > 0.1).sum())
    print(f'Likelihood >99%: {n99}   >50%: {n50}   >10%: {n10}')

    best_partner = {}
    for idx, f in enumerate(files):
        best = None
        for p in pairs:
            if f['name'] not in (p['a'], p['b']):
                continue
            if best is None or p['score'] < best['score']:
                best = p
        best_partner[f['name']] = best

    verdict_block = (f'<div class="verdict-box verdict-confirm">'
                     f'<b>{n50} duplicate pair(s) identified</b> at ≥50% likelihood; '
                     f'{n99} at ≥99% likelihood across {len(pairs)} pairs.</div>'
                     if n50 else
                     '<div class="verdict-box verdict-dispute">'
                     '<b>No duplicate pairs identified</b> at ≥50% likelihood.</div>')

    shape_rs = [p.get('shape_r') for p in pairs]
    dist_chart = _distribution_chart(scores, p_dup, stats, shape_rs=shape_rs)

    file_rows = ''
    for f in sorted(files, key=lambda x: x['name']):
        bp = best_partner.get(f['name'])
        if bp is None:
            continue
        partner = bp['b'] if bp['a'] == f['name'] else bp['a']
        pd_val = bp['p_dup']
        pd_color = '#2d8f48' if pd_val > 0.9 else ('#b97000' if pd_val > 0.1 else '#888')
        verdict_cell = (f'<span class="dup">DUPLICATE of {partner}</span>'
                        if pd_val > 0.5 else
                        f'<span class="na">unique (closest: {partner})</span>')
        loss_cell = f'{f["loss"]:.3f}' if f['loss'] is not None else '—'
        r_val = bp.get('shape_r')
        r_cell = ('<td class="center na">—</td>' if r_val is None else
                  f'<td class="center" style="color:{_shape_color(r_val)};font-weight:600">{r_val:.4f}</td>')
        file_rows += (f'<tr><td class="pair-cell">{f["name"]}</td>'
                      f'<td class="center">{f["length"]/1000:.3f}</td>'
                      f'<td class="center">{loss_cell}</td>'
                      f'<td class="center">{bp["score"]:.4f}</td>'
                      f'<td class="center" style="color:{pd_color};font-weight:600">{pd_val*100:.2f}%</td>'
                      f'{r_cell}'
                      f'<td class="center">{verdict_cell}</td></tr>')

    top_rows = ''
    for rank, k in enumerate(order[:30], 1):
        p = pairs[k]
        pd_val = p['p_dup']
        pd_color = '#2d8f48' if pd_val > 0.9 else ('#b97000' if pd_val > 0.1 else '#888')
        r_val = p.get('shape_r')
        r_cell = ('<td class="center na">—</td>' if r_val is None else
                  f'<td class="center" style="color:{_shape_color(r_val)};font-weight:600">{r_val:.4f}</td>')
        top_rows += (f'<tr><td class="center">{rank}</td>'
                     f'<td class="pair-cell">{p["a"]} ↔ {p["b"]}</td>'
                     f'<td class="center">{p["score"]:.4f}</td>'
                     f'<td class="center" style="color:{pd_color};font-weight:600">{pd_val*100:.2f}%</td>'
                     f'{r_cell}</tr>')

    # Top 30 by similarity (highest first). Skip pairs where similarity is None.
    sim_pairs = [(i, p) for i, p in enumerate(pairs) if p.get('shape_r') is not None]
    sim_order = sorted(sim_pairs, key=lambda x: -x[1]['shape_r'])[:30]
    sim_rows = ''
    for rank, (k, p) in enumerate(sim_order, 1):
        pd_val = p['p_dup']
        pd_color = '#2d8f48' if pd_val > 0.9 else ('#b97000' if pd_val > 0.1 else '#888')
        r_val = p['shape_r']
        sim_rows += (f'<tr><td class="center">{rank}</td>'
                     f'<td class="pair-cell">{p["a"]} ↔ {p["b"]}</td>'
                     f'<td class="center" style="color:{_shape_color(r_val)};font-weight:600">{r_val:.4f}</td>'
                     f'<td class="center">{p["score"]:.4f}</td>'
                     f'<td class="center" style="color:{pd_color};font-weight:600">{pd_val*100:.2f}%</td></tr>')

    # Confirmed-duplicate detail table (p_dup > 0.5)
    file_by_name = {f['name']: f for f in files}
    dup_pairs_sorted = sorted([p for p in pairs if p['p_dup'] > 0.5],
                              key=lambda q: -q['p_dup'])
    dup_detail_rows = ''
    for p in dup_pairs_sorted:
        fa = file_by_name.get(p['a']); fb = file_by_name.get(p['b'])
        if fa is None or fb is None:
            continue
        ta, tb = fa.get('timestamp'), fb.get('timestamp')
        gap_str = _fmt_time_gap(abs(ta - tb)) if ta and tb else '—'
        a_ms, b_ms = fa.get('max_splice_dB'), fb.get('max_splice_dB')
        a_sl, b_sl = fa.get('loss'), fb.get('loss')
        ms_cell = (f'<td class="center">{abs(a_ms - b_ms)*1000:.0f}</td>'
                   if a_ms is not None and b_ms is not None
                   else '<td class="center na">—</td>')
        sl_cell = (f'<td class="center">{abs(a_sl - b_sl)*1000:.0f}</td>'
                   if a_sl is not None and b_sl is not None
                   else '<td class="center na">—</td>')
        # Same OTDR serial → both shots came from the same instrument.
        sn_a, sn_b = fa.get('serial_number'), fb.get('serial_number')
        if sn_a and sn_b:
            same_sn = (sn_a == sn_b)
            sn_cell = (f'<td class="center" style="color:#2d8f48;font-weight:700">Yes</td>'
                       if same_sn else
                       f'<td class="center" style="color:#c0392b;font-weight:700">No</td>')
        else:
            sn_cell = '<td class="center na">—</td>'
        pd_val = p['p_dup']
        pd_color = '#2d8f48' if pd_val > 0.9 else '#b97000'
        r_val = p.get('shape_r')
        r_cell = ('<td class="center na">—</td>' if r_val is None else
                  f'<td class="center" style="color:{_shape_color(r_val)};font-weight:600">{r_val:.4f}</td>')
        dup_detail_rows += (f'<tr><td class="pair-cell">{p["a"]} ↔ {p["b"]}</td>'
                            f'<td class="center">{gap_str}</td>'
                            f'{ms_cell}{sl_cell}{r_cell}{sn_cell}'
                            f'<td class="center" style="color:{pd_color};font-weight:600">{pd_val*100:.2f}%</td></tr>')
    dup_detail_block = ''
    if dup_detail_rows:
        wl_hdr = f'{int(files[0].get("wavelength") or 0)} nm' if files else ''
        dup_detail_block = f'''
<div class="section-block">
<div class="dir-banner">3. Confirmed duplicate pairs (≥50% likelihood) — detail ({wl_hdr})</div>
<table class="vote-table">
<tr><th style="text-align:left">Pair</th><th>Time gap</th>
  <th>max splice Δ (mdB)</th><th>span loss Δ (mdB)</th>
  <th>similarity</th><th>Same OTDR</th><th>Duplicate likelihood</th></tr>
{dup_detail_rows}
</table>
</div>
'''

    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    html = f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{title}</title>
<style>{_BASE_CSS}</style></head><body>
{_embed_logo()}
<h1>{title}</h1>
<div class="subtitle">{len(files)} files &bull; {len(pairs)} pairs &bull; generated {generated}</div>

<div class="section-block">
<div class="dir-banner">1. Distribution</div>
<img src="data:image/png;base64,{dist_chart}" class="chart-img" />
</div>

{verdict_block}

<div class="cards">
  <div class="card"><div class="card-label">Files</div><div class="card-value">{len(files)}</div></div>
  <div class="card"><div class="card-label">Pairs</div><div class="card-value">{len(pairs)}</div></div>
  <div class="card"><div class="card-label">Likelihood &gt; 99%</div>
    <div class="card-value good">{n99}</div></div>
  <div class="card"><div class="card-label">Likelihood &gt; 50%</div>
    <div class="card-value">{n50}</div></div>
  <div class="card"><div class="card-label">Likelihood &gt; 10%</div>
    <div class="card-value">{n10}</div></div>
</div>

<div class="section-block">
<div class="dir-banner">2. Per-file verdict</div>
<table class="vote-table">
<tr><th style="text-align:left">File</th>
    <th>Length (km)</th><th>Span loss (dB)</th>
    <th>lowest disagreement</th><th>Duplicate likelihood</th>
    <th>similarity</th><th>Verdict</th></tr>
{file_rows}
</table>
</div>

{dup_detail_block}

<div class="section-block">
<div class="dir-banner">4. Top 30 pairs — lowest level of disagreement</div>
<table class="vote-table">
<tr><th>Rank</th><th style="text-align:left">Pair</th>
    <th>level of disagreement</th><th>Duplicate likelihood</th><th>similarity</th></tr>
{top_rows}
</table>
</div>

<div class="section-block">
<div class="dir-banner">5. Top 30 pairs — highest similarity</div>
<table class="vote-table">
<tr><th>Rank</th><th style="text-align:left">Pair</th>
    <th>similarity</th><th>level of disagreement</th><th>Duplicate likelihood</th></tr>
{sim_rows}
</table>
</div>
</body></html>'''

    pdf_bytes = html_to_pdf_bytes(html, base_url=folder)
    with open(out_pdf, 'wb') as fh:
        fh.write(pdf_bytes)
    print(f'PDF:  {out_pdf}')
    return out_pdf


def run_sor_bytes(folder, title):
    """Run SOR mode and return (pdf_bytes, n_files, n_pairs)."""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        tmp_pdf = os.path.join(td, 'report.pdf')
        build_report_sor(folder, title, tmp_pdf)
        with open(tmp_pdf, 'rb') as fh:
            pdf_bytes = fh.read()
    n_files = len(glob.glob(os.path.join(folder, '*.sor')))
    n_pairs = n_files * (n_files - 1) // 2
    return pdf_bytes, n_files, n_pairs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sor-dir', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--out-pdf', required=True)
    args = parser.parse_args()
    build_report_sor(args.sor_dir, args.title, args.out_pdf)


if __name__ == '__main__':
    main()
