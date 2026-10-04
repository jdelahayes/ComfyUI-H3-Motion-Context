"""Lock a song into an H3 clip, positioned for a Motion Context chain.

For music video: the soundtrack already exists, so instead of letting H3
generate sound, the exact song is encoded into the target audio latent
and masked out of denoising. ComfyUI's H3 support handles a nested
noise_mask natively: preserved rows are injected clean and run at the
cond timestep, so H3 denoises only the picture while attending to the
song. Nothing here patches the model.

What this node adds over a plain lock is the position in the song.

In a chain the new clip's timeline starts trim_frames BEFORE the point
where the previous clip's delivered picture ended, because the pinned
head replays the previous clip's tail. So the song window for clip N+1
starts at

    song_end(N) - trim_frames / FPS

and the Trim node cuts the head off picture and sound together. Getting
that offset by hand for every clip is where lip sync goes wrong, so the
position travels with the latent instead: this node writes the song
position where the clip's delivered picture ENDS into the latent dict,
the sampler copies it through, Save Latent stores it in the file's
metadata, and Load Latent hands it back on the context_latent the next
clip reads. First clip of a chain (no context_latent): song_offset.

The audio window is cut in the waveform, not in the latent. Padding a
latent with zeros is not silence: latents are normalised, so zero is the
mean latent, which decodes to a faint wash. Out-of-song samples are
zeros in the waveform before encoding, which is silence.
"""

import logging

import torch
import torch.nn.functional as F

try:
    import torchaudio
except ImportError:
    torchaudio = None

from .nodes import (AUDIO_HZ, FPS, SONG_END_KEY, _pixel_frames,
                    _streams_from_latent, _video_from_latent)

_LOG = logging.getLogger("h3_motion_context")


def _window(waveform, sr, start_s, n):
    """n samples of waveform [B, C, L] starting at start_s seconds.

    Anything before the song's start or past its end is zeros, which is
    silence in the waveform domain. Returns (window, silent_front,
    silent_back) in samples.
    """
    length = int(waveform.shape[-1])
    start = int(round(start_s * sr))
    lo, hi = max(0, start), min(length, start + n)
    seg = waveform[..., lo:hi] if hi > lo else waveform[..., :0]
    front = min(n, max(0, lo - start))
    back = n - front - int(seg.shape[-1])
    if front or back:
        seg = F.pad(seg, (front, back))
    return seg, front, back


def _fit(waveform, n):
    """Truncate or zero-pad the last axis to exactly n samples."""
    have = int(waveform.shape[-1])
    if have > n:
        return waveform[..., :n]
    if have < n:
        return F.pad(waveform, (0, n - have))
    return waveform


def _stereo(waveform):
    """[B, C, L] -> [B, 2, L]. The H3 audio VAE encodes channels
    separately and the layout reserves two, so a mono song would come
    out as [1, 32, 1, T] and fail against the [1, 32, 2, T] target."""
    c = int(waveform.shape[1])
    if c == 2:
        return waveform
    if c == 1:
        return waveform.repeat(1, 2, 1)
    _LOG.warning("h3_motion_context: song has %d channels; locking the "
                 "first two.", c)
    return waveform[:, :2]


