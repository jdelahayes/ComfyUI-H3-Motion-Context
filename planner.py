"""Plan a chain from timecodes: clip lengths and per-clip prompts.

The user lists the cut points of the piece, each with the prompt for the
segment it opens, plus where the last one ends. For the clip being
generated, the Planner works out how long to render it and hands over
its prompt, so a whole music video runs from one table.

How a clip's length is chosen
-----------------------------
Everything is in whole frames at FPS, measured from the first timecode.
For clip k, starting at S where the previous clip REALLY ended:

    deliver   E - S             E = the next cut point
    generate  E - S + head      head = Motion Context's pinned run, 0 for clip 1
    snapped   to H3's 17m+5 grid: nearest for every cut but the last,
              up for the last so the piece is covered, the overshoot
              going out on tail_trim for the Trim node

Each clip starts from the real end of the one before, not the planned
one, so a cut is never more than half a grid step (8 frames) from its
timecode and the error does not accumulate down a chain. Cutting a clip
early to land exactly on its timecode is not an option: the next clip's
pinned head is the last frames of this clip's latent, so the clip has to
end where its latent ends.

Which clip is being generated
-----------------------------
Read from the graph itself, through the hidden PROMPT input: the Save
Latent node's clip_index is the clip being made, and Motion Context's
context_length is the head. One source for each, nothing to keep in
sync, and the Chain node needs no change to drive it. The plan is
deterministic, so knowing k is enough to replay clips 1..k-1 and find
where k starts. When Audio Lock is in the graph, the previous clip's
latent carries the song position it really ended at, which is checked
against the replay: a mismatch means the timecodes or the head changed
mid-chain.

Plan formats
------------
The plan widget takes text (written by hand or exported) or JSON (what
the editor will store). Text:

    [prefix]
    Use <Picture 1> as the exact character identity...
    [suffix]
    No text, no logo.
    [0:00]
    Close-up of the singer,
    slow push-in.
    [0:10]
    Wide shot...
    [end 0:19.917]

A line is a tag only when it parses as one, so prompt lines in brackets
are kept as text. JSON:

    {"v": 1, "prefix": "...", "suffix": "...", "end": "0:19.917",
     "segments": [{"start": "0:00", "prompt": "..."}, ...]}
"""

import json
import logging
import math
import re

_LOG = logging.getLogger("h3_motion_context")

FPS = 24
MAX_FRAMES = 362  # H3's trained maximum, ~15.1 s; longer is untested
SAVE_NODE = "MiniMaxH3MotionContextSaveLatent"
LOAD_NODE = "MiniMaxH3MotionContextLoadLatent"
CONTEXT_NODE = "MiniMaxH3MotionContext"
SONG_END_KEY = "h3_song_end"  # same key as nodes.SONG_END_KEY

_TAG = re.compile(r"^\s*\[\s*([^\[\]]+?)\s*\]\s*$")


class PlanError(ValueError):
    """A plan that cannot be rendered, with a message for the user."""


# ---------------------------------------------------------------- timecodes

def parse_timecode(text):
    """'62.5', '62,5', '1:02.5' or '0:01:02.500' -> seconds."""
    s = str(text).strip().replace(",", ".")
    parts = s.split(":")
    if not s or len(parts) > 3:
        raise PlanError("not a timecode: %r" % text)
    try:
        sec = float(parts[-1])
        whole = [int(p) for p in parts[:-1]]
    except ValueError:
        raise PlanError("not a timecode: %r" % text) from None
    if sec < 0 or any(w < 0 for w in whole):
        raise PlanError("negative timecode: %r" % text)
    if whole and sec >= 60:
        raise PlanError("seconds must be under 60 in %r" % text)
    if len(whole) == 2 and whole[1] >= 60:
        raise PlanError("minutes must be under 60 in %r" % text)
    total = sec
    for i, w in enumerate(reversed(whole)):
        total += w * 60 ** (i + 1)
    return total


