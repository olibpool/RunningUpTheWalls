from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Block:
    index: int
    label: str
    weeks: int


def split_blocks(n_weeks: int, distance: str, cfg: object) -> list[Block]:
    if n_weeks <= 0:
        return []
    base_weeks = max(1, int(round(n_weeks * 0.4)))
    build_weeks = max(1, int(round(n_weeks * 0.4)))
    peak_weeks = max(1, n_weeks - base_weeks - build_weeks)
    while base_weeks + build_weeks + peak_weeks > n_weeks:
        peak_weeks -= 1
    blocks = [
        ("base", base_weeks),
        ("build", build_weeks),
        ("peak", peak_weeks),
    ]
    out: list[Block] = []
    for idx, (label, weeks) in enumerate(blocks):
        if weeks <= 0:
            continue
        out.append(Block(index=idx, label=label, weeks=weeks))
    return out


def weekly_targets(start_km: float, n_weeks: int, distance: str, cfg: object) -> list[float]:
    cfg_dict = getattr(cfg, "progression", None) if cfg is not None else None
    max_increase = float(getattr(cfg_dict, "max_weekly_increase", 0.10)) if cfg_dict else 0.10
    cutback_every = int(getattr(cfg_dict, "cutback_every_n_weeks", 4)) if cfg_dict else 4
    cutback_factor = float(getattr(cfg_dict, "cutback_factor", 0.80)) if cfg_dict else 0.80

    target = float(start_km)
    output: list[float] = []
    for week in range(1, n_weeks + 1):
        if week > 1 and week % cutback_every == 0:
            target *= cutback_factor
        else:
            target *= 1.0 + max_increase
        output.append(round(target, 2))
    return output
