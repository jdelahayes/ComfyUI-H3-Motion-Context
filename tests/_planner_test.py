"""Planner test: clip lengths, prompts and graph lookup, known answers.

Plain Python, no ComfyUI, torch or numpy. Drives the planner the way a
graph would, with a fake PROMPT holding the Save / Load / Motion Context
widgets the node reads.
"""

import math
import os
import sys
import types

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TESTS_DIR)


def load_planner():
    # load as a package member so the module resolves like it does in
    # ComfyUI, without running the package __init__ (which needs torch)
    pkg = types.ModuleType("h3mc_pkg")
    pkg.__path__ = [_PKG_DIR]
    sys.modules["h3mc_pkg"] = pkg
    import h3mc_pkg.planner as planner
    return planner


def graph(save=1, load=None, head="22", extra=None):
    g = {
        "1": {"class_type": "MiniMaxH3MotionContextSaveLatent",
              "inputs": {"clip_index": save, "filename_prefix": "h3_context/clip"}},
        "2": {"class_type": "MiniMaxH3MotionContext",
              "inputs": {"context_length": head, "audio_context_length": 24}},
        "3": {"class_type": "MiniMaxH3MotionContextLoadLatent",
              "inputs": {"clip_index": save - 1 if load is None else load,
                         "latent_path": "h3_context"}},
    }
    g.update(extra or {})
    return g


def raises(fn, needle):
    try:
        fn()
    except ValueError as e:
        assert needle in str(e), "expected %r in: %s" % (needle, e)
        return str(e)
    raise AssertionError("no error, expected one mentioning %r" % needle)


EXAMPLE = """[prefix]
Use <Picture 1> as the exact character identity.
[suffix]
No text, no logo.
[0:00]
Close-up of the singer,
slow push-in.
[close-up stays tight]
[0:10]
Wide shot, the camera pulls back.
[end 0:19,917]
"""