class MiniMaxH3MotionContextAudioLock:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "av_latent": ("LATENT", {
                    "tooltip": "The empty AV latent from the stock H3 "
                               "conditioning node. The locked latent goes "
                               "on to the sampler."}),
                "audio_vae": ("VAE", {"tooltip": "H3 audio VAE."}),
                "audio": ("AUDIO", {
                    "tooltip": "The WHOLE song. This node cuts the window "
                               "for the clip; do not pre-trim it."}),
                "trim_frames": ("INT", {
                    "default": 0, "min": 0, "max": 4096,
                    "tooltip": "Wire from H3 Motion Context, the same value "
                               "the Trim node gets. The clip's timeline "
                               "starts that many frames before the join, "
                               "so the song window starts that much "
                               "earlier too."}),
                "song_offset": ("FLOAT", {
                    "default": 0.0, "min": 0.0, "max": 36000.0,
                    "step": 0.001,
                    "tooltip": "Seconds into the song where the FIRST clip "
                               "starts. Later clips take their position "
                               "from context_latent and ignore this."}),
            },
            "optional": {
                "context_latent": ("LATENT", {
                    "tooltip": "Same Load Latent output that feeds Motion "
                               "Context. Carries the song position where "
                               "the previous clip ended, so each clip "
                               "picks up exactly there."}),
            },
        }

    RETURN_TYPES = ("LATENT", "AUDIO", "FLOAT")
    RETURN_NAMES = ("av_latent", "exact_audio", "song_position")
    FUNCTION = "lock"
    CATEGORY = "conditioning/minimax"
    DESCRIPTION = ("Encode the exact song into the H3 target audio and mask "
                   "it out of denoising, so H3 renders picture only, in sync "
                   "with your track. Positions the song window for a Motion "
                   "Context chain automatically. exact_audio is the song cut "
                   "to this clip, untrimmed: wire it into the Trim node.")

    def lock(self, av_latent, audio_vae, audio, trim_frames, song_offset=0.0,
             context_latent=None):
        import comfy.nested_tensor

        samples = av_latent.get("samples")
        if not getattr(samples, "is_nested", False):
            raise ValueError(
                "h3_motion_context: Audio Lock needs the joint MiniMax H3 AV "
                "latent from the stock conditioning node.")
        parts = _streams_from_latent(av_latent)
        if len(parts) < 2:
            raise ValueError("h3_motion_context: AV latent has no audio "
                             "stream to lock.")
        video, template = parts[0], parts[1]
        frame_count = _pixel_frames(int(_video_from_latent(av_latent).shape[2]))
        target_t = int(template.shape[-1])

        trim = int(trim_frames)
        if trim >= frame_count:
            raise ValueError(
                "h3_motion_context: trim_frames %d leaves nothing of a %d "
                "frame clip." % (trim, frame_count))

        if context_latent is not None:
            song_end = context_latent.get(SONG_END_KEY)
            if song_end is None:
                raise ValueError(
                    "h3_motion_context: context_latent carries no song "
                    "position, so the previous clip was not rendered "
                    "through Audio Lock (or was saved by an older version). "
                    "Re-render it with Audio Lock, or unwire context_latent "
                    "from this node and set song_offset to where this "
                    "clip's delivered picture starts.")
            start = float(song_end)
            src = "previous clip"
        else:
            start = float(song_offset)
            src = "song_offset"
        sampled_start = start - trim / float(FPS)
        delivered_end = start + (frame_count - trim) / float(FPS)

        waveform = audio["waveform"][:1]
        sr = int(audio["sample_rate"])
        song_s = int(waveform.shape[-1]) / float(sr)

        # the target grid: step k covers [k, k+1) / AUDIO_HZ from the
        # clip's first frame, so encode exactly target_t steps of song
        vae_sr = int(getattr(audio_vae, "audio_sample_rate", 32000))
        enc_s = target_t / AUDIO_HZ
        enc, _, _ = _window(waveform, sr, sampled_start,
                            int(round(enc_s * sr)))
        if sr != vae_sr:
            if torchaudio is None:
                raise RuntimeError(
                    "h3_motion_context: the song is %d Hz, the VAE wants "
                    "%d Hz and torchaudio is not available to resample."
                    % (sr, vae_sr))
            enc = torchaudio.functional.resample(enc, sr, vae_sr)
        enc = _fit(enc, int(round(enc_s * vae_sr)))
        z = audio_vae.encode(_stereo(enc).movedim(1, -1))

        if tuple(z.shape[1:3]) != tuple(template.shape[1:3]):
            raise ValueError(
                "h3_motion_context: encoded song latent is %s, the clip's "
                "audio latent is %s. Is audio_vae the H3 audio VAE?"
                % (tuple(z.shape), tuple(template.shape)))
        diff = int(z.shape[-1]) - target_t
        if abs(diff) > 1:
            raise RuntimeError(
                "h3_motion_context: %.3fs of song encoded to %d audio "
                "steps, expected %d. The audio VAE's hop changed; refusing "
                "rather than locking a drifting soundtrack."
                % (enc_s, int(z.shape[-1]), target_t))
        if diff > 0:
            z = z[..., :target_t]
        elif diff < 0:
            z = torch.cat([z, z[..., -1:]], dim=-1)
        z = z.to(device=template.device, dtype=template.dtype)

        video_mask = torch.ones_like(video)
        prior = av_latent.get("noise_mask")
        if getattr(prior, "is_nested", False):
            video_mask = prior.unbind()[0]
            _LOG.info("h3_motion_context: kept the incoming video noise_mask.")

        locked = dict(av_latent)
        locked["samples"] = comfy.nested_tensor.NestedTensor((video, z))
        locked["noise_mask"] = comfy.nested_tensor.NestedTensor(
            (video_mask, torch.zeros_like(z)))
        locked[SONG_END_KEY] = delivered_end

        # the song cut to the whole sampled clip, pinned head included, at
        # the song's own rate: the Trim node takes the head off and leaves
        # exactly the delivered window
        exact, front, back = _window(waveform, sr, sampled_start,
                                     int(round(frame_count / float(FPS) * sr)))
        if front or back:
            _LOG.warning(
                "h3_motion_context: clip covers %.3fs..%.3fs of a %.3fs "
                "song; %.3fs before and %.3fs after it are silence.",
                sampled_start, sampled_start + frame_count / float(FPS),
                song_s, front / float(sr), back / float(sr))

        _LOG.info(
            "h3_motion_context: song locked from %s, delivered %.3fs..%.3fs "
            "(sampled from %.3fs, %d frame head), %d audio steps",
            src, start, delivered_end, sampled_start, trim, target_t)
        return (locked, {"waveform": exact, "sample_rate": sr}, start)


NODE_CLASS_MAPPINGS = {
    "MiniMaxH3MotionContextAudioLock": MiniMaxH3MotionContextAudioLock,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MiniMaxH3MotionContextAudioLock": "H3 Motion Context Audio Lock",
}
