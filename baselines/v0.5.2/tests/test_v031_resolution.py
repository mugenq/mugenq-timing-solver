"""v0.3.1: no fabricated waveform precision, exact bin alignment and LOD."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pytest
from audio_resolution import (stream_peaks_and_pcm, exact_envelope,
                              WaveformCache)
from node_engine import AudioData, Curve, default_registry


def cache_for(samples, rate=48000, coarse_ms=2):
    bin_frames = round(rate*coarse_ms/1000)
    lo, hi = exact_envelope(samples, bin_frames)
    return WaveformCache(rate,len(samples),lo,hi,bin_frames,samples.copy())


def test_streaming_chunk_boundaries_and_incomplete_final_bin():
    samples = np.array([2.,-8.,1.,9.,3.,-4.,7.,0.,5.,-9.,2.], np.float32)
    chunks = (samples[:1], samples[1:4], samples[4:10], samples[10:])
    lo,hi,total,raw = stream_peaks_and_pcm(chunks,3)
    np.testing.assert_array_equal(lo,[-8,-4,0,-9])
    np.testing.assert_array_equal(hi,[2,9,7,2])
    np.testing.assert_array_equal(raw,samples)
    assert total==11


def test_large_audio_falls_back_without_lying_about_precision():
    samples = np.arange(200,dtype=np.float32)
    lo,hi,count,raw = stream_peaks_and_pcm((samples[:60],samples[60:]),10,
                                          max_pcm_bytes=100)
    assert raw is None
    cache = WaveformCache(44100,count,lo,hi,10,raw)
    assert not cache.has_pcm
    a,b,dt = cache.analysis_envelope(.01)
    assert a is cache.coarse_min and b is cache.coarse_max
    assert dt==pytest.approx(10/44100)
    assert cache.level_for_pixel(.01)[0]==10


def test_native_pcm_is_visible_and_finest_level_is_one_sample():
    x=np.zeros(480,dtype=np.float32)
    x[61]=1.0
    cache=cache_for(x)
    assert cache.native_step_ms == pytest.approx(1000/48000)
    assert cache.level_for_pixel(1/48000)[0]==1
    assert cache.extrema_for_seconds(61/48000,62/48000,tier_frames=1)==(1.,1.)
    assert cache.extrema_for_seconds(59/48000,63/48000,tier_frames=1)==(0.,1.)


def test_refinement_uses_true_source_samples_not_interpolation():
    raw=np.zeros(4800,dtype=np.float32)
    raw[102]=0.8   # t=2.125 ms, distinct within one 2ms bin
    raw[118]=-0.3
    cache=cache_for(raw)
    coarse,_,coarse_dt=cache.analysis_envelope(2)
    fine,_,fine_dt=cache.analysis_envelope(0.25)
    assert len(fine) > len(coarse)
    assert fine_dt == pytest.approx(12/48000)
    assert coarse_dt == pytest.approx(96/48000)
    assert fine[8] == 0
    assert fine[9] == pytest.approx(-.3)
    assert len(fine)==400


def test_native_timebase_propagates_to_candidate_times():
    rate=48000
    x=np.zeros(rate//2,dtype=np.float32)
    x[2411:2480]=.7
    cache=cache_for(x)
    lo,hi,step=cache.analysis_envelope(.25)
    audio=AudioData('test.wav',cache.duration,lo,hi,step,rate)
    r=default_registry()
    energy=r.defs['wave.energy'].process({'audio': audio},{'smoothing_ms':0.0},audio)['curve']
    assert energy.sample_step_s==pytest.approx(12/48000)
    assert energy.time_at(100) == pytest.approx(.025)
    transient=r.defs['signal.transients'].process({'curve':energy},
            {'smoothing_ms':0.,'lag_ms':.25},audio)['curve']
    assert transient.sample_step_s==pytest.approx(energy.sample_step_s)
    result=r.defs['signal.peaks'].process({'curve':transient},
            {'percentile':30.,'min_spacing_ms':0.},audio)['candidates']
    assert len(result.times)>0
    assert np.all(np.abs(result.times/step-np.round(result.times/step))<1e-7)
    assert result.times.max() <= cache.duration


def test_old_plugin_curve_timebase_still_supported():
    x=Curve(np.zeros(17,dtype=np.float32),4.,start_time=2.)
    assert x.time_at(16)==6.
    assert x.index_at(6.)==16.


def test_cancelled_decode_does_not_complete():
    with pytest.raises(InterruptedError):
        stream_peaks_and_pcm([np.ones(8,dtype=np.float32)],2,cancelled=lambda:True)