def format_timecode(seconds):
    """seconds -> 'm:ss.mmm' (or 'h:mm:ss.mmm')."""
    ms = int(round(seconds * 1000))
    sign = "-" if ms < 0 else ""
    ms = abs(ms)
    h, rem = divmod(ms, 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    if h:
        return "%s%d:%02d:%02d.%03d" % (sign, h, m, s, ms)
    return "%s%d:%02d.%03d" % (sign, m, s, ms)


def _is_timecode(text):
    try:
        parse_timecode(text)
        return True
    except PlanError:
        return False


# ------------------------------------------------------------- plan format

def parse_plan(text):
    """Plan text or JSON -> {'prefix', 'suffix', 'end', 'segments'}.

    Timecodes stay strings here; compute() parses them, so an error can
    name the segment it is in.
    """
    text = text or ""
    if text.strip().startswith("{"):
        try:
            data = json.loads(text)
        except ValueError as e:
            raise PlanError("plan JSON is malformed: %s" % e) from None
        segs = data.get("segments") or []
        return {
            "prefix": str(data.get("prefix") or ""),
            "suffix": str(data.get("suffix") or ""),
            "end": str(data.get("end") or ""),
            "segments": [{"start": str(s.get("start", "")),
                          "prompt": str(s.get("prompt") or "")}
                         for s in segs],
        }

    plan = {"prefix": "", "suffix": "", "end": "", "segments": []}
    target, buf = None, []

    def flush():
        body = "\n".join(buf).strip("\n")
        if target is None:
            if body.strip():
                raise PlanError(
                    "text before the first tag: put a [timecode] line "
                    "above the first prompt")
        elif target in ("prefix", "suffix"):
            plan[target] = body
        elif target == "end":
            if body.strip():
                raise PlanError("text after [end ...]: the last segment's "
                                "prompt goes above the end tag")
        else:
            target["prompt"] = body
        buf.clear()

    for line in text.splitlines():
        m = _TAG.match(line)
        tag = m.group(1) if m else None
        low = (tag or "").lower()
        if tag is not None and low in ("prefix", "suffix"):
            flush()
            target = low
        elif tag is not None and low.startswith("end") \
                and _is_timecode(tag[3:]):
            flush()
            plan["end"] = tag[3:].strip()
            target = "end"
        elif tag is not None and _is_timecode(tag):
            flush()
            target = {"start": tag, "prompt": ""}
            plan["segments"].append(target)
        else:
            buf.append(line)
    flush()
    return plan


def plan_to_text(plan):
    out = []
    if plan.get("prefix"):
        out += ["[prefix]", plan["prefix"]]
    if plan.get("suffix"):
        out += ["[suffix]", plan["suffix"]]
    for seg in plan.get("segments", []):
        out += ["[%s]" % seg["start"], seg.get("prompt", "")]
    if plan.get("end"):
        out.append("[end %s]" % plan["end"])
    return "\n".join(out) + "\n"


def plan_to_json(plan):
    return json.dumps({"v": 1, "prefix": plan.get("prefix", ""),
                       "suffix": plan.get("suffix", ""),
                       "end": plan.get("end", ""),
                       "segments": plan.get("segments", [])},
                      ensure_ascii=False, indent=1)


def compose_prompt(plan, index):
    """Prefix + segment prompt + suffix, blank parts skipped."""
    parts = (plan.get("prefix", ""),
             plan["segments"][index].get("prompt", ""),
             plan.get("suffix", ""))
    return "\n".join(p.strip() for p in parts if p and p.strip())


# ------------------------------------------------------------- arithmetic

def _to_frames(seconds, fps):
    # half up, with a hair of slack so 19.917 * 24 = 478.008 and exact
    # decimals like 10.0 * 24 never land a float ulp below the half
    return int(math.floor(seconds * fps + 0.5 + 1e-9))


def grid_up(n):
    """Smallest H3 clip length (17m+5) >= n. Same formula as the stock
    length conversion, so a length coming out of here goes back in
    unchanged."""
    n = max(5, int(n))
    return n + (5 - n % 17) % 17


def grid_nearest(n, minimum):
    """H3 clip length nearest n, at least `minimum`. A tie goes up, so a
    cut never lands early when early and late are equally close."""
    up = grid_up(max(n, minimum))
    down = up - 17
    if down >= max(5, minimum) and abs(down - n) < abs(up - n):
        return down
    return up


def compute(plan, head, fps=FPS):
    """Lay the plan out clip by clip.

    Returns {'clips': [...], 'tail_trim', 'song_offset', 'head'}; each
    clip has start/target/end in frames from the first timecode,
    generated/delivered frame counts and the signed error at its cut.
    """
    segs = plan.get("segments") or []
    if not segs:
        raise PlanError("the plan has no segments: add a [timecode] line "
                        "and a prompt under it")
    if not str(plan.get("end") or "").strip():
        raise PlanError("the plan has no end: add [end m:ss.mmm] after the "
                        "last segment")
    head = int(head)
    if head < 0:
        raise PlanError("head must be 0 or more")

    times = []
    for i, seg in enumerate(segs):
        try:
            times.append(parse_timecode(seg["start"]))
        except PlanError as e:
            raise PlanError("segment %d: %s" % (i + 1, e)) from None
    try:
        times.append(parse_timecode(plan["end"]))
    except PlanError as e:
        raise PlanError("end: %s" % e) from None
    for i in range(1, len(times)):
        if times[i] <= times[i - 1]:
            what = "end" if i == len(segs) else "segment %d" % (i + 1)
            raise PlanError(
                "%s (%s) is not after segment %d (%s): timecodes must "
                "increase" % (what, format_timecode(times[i]), i,
                              format_timecode(times[i - 1])))

    origin = times[0]
    marks = [_to_frames(t - origin, fps) for t in times]
    clips = []
    start = 0
    for i in range(len(segs)):
        target = marks[i + 1]
        h = 0 if i == 0 else head
        last = i == len(segs) - 1
        deliver = target - start
        if deliver < 1:
            raise PlanError(
                "segment %d is too short: the previous clip already runs "
                "to %s, past this segment's end at %s. Merge it with its "
                "neighbour or move the cut."
                % (i + 1, format_timecode(origin + start / fps),
                   format_timecode(origin + target / fps)))
        need = deliver + h
        generated = grid_up(need) if last else grid_nearest(need, h + 1)
        if generated > MAX_FRAMES:
            room = MAX_FRAMES - h
            raise PlanError(
                "segment %d needs %d frames (%.2fs), over H3's %d frame "
                "maximum. Add a cut before %s to split it."
                % (i + 1, generated, generated / float(fps), MAX_FRAMES,
                   format_timecode(origin + (start + room) / fps)))
        delivered = generated - h
        end = start + delivered
        clips.append({
            "index": i + 1,
            "head": h,
            "start": start,
            "target": target,
            "generated": generated,
            "delivered": delivered,
            "end": end,
            "error": end - target,
        })
        start = end
    return {
        "clips": clips,
        "tail_trim": clips[-1]["error"],
        "song_offset": origin,
        "head": head,
        "fps": fps,
    }


def report(result, current=None):
    """Plain-text table of a computed plan."""
    fps = result["fps"]
    off = result["song_offset"]

    def tc(frames):
        return format_timecode(off + frames / float(fps))

    lines = ["Plan: %d clip(s), head %d frames, from %s to %s"
             % (len(result["clips"]), result["head"], tc(0),
                tc(result["clips"][-1]["target"])),
             " #  start       target      generated  delivered  cut         error"]
    for c in result["clips"]:
        mark = ">" if current == c["index"] else " "
        lines.append("%s%-2d %-11s %-11s %9d  %9d  %-11s %+5d ms"
                     % (mark, c["index"], tc(c["start"]), tc(c["target"]),
                        c["generated"], c["delivered"], tc(c["end"]),
                        int(round(c["error"] * 1000.0 / fps))))
    if result["tail_trim"]:
        lines.append("tail_trim %d frame(s) (%d ms) on the last clip"
                     % (result["tail_trim"],
                        int(round(result["tail_trim"] * 1000.0 / fps))))
    return "\n".join(lines)


# ------------------------------------------------------------ graph lookup

def _nodes_of(prompt, class_type):
    return [(nid, n) for nid, n in (prompt or {}).items()
            if isinstance(n, dict) and n.get("class_type") == class_type]


def _widget(node_id, node, name, what):
    value = (node.get("inputs") or {}).get(name)
    if isinstance(value, list):
        raise PlanError(
            "%s (node %s) has %s wired to another node. The Planner reads "
            "it from the graph, so it has to stay a widget."
            % (what, node_id, name))
    return value


def graph_position(prompt, clip_count):
    """(clip k, head) for this run, read from the graph.

    k is the Save Latent node's clip_index, head is Motion Context's
    context_length. A one-clip plan needs neither.
    """
    saves = _nodes_of(prompt, SAVE_NODE)
    contexts = _nodes_of(prompt, CONTEXT_NODE)
    if len(saves) > 1 or len(contexts) > 1:
        raise PlanError(
            "the graph has %d Save Latent and %d Motion Context nodes; the "
            "Planner needs exactly one of each to know which clip it is "
            "making" % (len(saves), len(contexts)))
    if not saves or not contexts:
        if clip_count == 1:
            return 1, 0
        raise PlanError(
            "a %d clip plan needs H3 Motion Context and H3 Motion Context "
            "Save Latent in the graph: Save Latent's clip_index says which "
            "clip is being made" % clip_count)

    sid, save = saves[0]
    k = int(_widget(sid, save, "clip_index", "Save Latent") or 0)
    if k < 1:
        raise PlanError(
            "Save Latent clip_index is 0 (auto-numbered). The Planner needs "
            "numbered slots: Load 0 / Save 1 for the first clip.")
    loads = _nodes_of(prompt, LOAD_NODE)
    if len(loads) == 1:
        lid, load = loads[0]
        li = _widget(lid, load, "clip_index", "Load Latent")
        if li is not None and int(li) != k - 1:
            raise PlanError(
                "Load Latent clip_index is %d but Save is %d. Making clip %d "
                "means continuing from clip %d: use Load %d / Save %d, or "
                "let the Chain node set them."
                % (int(li), k, k, k - 1, k - 1, k))
    cid, ctx = contexts[0]
    head = int(_widget(cid, ctx, "context_length", "Motion Context") or 0)
    return k, head


# -------------------------------------------------------------------- node

class MiniMaxH3MotionContextPlanner:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "plan": ("STRING", {
                    "multiline": True,
                    "default": "[0:00]\n\n[end 0:10]\n",
                    "tooltip": "One [timecode] line per cut, the segment's "
                               "prompt under it, [end timecode] last. "
                               "Optional [prefix] and [suffix] blocks are "
                               "added to every prompt. Timecodes: 62.5, "
                               "1:02.5 or 0:01:02.500."}),
            },
            "optional": {
                "context_latent": ("LATENT", {
                    "tooltip": "Same Load Latent output that feeds Motion "
                               "Context. With Audio Lock in the graph it "
                               "carries where the previous clip really "
                               "ended, which is checked against the plan."}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING", "FLOAT", "INT", "FLOAT", "INT", "STRING")
    RETURN_NAMES = ("prompt", "seconds", "frames", "song_offset",
                    "tail_trim", "report")
    FUNCTION = "plan_clip"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = ("Plan a chain from timecodes. For the clip being made "
                   "(Save Latent's clip_index) it outputs the prompt and the "
                   "length to render, snapped to H3's frame grid with each "
                   "cut as close to its timecode as the grid allows. "
                   "song_offset goes to Audio Lock, tail_trim to the Trim "
                   "node.")

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        # the clip being made is read from the graph, which ComfyUI does
        # not hand to IS_CHANGED, so caching on the widgets alone would
        # replay the previous clip's prompt and length. The node is
        # instant; always run it.
        return float("NaN")

    def plan_clip(self, plan, context_latent=None, prompt=None,
                  unique_id=None):
        parsed = parse_plan(plan)
        count = len(parsed["segments"])
        k, head = graph_position(prompt, count)
        result = compute(parsed, head)
        if k > count:
            raise PlanError(
                "the plan has %d clip(s) and all of them are done: clip %d "
                "is past its end. Reset the chain or add segments."
                % (count, k))
        clip = result["clips"][k - 1]
        fps = result["fps"]

        song_end = (context_latent or {}).get(SONG_END_KEY)
        if song_end is not None and k > 1:
            expected = result["song_offset"] + clip["start"] / float(fps)
            if abs(float(song_end) - expected) > 0.5 / fps:
                raise PlanError(
                    "clip %d should start at %s but clip %d really ended "
                    "at %s. The timecodes or the head changed after clip "
                    "%d was made: regenerate from the first clip whose "
                    "plan changed, or restore the plan."
                    % (k, format_timecode(expected), k - 1,
                       format_timecode(float(song_end)), k - 1))

        is_last = k == count
        tail = result["tail_trim"] if is_last else 0
        text = report(result, current=k)
        _LOG.info("h3_motion_context: planner clip %d/%d, %d frames "
                  "(%d delivered), cut at %s, tail_trim %d", k, count,
                  clip["generated"], clip["delivered"],
                  format_timecode(result["song_offset"]
                                  + clip["end"] / float(fps)), tail)
        return {
            "ui": {"h3_plan": [{"clips": count, "current": k}]},
            "result": (compose_prompt(parsed, k - 1),
                       clip["generated"] / float(fps),
                       clip["generated"],
                       float(result["song_offset"]),
                       tail,
                       text),
        }


def register_planner_routes():
    """POST /h3_motion_context/plan: the same computation for the editor's
    live preview, so the table and the render can never disagree."""
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    server = getattr(PromptServer, "instance", None)
    if server is None or getattr(register_planner_routes, "_done", False):
        return

    from .csrf_guard import require_same_origin

    @server.routes.post("/h3_motion_context/plan")
    @require_same_origin
    async def _plan_route(request):
        data = await request.json()
        try:
            parsed = parse_plan(data.get("plan") or "")
        except PlanError as e:
            return web.json_response({"ok": False, "error": str(e)})
        try:
            result = compute(parsed, int(data.get("head") or 0))
        except PlanError as e:
            # the editor still needs the parsed plan to show the rows
            # around the error
            return web.json_response({"ok": False, "error": str(e),
                                      "plan": parsed})
        return web.json_response({
            "ok": True,
            "plan": parsed,
            "clips": result["clips"],
            "tail_trim": result["tail_trim"],
            "song_offset": result["song_offset"],
            "fps": result["fps"],
            "report": report(result),
        })

    register_planner_routes._done = True


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3MotionContextPlanner": MiniMaxH3MotionContextPlanner,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3MotionContextPlanner": "H3 Motion Context Planner",
}
