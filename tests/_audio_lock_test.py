"""Audio Lock test: song position across a chain, with known answers.

Needs real torch (and torchaudio for the resample case), no ComfyUI and
no GPU. Run it with ComfyUI's own Python:

    python_embeded\\python.exe tests\\_audio_lock_test.py

The song is a ramp whose sample value is its own time in seconds, and the
fake audio VAE returns the mean of each 800-sample hop, so every latent
step reads back the song time it was cut from. That turns "is the window
in the right place" into plain arithmetic.
"""

import os
import sys
import tempfile
import types

import torch

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PKG_DIR = os.path.dirname(_TESTS_DIR)
sys.path.insert(0, _TESTS_DIR)
from _mock_harness import make_mm  # noqa: E402

FPS, HZ, VAE_SR = 24.0, 40.0, 32000


class NestedTensor:
    def __init__(self, tensors):
        self.tensors = list(tensors)
        self.is_nested = True

    def unbind(self):
        return list(self.tensors)


def install_fakes(outdir):
    mm = make_mm()
    for name in ("comfy", "comfy.ldm", "comfy.ldm.minimax"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["comfy.ldm.minimax.model"] = mm
    sys.modules["comfy"].ldm = sys.modules["comfy.ldm"]
    sys.modules["comfy.ldm"].minimax = sys.modules["comfy.ldm.minimax"]
    sys.modules["comfy.ldm.minimax"].model = mm
    nt = types.ModuleType("comfy.nested_tensor")
    nt.NestedTensor = NestedTensor
    sys.modules["comfy.nested_tensor"] = nt
    sys.modules["comfy"].nested_tensor = nt
    cu = types.ModuleType("comfy.utils")
    sys.modules["comfy.utils"] = cu
    sys.modules["comfy"].utils = cu
    sys.modules["node_helpers"] = types.ModuleType("node_helpers")
    fp = types.ModuleType("folder_paths")
    fp.get_output_directory = lambda: outdir

    def get_save_image_path(prefix, out, *a):
        sub, name = os.path.split(prefix)
        folder = os.path.join(out, sub)
        os.makedirs(folder, exist_ok=True)
        return folder, name, 1, sub, prefix
    fp.get_save_image_path = get_save_image_path
    sys.modules["folder_paths"] = fp

    pkg = types.ModuleType("h3mc_pkg")
    pkg.__path__ = [_PKG_DIR]
    pkg.__file__ = os.path.join(_PKG_DIR, "__init__.py")
    sys.modules["h3mc_pkg"] = pkg
    import h3mc_pkg.nodes as nodes
    import h3mc_pkg.audio_lock as audio_lock
    return nodes, audio_lock


class FakeAudioVAE:
    audio_sample_rate = VAE_SR
    hop = 800

    def __init__(self):
        self.last_in = None

    def encode(self, x):  # [B, L, C], like comfy.sd.VAE
        w = x.movedim(-1, 1)  # [B, C, L]
        self.last_in = w
        b, c, n = w.shape
        steps = -(-n // self.hop)
        w = torch.nn.functional.pad(w, (0, steps * self.hop - n))
        m = w.reshape(b, c, steps, self.hop).mean(-1)  # [B, C, T]
        return m.unsqueeze(1).repeat(1, 32, 1, 1)      # [B, 32, C, T]


def empty_av(frames):
    latent_t = (frames - 5) // 17 * 5 + 2
    audio_t = int(round(frames * 5 / 3))
    return {"samples": NestedTensor((
        torch.zeros(1, 16, latent_t, 30, 54),
        torch.zeros(1, 32, 2, audio_t)))}


def ramp(seconds, sr, channels=1):
    t = torch.arange(int(seconds * sr), dtype=torch.float64) / sr
    return {"waveform": t.float().reshape(1, 1, -1).repeat(1, channels, 1),
            "sample_rate": sr}


def step_time(z, k):
    """Song time the fake VAE says latent step k was cut from (its centre)."""
    return float(z[0, 0, 0, k])


def close(a, b, tol, what):
    assert abs(a - b) <= tol, "%s: got %.6f, expected %.6f" % (what, a, b)


def main():
    outdir = tempfile.mkdtemp()
    nodes, audio_lock = install_fakes(outdir)
    lock = audio_lock.MiniMaxH3MotionContextAudioLock()
    trim_node = nodes.MiniMaxH3MotionContextTrim()
    saver = nodes.MiniMaxH3MotionContextSaveLatent()
    loader = nodes.MiniMaxH3MotionContextLoadLatent()
    vae = FakeAudioVAE()
    frames, head = 124, 22
    hop_s = 1.0 / HZ

    # --- clip 1: mono song at the VAE rate, starts 10 s in, no head ---
    song = ramp(60.0, VAE_SR, channels=1)
    av = empty_av(frames)
    latent1, exact1, pos1 = lock.lock(av, vae, song, trim_frames=0,
                                      song_offset=10.0)
    z = latent1["samples"].unbind()[1]
    assert tuple(z.shape) == (1, 32, 2, 207), tuple(z.shape)
    assert vae.last_in.shape[1] == 2, "mono song was not made stereo"
    close(pos1, 10.0, 1e-9, "clip 1 position")
    close(step_time(z, 0), 10.0 + hop_s / 2, 1e-4, "clip 1 first step")
    close(step_time(z, 206), 10.0 + 206.5 * hop_s, 1e-4, "clip 1 last step")
    mask = latent1["noise_mask"].unbind()
    assert float(mask[0].min()) == 1.0 and float(mask[1].max()) == 0.0
    end1 = 10.0 + frames / FPS
    close(latent1[nodes.SONG_END_KEY], end1, 1e-9, "clip 1 song end")
    assert exact1["waveform"].shape[-1] == round(frames / FPS * VAE_SR)
    close(float(exact1["waveform"][0, 0, 0]), 10.0, 1e-4, "exact_audio start")
    print("clip 1: mono made stereo, 207 steps from 10.000s, ends %.4fs"
          % end1)

    # --- carry it across runs the way a chain does ---
    sampled = dict(latent1)  # samplers copy the latent dict, keys included
    (path,) = saver.save(sampled, "h3_context/clip", clip_index=1)
    ctx = loader.load("h3_context", clip_index=1)[0]
    close(ctx[nodes.SONG_END_KEY], end1, 1e-9, "song end through disk")
    print("song position survives Save / Load:", os.path.basename(path))

    # --- clip 2: 48 kHz stereo song, 22-frame head ---
    song48 = ramp(60.0, 48000, channels=2)
    latent2, exact2, pos2 = lock.lock(empty_av(frames), vae, song48,
                                      trim_frames=head, song_offset=99.0,
                                      context_latent=ctx)
    close(pos2, end1, 1e-9, "clip 2 ignores song_offset, starts at clip 1 end")
    z2 = latent2["samples"].unbind()[1]
    sampled_start = end1 - head / FPS
    # the resampler's sinc filter has a DC gain of about 1.0004, which on
    # a ramp sitting at 14 s reads as several ms. Solve two interior steps
    # for gain and start separately, so only the placement is judged.
    a, b = step_time(z2, 1), step_time(z2, 100)
    gain = (b - a) / (99 * hop_s)
    close(gain, 1.0, 1e-3, "clip 2 resample gain")
    close(a / gain - 1.5 * hop_s, sampled_start, 1e-4, "clip 2 window start")
    end2 = end1 + (frames - head) / FPS
    close(latent2[nodes.SONG_END_KEY], end2, 1e-9, "clip 2 song end")

    # the Trim node takes the head off and leaves the delivered window
    images = torch.zeros(frames, 4, 4, 3)
    _, delivered = trim_node.trim(images, head, audio=exact2, fps=FPS)
    wav = delivered["waveform"]
    assert wav.shape[-1] == round((frames - head) / FPS * 48000)
    close(float(wav[0, 0, 0]), end1, 1e-4, "delivered clip 2 starts at join")
    close(float(wav[0, 0, -1]), end2 - 1 / 48000.0, 1e-4,
          "delivered clip 2 ends")
    print("clip 2: window from %.4fs (22-frame head), Trim delivers "
          "%.4fs..%.4fs, seamless with clip 1" % (sampled_start, end1, end2))

    # --- a context latent from a clip that was not locked: refuse ---
    try:
        lock.lock(empty_av(frames), vae, song, trim_frames=head,
                  context_latent={"samples": ctx["samples"]})
    except ValueError as e:
        assert "song position" in str(e), str(e)
    else:
        raise AssertionError("missing song position was not refused")
    print("context without a song position: refused")

    # --- past the end of the song: silence in the waveform ---
    short = ramp(3.0, VAE_SR)
    latent3, exact3, _ = lock.lock(empty_av(frames), vae, short,
                                   trim_frames=0, song_offset=1.0)
    z3 = latent3["samples"].unbind()[1]
    assert float(z3[0, 0, 0, -1]) == 0.0, "tail past the song is not silence"
    assert float(exact3["waveform"][0, 0, -1]) == 0.0
    close(step_time(z3, 0), 1.0 + hop_s / 2, 1e-4, "short song start")
    print("song shorter than the clip: the rest is waveform silence")

    print("audio lock test passed")


if __name__ == "__main__":
    main()