def main():
    p = load_planner()

    # --- timecodes ---
    assert p.parse_timecode("62.5") == 62.5
    assert p.parse_timecode("62,5") == 62.5
    assert p.parse_timecode("1:02.5") == 62.5
    assert p.parse_timecode("0:01:02.500") == 62.5
    for bad in ("", "abc", "1:75", "1:61:00", "-3"):
        raises(lambda b=bad: p.parse_timecode(b), "")
    assert p.format_timecode(20.0416667) == "0:20.042"
    assert p.format_timecode(3725.5) == "1:02:05.500"
    print("timecodes: seconds, m:ss, h:mm:ss, comma decimals")

    # --- parsing: brackets in a prompt that are not tags stay text ---
    plan = p.parse_plan(EXAMPLE)
    assert len(plan["segments"]) == 2
    assert plan["segments"][0]["prompt"] == ("Close-up of the singer,\n"
                                             "slow push-in.\n"
                                             "[close-up stays tight]")
    assert plan["end"] == "0:19,917"
    assert plan["prefix"].startswith("Use <Picture 1>")
    again = p.parse_plan(p.plan_to_text(plan))
    assert again == plan, (again, plan)
    assert p.parse_plan(p.plan_to_json(plan)) == plan
    raises(lambda: p.parse_plan("loose text\n[0:00]\nx\n[end 0:05]"),
           "before the first tag")
    raises(lambda: p.parse_plan("[0:00]\nx\n[end 0:05]\nstray"),
           "after [end")
    print("plan text: multi-line prompts, bracketed prompt lines kept, "
          "text and JSON round trips")

    # --- the grid ---
    assert [p.grid_up(n) for n in (1, 240, 243, 244, 257, 259)] == \
        [5, 243, 243, 260, 260, 260]
    assert p.grid_nearest(240, 1) == 243
    assert p.grid_nearest(257, 23) == 260
    assert p.grid_nearest(250, 23) == 243  # 7 below beats 10 above
    assert p.grid_nearest(30, 23) == 39    # 22 is nearer but under the minimum
    print("grid: up, nearest, minimum respected")

    # --- the worked example: 0 / 0:10 / end 0:19.917, head 22 ---
    r = p.compute(plan, 22)
    got = [(c["generated"], c["delivered"], c["end"], c["error"])
           for c in r["clips"]]
    assert got == [(243, 243, 243, 3), (260, 238, 481, 3)], got
    assert r["tail_trim"] == 3 and r["song_offset"] == 0.0
    print(p.report(r, current=2))

    # --- the node, clip by clip, reading the graph ---
    node = p.MiniMaxH3MotionContextPlanner()
    assert math.isnan(node.IS_CHANGED(plan=EXAMPLE))
    out = node.plan_clip(EXAMPLE, prompt=graph(save=1))
    text, seconds, frames, offset, tail, rep, seed = out["result"]
    assert frames == 243 and tail == 0 and offset == 0.0
    assert seconds * 24 == 243
    assert text == ("Use <Picture 1> as the exact character identity.\n"
                    "Close-up of the singer,\nslow push-in.\n"
                    "[close-up stays tight]\nNo text, no logo.")
    assert 0 <= seed < 2 ** 50
    assert out["ui"]["h3_plan"] == [{"clips": 2, "current": 1,
                                     "seed": seed, "random": True}]
    out = node.plan_clip(EXAMPLE, prompt=graph(save=2),
                         context_latent={"h3_song_end": 243 / 24.0})
    text, seconds, frames, offset, tail, rep, seed = out["result"]
    assert frames == 260 and tail == 3
    assert text.splitlines()[1] == "Wide shot, the camera pulls back."
    assert rep.splitlines()[3].startswith(">2")
    # the stock length formula gives the same frame count back
    a, b = seconds, 24
    n = max(5, round(a * b))
    assert n + (5 - n % 17) % 17 == frames
    print("node: clip 1 -> 243 frames, clip 2 -> 260 frames with "
          "tail_trim 3, prompts composed, stock formula round-trips")

    # --- a long chain: errors stay within half a grid step ---
    marks = ["%d:%06.3f" % divmod(i * 7.37, 60) for i in range(31)]
    long_plan = "".join("[%s]\nshot %d\n" % (m, i)
                        for i, m in enumerate(marks[:-1]))
    long_plan += "[end %s]\n" % marks[-1]
    r = p.compute(p.parse_plan(long_plan), 22)
    errs = [c["error"] for c in r["clips"]]
    assert all(abs(e) <= 8 for e in errs[:-1]), errs
    assert 0 <= errs[-1] <= 16 and r["tail_trim"] == errs[-1]
    print("30 clip chain: worst interior cut %+d frames, no accumulation"
          % max(errs[:-1], key=abs))

    # --- plans that cannot be rendered ---
    raises(lambda: p.compute(p.parse_plan("[0:10]\na\n[0:05]\nb\n[end 0:20]"),
                             22), "timecodes must increase")
    raises(lambda: p.compute(p.parse_plan("[0:00]\na\n[end 0:20]"), 22),
           "Add a cut before 0:15.083")
    raises(lambda: p.compute(p.parse_plan("[0:00]\na\n[0:10]\nb\n"
                                          "[0:10.1]\nc\n[end 0:20]"), 22),
           "too short")
    raises(lambda: p.compute(p.parse_plan("[0:00]\na\n"), 22), "no end")
    print("refused: decreasing timecodes, over-long and too-short "
          "segments, missing end")

    # --- graph lookup failures ---
    raises(lambda: node.plan_clip(EXAMPLE, prompt=graph(save=3)),
           "all of them are done")
    raises(lambda: node.plan_clip(EXAMPLE, prompt=graph(save=2, load=0)),
           "Load Latent clip_index is 0 but Save is 2")
    raises(lambda: node.plan_clip(EXAMPLE, prompt=graph(save=0)),
           "auto-numbered")
    raises(lambda: node.plan_clip(EXAMPLE, prompt=graph(
        extra={"9": {"class_type": "MiniMaxH3MotionContextSaveLatent",
                     "inputs": {"clip_index": 1}}})), "exactly one of each")
    raises(lambda: node.plan_clip(EXAMPLE, prompt=graph(save=["7", 0], load=1)),
           "stay a widget")
    raises(lambda: node.plan_clip(EXAMPLE, prompt={}),
           "needs H3 Motion Context")
    one = node.plan_clip("[0:05]\nsolo\n[end 0:15]", prompt={})["result"]
    assert one[2] == 243 and one[3] == 5.0 and one[4] == 3, one
    print("graph: done chain, Load/Save mismatch, auto slots, duplicates "
          "and wired widgets refused; a one-clip plan needs no chain nodes")

    # --- the song position cross-check ---
    raises(lambda: node.plan_clip(EXAMPLE, prompt=graph(save=2),
                                  context_latent={"h3_song_end": 10.0}),
           "changed after clip 1 was made")
    print("song position mismatch refused")

    # --- seeds: random draws, fixed repeats, text and JSON keep both ---
    seeded = ("[0:00 seed=4711]\na\n[0:10 seed=random:99]\nb\n"
              "[0:15]\nc\n[end 0:20]\n")
    sp = p.parse_plan(seeded)
    assert [(s["seed"], s["random"]) for s in sp["segments"]] == \
        [(4711, False), (99, True), (None, True)], sp["segments"]
    assert p.parse_plan(p.plan_to_text(sp)) == sp
    assert p.parse_plan(p.plan_to_json(sp)) == sp
    assert "[0:00 seed=4711]" in p.plan_to_text(sp)
    # a bracketed prompt line that merely mentions a seed stays text
    assert p.parse_plan("[0:00]\n[wide seed=3]\n[end 0:05]")["segments"][0][
        "prompt"] == "[wide seed=3]"
    # older JSON plans without seeds are random
    old = '{"v": 1, "end": "0:05", "segments": [{"start": "0:00", "prompt": "x"}]}'
    assert p.parse_plan(old)["segments"][0] == {
        "start": "0:00", "prompt": "x", "seed": None, "random": True}
    g = lambda k: graph(save=k)
    runs = [node.plan_clip(seeded, prompt=g(1))["result"][6] for _ in range(3)]
    assert runs == [4711] * 3
    out = node.plan_clip(seeded, prompt=g(2),
                         context_latent={"h3_song_end": 243 / 24.0})
    assert out["ui"]["h3_plan"][0]["random"] is True
    assert "clip 2 seed %d (random)" % out["result"][6] in out["result"][5]
    drawn = {node.plan_clip(seeded, prompt=g(2))["result"][6]
             for _ in range(5)}
    assert len(drawn) > 1, drawn  # 99 was the last take, not a fixed seed
    for bad, needle in (("[0:00 seed=-1]", "seed must be"),
                        ("[0:00 seed=abc]", "seed must be"),
                        ("[0:00 seed=%d]" % 2 ** 53, "seed must be"),
                        ("[0:00 seed=randomly]", "seed=random:N")):
        raises(lambda b=bad: p.parse_plan(b + "\nx\n[end 0:05]"), needle)
    raises(lambda: p.parse_plan('{"segments": [{"start": "0:00", '
                                '"seed": "x"}]}'), "segment 1: seed must be")
    nofix = {"prefix": "", "suffix": "", "end": "0:05",
             "segments": [{"start": "0:00", "prompt": "x", "seed": None,
                           "random": False}]}
    raises(lambda: p.compute(nofix, 22), "segment 1 is set to a fixed seed")
    print("seeds: fixed repeats, random draws, text and JSON round trips, "
          "bad seeds refused")

    print("planner test passed")


if __name__ == "__main__":
    main()
